"""End-to-end against the real X server, if there is one.

Everything else stubs the hardware. This test does not: a second node drives
this machine's actual cursor and actual clipboard over a real encrypted socket,
which is the only way to find out whether XTEST and the selection code really
do what the unit tests assume.

Skipped automatically off X11.
"""

import os
import subprocess
import sys
import threading
import time
import unittest

from mb import keymap
from mb.node import Node
from tests.fake_backend import FakeBackend
from tests.test_integration import free_port, make_config, wait_for

HAVE_X11 = bool(os.environ.get("DISPLAY")) and sys.platform.startswith("linux")

FOREIGN_READER = r"""
import ctypes, sys, time, select
from ctypes import byref, c_long
sys.path.insert(0, %r)
from mb.backend import _xlib as X
d = X.libX11.XOpenDisplay(None)
scr = X.libX11.XDefaultScreen(d); root = X.libX11.XRootWindow(d, scr)
win = X.libX11.XCreateSimpleWindow(d, root, -10, -10, 1, 1, 0, 0, 0)
A = lambda n: X.libX11.XInternAtom(d, n.encode(), False)
X.libX11.XConvertSelection(d, A("CLIPBOARD"), A("UTF8_STRING"), A("P"), win, 0)
X.libX11.XFlush(d)
ev = X.XEvent(); end = time.monotonic() + 5
while time.monotonic() < end:
    if X.libX11.XPending(d) == 0:
        select.select([X.libX11.XConnectionNumber(d)], [], [], 0.2); continue
    X.libX11.XNextEvent(d, byref(ev))
    if ev.type == X.SELECTION_NOTIFY:
        if ev.xselection.property == 0: print("REFUSED"); break
        print(X.get_property(d, win, A("P"))[2].decode("utf-8")); break
"""


@unittest.skipUnless(HAVE_X11, "needs an X11 display")
class LiveX11(unittest.TestCase):
    SECRET = "live-test-secret"

    @classmethod
    def setUpClass(cls):
        from mb.backend.x11 import X11Backend
        port_local, port_remote = free_port(), free_port()

        cls.real = X11Backend({"max_bytes": 4 << 20})
        cls.saved_cursor = cls.real.cursor_position()
        width, height = cls.real.screen_size()

        # "remote" sorts before "thisbox", so remote dials and this node listens.
        cls.local = Node(
            make_config("thisbox", port_local, "remote", port_remote, "left"),
            cls.SECRET, backend=cls.real)
        cls.fake = FakeBackend(2560, 1440)
        cls.remote = Node(
            make_config("remote", port_remote, "thisbox", port_local, "right"),
            cls.SECRET, backend=cls.fake)

        for node in (cls.local, cls.remote):
            threading.Thread(target=node.run, daemon=True).start()
        wait_for(lambda: cls.local.peers["remote"].alive
                 and cls.remote.peers["thisbox"].alive, what="nodes to connect")
        wait_for(lambda: "thisbox" in cls.remote.cluster.screens,
                 what="screen exchange")
        cls.screen = (width, height)

    @classmethod
    def tearDownClass(cls):
        for node in (cls.local, cls.remote):
            node.running = False
            node.shutdown()
        try:
            cls.real.warp_cursor(*cls.saved_cursor)
        except Exception:
            pass

    def take_control_from_remote(self):
        """Make the fake remote node grab control and hand the cursor here."""
        with self.remote.lock:
            self.remote._edge_armed = True
            self.remote.virtual = (2559, 720)
            node, x, y = self.remote.cluster.cross("remote", "right", 2559, 720)
            self.remote._move_focus(node, x, y)
        wait_for(lambda: self.local.controlled_by == "remote",
                 what="this machine to be driven")

    def test_remote_node_moves_this_machines_real_cursor(self):
        self.take_control_from_remote()
        width, height = self.screen
        for target in ((0, 540), (400, 300), (900, 700)):
            with self.remote.lock:
                self.remote.virtual = target
            self.remote._send_to("thisbox", 1, target)   # MSG_MOTION
            wait_for(lambda t=target: self.real.cursor_position() == t,
                     timeout=3, what=f"the real cursor to reach {target}")
        self.assertEqual(self.real.cursor_position(), (900, 700))

    def test_remote_node_sets_this_machines_real_clipboard(self):
        payload = "live X11 test — éàü \U0001F5B1"
        self.fake.local_copy(payload)
        wait_for(lambda: self.real.clipboard_read()
                 and self.real.clipboard_read()[1].decode() == payload,
                 timeout=5, what="the real clipboard to be set")

        # And prove an unrelated X client can actually paste it.
        result = subprocess.run(
            [sys.executable, "-c", FOREIGN_READER % os.path.abspath(".")],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.stdout.strip(), payload,
                         f"foreign client got {result.stdout!r} {result.stderr!r}")

    def test_keystrokes_reach_the_real_x_server(self):
        """XTEST accepts the keycode without error and we track it for release."""
        self.take_control_from_remote()
        self.real.inject_key(keymap.KEY_LEFTSHIFT, True)
        self.assertIn(keymap.to_x11(keymap.KEY_LEFTSHIFT), self.real._injected_keys)
        self.real.release_all()
        self.assertEqual(self.real._injected_keys, set())


if __name__ == "__main__":
    unittest.main()
