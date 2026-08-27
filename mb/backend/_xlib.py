"""Raw ctypes bindings for libX11, libXtst and libXfixes.

Hand-written rather than pulled from python-xlib so the whole program stays a
`git clone` with no wheels to build. Only the calls mousebridge actually makes
are declared; the structs are laid out to match Xlib.h exactly, because
ctypes will happily read garbage out of a struct whose fields you got wrong.
"""

import ctypes
import ctypes.util
from ctypes import (
    CFUNCTYPE, POINTER, Structure, Union, byref, c_char, c_char_p, c_int,
    c_long, c_uint, c_ubyte, c_ulong, c_void_p,
)

# --------------------------------------------------------------------------
# Library loading
# --------------------------------------------------------------------------


class X11Unavailable(RuntimeError):
    pass


def _load(name, package_hint):
    path = ctypes.util.find_library(name)
    if not path:
        raise X11Unavailable(
            f"lib{name} not found. On Debian/Ubuntu: sudo apt install {package_hint}"
        )
    try:
        return ctypes.CDLL(path)
    except OSError as exc:
        raise X11Unavailable(f"could not load lib{name}: {exc}") from exc


try:
    libX11 = _load("X11", "libx11-6")
    libXtst = _load("Xtst", "libxtst6")
    libXfixes = _load("Xfixes", "libxfixes3")
except X11Unavailable:
    libX11 = libXtst = libXfixes = None

# --------------------------------------------------------------------------
# Types and constants
# --------------------------------------------------------------------------

Display = c_void_p
Window = c_ulong
Atom = c_ulong
Time = c_ulong
Cursor = c_ulong
Pixmap = c_ulong
Bool = c_int
KeySym = c_ulong
KeyCode = c_ubyte

NONE = 0
CURRENT_TIME = 0
POINTER_WINDOW = 0
GRAB_MODE_ASYNC = 1
PROP_MODE_REPLACE = 0
ANY_MODIFIER = 1 << 15

# Event types
KEY_PRESS, KEY_RELEASE = 2, 3
BUTTON_PRESS, BUTTON_RELEASE = 4, 5
MOTION_NOTIFY = 6
PROPERTY_NOTIFY = 28
SELECTION_CLEAR, SELECTION_REQUEST, SELECTION_NOTIFY = 29, 30, 31

# Event masks
KEY_PRESS_MASK = 1 << 0
KEY_RELEASE_MASK = 1 << 1
BUTTON_PRESS_MASK = 1 << 2
BUTTON_RELEASE_MASK = 1 << 3
POINTER_MOTION_MASK = 1 << 6
STRUCTURE_NOTIFY_MASK = 1 << 17
PROPERTY_CHANGE_MASK = 1 << 22

# Modifier masks in XKeyEvent.state
SHIFT_MASK, LOCK_MASK, CONTROL_MASK = 1 << 0, 1 << 1, 1 << 2
MOD1_MASK, MOD2_MASK, MOD4_MASK = 1 << 3, 1 << 4, 1 << 6

# XFixes
XFIXES_SET_SELECTION_OWNER_NOTIFY_MASK = 1 << 0
XFIXES_SET_SELECTION_OWNER_NOTIFY = 0

# PropertyNotify states
PROPERTY_NEW_VALUE, PROPERTY_DELETE = 0, 1


class XAnyEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("window", Window),
    ]


class XKeyEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("window", Window), ("root", Window),
        ("subwindow", Window), ("time", Time),
        ("x", c_int), ("y", c_int), ("x_root", c_int), ("y_root", c_int),
        ("state", c_uint), ("keycode", c_uint), ("same_screen", Bool),
    ]


class XButtonEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("window", Window), ("root", Window),
        ("subwindow", Window), ("time", Time),
        ("x", c_int), ("y", c_int), ("x_root", c_int), ("y_root", c_int),
        ("state", c_uint), ("button", c_uint), ("same_screen", Bool),
    ]


class XMotionEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("window", Window), ("root", Window),
        ("subwindow", Window), ("time", Time),
        ("x", c_int), ("y", c_int), ("x_root", c_int), ("y_root", c_int),
        ("state", c_uint), ("is_hint", c_char), ("same_screen", Bool),
    ]


class XSelectionRequestEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("owner", Window), ("requestor", Window),
        ("selection", Atom), ("target", Atom), ("property", Atom), ("time", Time),
    ]


class XSelectionEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("requestor", Window), ("selection", Atom),
        ("target", Atom), ("property", Atom), ("time", Time),
    ]


class XSelectionClearEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("window", Window), ("selection", Atom), ("time", Time),
    ]


class XPropertyEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("window", Window), ("atom", Atom),
        ("time", Time), ("state", c_int),
    ]


class XFixesSelectionNotifyEvent(Structure):
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", Bool),
        ("display", Display), ("window", Window), ("subtype", c_int),
        ("owner", Window), ("selection", Atom), ("timestamp", Time),
        ("selection_timestamp", Time),
    ]


class XEvent(Union):
    _fields_ = [
        ("type", c_int),
        ("xany", XAnyEvent),
        ("xkey", XKeyEvent),
        ("xbutton", XButtonEvent),
        ("xmotion", XMotionEvent),
        ("xselectionrequest", XSelectionRequestEvent),
        ("xselection", XSelectionEvent),
        ("xselectionclear", XSelectionClearEvent),
        ("xproperty", XPropertyEvent),
        ("xfixesselection", XFixesSelectionNotifyEvent),
        ("pad", c_long * 24),
    ]


class XErrorEvent(Structure):
    _fields_ = [
        ("type", c_int), ("display", Display), ("resourceid", c_ulong),
        ("serial", c_ulong), ("error_code", c_ubyte), ("request_code", c_ubyte),
        ("minor_code", c_ubyte),
    ]


XErrorHandler = CFUNCTYPE(c_int, Display, POINTER(XErrorEvent))


def _declare():
    x = libX11
    x.XInitThreads.restype = c_int
    x.XOpenDisplay.argtypes = [c_char_p]
    x.XOpenDisplay.restype = Display
    x.XCloseDisplay.argtypes = [Display]
    x.XDefaultScreen.argtypes = [Display]
    x.XDefaultScreen.restype = c_int
    x.XRootWindow.argtypes = [Display, c_int]
    x.XRootWindow.restype = Window
    x.XDisplayWidth.argtypes = [Display, c_int]
    x.XDisplayWidth.restype = c_int
    x.XDisplayHeight.argtypes = [Display, c_int]
    x.XDisplayHeight.restype = c_int
    x.XConnectionNumber.argtypes = [Display]
    x.XConnectionNumber.restype = c_int
    x.XFlush.argtypes = [Display]
    x.XSync.argtypes = [Display, Bool]
    x.XPending.argtypes = [Display]
    x.XPending.restype = c_int
    x.XNextEvent.argtypes = [Display, POINTER(XEvent)]
    x.XSelectInput.argtypes = [Display, Window, c_long]
    x.XCreateSimpleWindow.argtypes = [
        Display, Window, c_int, c_int, c_uint, c_uint, c_uint, c_ulong, c_ulong]
    x.XCreateSimpleWindow.restype = Window
    x.XDestroyWindow.argtypes = [Display, Window]

    x.XQueryPointer.argtypes = [
        Display, Window, POINTER(Window), POINTER(Window),
        POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_uint)]
    x.XQueryPointer.restype = Bool
    x.XWarpPointer.argtypes = [
        Display, Window, Window, c_int, c_int, c_uint, c_uint, c_int, c_int]

    x.XGrabPointer.argtypes = [
        Display, Window, Bool, c_uint, c_int, c_int, Window, Cursor, Time]
    x.XGrabPointer.restype = c_int
    x.XUngrabPointer.argtypes = [Display, Time]
    x.XGrabKeyboard.argtypes = [Display, Window, Bool, c_int, c_int, Time]
    x.XGrabKeyboard.restype = c_int
    x.XUngrabKeyboard.argtypes = [Display, Time]
    x.XGrabKey.argtypes = [Display, c_int, c_uint, Window, Bool, c_int, c_int]
    x.XUngrabKey.argtypes = [Display, c_int, c_uint, Window]

    x.XInternAtom.argtypes = [Display, c_char_p, Bool]
    x.XInternAtom.restype = Atom
    x.XGetAtomName.argtypes = [Display, Atom]
    x.XGetAtomName.restype = c_void_p

    x.XSetSelectionOwner.argtypes = [Display, Atom, Window, Time]
    x.XGetSelectionOwner.argtypes = [Display, Atom]
    x.XGetSelectionOwner.restype = Window
    x.XConvertSelection.argtypes = [Display, Atom, Atom, Atom, Window, Time]
    x.XSendEvent.argtypes = [Display, Window, Bool, c_long, POINTER(XEvent)]

    x.XGetWindowProperty.argtypes = [
        Display, Window, Atom, c_long, c_long, Bool, Atom,
        POINTER(Atom), POINTER(c_int), POINTER(c_ulong), POINTER(c_ulong),
        POINTER(POINTER(c_ubyte))]
    x.XGetWindowProperty.restype = c_int
    x.XChangeProperty.argtypes = [
        Display, Window, Atom, Atom, c_int, c_int, c_void_p, c_int]
    x.XDeleteProperty.argtypes = [Display, Window, Atom]
    x.XFree.argtypes = [c_void_p]

    x.XKeysymToKeycode.argtypes = [Display, KeySym]
    x.XKeysymToKeycode.restype = KeyCode
    x.XGetKeyboardMapping.argtypes = [Display, KeyCode, c_int, POINTER(c_int)]
    x.XGetKeyboardMapping.restype = POINTER(KeySym)
    x.XkbSetDetectableAutoRepeat.argtypes = [Display, Bool, POINTER(Bool)]
    x.XkbSetDetectableAutoRepeat.restype = Bool
    x.XQueryKeymap.argtypes = [Display, c_char * 32]

    x.XCreateBitmapFromData.argtypes = [Display, Window, c_char_p, c_uint, c_uint]
    x.XCreateBitmapFromData.restype = Pixmap
    x.XCreatePixmapCursor.argtypes = [
        Display, Pixmap, Pixmap, c_void_p, c_void_p, c_uint, c_uint]
    x.XCreatePixmapCursor.restype = Cursor
    x.XFreePixmap.argtypes = [Display, Pixmap]
    x.XFreeCursor.argtypes = [Display, Cursor]
    x.XSetErrorHandler.argtypes = [XErrorHandler]
    x.XSetErrorHandler.restype = c_void_p

    t = libXtst
    t.XTestQueryExtension.argtypes = [
        Display, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]
    t.XTestQueryExtension.restype = Bool
    t.XTestFakeMotionEvent.argtypes = [Display, c_int, c_int, c_int, c_ulong]
    t.XTestFakeRelativeMotionEvent.argtypes = [Display, c_int, c_int, c_ulong]
    t.XTestFakeButtonEvent.argtypes = [Display, c_uint, Bool, c_ulong]
    t.XTestFakeKeyEvent.argtypes = [Display, c_uint, Bool, c_ulong]
    t.XTestGrabControl.argtypes = [Display, Bool]

    f = libXfixes
    f.XFixesQueryExtension.argtypes = [Display, POINTER(c_int), POINTER(c_int)]
    f.XFixesQueryExtension.restype = Bool
    f.XFixesSelectSelectionInput.argtypes = [Display, Window, Atom, c_ulong]
    f.XFixesHideCursor.argtypes = [Display, Window]
    f.XFixesShowCursor.argtypes = [Display, Window]


class XColor(Structure):
    _fields_ = [
        ("pixel", c_ulong), ("red", ctypes.c_ushort), ("green", ctypes.c_ushort),
        ("blue", ctypes.c_ushort), ("flags", c_char), ("pad", c_char),
    ]


if libX11 is not None:
    _declare()
    libX11.XInitThreads()

    # A dead clipboard requestor produces BadWindow on the reply. The default
    # Xlib handler responds by killing the process, which would mean a browser
    # tab closing at the wrong moment takes the whole daemon with it.
    _SILENCED = {3, 8, 9, 10}   # BadWindow, BadMatch, BadDrawable, BadAccess

    @XErrorHandler
    def _error_handler(display, event):
        code = event.contents.error_code
        if code not in _SILENCED:
            import sys
            print(
                f"mousebridge: X error {code} on request "
                f"{event.contents.request_code}.{event.contents.minor_code}",
                file=sys.stderr,
            )
        return 0

    libX11.XSetErrorHandler(_error_handler)


def atom_name(display, atom):
    ptr = libX11.XGetAtomName(display, atom)
    if not ptr:
        return f"<atom {atom}>"
    try:
        return ctypes.cast(ptr, c_char_p).value.decode("latin-1")
    finally:
        libX11.XFree(ptr)


def query_pointer(display, root):
    root_ret, child = Window(), Window()
    rx, ry, wx, wy = c_int(), c_int(), c_int(), c_int()
    mask = c_uint()
    ok = libX11.XQueryPointer(
        display, root, byref(root_ret), byref(child),
        byref(rx), byref(ry), byref(wx), byref(wy), byref(mask))
    if not ok:
        return None
    return rx.value, ry.value, mask.value


def get_property(display, window, prop, delete=False, max_len=(1 << 24)):
    """Read a window property whole. Returns (type_atom, format, bytes)."""
    actual_type, actual_format = Atom(), c_int()
    nitems, remaining = c_ulong(), c_ulong()
    data = POINTER(c_ubyte)()
    status = libX11.XGetWindowProperty(
        display, window, prop, 0, max_len // 4, Bool(delete), 0,
        byref(actual_type), byref(actual_format), byref(nitems),
        byref(remaining), byref(data))
    if status != 0 or not data:
        return 0, 0, b""
    try:
        width = {8: 1, 16: 2, 32: ctypes.sizeof(c_long)}.get(actual_format.value, 1)
        length = nitems.value * width
        return actual_type.value, actual_format.value, bytes(
            ctypes.cast(data, POINTER(c_ubyte * length)).contents)
    finally:
        libX11.XFree(data)
