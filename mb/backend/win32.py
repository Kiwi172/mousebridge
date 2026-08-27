"""Windows backend: capture with low-level hooks, inject with SendInput,
clipboard through the ordinary Win32 clipboard API.

The shape mirrors the X11 backend but the mechanisms invert. On X11 you take
input away by *grabbing* it; on Windows you install a hook that sees every
event before anyone else and returns non-zero to make it disappear. The hook
runs on whichever thread installed it and only fires while that thread pumps
messages, so the capture thread here is a real Win32 message loop rather than a
select() -- everything else about the design carries over unchanged.

Two Windows-specific hazards this handles:

  * Injected input comes straight back through our own hook. It is tagged with
    a private value in `dwExtraInfo` and dropped on the way in, or a machine
    controlling itself would feed events to itself forever.
  * A low-level hook that takes too long is silently uninstalled by Windows.
    The hook callback therefore only appends to a queue; all real work happens
    on another thread.
"""

import ctypes
import queue
import sys
import threading
import time
from ctypes import POINTER, Structure, Union, byref, c_int, c_long, c_uint, c_ulong, c_void_p

from .. import keymap
from .base import Backend

if sys.platform == "win32":
    from ctypes import WINFUNCTYPE, windll
    from ctypes.wintypes import (
        DWORD, HANDLE, HHOOK, HINSTANCE, HWND, LONG, LPARAM, LPVOID, MSG,
        POINT, UINT, WORD, WPARAM,
    )
    user32 = windll.user32
    kernel32 = windll.kernel32
else:                       # importable off-Windows so the tests can run anywhere
    user32 = kernel32 = None
    WINFUNCTYPE = None
    # Windows is LLP64: LONG is 32 bits there but 64 on Linux, so pin the
    # widths explicitly or the struct sizes asserted by the tests would be
    # right on Windows and wrong everywhere else.
    DWORD = UINT = ctypes.c_uint32
    LONG = ctypes.c_int32
    WPARAM = LPARAM = ctypes.c_ssize_t
    HHOOK = HINSTANCE = HWND = HANDLE = LPVOID = c_void_p
    WORD = ctypes.c_ushort

    class POINT(Structure):
        _fields_ = [("x", LONG), ("y", LONG)]

    class MSG(Structure):
        _fields_ = [("hWnd", HWND), ("message", UINT), ("wParam", WPARAM),
                    ("lParam", LPARAM), ("time", DWORD), ("pt", POINT)]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
WM_QUIT, WM_CLIPBOARDUPDATE = 0x0012, 0x031D

LLKHF_EXTENDED = 0x01
LLMHF_INJECTED = 0x01

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP = 0x0080, 0x0100
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
XBUTTON1, XBUTTON2 = 0x0001, 0x0002

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

CF_UNICODETEXT, CF_TEXT, CF_DIB = 13, 1, 8
GMEM_MOVEABLE = 0x0002

# Anything we inject carries this in dwExtraInfo so our own hook can ignore it.
INJECT_TAG = 0x4D42_5247      # 'MBRG'

_VK_SHIFT_KEYS = {0xA0, 0xA1, 0x10}
_VK_CTRL_KEYS = {0xA2, 0xA3, 0x11}
_VK_ALT_KEYS = {0xA4, 0xA5, 0x12}
_VK_META_KEYS = {0x5B, 0x5C}


class KBDLLHOOKSTRUCT(Structure):
    _fields_ = [("vkCode", DWORD), ("scanCode", DWORD), ("flags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class MSLLHOOKSTRUCT(Structure):
    _fields_ = [("pt", POINT), ("mouseData", DWORD), ("flags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class MOUSEINPUT(Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class KEYBDINPUT(Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class HARDWAREINPUT(Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


def _declare():
    """Pin argument and return types.

    ctypes defaults an undeclared return value to `int`, which on 64-bit
    Windows silently truncates every HANDLE, HHOOK and HWND to 32 bits. The
    resulting handles look plausible and fail much later, so declare the lot.
    """
    LRESULT = ctypes.c_ssize_t
    ULONG_PTR = ctypes.c_size_t

    user32.SetWindowsHookExW.argtypes = [c_int, c_void_p, HINSTANCE, DWORD]
    user32.SetWindowsHookExW.restype = HHOOK
    user32.UnhookWindowsHookEx.argtypes = [HHOOK]
    user32.CallNextHookEx.argtypes = [HHOOK, c_int, WPARAM, LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.GetMessageW.argtypes = [POINTER(MSG), HWND, UINT, UINT]
    user32.GetMessageW.restype = c_int
    user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.PostThreadMessageW.argtypes = [DWORD, UINT, WPARAM, LPARAM]
    user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]

    user32.GetCursorPos.argtypes = [POINTER(POINT)]
    user32.SetCursorPos.argtypes = [c_int, c_int]
    user32.GetSystemMetrics.argtypes = [c_int]
    user32.GetSystemMetrics.restype = c_int
    user32.GetAsyncKeyState.argtypes = [c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetKeyState.argtypes = [c_int]
    user32.GetKeyState.restype = ctypes.c_short
    user32.ShowCursor.argtypes = [ctypes.c_bool]
    user32.ShowCursor.restype = c_int
    user32.SendInput.argtypes = [UINT, c_void_p, c_int]
    user32.SendInput.restype = UINT

    user32.CreateWindowExW.argtypes = [
        DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD,
        c_int, c_int, c_int, c_int, HWND, HANDLE, HINSTANCE, LPVOID]
    user32.CreateWindowExW.restype = HWND
    user32.RegisterClassW.restype = ctypes.c_ushort
    user32.AddClipboardFormatListener.argtypes = [HWND]
    user32.RemoveClipboardFormatListener.argtypes = [HWND]
    user32.GetClipboardSequenceNumber.restype = DWORD
    user32.OpenClipboard.argtypes = [HWND]
    user32.GetClipboardData.argtypes = [UINT]
    user32.GetClipboardData.restype = HANDLE
    user32.SetClipboardData.argtypes = [UINT, HANDLE]
    user32.SetClipboardData.restype = HANDLE

    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetModuleHandleW.restype = HINSTANCE
    kernel32.GetCurrentThreadId.restype = DWORD
    kernel32.GlobalAlloc.argtypes = [UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = HANDLE
    kernel32.GlobalLock.argtypes = [HANDLE]
    kernel32.GlobalLock.restype = LPVOID
    kernel32.GlobalUnlock.argtypes = [HANDLE]


if sys.platform == "win32":
    _declare()


class Win32Unavailable(RuntimeError):
    pass


class Win32Backend(Backend):
    name = "windows"

    def __init__(self, clipboard_config=None):
        if sys.platform != "win32":
            raise Win32Unavailable("the Windows backend needs Windows")
        self._handler = lambda *a: None
        self._capturing = False
        self._hotkeys = {}
        self._held_mods = 0
        self._running = False
        self._hook_thread = None
        self._hook_tid = None
        self._events = queue.Queue()
        self._pump_thread = None
        self._injected_keys = set()
        self._injected_buttons = set()
        self._park = None
        self._mouse_hook = None
        self._keyboard_hook = None
        self.clipboard = Win32Clipboard(clipboard_config or {})
        self._refresh_geometry()

    def _refresh_geometry(self):
        self.origin_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        self.origin_y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        self.width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        self.height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

    # ------------------------------------------------------------- geometry
    def screen_size(self):
        return self.width, self.height

    def cursor_position(self):
        point = POINT()
        user32.GetCursorPos(byref(point))
        return point.x - self.origin_x, point.y - self.origin_y

    def warp_cursor(self, x, y):
        user32.SetCursorPos(int(x) + self.origin_x, int(y) + self.origin_y)

    def modifier_state(self):
        mods = 0
        if user32.GetAsyncKeyState(0x10) & 0x8000:
            mods |= keymap.MOD_SHIFT
        if user32.GetAsyncKeyState(0x11) & 0x8000:
            mods |= keymap.MOD_CTRL
        if user32.GetAsyncKeyState(0x12) & 0x8000:
            mods |= keymap.MOD_ALT
        if (user32.GetAsyncKeyState(0x5B) | user32.GetAsyncKeyState(0x5C)) & 0x8000:
            mods |= keymap.MOD_META
        if user32.GetKeyState(0x14) & 1:
            mods |= keymap.MOD_CAPS
        if user32.GetKeyState(0x90) & 1:
            mods |= keymap.MOD_NUM
        return mods

    # ------------------------------------------------------------ lifecycle
    def start(self, handler):
        self._handler = handler
        self._running = True
        self._hook_thread = threading.Thread(
            target=self._hook_loop, name="win32-hooks", daemon=True)
        self._hook_thread.start()
        self._pump_thread = threading.Thread(
            target=self._drain_events, name="win32-dispatch", daemon=True)
        self._pump_thread.start()
        self.clipboard.start()

    def stop(self):
        self._running = False
        if self._hook_tid:
            user32.PostThreadMessageW(self._hook_tid, WM_QUIT, 0, 0)
        self._events.put(None)
        for thread in (self._hook_thread, self._pump_thread):
            if thread:
                thread.join(timeout=2)
        self.clipboard.stop()

    def set_capturing(self, capturing):
        capturing = bool(capturing)
        if capturing == self._capturing:
            return
        if capturing:
            # Park the physical cursor in the far corner. Blocking the events
            # normally stops it moving anyway, but drivers with their own
            # cursor handling do not always cooperate, and a cursor stuck in a
            # corner is much less confusing than one drifting across the screen
            # you are no longer looking at.
            self._park = (self.width - 1, self.height - 1)
            self.warp_cursor(*self._park)
            user32.ShowCursor(False)
        else:
            user32.ShowCursor(True)
            self._park = None
            self._held_mods = 0
        self._capturing = capturing

    def register_hotkeys(self, hotkeys):
        self._hotkeys = dict(hotkeys)

    # ----------------------------------------------------------- hook thread
    def _hook_loop(self):
        # The callbacks must stay referenced for as long as the hooks are
        # installed; letting them be collected crashes the process from inside
        # the Windows message pump, where the traceback is useless.
        self._mouse_proc = WINFUNCTYPE(
            LPARAM, c_int, WPARAM, LPARAM)(self._on_mouse)
        self._key_proc = WINFUNCTYPE(
            LPARAM, c_int, WPARAM, LPARAM)(self._on_key)

        self._hook_tid = kernel32.GetCurrentThreadId()
        module = kernel32.GetModuleHandleW(None)
        self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, module, 0)
        self._keyboard_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._key_proc, module, 0)
        if not self._mouse_hook or not self._keyboard_hook:
            self._handler("capture_failed", "could not install low-level input hooks")
            return

        message = MSG()
        while self._running and user32.GetMessageW(byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(byref(message))
            user32.DispatchMessageW(byref(message))

        user32.UnhookWindowsHookEx(self._mouse_hook)
        user32.UnhookWindowsHookEx(self._keyboard_hook)

    def _on_mouse(self, code, wparam, lparam):
        if code < 0:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        info = ctypes.cast(c_void_p(lparam), POINTER(MSLLHOOKSTRUCT)).contents
        if (info.dwExtraInfo or 0) == INJECT_TAG:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        if not self._capturing:
            return user32.CallNextHookEx(None, code, wparam, lparam)

        message = wparam
        if message == WM_MOUSEMOVE:
            park_x = self._park[0] + self.origin_x if self._park else info.pt.x
            park_y = self._park[1] + self.origin_y if self._park else info.pt.y
            dx, dy = info.pt.x - park_x, info.pt.y - park_y
            if dx or dy:
                self._events.put(("motion", dx, dy))
                user32.SetCursorPos(park_x, park_y)
        elif message in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
            if message == WM_MOUSEWHEEL:
                self._events.put(("scroll", 0, delta))
            else:
                self._events.put(("scroll", delta, 0))
        else:
            mapping = {
                WM_LBUTTONDOWN: (keymap.BTN_LEFT, True), WM_LBUTTONUP: (keymap.BTN_LEFT, False),
                WM_RBUTTONDOWN: (keymap.BTN_RIGHT, True), WM_RBUTTONUP: (keymap.BTN_RIGHT, False),
                WM_MBUTTONDOWN: (keymap.BTN_MIDDLE, True), WM_MBUTTONUP: (keymap.BTN_MIDDLE, False),
            }
            if message in mapping:
                self._events.put(("button", *mapping[message]))
            elif message in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                which = (info.mouseData >> 16) & 0xFFFF
                code_out = keymap.BTN_SIDE if which == XBUTTON1 else keymap.BTN_EXTRA
                self._events.put(("button", code_out, message == WM_XBUTTONDOWN))
        return 1        # swallow: the local machine must not also act on this

    def _on_key(self, code, wparam, lparam):
        if code < 0:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        info = ctypes.cast(c_void_p(lparam), POINTER(KBDLLHOOKSTRUCT)).contents
        if (info.dwExtraInfo or 0) == INJECT_TAG:
            return user32.CallNextHookEx(None, code, wparam, lparam)

        pressed = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        key = keymap.from_win32(info.vkCode, info.scanCode, bool(info.flags & LLKHF_EXTENDED))
        if key is None:
            return user32.CallNextHookEx(None, code, wparam, lparam)

        # Track modifiers even when not capturing, so hotkeys work either way.
        mod = keymap.MODIFIER_KEYS.get(key)
        if mod:
            if pressed:
                self._held_mods |= mod
            else:
                self._held_mods &= ~mod

        if pressed:
            for name, (mods, hotkey) in self._hotkeys.items():
                if key == hotkey and (self._held_mods & mods) == mods:
                    self._events.put(("hotkey", name))
                    return 1
        if not self._capturing:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        self._events.put(("key", key, pressed))
        return 1

    def _drain_events(self):
        """Hook callbacks must return fast, so they only enqueue. The real work
        -- encrypting and writing to a socket -- happens here."""
        while self._running:
            item = self._events.get()
            if item is None:
                break
            try:
                self._handler(*item)
            except Exception as exc:            # never let one bad event kill capture
                print(f"mousebridge: event handler failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------ injection
    @staticmethod
    def _send(*inputs):
        array = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))

    def _mouse_input(self, flags, dx=0, dy=0, data=0):
        return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(
            dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0,
            dwExtraInfo=ctypes.c_void_p(INJECT_TAG)))

    def inject_motion(self, x, y):
        # SendInput's absolute coordinates are normalised to 0..65535 across the
        # whole virtual desktop, not pixels.
        if self.width <= 1 or self.height <= 1:
            return
        nx = int((int(x) * 65535) / (self.width - 1))
        ny = int((int(y) * 65535) / (self.height - 1))
        self._send(self._mouse_input(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))

    def inject_button(self, code, pressed):
        table = {
            keymap.BTN_LEFT: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
            keymap.BTN_RIGHT: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
            keymap.BTN_MIDDLE: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
            keymap.BTN_SIDE: (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
            keymap.BTN_EXTRA: (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
        }
        if code not in table:
            return
        down, up, data = table[code]
        self._send(self._mouse_input(down if pressed else up, data=data))
        if pressed:
            self._injected_buttons.add(code)
        else:
            self._injected_buttons.discard(code)

    def inject_scroll(self, dx, dy):
        events = []
        if dy:
            events.append(self._mouse_input(MOUSEEVENTF_WHEEL, data=dy & 0xFFFFFFFF))
        if dx:
            events.append(self._mouse_input(MOUSEEVENTF_HWHEEL, data=dx & 0xFFFFFFFF))
        if events:
            self._send(*events)

    def inject_key(self, code, pressed):
        scancode, extended, vk = keymap.to_win32(code)
        flags = 0 if pressed else KEYEVENTF_KEYUP
        if scancode:
            # Scancodes are layout-independent: the far machine's own keyboard
            # layout decides what character comes out, which is what you want
            # when the two machines have different layouts.
            flags |= KEYEVENTF_SCANCODE
            if extended:
                flags |= KEYEVENTF_EXTENDEDKEY
        elif not vk:
            return
        self._send(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(
            wVk=vk, wScan=scancode, dwFlags=flags, time=0,
            dwExtraInfo=ctypes.c_void_p(INJECT_TAG))))
        if pressed:
            self._injected_keys.add(code)
        else:
            self._injected_keys.discard(code)

    def release_all(self):
        for code in sorted(self._injected_keys):
            self.inject_key(code, False)
        for code in sorted(self._injected_buttons):
            self.inject_button(code, False)
        self._injected_keys.clear()
        self._injected_buttons.clear()

    # ------------------------------------------------------------ clipboard
    def clipboard_read(self):
        return self.clipboard.read()

    def clipboard_write(self, mime, data):
        self.clipboard.write(mime, data)

    def set_clipboard_listener(self, callback):
        self.clipboard.listener = callback


class Win32Clipboard:
    """The Win32 clipboard, plus AddClipboardFormatListener for change events.

    Unlike X11 the data is handed to the OS and forgotten, so this is much
    shorter -- but it still needs a window with a message loop, because that is
    the only way Windows will tell you the clipboard changed.
    """

    def __init__(self, config):
        self.config = config
        self.max_bytes = int(config.get("max_bytes", 4 << 20))
        self.allow_text = config.get("text", True)
        self.allow_images = config.get("images", True)
        self.listener = None
        self._running = False
        self._thread = None
        self._hwnd = None
        self._own_sequence = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="win32-clipboard", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self):
        self._wndproc = WINFUNCTYPE(
            LPARAM, HWND, UINT, WPARAM, LPARAM)(self._on_message)
        class WNDCLASS(Structure):
            _fields_ = [("style", UINT), ("lpfnWndProc", c_void_p),
                        ("cbClsExtra", c_int), ("cbWndExtra", c_int),
                        ("hInstance", HINSTANCE), ("hIcon", HANDLE),
                        ("hCursor", HANDLE), ("hbrBackground", HANDLE),
                        ("lpszMenuName", ctypes.c_wchar_p),
                        ("lpszClassName", ctypes.c_wchar_p)]
        cls = WNDCLASS()
        cls.lpfnWndProc = ctypes.cast(self._wndproc, c_void_p)
        cls.hInstance = kernel32.GetModuleHandleW(None)
        cls.lpszClassName = "MousebridgeClipboard"
        user32.RegisterClassW(byref(cls))
        # HWND_MESSAGE (-3) creates a window that never appears on screen.
        HWND_MESSAGE = HWND(-3)     # a window that exists only to receive messages
        self._hwnd = user32.CreateWindowExW(
            0, "MousebridgeClipboard", "mousebridge", 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, cls.hInstance, None)
        user32.AddClipboardFormatListener(self._hwnd)

        message = MSG()
        while self._running and user32.GetMessageW(byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(byref(message))
            user32.DispatchMessageW(byref(message))
        user32.RemoveClipboardFormatListener(self._hwnd)

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            sequence = user32.GetClipboardSequenceNumber()
            if sequence != self._own_sequence and self.listener:
                got = self.read()
                if got:
                    self.listener(*got)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _open(self, retries=8):
        """The clipboard is a global lock another app may be holding."""
        for attempt in range(retries):
            if user32.OpenClipboard(None):
                return True
            time.sleep(0.01 * (attempt + 1))
        return False

    def read(self):
        if not self.allow_text:
            return None
        if not self._open():
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                text = ctypes.c_wchar_p(pointer).value or ""
            finally:
                kernel32.GlobalUnlock(handle)
            data = text.encode("utf-8")
            if len(data) > self.max_bytes:
                return None
            return "text/plain", data
        finally:
            user32.CloseClipboard()

    def write(self, mime, data):
        if mime.startswith("image/"):
            # PNG on the wire, DIB on the Windows clipboard: converting between
            # them needs an image decoder, which would mean a dependency.
            return
        if len(data) > self.max_bytes or not self._open():
            return
        try:
            user32.EmptyClipboard()
            text = data.decode("utf-8", "replace")
            size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                return
            pointer = kernel32.GlobalLock(handle)
            ctypes.memmove(pointer, ctypes.create_unicode_buffer(text), size)
            kernel32.GlobalUnlock(handle)
            user32.SetClipboardData(CF_UNICODETEXT, handle)   # Windows owns it now
        finally:
            user32.CloseClipboard()
            self._own_sequence = user32.GetClipboardSequenceNumber()
