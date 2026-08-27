import unittest

from mb import keymap


class Windows(unittest.TestCase):
    def test_base_range_is_the_identity(self):
        """evdev keycodes were built on AT set-1 scancodes, so most of the
        keyboard needs no translation at all."""
        self.assertEqual(keymap.from_win32(0x41, 0x1E, False), keymap.KEY_A)
        self.assertEqual(keymap.from_win32(0, 0x1C, False), keymap.KEY_ENTER)

    def test_extended_keys_are_translated(self):
        self.assertEqual(keymap.from_win32(0, 0x1C, True), keymap.KEY_KPENTER)
        self.assertEqual(keymap.from_win32(0, 0x4B, True), keymap.KEY_LEFT)
        self.assertEqual(keymap.from_win32(0xA3, 0x1D, True), keymap.KEY_RIGHTCTRL)

    def test_pause_and_numlock_share_a_scancode(self):
        """Windows gives both 0x45 and disagrees with everyone about the
        extended flag, so the virtual key has to break the tie."""
        self.assertEqual(keymap.from_win32(0x13, 0x45, False), keymap.KEY_PAUSE)
        self.assertEqual(keymap.from_win32(0x90, 0x45, True), keymap.KEY_NUMLOCK)

    def test_synthetic_events_fall_back_to_the_virtual_key(self):
        """SendInput and on-screen keyboards report scancode 0."""
        self.assertEqual(keymap.from_win32(0x25, 0, True), keymap.KEY_LEFT)
        self.assertEqual(keymap.from_win32(0x31, 0, False), keymap.KEY_1)

    def test_every_mapped_key_survives_a_round_trip(self):
        keys = [c for c in range(1, 0x59) if c not in keymap._PLAIN_GAPS]
        keys += list(keymap._EXT_TO_KEY.values())
        keys += [keymap.KEY_PAUSE, keymap.KEY_NUMLOCK]
        for key in keys:
            with self.subTest(key=keymap.key_name(key)):
                scancode, extended, vk = keymap.to_win32(key)
                self.assertEqual(keymap.from_win32(vk, scancode, extended), key)


class X11(unittest.TestCase):
    def test_offset_is_eight(self):
        self.assertEqual(keymap.to_x11(keymap.KEY_A), 38)
        self.assertEqual(keymap.from_x11(38), keymap.KEY_A)

    def test_buttons_map_both_ways(self):
        for x_button, code in keymap.X_BUTTON_TO_BTN.items():
            self.assertEqual(keymap.BTN_TO_X_BUTTON[code], x_button)

    def test_wheel_buttons_are_not_buttons(self):
        for button in (4, 5, 6, 7):
            self.assertIn(button, keymap.X_SCROLL_BUTTONS)
            self.assertNotIn(button, keymap.X_BUTTON_TO_BTN)


class HotkeyParsing(unittest.TestCase):
    def test_modifiers_and_key(self):
        self.assertEqual(
            keymap.parse_hotkey("ctrl+alt+left"),
            (keymap.MOD_CTRL | keymap.MOD_ALT, keymap.KEY_LEFT))

    def test_aliases(self):
        self.assertEqual(keymap.parse_hotkey("super+f1"), keymap.parse_hotkey("win+f1"))

    def test_bad_key_is_rejected(self):
        with self.assertRaises(ValueError):
            keymap.parse_hotkey("ctrl+banana")

    def test_modifiers_alone_are_rejected(self):
        with self.assertRaises(ValueError):
            keymap.parse_hotkey("ctrl+")


if __name__ == "__main__":
    unittest.main()
