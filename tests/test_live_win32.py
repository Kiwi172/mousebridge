"""End-to-end against real Windows, if this is real Windows.

The mirror of tests/test_live_x11.py. Everything else about the Windows
backend is verified structurally -- struct sizes against their documented x64
values, keycodes round-tripping -- which proves the code is *shaped* right and
proves nothing about whether Windows accepts it.

This is the file that finds out. Skipped automatically off Windows.
"""

import sys
import time
import unittest

from mb import keymap

IS_WINDOWS = sys.platform == "win32"


@unittest.skipUnless(IS_WINDOWS, "needs Windows")
class LiveWin32(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mb.backend.win32 import Win32Backend
        cls.backend = Win32Backend({"max_bytes": 1 << 20})
        cls.saved_cursor = cls.backend.cursor_position()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.backend.warp_cursor(*cls.saved_cursor)
            cls.backend.stop()
        except Exception:
            pass

    def test_geometry_is_sane(self):
        width, height = self.backend.screen_size()
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)

    def test_cursor_can_be_warped_and_read_back(self):
        width, height = self.backend.screen_size()
        target = (width // 3, height // 3)
        self.backend.warp_cursor(*target)
        time.sleep(0.05)
        got = self.backend.cursor_position()
        self.assertLess(abs(got[0] - target[0]) + abs(got[1] - target[1]), 4,
                        f"SetCursorPos asked for {target}, cursor is at {got}")

    def test_sendinput_moves_the_real_cursor(self):
        """The single most important unverified claim in the whole project."""
        width, height = self.backend.screen_size()
        for target in ((width // 4, height // 4), (width // 2, height // 2)):
            self.backend.inject_motion(*target)
            time.sleep(0.08)
            got = self.backend.cursor_position()
            # SendInput's absolute coordinates are normalised to 0..65535, so a
            # pixel or two of rounding is expected and fine.
            self.assertLess(
                abs(got[0] - target[0]) + abs(got[1] - target[1]), 6,
                f"SendInput asked for {target}, cursor went to {got}")

    def test_scancode_injection_is_accepted(self):
        """We cannot read the far end, but SendInput reports how many events it
        queued, and tracking must stay consistent for release_all."""
        self.backend.inject_key(keymap.KEY_LEFTSHIFT, True)
        self.assertIn(keymap.KEY_LEFTSHIFT, self.backend._injected_keys)
        self.backend.release_all()
        self.assertEqual(self.backend._injected_keys, set())

    def test_clipboard_round_trips(self):
        payload = "mousebridge live win32 — ünïcode"
        self.backend.clipboard_write("text/plain", payload.encode("utf-8"))
        time.sleep(0.15)
        got = self.backend.clipboard_read()
        self.assertIsNotNone(got, "clipboard came back empty")
        self.assertEqual(got[1].decode("utf-8"), payload)

    def test_low_level_hooks_install(self):
        self.backend.start(lambda *a: None)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.backend._mouse_hook:
                time.sleep(0.05)
            self.assertTrue(self.backend._mouse_hook, "WH_MOUSE_LL hook failed to install")
            self.assertTrue(self.backend._keyboard_hook, "WH_KEYBOARD_LL hook failed to install")
        finally:
            self.backend.stop()


if __name__ == "__main__":
    unittest.main()
