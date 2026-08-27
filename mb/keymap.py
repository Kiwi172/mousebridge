"""One keycode space shared by every platform.

The canonical code is the **Linux evdev keycode** (`KEY_A` == 30). That choice
is not arbitrary: for the whole base range, evdev keycodes are numerically
identical to AT set-1 scancodes, which is exactly what the Windows low-level
keyboard hook hands us. So the Linux <-> Windows translation for most of the
keyboard is the identity function, and only the extended (0xE0-prefixed) keys
need a table.

Keys are identified by *physical position*, never by the character they
produce. Press the key left of Enter on a UK keyboard and the US-layout machine
next to it types whatever its own layout puts there -- which is what you want,
because you are looking at your own keyboard while you type.
"""

# --------------------------------------------------------------------------
# Canonical key codes (subset of linux/input-event-codes.h)
# --------------------------------------------------------------------------

KEY_ESC, KEY_1, KEY_MINUS, KEY_EQUAL = 1, 2, 12, 13
KEY_BACKSPACE, KEY_TAB, KEY_Q, KEY_A = 14, 15, 16, 30
KEY_ENTER, KEY_LEFTCTRL, KEY_LEFTSHIFT = 28, 29, 42
KEY_BACKSLASH, KEY_Z, KEY_RIGHTSHIFT = 43, 44, 54
KEY_KPASTERISK, KEY_LEFTALT, KEY_SPACE, KEY_CAPSLOCK = 55, 56, 57, 58
KEY_F1, KEY_F10, KEY_NUMLOCK, KEY_SCROLLLOCK = 59, 68, 69, 70
KEY_KP7, KEY_KPDOT, KEY_102ND, KEY_F11, KEY_F12 = 71, 83, 86, 87, 88
KEY_KPENTER, KEY_RIGHTCTRL, KEY_KPSLASH, KEY_SYSRQ, KEY_RIGHTALT = 96, 97, 98, 99, 100
KEY_HOME, KEY_UP, KEY_PAGEUP, KEY_LEFT, KEY_RIGHT = 102, 103, 104, 105, 106
KEY_END, KEY_DOWN, KEY_PAGEDOWN, KEY_INSERT, KEY_DELETE = 107, 108, 109, 110, 111
KEY_MUTE, KEY_VOLUMEDOWN, KEY_VOLUMEUP, KEY_POWER = 113, 114, 115, 116
KEY_PAUSE, KEY_LEFTMETA, KEY_RIGHTMETA, KEY_COMPOSE = 119, 125, 126, 127
KEY_STOP, KEY_AGAIN, KEY_UNDO, KEY_COPY, KEY_PASTE, KEY_CUT = 128, 129, 131, 133, 135, 137
KEY_F13, KEY_F24 = 183, 194
KEY_PLAYPAUSE, KEY_NEXTSONG, KEY_PREVIOUSSONG, KEY_STOPCD = 164, 163, 165, 166

# --------------------------------------------------------------------------
# Canonical mouse button codes (evdev BTN_*)
# --------------------------------------------------------------------------

BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114

# --------------------------------------------------------------------------
# Modifier bitmask, carried across a screen hand-off so held keys survive
# --------------------------------------------------------------------------

MOD_SHIFT, MOD_CTRL, MOD_ALT, MOD_META = 1 << 0, 1 << 1, 1 << 2, 1 << 3
MOD_CAPS, MOD_NUM = 1 << 4, 1 << 5

MODIFIER_KEYS = {
    KEY_LEFTSHIFT: MOD_SHIFT, KEY_RIGHTSHIFT: MOD_SHIFT,
    KEY_LEFTCTRL: MOD_CTRL, KEY_RIGHTCTRL: MOD_CTRL,
    KEY_LEFTALT: MOD_ALT, KEY_RIGHTALT: MOD_ALT,
    KEY_LEFTMETA: MOD_META, KEY_RIGHTMETA: MOD_META,
}

# One representative key per modifier, for re-asserting state after a hand-off.
MOD_REPRESENTATIVE = [
    (MOD_SHIFT, KEY_LEFTSHIFT), (MOD_CTRL, KEY_LEFTCTRL),
    (MOD_ALT, KEY_LEFTALT), (MOD_META, KEY_LEFTMETA),
]

# --------------------------------------------------------------------------
# Windows: AT set-1 scancode <-> canonical
# --------------------------------------------------------------------------

# Non-extended scancodes are identity-mapped, except for these two gaps.
_PLAIN_GAPS = {0x54, 0x55, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F}

# Extended (0xE0-prefixed) scancode -> canonical.
_EXT_TO_KEY = {
    0x1C: KEY_KPENTER, 0x1D: KEY_RIGHTCTRL, 0x35: KEY_KPSLASH, 0x37: KEY_SYSRQ,
    0x38: KEY_RIGHTALT, 0x47: KEY_HOME, 0x48: KEY_UP, 0x49: KEY_PAGEUP,
    0x4B: KEY_LEFT, 0x4D: KEY_RIGHT, 0x4F: KEY_END, 0x50: KEY_DOWN,
    0x51: KEY_PAGEDOWN, 0x52: KEY_INSERT, 0x53: KEY_DELETE,
    0x5B: KEY_LEFTMETA, 0x5C: KEY_RIGHTMETA, 0x5D: KEY_COMPOSE,
    0x5E: KEY_POWER, 0x20: KEY_MUTE, 0x2E: KEY_VOLUMEDOWN, 0x30: KEY_VOLUMEUP,
    0x19: KEY_NEXTSONG, 0x10: KEY_PREVIOUSSONG, 0x22: KEY_PLAYPAUSE, 0x24: KEY_STOPCD,
}
_KEY_TO_EXT = {v: k for k, v in _EXT_TO_KEY.items()}

# Windows reports Pause and NumLock with the same scancode 0x45 and disagrees
# with the rest of the world about which one carries the extended flag, so the
# virtual-key code is the tiebreaker for these.
_VK_OVERRIDE = {0x13: KEY_PAUSE, 0x90: KEY_NUMLOCK, 0x91: KEY_SCROLLLOCK}

# Fallback for synthetic events that arrive with scancode 0.
_VK_TO_KEY = {
    0x08: KEY_BACKSPACE, 0x09: KEY_TAB, 0x0D: KEY_ENTER, 0x1B: KEY_ESC,
    0x20: KEY_SPACE, 0x14: KEY_CAPSLOCK,
    0x21: KEY_PAGEUP, 0x22: KEY_PAGEDOWN, 0x23: KEY_END, 0x24: KEY_HOME,
    0x25: KEY_LEFT, 0x26: KEY_UP, 0x27: KEY_RIGHT, 0x28: KEY_DOWN,
    0x2D: KEY_INSERT, 0x2E: KEY_DELETE,
    0xA0: KEY_LEFTSHIFT, 0xA1: KEY_RIGHTSHIFT, 0xA2: KEY_LEFTCTRL,
    0xA3: KEY_RIGHTCTRL, 0xA4: KEY_LEFTALT, 0xA5: KEY_RIGHTALT,
    0x5B: KEY_LEFTMETA, 0x5C: KEY_RIGHTMETA, 0x5D: KEY_COMPOSE,
}
_VK_TO_KEY[0x30] = 11                      # VK '0' -> KEY_0
for _i in range(1, 10):                    # VK '1'..'9' -> KEY_1..KEY_9
    _VK_TO_KEY[0x30 + _i] = KEY_1 + _i - 1
for _i in range(26):                       # A-Z
    _VK_TO_KEY[0x41 + _i] = None           # filled below via the scancode path
for _i in range(12):                       # F1-F12
    _VK_TO_KEY[0x70 + _i] = KEY_F1 + _i
_VK_TO_KEY = {k: v for k, v in _VK_TO_KEY.items() if v is not None}
_KEY_TO_VK = {v: k for k, v in _VK_TO_KEY.items()}


def from_win32(vk, scancode, extended):
    """Windows low-level hook -> canonical keycode, or None if unmapped."""
    if vk in _VK_OVERRIDE:
        return _VK_OVERRIDE[vk]
    if extended:
        # Synthetic events (SendInput, on-screen keyboards, some KVMs) arrive
        # with scancode 0, so the VK is all we have to go on.
        return _EXT_TO_KEY.get(scancode) or _VK_TO_KEY.get(vk)
    if scancode and scancode not in _PLAIN_GAPS and 0x01 <= scancode <= 0x58:
        return scancode                     # identity: evdev was built on set-1
    return _VK_TO_KEY.get(vk)


def to_win32(key):
    """Canonical keycode -> (scancode, extended, vk). vk is 0 when the scancode
    is authoritative, which is the case for everything SendInput needs."""
    if key in _KEY_TO_EXT:
        return _KEY_TO_EXT[key], True, 0
    if key == KEY_PAUSE:
        return 0x45, False, 0x13
    if key == KEY_NUMLOCK:
        return 0x45, False, 0x90
    if 0x01 <= key <= 0x58 and key not in _PLAIN_GAPS:
        return key, False, 0
    return 0, False, _KEY_TO_VK.get(key, 0)


# --------------------------------------------------------------------------
# X11: keycode <-> canonical
# --------------------------------------------------------------------------

# On any X server driven by xf86-input-evdev or libinput -- which is every
# modern Linux desktop -- the X keycode is the evdev code plus 8. The backend
# verifies this at startup against two layout-independent keys and falls back
# to keysym lookup if the assumption does not hold.
X_KEYCODE_OFFSET = 8


def from_x11(keycode):
    code = keycode - X_KEYCODE_OFFSET
    return code if code > 0 else None


def to_x11(key):
    return key + X_KEYCODE_OFFSET


# Layout-independent keysyms, used both to verify the +8 assumption and to
# build a fallback map on an X server that does not use evdev keycodes.
FALLBACK_KEYSYMS = {
    KEY_ESC: 0xFF1B, KEY_TAB: 0xFF09, KEY_ENTER: 0xFF0D, KEY_BACKSPACE: 0xFF08,
    KEY_SPACE: 0x0020, KEY_CAPSLOCK: 0xFFE5, KEY_NUMLOCK: 0xFF7F,
    KEY_SCROLLLOCK: 0xFF14, KEY_PAUSE: 0xFF13, KEY_SYSRQ: 0xFF61,
    KEY_LEFTSHIFT: 0xFFE1, KEY_RIGHTSHIFT: 0xFFE2,
    KEY_LEFTCTRL: 0xFFE3, KEY_RIGHTCTRL: 0xFFE4,
    KEY_LEFTALT: 0xFFE9, KEY_RIGHTALT: 0xFFEA,
    KEY_LEFTMETA: 0xFFEB, KEY_RIGHTMETA: 0xFFEC, KEY_COMPOSE: 0xFF67,
    KEY_HOME: 0xFF50, KEY_LEFT: 0xFF51, KEY_UP: 0xFF52, KEY_RIGHT: 0xFF53,
    KEY_DOWN: 0xFF54, KEY_PAGEUP: 0xFF55, KEY_PAGEDOWN: 0xFF56, KEY_END: 0xFF57,
    KEY_INSERT: 0xFF63, KEY_DELETE: 0xFFFF,
    KEY_KPENTER: 0xFF8D, KEY_KPSLASH: 0xFFAF, KEY_KPASTERISK: 0xFFAA,
    KEY_MINUS: 0x002D, KEY_EQUAL: 0x003D,
}
for _i in range(12):
    FALLBACK_KEYSYMS[KEY_F1 + _i] = 0xFFBE + _i
# a-z by physical position on a US layout, which is the only sane guess when
# the +8 assumption has already failed.
_QWERTY_ROWS = [
    (KEY_Q, "qwertyuiop"),
    (KEY_A, "asdfghjkl"),
    (KEY_Z, "zxcvbnm"),
]
for _start, _letters in _QWERTY_ROWS:
    for _i, _ch in enumerate(_letters):
        FALLBACK_KEYSYMS[_start + _i] = ord(_ch)
for _i in range(9):
    FALLBACK_KEYSYMS[KEY_1 + _i] = ord("1") + _i
FALLBACK_KEYSYMS[11] = ord("0")   # KEY_0 sits after KEY_9, not before KEY_1

# X11 pointer button numbers <-> canonical button codes.
X_BUTTON_TO_BTN = {1: BTN_LEFT, 2: BTN_MIDDLE, 3: BTN_RIGHT, 8: BTN_SIDE, 9: BTN_EXTRA}
BTN_TO_X_BUTTON = {v: k for k, v in X_BUTTON_TO_BTN.items()}

# X11 delivers wheel motion as button presses. 4/5 are vertical, 6/7 horizontal.
X_SCROLL_BUTTONS = {4: (0, 120), 5: (0, -120), 6: (-120, 0), 7: (120, 0)}


def key_name(key):
    """Human-readable name, for logs and for parsing hotkeys from the config."""
    return _NAMES.get(key, f"key{key}")


_NAMES = {}


def _build_names():
    import sys
    module = sys.modules[__name__]
    for name in dir(module):
        if name.startswith(("KEY_", "BTN_")):
            value = getattr(module, name)
            if isinstance(value, int):
                _NAMES.setdefault(value, name[4:].lower())
    for start, letters in _QWERTY_ROWS:
        for i, ch in enumerate(letters):
            _NAMES[start + i] = ch
    for i in range(9):
        _NAMES[KEY_1 + i] = str(i + 1)
    _NAMES[11] = "0"
    for i in range(12):
        _NAMES[KEY_F1 + i] = f"f{i + 1}"


_build_names()
NAME_TO_KEY = {v: k for k, v in _NAMES.items()}
NAME_TO_KEY.update({
    "ctrl": KEY_LEFTCTRL, "control": KEY_LEFTCTRL, "shift": KEY_LEFTSHIFT,
    "alt": KEY_LEFTALT, "meta": KEY_LEFTMETA, "super": KEY_LEFTMETA,
    "win": KEY_LEFTMETA, "cmd": KEY_LEFTMETA, "esc": KEY_ESC, "escape": KEY_ESC,
    "return": KEY_ENTER, "del": KEY_DELETE, "ins": KEY_INSERT, "pgup": KEY_PAGEUP,
    "pgdn": KEY_PAGEDOWN, "pagedown": KEY_PAGEDOWN, "pageup": KEY_PAGEUP,
})


def parse_hotkey(spec):
    """'ctrl+alt+left' -> (modifier_mask, key). Raises ValueError on nonsense."""
    parts = [p.strip().lower() for p in spec.split("+")]
    if not parts or any(not p for p in parts):
        raise ValueError(f"malformed hotkey {spec!r}: empty segment")
    mods, key = 0, None
    for part in parts:
        code = NAME_TO_KEY.get(part)
        if code is None:
            raise ValueError(f"unknown key {part!r} in hotkey {spec!r}")
        if code in MODIFIER_KEYS and part != parts[-1]:
            mods |= MODIFIER_KEYS[code]
        else:
            key = code
    if key is None:
        raise ValueError(f"hotkey {spec!r} has modifiers but no key")
    if key in MODIFIER_KEYS:
        # A hotkey that is only modifiers would fire on every chord the user
        # types, so it is a configuration mistake rather than a valid binding.
        raise ValueError(
            f"hotkey {spec!r} is only modifiers; it needs a real key too")
    return mods, key
