"""Two whole nodes, real sockets, real crypto -- only the hardware is fake."""

import socket
import threading
import time
import unittest

from mb import config as config_module
from mb import keymap, wire
from mb.node import Node
from tests.fake_backend import FakeBackend

ALPHA = (1920, 1080)
BRAVO = (2560, 1440)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(predicate, timeout=8.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def make_config(name, port, peer_name, peer_port, side):
    return config_module._merge(config_module.DEFAULTS, {
        "cluster": "test",
        "node": name,
        "bind": "127.0.0.1",
        "port": port,
        "peers": {peer_name: f"127.0.0.1:{peer_port}"},
        "layout": {
            name: {side: peer_name},
            peer_name: {config_module.OPPOSITE[side]: name},
        },
        "edge": {"corner_size": 40, "switch_delay_ms": 0, "wrap": False},
        "reconnect_seconds": 0.2,
    })


class TwoNodes(unittest.TestCase):
    """alpha (1920x1080) with bravo (2560x1440) to its right."""

    SECRET = "correct-horse-battery-staple"

    @classmethod
    def setUpClass(cls):
        port_a, port_b = free_port(), free_port()
        cls.backend_a = FakeBackend(*ALPHA)
        cls.backend_b = FakeBackend(*BRAVO)
        cls.alpha = Node(make_config("alpha", port_a, "bravo", port_b, "right"),
                         cls.SECRET, backend=cls.backend_a)
        cls.bravo = Node(make_config("bravo", port_b, "alpha", port_a, "left"),
                         cls.SECRET, backend=cls.backend_b)
        cls.threads = []
        for node in (cls.alpha, cls.bravo):
            thread = threading.Thread(target=node.run, daemon=True)
            thread.start()
            cls.threads.append(thread)

        wait_for(lambda: cls.alpha.peers["bravo"].alive and cls.bravo.peers["alpha"].alive,
                 what="the two nodes to connect")
        wait_for(lambda: "bravo" in cls.alpha.cluster.screens
                 and "alpha" in cls.bravo.cluster.screens,
                 what="screen sizes to be exchanged")

    @classmethod
    def tearDownClass(cls):
        for node in (cls.alpha, cls.bravo):
            node.running = False
        for node in (cls.alpha, cls.bravo):
            node.shutdown()

    def setUp(self):
        # Every test starts with the cursor at home on alpha.
        with self.alpha.lock:
            self.alpha._take_focus_locally()
        wait_for(lambda: self.bravo.focus == "alpha", what="bravo to agree focus is alpha")
        self.backend_a.warp_cursor(960, 540)
        self.backend_a.drain()
        self.backend_b.drain()
        time.sleep(0.05)

    # ------------------------------------------------------------------
    def cross_to_bravo(self):
        self.backend_a.warp_cursor(900, 540)        # away from the edge, to arm it
        wait_for(lambda: self.alpha._edge_armed, what="the edge to arm")
        self.backend_a.warp_cursor(1919, 540)       # now rest against it
        wait_for(lambda: self.alpha.controlling, what="alpha to take control")

    def test_screens_are_exchanged(self):
        self.assertEqual(self.alpha.cluster.screens["bravo"], BRAVO)
        self.assertEqual(self.bravo.cluster.screens["alpha"], ALPHA)

    def test_cursor_crosses_to_the_machine_on_the_right(self):
        self.cross_to_bravo()
        self.assertEqual(self.alpha.focus, "bravo")
        self.assertTrue(self.backend_a.capturing, "alpha must be swallowing local input")
        wait_for(lambda: self.bravo.controlled_by == "alpha", what="bravo to know it is driven")

        # It should land on bravo's left edge at the proportional height.
        wait_for(lambda: self.backend_b.drain("motion"), what="bravo to be moved")
        self.assertEqual(self.backend_b.cursor[0], 0)
        self.assertAlmostEqual(self.backend_b.cursor[1], 720, delta=2)

    def test_bravo_does_not_bounce_the_cursor_straight_back(self):
        """The cursor lands on bravo's left edge, which is a boundary with
        alpha. Nothing should send it home again."""
        self.cross_to_bravo()
        time.sleep(0.4)
        self.assertEqual(self.alpha.focus, "bravo")
        self.assertTrue(self.alpha.controlling)

    def test_mouse_motion_is_forwarded(self):
        self.cross_to_bravo()
        self.backend_b.drain()
        self.backend_a.feed("motion", 300, 100)
        wait_for(lambda: self.backend_b.drain("motion") or self.backend_b.cursor[0] >= 300,
                 what="motion to arrive")
        self.assertGreaterEqual(self.backend_b.cursor[0], 250)

    def test_keystrokes_are_forwarded_in_order(self):
        key_b = keymap.NAME_TO_KEY["b"]
        self.cross_to_bravo()
        self.backend_b.drain()
        for event in [(keymap.KEY_LEFTSHIFT, True), (key_b, True),
                      (key_b, False), (keymap.KEY_LEFTSHIFT, False)]:
            self.backend_a.feed("key", *event)
        wait_for(lambda: self.backend_b.count("key") >= 4, what="four key events")
        self.assertEqual(self.backend_b.drain("key"), [
            ("key", keymap.KEY_LEFTSHIFT, True),
            ("key", key_b, True),
            ("key", key_b, False),
            ("key", keymap.KEY_LEFTSHIFT, False),
        ], "shift+B must arrive as shift down, B down, B up, shift up")

    def test_buttons_and_scroll_are_forwarded(self):
        self.cross_to_bravo()
        self.backend_b.drain()
        self.backend_a.feed("button", keymap.BTN_LEFT, True)
        self.backend_a.feed("button", keymap.BTN_LEFT, False)
        self.backend_a.feed("scroll", 0, -120)
        wait_for(lambda: self.backend_b.count() >= 3, what="button and scroll events")
        events = self.backend_b.drain()
        self.assertIn(("button", keymap.BTN_LEFT, True), events)
        self.assertIn(("button", keymap.BTN_LEFT, False), events)
        self.assertIn(("scroll", 0, -120), events)

    def test_cursor_comes_home_across_the_same_edge(self):
        self.cross_to_bravo()
        self.backend_a.feed("motion", -50, 0)        # push back past bravo's left edge
        wait_for(lambda: self.alpha.focus == "alpha", what="the cursor to come home")
        self.assertFalse(self.alpha.controlling)
        self.assertFalse(self.backend_a.capturing, "alpha must stop swallowing input")
        self.assertGreater(self.backend_a.cursor[0], 1800)
        wait_for(lambda: self.bravo.controlled_by is None,
                 what="bravo to stop being driven")

    def test_held_modifiers_survive_the_crossing(self):
        self.backend_a.mods = keymap.MOD_SHIFT | keymap.MOD_CTRL
        try:
            self.cross_to_bravo()
            wait_for(lambda: self.backend_b.count("key") >= 2,
                     what="modifiers to be re-asserted on bravo")
            events = self.backend_b.drain("key")
            self.assertIn(("key", keymap.KEY_LEFTSHIFT, True), events)
            self.assertIn(("key", keymap.KEY_LEFTCTRL, True), events)
        finally:
            self.backend_a.mods = 0

    def test_leaving_releases_everything_held_on_the_far_side(self):
        self.cross_to_bravo()
        before = self.backend_b.released
        self.backend_a.feed("motion", -50, 0)
        wait_for(lambda: self.backend_b.released > before,
                 what="bravo to release held keys")

    def test_hotkey_switches_screens(self):
        self.assertEqual(self.alpha.focus, "alpha")
        self.backend_a.feed("hotkey", "switch_right")
        wait_for(lambda: self.alpha.focus == "bravo", what="the hotkey to switch")
        self.assertTrue(self.alpha.controlling)
        self.backend_a.feed("hotkey", "reclaim")
        wait_for(lambda: self.alpha.focus == "alpha", what="reclaim to bring it back")
        self.assertFalse(self.alpha.controlling)

    def test_clipboard_propagates(self):
        self.backend_a.local_copy("copied on alpha")
        wait_for(lambda: self.backend_b.clipboard
                 and self.backend_b.clipboard[1] == b"copied on alpha",
                 what="the clipboard to reach bravo")

    def test_clipboard_does_not_echo_back_and_forth(self):
        """bravo writing what it received must not offer it straight back."""
        self.backend_a.local_copy("no echo please")
        wait_for(lambda: self.backend_b.clipboard
                 and self.backend_b.clipboard[1] == b"no echo please",
                 what="the clipboard to reach bravo")
        seq_before = self.bravo._clipboard_seq
        # Simulate bravo's OS notifying about the write we just performed.
        if self.backend_b.clipboard_listener:
            self.backend_b.clipboard_listener(*self.backend_b.clipboard)
        time.sleep(0.2)
        self.assertEqual(self.bravo._clipboard_seq, seq_before,
                         "bravo re-offered the clipboard it had just been given")

    def test_oversized_clipboard_is_refused(self):
        limit = self.bravo.cfg["clipboard"]["max_bytes"]
        seq_before = self.alpha._clipboard_seq
        self.bravo._on_clipboard_offer(
            self.bravo.peers["alpha"],
            {"seq": 999, "mime": "text/plain", "size": limit + 1})
        time.sleep(0.2)
        # bravo must not have requested it, so alpha never served anything.
        self.assertEqual(self.alpha._clipboard_seq, seq_before)


class Security(unittest.TestCase):
    def test_a_peer_with_the_wrong_secret_cannot_connect(self):
        port_a, port_b = free_port(), free_port()
        good = Node(make_config("alpha", port_a, "bravo", port_b, "right"),
                    "the-right-secret", backend=FakeBackend(*ALPHA))
        thread = threading.Thread(target=good.run, daemon=True)
        thread.start()
        try:
            time.sleep(0.4)
            impostor = socket.create_connection(("127.0.0.1", port_a), timeout=5)
            impostor.settimeout(5)
            from mb import crypto
            with self.assertRaises((crypto.AuthenticationError, OSError)):
                wire.handshake(
                    impostor, crypto.derive_cluster_key("the-wrong-secret", "test"),
                    "bravo", initiator=True)
            impostor.close()
            self.assertIsNone(good.peers["bravo"].channel)
        finally:
            good.running = False
            good.shutdown()

    def test_an_unknown_node_name_is_refused(self):
        port_a, port_b = free_port(), free_port()
        good = Node(make_config("alpha", port_a, "bravo", port_b, "right"),
                    "shared", backend=FakeBackend(*ALPHA))
        thread = threading.Thread(target=good.run, daemon=True)
        thread.start()
        try:
            time.sleep(0.4)
            from mb import crypto
            stranger = socket.create_connection(("127.0.0.1", port_a), timeout=5)
            stranger.settimeout(5)
            channel, _ = wire.handshake(
                stranger, crypto.derive_cluster_key("shared", "test"),
                "charlie", initiator=True)
            # Right secret, wrong identity: the node must still hang up.
            time.sleep(0.4)
            with self.assertRaises((crypto.AuthenticationError, OSError, ValueError)):
                for _ in range(50):
                    channel.send(wire.MSG_PING, {})
                    time.sleep(0.02)
            stranger.close()
        finally:
            good.running = False
            good.shutdown()


if __name__ == "__main__":
    unittest.main()
