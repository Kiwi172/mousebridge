import time
import unittest

from mb import crypto


class ChaChaVectors(unittest.TestCase):
    """RFC 8439 test vectors."""

    plaintext = (b"Ladies and Gentlemen of the class of '99: If I could offer you "
                 b"only one tip for the future, sunscreen would be it.")

    def test_chacha20_keystream(self):
        key = bytes(range(32))
        nonce = bytes.fromhex("000000000000004a00000000")
        out = crypto.chacha20_xor(key, 1, nonce, self.plaintext)
        self.assertTrue(out.hex().startswith("6e2e359a2568f98041ba0728dd0d6981"))
        self.assertEqual(crypto.chacha20_xor(key, 1, nonce, out), self.plaintext)

    def test_poly1305(self):
        key = bytes.fromhex(
            "85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
        self.assertEqual(
            crypto.poly1305_mac(key, b"Cryptographic Forum Research Group").hex(),
            "a8061dc1305136c6c22b8baf0c0127a9")

    def test_aead(self):
        key = bytes.fromhex(
            "808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        nonce = bytes.fromhex("070000004041424344454647")
        aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        sealed = crypto.aead_encrypt(key, nonce, self.plaintext, aad)
        self.assertEqual(sealed[-16:].hex(), "1ae10b594f09e26a7e902ecbd0600691")
        self.assertEqual(crypto.aead_decrypt(key, nonce, sealed, aad), self.plaintext)

    def test_x25519(self):
        private = bytes.fromhex(
            "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        public = bytes.fromhex(
            "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f")
        self.assertEqual(
            crypto.x25519_shared(private, public).hex(),
            "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")


class Tampering(unittest.TestCase):
    key = bytes(range(32))
    nonce = bytes(12)

    def test_flipped_bit_is_rejected(self):
        sealed = bytearray(crypto.aead_encrypt(self.key, self.nonce, b"click"))
        sealed[0] ^= 1
        with self.assertRaises(crypto.AuthenticationError):
            crypto.aead_decrypt(self.key, self.nonce, bytes(sealed))

    def test_forged_tag_is_rejected(self):
        sealed = crypto.aead_encrypt(self.key, self.nonce, b"click")
        with self.assertRaises(crypto.AuthenticationError):
            crypto.aead_decrypt(self.key, self.nonce, sealed[:-16] + bytes(16))

    def test_wrong_key_is_rejected(self):
        sealed = crypto.aead_encrypt(self.key, self.nonce, b"click")
        with self.assertRaises(crypto.AuthenticationError):
            crypto.aead_decrypt(bytes(32), self.nonce, sealed)

    def test_low_order_public_key_is_rejected(self):
        with self.assertRaises(crypto.AuthenticationError):
            crypto.x25519_shared(bytes(range(32)), bytes(32))


class Performance(unittest.TestCase):
    def test_input_event_latency_is_acceptable(self):
        """A mouse reports at up to ~1 kHz; crypto must not be the bottleneck."""
        key, message = bytes(range(32)), bytes(16)
        start = time.perf_counter()
        rounds = 500
        for i in range(rounds):
            nonce = b"\x00" * 4 + i.to_bytes(8, "big")
            crypto.aead_decrypt(key, nonce, crypto.aead_encrypt(key, nonce, message))
        micros = (time.perf_counter() - start) / rounds * 1e6
        self.assertLess(micros, 2000, f"seal+open took {micros:.0f} us per event")


if __name__ == "__main__":
    unittest.main()
