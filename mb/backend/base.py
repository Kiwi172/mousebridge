"""What every platform backend has to provide.

Two responsibilities that sound similar and are not:

  * **Capture** -- take the local mouse and keyboard away from this machine and
    hand the events to us instead. Needs a grab (X11) or a low-level hook
    (Windows), and has to *swallow* the events so they do not also act locally.
  * **Injection** -- pretend to be a mouse and keyboard. Needs XTEST or
    SendInput, and must not be visible to our own capture, or a machine
    controlling itself would melt down.

The node layer talks only to this interface, so adding macOS later means
writing one file and touching nothing else.
"""


class Backend:
    name = "abstract"

    # -- geometry ----------------------------------------------------------
    def screen_size(self):
        """(width, height) of the full desktop, spanning all monitors."""
        raise NotImplementedError

    def cursor_position(self):
        raise NotImplementedError

    def warp_cursor(self, x, y):
        raise NotImplementedError

    # -- lifecycle ---------------------------------------------------------
    def start(self, handler):
        """Begin the platform event loop in a background thread.

        `handler` is called with (kind, *args) from that thread:
            ("motion", dx, dy)            relative, only while capturing
            ("button", canonical, bool)
            ("scroll", dx, dy)            in 120ths of a detent, like Windows
            ("key",    canonical, bool)
            ("hotkey", name)
        """
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    # -- capture -----------------------------------------------------------
    def set_capturing(self, capturing):
        """Swallow local input (True) or let it through (False)."""
        raise NotImplementedError

    def modifier_state(self):
        """Bitmask of keymap.MOD_* currently held, for surviving a hand-off."""
        raise NotImplementedError

    def register_hotkeys(self, hotkeys):
        """`hotkeys` maps name -> (modifier_mask, canonical_key)."""
        raise NotImplementedError

    # -- injection ---------------------------------------------------------
    def inject_motion(self, x, y):
        raise NotImplementedError

    def inject_button(self, code, pressed):
        raise NotImplementedError

    def inject_scroll(self, dx, dy):
        raise NotImplementedError

    def inject_key(self, code, pressed):
        raise NotImplementedError

    def release_all(self):
        """Release every key and button we injected. Called when the cursor
        leaves, so a machine is never left with a stuck Ctrl."""
        raise NotImplementedError

    # -- clipboard ---------------------------------------------------------
    def clipboard_read(self):
        """Return (mime, bytes) for the current clipboard, or None."""
        raise NotImplementedError

    def clipboard_write(self, mime, data):
        """Take ownership of the clipboard and serve `data` as `mime`."""
        raise NotImplementedError

    def set_clipboard_listener(self, callback):
        """`callback()` fires when another local app changes the clipboard."""
        raise NotImplementedError
