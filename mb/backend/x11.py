"""Linux/X11 backend: capture by grabbing, inject with XTEST, clipboard by
speaking the ICCCM selection protocol directly.

Three X connections, because Xlib display handles are not safe to share across
threads and separate connections are cheaper to reason about than locks:

    dpy         the capture loop -- grabs, edge polling, hotkeys
    dpy_inject  XTEST, driven from the network thread
    dpy_clip    the clipboard thread, which owns the selection

Each loop is a select() over the X connection's file descriptor plus a
self-pipe, so another thread can wake it up to hand over work without either
polling or sharing a display.
"""

import ctypes
import os
import select
import struct
import sys
import threading
import time
from ctypes import byref, c_int, c_long, c_uint, c_ulong

from .. import keymap
from . import _xlib as X
from .base import Backend

CAPTURE_EVENT_MASK = (
    X.POINTER_MOTION_MASK | X.BUTTON_PRESS_MASK | X.BUTTON_RELEASE_MASK
)

# Modifiers that must be ignored when grabbing a hotkey, or the hotkey stops
# working the moment someone leaves Num Lock on.
_LOCK_COMBOS = (0, X.LOCK_MASK, X.MOD2_MASK, X.LOCK_MASK | X.MOD2_MASK)

_MOD_TO_X = {
    keymap.MOD_SHIFT: X.SHIFT_MASK,
    keymap.MOD_CTRL: X.CONTROL_MASK,
    keymap.MOD_ALT: X.MOD1_MASK,
    keymap.MOD_META: X.MOD4_MASK,
}

_X_TO_MOD = (
    (X.SHIFT_MASK, keymap.MOD_SHIFT),
    (X.CONTROL_MASK, keymap.MOD_CTRL),
    (X.MOD1_MASK, keymap.MOD_ALT),
    (X.MOD4_MASK, keymap.MOD_META),
    (X.LOCK_MASK, keymap.MOD_CAPS),
    (X.MOD2_MASK, keymap.MOD_NUM),
)


class Waker:
    """A self-pipe, so one thread can interrupt another's select()."""

    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        os.set_blocking(self.write_fd, False)

    def wake(self):
        try:
            os.write(self.write_fd, b"\x01")
        except BlockingIOError:
            pass        # pipe full: a wakeup is already pending, which is enough

    def drain(self):
        try:
            while os.read(self.read_fd, 4096):
                pass
        except BlockingIOError:
            pass

    def close(self):
        for fd in (self.read_fd, self.write_fd):
            try:
                os.close(fd)
            except OSError:
                pass


class X11Backend(Backend):
    name = "x11"

    def __init__(self, clipboard_config=None):
        if X.libX11 is None:
            raise X.X11Unavailable("libX11 is not available")
        if not os.environ.get("DISPLAY"):
            raise X.X11Unavailable("DISPLAY is not set; is this an X11 session?")

        self.dpy = X.libX11.XOpenDisplay(None)
        self.dpy_inject = X.libX11.XOpenDisplay(None)
        if not self.dpy or not self.dpy_inject:
            raise X.X11Unavailable(f"cannot connect to X display {os.environ['DISPLAY']!r}")

        self.screen = X.libX11.XDefaultScreen(self.dpy)
        self.root = X.libX11.XRootWindow(self.dpy, self.screen)
        self.width = X.libX11.XDisplayWidth(self.dpy, self.screen)
        self.height = X.libX11.XDisplayHeight(self.dpy, self.screen)

        self._check_xtest()
        self._keycode_of = self._build_keycode_map()

        # Without this, holding a key produces a stream of release/press pairs
        # and the far machine sees your key bouncing instead of repeating.
        X.libX11.XkbSetDetectableAutoRepeat(self.dpy, True, None)
        X.libXtst.XTestGrabControl(self.dpy_inject, True)

        self._handler = lambda *a: None
        self._capturing = False
        self._want_capture = False
        self._hotkeys = {}
        self._held_mods = 0
        self._blank_cursor = self._make_blank_cursor()
        self._cursor_hidden = False
        self._last_pos = (0, 0)
        self._injected_keys = set()
        self._injected_buttons = set()
        self._scroll_remainder = [0, 0]
        self._waker = Waker()
        self._running = False
        self._thread = None
        self._inject_lock = threading.Lock()
        self.clipboard = Clipboard(clipboard_config or {})

    # ---------------------------------------------------------------- setup
    def _check_xtest(self):
        ev, er, major, minor = c_int(), c_int(), c_int(), c_int()
        if not X.libXtst.XTestQueryExtension(
                self.dpy, byref(ev), byref(er), byref(major), byref(minor)):
            raise X.X11Unavailable(
                "this X server has no XTEST extension, so mousebridge cannot "
                "move the pointer or type. Check your Xorg build."
            )

    def _build_keycode_map(self):
        """Canonical key -> X keycode.

        Every modern X server uses evdev keycodes, where the X keycode is the
        evdev code plus 8. Rather than trust that, check it against two keys
        whose position no keyboard layout moves -- Escape and Tab -- and fall
        back to per-key keysym lookup if the check fails.
        """
        def keysym_of(keycode):
            count = c_int()
            ptr = X.libX11.XGetKeyboardMapping(self.dpy, keycode, 1, byref(count))
            if not ptr:
                return 0
            try:
                return ptr[0]
            finally:
                X.libX11.XFree(ptr)

        offset_ok = (
            keysym_of(keymap.to_x11(keymap.KEY_ESC)) == 0xFF1B
            and keysym_of(keymap.to_x11(keymap.KEY_TAB)) == 0xFF09
        )
        if offset_ok:
            return None        # None means "use the +8 identity"

        print(
            "mousebridge: this X server does not use evdev keycodes; falling back "
            "to keysym lookup (keys outside the standard set may not map)",
            file=sys.stderr,
        )
        table = {}
        for key, sym in keymap.FALLBACK_KEYSYMS.items():
            code = X.libX11.XKeysymToKeycode(self.dpy, sym)
            if code:
                table[key] = code
        return table

    def _x_keycode(self, key):
        if self._keycode_of is None:
            return keymap.to_x11(key)
        return self._keycode_of.get(key, 0)

    def _make_blank_cursor(self):
        data = ctypes.create_string_buffer(b"\x00" * 8, 8)
        pixmap = X.libX11.XCreateBitmapFromData(self.dpy, self.root, data, 8, 8)
        colour = X.XColor()
        cursor = X.libX11.XCreatePixmapCursor(
            self.dpy, pixmap, pixmap, byref(colour), byref(colour), 0, 0)
        X.libX11.XFreePixmap(self.dpy, pixmap)
        return cursor

    # ------------------------------------------------------------- geometry
    def screen_size(self):
        return self.width, self.height

    def cursor_position(self):
        got = X.query_pointer(self.dpy_inject, self.root)
        return (got[0], got[1]) if got else self._last_pos

    def warp_cursor(self, x, y):
        with self._inject_lock:
            X.libX11.XWarpPointer(self.dpy_inject, 0, self.root, 0, 0, 0, 0, int(x), int(y))
            X.libX11.XFlush(self.dpy_inject)

    def modifier_state(self):
        got = X.query_pointer(self.dpy_inject, self.root)
        if not got:
            return self._held_mods
        state, mods = got[2], 0
        for x_bit, mod in _X_TO_MOD:
            if state & x_bit:
                mods |= mod
        return mods

    # ------------------------------------------------------------ lifecycle
    def start(self, handler):
        self._handler = handler
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="x11-capture", daemon=True)
        self._thread.start()
        self.clipboard.start()

    def stop(self):
        self._running = False
        self._waker.wake()
        if self._thread:
            self._thread.join(timeout=2)
        self.clipboard.stop()
        self._set_capture_now(False)
        self._waker.close()

    def set_capturing(self, capturing):
        self._want_capture = bool(capturing)
        self._waker.wake()

    def register_hotkeys(self, hotkeys):
        self._hotkeys = dict(hotkeys)
        self._waker.wake()

    # ----------------------------------------------------------- main loop
    def _loop(self):
        fd = X.libX11.XConnectionNumber(self.dpy)
        self._apply_hotkey_grabs()
        event = X.XEvent()
        while self._running:
            if self._want_capture != self._capturing:
                self._set_capture_now(self._want_capture)
            # A pending-event count of zero does not mean the socket is empty,
            # so flush and check before blocking, or events can sit unread.
            X.libX11.XFlush(self.dpy)
            if X.libX11.XPending(self.dpy) == 0:
                try:
                    select.select([fd, self._waker.read_fd], [], [], 0.25)
                except (OSError, ValueError):
                    break
                self._waker.drain()
            while self._running and X.libX11.XPending(self.dpy) > 0:
                X.libX11.XNextEvent(self.dpy, byref(event))
                self._dispatch(event)

    def _dispatch(self, event):
        kind = event.type
        if kind == X.MOTION_NOTIFY:
            self._on_motion(event.xmotion)
        elif kind in (X.BUTTON_PRESS, X.BUTTON_RELEASE):
            self._on_button(event.xbutton, kind == X.BUTTON_PRESS)
        elif kind in (X.KEY_PRESS, X.KEY_RELEASE):
            self._on_key(event.xkey, kind == X.KEY_PRESS)

    def _on_motion(self, ev):
        if not self._capturing:
            return
        cx, cy = self.width // 2, self.height // 2
        dx = ev.x_root - self._last_pos[0]
        dy = ev.y_root - self._last_pos[1]
        self._last_pos = (ev.x_root, ev.y_root)
        if dx or dy:
            self._handler("motion", dx, dy)
        # Re-centre only near the edges of a generous dead zone. Warping on
        # every event would double the number of round trips to the server for
        # no benefit; the warp itself lands exactly on centre, producing a
        # zero delta that the check above discards.
        if abs(ev.x_root - cx) > self.width // 4 or abs(ev.y_root - cy) > self.height // 4:
            X.libX11.XWarpPointer(self.dpy, 0, self.root, 0, 0, 0, 0, cx, cy)
            X.libX11.XFlush(self.dpy)
            self._last_pos = (cx, cy)

    def _on_button(self, ev, pressed):
        if not self._capturing:
            return
        button = ev.button
        if button in keymap.X_SCROLL_BUTTONS:
            if pressed:            # X sends a matching release we do not need
                dx, dy = keymap.X_SCROLL_BUTTONS[button]
                self._handler("scroll", dx, dy)
            return
        code = keymap.X_BUTTON_TO_BTN.get(button)
        if code is not None:
            self._handler("button", code, pressed)

    def _on_key(self, ev, pressed):
        key = self._canonical_key(ev.keycode)
        if key is None:
            return
        mod = keymap.MODIFIER_KEYS.get(key)
        if mod:
            if pressed:
                self._held_mods |= mod
            else:
                self._held_mods &= ~mod

        if pressed and self._match_hotkey(key):
            return
        if self._capturing:
            self._handler("key", key, pressed)

    def _canonical_key(self, keycode):
        if self._keycode_of is None:
            return keymap.from_x11(keycode)
        for key, code in self._keycode_of.items():
            if code == keycode:
                return key
        return None

    def _match_hotkey(self, key):
        for name, (mods, hotkey) in self._hotkeys.items():
            if key == hotkey and (self._held_mods & mods) == mods:
                self._handler("hotkey", name)
                return True
        return False

    # -------------------------------------------------------------- capture
    def _apply_hotkey_grabs(self):
        X.libX11.XUngrabKey(self.dpy, 0, X.ANY_MODIFIER, self.root)
        X.libX11.XSelectInput(self.dpy, self.root, X.KEY_PRESS_MASK | X.KEY_RELEASE_MASK)
        for mods, key in self._hotkeys.values():
            keycode = self._x_keycode(key)
            if not keycode:
                continue
            x_mods = 0
            for bit, x_bit in _MOD_TO_X.items():
                if mods & bit:
                    x_mods |= x_bit
            for extra in _LOCK_COMBOS:
                X.libX11.XGrabKey(
                    self.dpy, keycode, x_mods | extra, self.root,
                    True, X.GRAB_MODE_ASYNC, X.GRAB_MODE_ASYNC)
        X.libX11.XFlush(self.dpy)

    def _set_capture_now(self, capturing):
        if capturing == self._capturing:
            return
        if capturing:
            got = X.query_pointer(self.dpy, self.root)
            status = X.libX11.XGrabPointer(
                self.dpy, self.root, False, CAPTURE_EVENT_MASK,
                X.GRAB_MODE_ASYNC, X.GRAB_MODE_ASYNC, 0,
                self._blank_cursor, X.CURRENT_TIME)
            if status != 0:
                self._handler("capture_failed", f"pointer grab refused (code {status})")
                self._want_capture = False
                return
            status = X.libX11.XGrabKeyboard(
                self.dpy, self.root, False,
                X.GRAB_MODE_ASYNC, X.GRAB_MODE_ASYNC, X.CURRENT_TIME)
            if status != 0:
                X.libX11.XUngrabPointer(self.dpy, X.CURRENT_TIME)
                self._handler("capture_failed", f"keyboard grab refused (code {status})")
                self._want_capture = False
                return
            cx, cy = self.width // 2, self.height // 2
            X.libX11.XWarpPointer(self.dpy, 0, self.root, 0, 0, 0, 0, cx, cy)
            self._last_pos = (cx, cy)
            if not self._cursor_hidden:
                X.libXfixes.XFixesHideCursor(self.dpy, self.root)
                self._cursor_hidden = True
            self._capturing = True
        else:
            X.libX11.XUngrabKeyboard(self.dpy, X.CURRENT_TIME)
            X.libX11.XUngrabPointer(self.dpy, X.CURRENT_TIME)
            if self._cursor_hidden:
                X.libXfixes.XFixesShowCursor(self.dpy, self.root)
                self._cursor_hidden = False
            self._capturing = False
            self._held_mods = 0
        X.libX11.XFlush(self.dpy)

    # ------------------------------------------------------------ injection
    def inject_motion(self, x, y):
        with self._inject_lock:
            X.libXtst.XTestFakeMotionEvent(self.dpy_inject, self.screen, int(x), int(y), 0)
            X.libX11.XFlush(self.dpy_inject)

    def inject_button(self, code, pressed):
        button = keymap.BTN_TO_X_BUTTON.get(code)
        if not button:
            return
        with self._inject_lock:
            X.libXtst.XTestFakeButtonEvent(self.dpy_inject, button, bool(pressed), 0)
            X.libX11.XFlush(self.dpy_inject)
        if pressed:
            self._injected_buttons.add(button)
        else:
            self._injected_buttons.discard(button)

    def inject_scroll(self, dx, dy):
        # X11 has no scroll axis: wheel motion is buttons 4-7, one press per
        # detent. Remainders are kept so a trackpad's fine-grained scrolling
        # accumulates into clicks instead of being rounded away.
        self._scroll_remainder[0] += dx
        self._scroll_remainder[1] += dy
        with self._inject_lock:
            for axis, (negative, positive) in enumerate(((6, 7), (5, 4))):
                while abs(self._scroll_remainder[axis]) >= 120:
                    step = 120 if self._scroll_remainder[axis] > 0 else -120
                    button = positive if step > 0 else negative
                    X.libXtst.XTestFakeButtonEvent(self.dpy_inject, button, True, 0)
                    X.libXtst.XTestFakeButtonEvent(self.dpy_inject, button, False, 0)
                    self._scroll_remainder[axis] -= step
            X.libX11.XFlush(self.dpy_inject)

    def inject_key(self, code, pressed):
        keycode = self._x_keycode(code)
        if not keycode:
            return
        with self._inject_lock:
            X.libXtst.XTestFakeKeyEvent(self.dpy_inject, keycode, bool(pressed), 0)
            X.libX11.XFlush(self.dpy_inject)
        if pressed:
            self._injected_keys.add(keycode)
        else:
            self._injected_keys.discard(keycode)

    def release_all(self):
        with self._inject_lock:
            for keycode in sorted(self._injected_keys):
                X.libXtst.XTestFakeKeyEvent(self.dpy_inject, keycode, False, 0)
            for button in sorted(self._injected_buttons):
                X.libXtst.XTestFakeButtonEvent(self.dpy_inject, button, False, 0)
            X.libX11.XFlush(self.dpy_inject)
        self._injected_keys.clear()
        self._injected_buttons.clear()
        self._scroll_remainder = [0, 0]

    # ------------------------------------------------------------ clipboard
    def clipboard_read(self):
        return self.clipboard.read()

    def clipboard_write(self, mime, data):
        self.clipboard.write(mime, data)

    def set_clipboard_listener(self, callback):
        self.clipboard.listener = callback


# ==========================================================================
# Clipboard
# ==========================================================================

TEXT_TARGETS = ("UTF8_STRING", "text/plain;charset=utf-8", "STRING", "TEXT", "text/plain")
IMAGE_TARGETS = ("image/png",)


class Clipboard:
    """An ICCCM selection owner and requestor, on its own thread and display.

    Owning the clipboard in X means staying alive to answer questions about it,
    which is why this cannot be a fire-and-forget "set clipboard" call the way
    it is on Windows: the data lives in this process until another app takes
    the selection away.
    """

    def __init__(self, config):
        self.config = config
        self.max_bytes = int(config.get("max_bytes", 4 << 20))
        self.allow_text = config.get("text", True)
        self.allow_images = config.get("images", True)
        self.listener = None

        self.dpy = X.libX11.XOpenDisplay(None)
        if not self.dpy:
            raise X.X11Unavailable("clipboard: cannot open a second X connection")
        screen = X.libX11.XDefaultScreen(self.dpy)
        self.root = X.libX11.XRootWindow(self.dpy, screen)
        self.window = X.libX11.XCreateSimpleWindow(
            self.dpy, self.root, -10, -10, 1, 1, 0, 0, 0)
        X.libX11.XSelectInput(self.dpy, self.window, X.PROPERTY_CHANGE_MASK)

        self.atoms = {}
        for name in ("CLIPBOARD", "TARGETS", "TIMESTAMP", "MULTIPLE", "INCR",
                     "MB_TRANSFER", "ATOM", "INTEGER", *TEXT_TARGETS, *IMAGE_TARGETS):
            self.atoms[name] = X.libX11.XInternAtom(self.dpy, name.encode(), False)
        self._atom_names = {v: k for k, v in self.atoms.items()}

        self._offer = None            # (mime, data) we are currently serving
        self._offer_time = X.CURRENT_TIME
        self._pending_write = None
        self._waker = Waker()
        self._running = False
        self._thread = None
        self._read_lock = threading.Lock()

        self._xfixes_base = c_int()
        error_base = c_int()
        self._has_xfixes = bool(X.libXfixes.XFixesQueryExtension(
            self.dpy, byref(self._xfixes_base), byref(error_base)))
        if self._has_xfixes:
            X.libXfixes.XFixesSelectSelectionInput(
                self.dpy, self.window, self.atoms["CLIPBOARD"],
                X.XFIXES_SET_SELECTION_OWNER_NOTIFY_MASK)
        X.libX11.XFlush(self.dpy)

    # ---------------------------------------------------------------- public
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="x11-clipboard", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._waker.wake()
        if self._thread:
            self._thread.join(timeout=2)
        self._waker.close()

    def write(self, mime, data):
        """Called from the network thread: hand the clipboard thread new data."""
        if len(data) > self.max_bytes:
            return
        self._pending_write = (mime, data)
        self._waker.wake()

    def read(self):
        """Synchronously fetch the current clipboard. Returns (mime, data) or None."""
        if self._offer is not None:
            return self._offer
        result = {}
        done = threading.Event()
        self._read_request = (result, done)
        self._waker.wake()
        done.wait(timeout=2.0)
        return result.get("value")

    _read_request = None

    # ------------------------------------------------------------------ loop
    def _loop(self):
        fd = X.libX11.XConnectionNumber(self.dpy)
        event = X.XEvent()
        while self._running:
            if self._pending_write is not None:
                mime, data = self._pending_write
                self._pending_write = None
                self._take_ownership(mime, data)
            if self._read_request is not None:
                result, done = self._read_request
                self._read_request = None
                try:
                    result["value"] = self._fetch()
                finally:
                    done.set()

            X.libX11.XFlush(self.dpy)
            if X.libX11.XPending(self.dpy) == 0:
                try:
                    select.select([fd, self._waker.read_fd], [], [], 0.25)
                except (OSError, ValueError):
                    break
                self._waker.drain()
            while self._running and X.libX11.XPending(self.dpy) > 0:
                X.libX11.XNextEvent(self.dpy, byref(event))
                self._dispatch(event)

    def _dispatch(self, event):
        if event.type == X.SELECTION_REQUEST:
            self._serve(event.xselectionrequest)
        elif event.type == X.SELECTION_CLEAR:
            # Another app took the clipboard. Drop our copy so a later read
            # goes out and asks them, rather than serving something stale.
            self._offer = None
        elif self._has_xfixes and event.type == self._xfixes_base.value + \
                X.XFIXES_SET_SELECTION_OWNER_NOTIFY:
            self._on_owner_changed(event.xfixesselection)

    def _on_owner_changed(self, ev):
        if ev.owner == self.window or ev.owner == 0:
            return                      # that was us, or nobody
        if self.listener is None:
            return
        got = self._fetch()
        if got:
            self.listener(*got)

    # ------------------------------------------------------------- ownership
    def _take_ownership(self, mime, data):
        self._offer = (mime, data)
        X.libX11.XSetSelectionOwner(
            self.dpy, self.atoms["CLIPBOARD"], self.window, X.CURRENT_TIME)
        if X.libX11.XGetSelectionOwner(self.dpy, self.atoms["CLIPBOARD"]) != self.window:
            self._offer = None
        X.libX11.XFlush(self.dpy)

    def _supported_targets(self, mime):
        names = ["TARGETS", "TIMESTAMP"]
        names.extend(IMAGE_TARGETS if mime.startswith("image/") else TEXT_TARGETS)
        return [self.atoms[n] for n in names if n in self.atoms]

    def _serve(self, req):
        prop = req.property or req.target      # pre-ICCCM clients send property=None
        granted = False
        if self._offer is not None and req.selection == self.atoms["CLIPBOARD"]:
            mime, data = self._offer
            targets = self._supported_targets(mime)
            if req.target == self.atoms["TARGETS"]:
                array = (c_long * len(targets))(*targets)
                X.libX11.XChangeProperty(
                    self.dpy, req.requestor, prop, self.atoms["ATOM"], 32,
                    X.PROP_MODE_REPLACE, ctypes.cast(array, ctypes.c_void_p), len(targets))
                granted = True
            elif req.target == self.atoms["TIMESTAMP"]:
                value = (c_long * 1)(self._offer_time)
                X.libX11.XChangeProperty(
                    self.dpy, req.requestor, prop, self.atoms["INTEGER"], 32,
                    X.PROP_MODE_REPLACE, ctypes.cast(value, ctypes.c_void_p), 1)
                granted = True
            elif req.target in targets:
                buf = ctypes.create_string_buffer(data, len(data))
                X.libX11.XChangeProperty(
                    self.dpy, req.requestor, prop, req.target, 8,
                    X.PROP_MODE_REPLACE, ctypes.cast(buf, ctypes.c_void_p), len(data))
                granted = True

        reply = X.XEvent()
        reply.xselection.type = X.SELECTION_NOTIFY
        reply.xselection.display = req.display
        reply.xselection.requestor = req.requestor
        reply.xselection.selection = req.selection
        reply.xselection.target = req.target
        reply.xselection.property = prop if granted else 0
        reply.xselection.time = req.time
        X.libX11.XSendEvent(self.dpy, req.requestor, False, 0, byref(reply))
        X.libX11.XFlush(self.dpy)

    # ------------------------------------------------------------- fetching
    def _fetch(self):
        """Ask whoever owns the clipboard for the best format we accept."""
        owner = X.libX11.XGetSelectionOwner(self.dpy, self.atoms["CLIPBOARD"])
        if not owner or owner == self.window:
            return self._offer

        offered = self._request(self.atoms["TARGETS"])
        available = set()
        if offered and offered[0] == self.atoms["ATOM"]:
            width = ctypes.sizeof(c_long)
            raw = offered[2]
            for i in range(len(raw) // width):
                atom = int.from_bytes(raw[i * width:(i + 1) * width], sys.byteorder)
                available.add(self._atom_names.get(atom, ""))

        wanted = []
        if self.allow_images:
            wanted.extend(IMAGE_TARGETS)
        if self.allow_text:
            wanted.extend(TEXT_TARGETS)
        # If TARGETS failed, ask for UTF8_STRING anyway; plenty of clients
        # answer a direct conversion request they never advertised.
        candidates = [t for t in wanted if t in available] or (
            ["UTF8_STRING"] if self.allow_text else [])

        for name in candidates:
            got = self._request(self.atoms[name])
            if got and got[2]:
                mime = "image/png" if name.startswith("image/") else "text/plain"
                data = got[2]
                if len(data) > self.max_bytes:
                    return None
                return mime, data
        return None

    def _request(self, target):
        """XConvertSelection plus the wait for its answer. Returns (type, format, bytes)."""
        with self._read_lock:
            prop = self.atoms["MB_TRANSFER"]
            X.libX11.XDeleteProperty(self.dpy, self.window, prop)
            X.libX11.XConvertSelection(
                self.dpy, self.atoms["CLIPBOARD"], target, prop,
                self.window, X.CURRENT_TIME)
            X.libX11.XFlush(self.dpy)

            notify = self._wait_for(X.SELECTION_NOTIFY, timeout=1.5)
            if notify is None or notify.xselection.property == 0:
                return None

            type_atom, fmt, data = X.get_property(self.dpy, self.window, prop)
            if type_atom == self.atoms["INCR"]:
                X.libX11.XDeleteProperty(self.dpy, self.window, prop)
                X.libX11.XFlush(self.dpy)
                return self._read_incr(prop)
            X.libX11.XDeleteProperty(self.dpy, self.window, prop)
            return type_atom, fmt, data

    def _read_incr(self, prop):
        """Large transfers arrive as a series of property writes ending in an
        empty one. Firefox and LibreOffice both do this for anything sizeable,
        so a clipboard that cannot read INCR is a clipboard that silently drops
        exactly the pastes you care about."""
        chunks, total, type_atom, fmt = [], 0, 0, 8
        while True:
            event = self._wait_for(X.PROPERTY_NOTIFY, timeout=3.0,
                                   match=lambda e: e.xproperty.atom == prop
                                   and e.xproperty.state == X.PROPERTY_NEW_VALUE)
            if event is None:
                return None
            got_type, got_fmt, data = X.get_property(self.dpy, self.window, prop)
            X.libX11.XDeleteProperty(self.dpy, self.window, prop)
            X.libX11.XFlush(self.dpy)
            if not data:
                break
            type_atom, fmt = got_type, got_fmt
            total += len(data)
            if total > self.max_bytes:
                return None
            chunks.append(data)
        return type_atom, fmt, b"".join(chunks)

    def _wait_for(self, event_type, timeout, match=None):
        """Block for one event, still answering anyone who asks us for data."""
        fd = X.libX11.XConnectionNumber(self.dpy)
        deadline = time.monotonic() + timeout
        event = X.XEvent()
        while time.monotonic() < deadline:
            if X.libX11.XPending(self.dpy) == 0:
                select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
                if X.libX11.XPending(self.dpy) == 0:
                    continue
            X.libX11.XNextEvent(self.dpy, byref(event))
            if event.type == event_type and (match is None or match(event)):
                copy = X.XEvent()
                ctypes.memmove(byref(copy), byref(event), ctypes.sizeof(X.XEvent))
                return copy
            # Not what we were waiting for, but somebody may be asking us for
            # the clipboard while we ask someone else for theirs.
            if event.type == X.SELECTION_REQUEST:
                self._serve(event.xselectionrequest)
            elif event.type == X.SELECTION_CLEAR:
                self._offer = None
        return None
