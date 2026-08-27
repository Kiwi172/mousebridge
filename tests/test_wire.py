import socket
import threading
import unittest

from mb import crypto, wire


class Codec(unittest.TestCase):
    def test_hot_paths_round_trip(self):
        cases = [
            (wire.MSG_MOTION, (1920, -40)),
            (wire.MSG_MOTION, (-2147483648, 2147483647)),
            (wire.MSG_BUTTON, (0x110, 1)),
            (wire.MSG_SCROLL, (0, -120)),
            (wire.MSG_KEY, (30, 0)),
            (wire.MSG_ENTER, (5, 5, 0b101)),
            (wire.MSG_LEAVE, ()),
        ]
        for msg_type, payload in cases:
            with self.subTest(msg_type=msg_type):
                self.assertEqual(
                    wire.decode(wire.encode(msg_type, payload)),
                    (msg_type, tuple(payload)))

    def test_input_events_stay_small(self):
        """Hot messages are struct-packed, not JSON, and it should show."""
        self.assertLessEqual(len(wire.encode(wire.MSG_MOTION, (1920, 1080))), 9)
        self.assertLessEqual(len(wire.encode(wire.MSG_KEY, (30, 1))), 4)

    def test_clipboard_carries_raw_bytes(self):
        payload = {"seq": 3, "mime": "image/png", "data": bytes(range(256))}
        _, got = wire.decode(wire.encode(wire.MSG_CLIPBOARD_DATA, payload))
        self.assertEqual(got, payload)

    def test_control_messages_are_json(self):
        payload = {"focus": "laptop", "by": "desktop"}
        self.assertEqual(
            wire.decode(wire.encode(wire.MSG_FOCUS, payload)),
            (wire.MSG_FOCUS, payload))


class Handshake(unittest.TestCase):
    def _pair(self, key_a, key_b):
        left, right = socket.socketpair()
        # A peer that rejects the handshake stops talking, so the other side
        # would block forever without this. Production sets the same timeout
        # in Node._accept and Node._dial_loop before handshaking.
        left.settimeout(5)
        right.settimeout(5)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        result = {}

        def acceptor():
            try:
                result["b"] = wire.handshake(right, key_b, "bravo", initiator=False)
            except Exception as exc:
                result["b"] = exc
                right.close()      # let the other side see EOF instead of hanging

        thread = threading.Thread(target=acceptor)
        thread.start()
        try:
            result["a"] = wire.handshake(left, key_a, "alpha", initiator=True)
        except Exception as exc:
            result["a"] = exc
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "handshake thread wedged")
        return result

    def test_matching_secret_establishes_a_channel(self):
        key = crypto.derive_cluster_key("open sesame", "home")
        result = self._pair(key, key)
        (channel_a, name_b), (channel_b, name_a) = result["a"], result["b"]
        self.assertEqual(name_a, "alpha")
        self.assertEqual(name_b, "bravo")

        channel_a.send(wire.MSG_KEY, (30, 1))
        self.assertEqual(channel_b.recv(), (wire.MSG_KEY, (30, 1)))
        channel_b.send(wire.MSG_FOCUS, {"focus": "alpha"})
        self.assertEqual(channel_a.recv(), (wire.MSG_FOCUS, {"focus": "alpha"}))

    def test_wrong_secret_is_refused(self):
        result = self._pair(
            crypto.derive_cluster_key("open sesame", "home"),
            crypto.derive_cluster_key("open barley", "home"))
        self.assertTrue(
            isinstance(result["a"], Exception) or isinstance(result["b"], Exception),
            "a peer with the wrong secret must not get a channel")

    def test_different_cluster_name_is_refused(self):
        result = self._pair(
            crypto.derive_cluster_key("same words", "home"),
            crypto.derive_cluster_key("same words", "office"))
        self.assertTrue(
            isinstance(result["a"], Exception) or isinstance(result["b"], Exception))

    def test_replayed_frame_is_rejected(self):
        key = crypto.derive_cluster_key("open sesame", "home")
        result = self._pair(key, key)
        channel_a, _ = result["a"]
        channel_b, _ = result["b"]

        # Capture a legitimate frame, then send it a second time. A replay
        # would otherwise let an attacker repeat a click.
        channel_a.send(wire.MSG_BUTTON, (0x110, 1))
        self.assertEqual(channel_b.recv(), (wire.MSG_BUTTON, (0x110, 1)))
        channel_a._send_counter -= 1        # force the same nonce again
        channel_a.send(wire.MSG_BUTTON, (0x110, 1))
        with self.assertRaises(crypto.AuthenticationError):
            channel_b.recv()


if __name__ == "__main__":
    unittest.main()
