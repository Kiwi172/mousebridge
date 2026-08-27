"""Authenticated, forward-secret transport crypto, using nothing but hashlib.

The link carries every keystroke you type on the other machine, so it is
encrypted unconditionally -- there is no "plaintext on the LAN" mode.

  * X25519 gives a fresh session key per connection (forward secrecy).
  * The exchange is authenticated with a pre-shared passphrase, so an attacker
    on your network cannot machine-in-the-middle it.
  * ChaCha20-Poly1305 (RFC 8439) protects each frame, with a strictly
    increasing counter per direction so frames cannot be replayed or reordered.

Everything here is pure Python. That is slow by cryptographic standards and
fast enough by input-latency standards: a mouse packet is ~16 bytes, which is
one ChaCha block, and the whole seal/open round trip costs well under half a
millisecond. See tests/test_crypto.py for the vectors and the timing check.
"""

import hashlib
import hmac
import os
import struct

# --------------------------------------------------------------------------
# ChaCha20 (RFC 8439 section 2.4)
# --------------------------------------------------------------------------

_MASK = 0xFFFFFFFF
_SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)


def _chacha20_block(key_words, counter, nonce_words):
    """One 64-byte keystream block. Deliberately unrolled: the quarter-round
    is the hot path, and function-call overhead dominates it in CPython."""
    s0, s1, s2, s3 = _SIGMA
    s4, s5, s6, s7, s8, s9, s10, s11 = key_words
    s12 = counter & _MASK
    s13, s14, s15 = nonce_words

    x0, x1, x2, x3 = s0, s1, s2, s3
    x4, x5, x6, x7 = s4, s5, s6, s7
    x8, x9, x10, x11 = s8, s9, s10, s11
    x12, x13, x14, x15 = s12, s13, s14, s15

    for _ in range(10):
        # column rounds
        x0 = (x0 + x4) & _MASK; x12 ^= x0; x12 = ((x12 << 16) | (x12 >> 16)) & _MASK
        x8 = (x8 + x12) & _MASK; x4 ^= x8; x4 = ((x4 << 12) | (x4 >> 20)) & _MASK
        x0 = (x0 + x4) & _MASK; x12 ^= x0; x12 = ((x12 << 8) | (x12 >> 24)) & _MASK
        x8 = (x8 + x12) & _MASK; x4 ^= x8; x4 = ((x4 << 7) | (x4 >> 25)) & _MASK

        x1 = (x1 + x5) & _MASK; x13 ^= x1; x13 = ((x13 << 16) | (x13 >> 16)) & _MASK
        x9 = (x9 + x13) & _MASK; x5 ^= x9; x5 = ((x5 << 12) | (x5 >> 20)) & _MASK
        x1 = (x1 + x5) & _MASK; x13 ^= x1; x13 = ((x13 << 8) | (x13 >> 24)) & _MASK
        x9 = (x9 + x13) & _MASK; x5 ^= x9; x5 = ((x5 << 7) | (x5 >> 25)) & _MASK

        x2 = (x2 + x6) & _MASK; x14 ^= x2; x14 = ((x14 << 16) | (x14 >> 16)) & _MASK
        x10 = (x10 + x14) & _MASK; x6 ^= x10; x6 = ((x6 << 12) | (x6 >> 20)) & _MASK
        x2 = (x2 + x6) & _MASK; x14 ^= x2; x14 = ((x14 << 8) | (x14 >> 24)) & _MASK
        x10 = (x10 + x14) & _MASK; x6 ^= x10; x6 = ((x6 << 7) | (x6 >> 25)) & _MASK

        x3 = (x3 + x7) & _MASK; x15 ^= x3; x15 = ((x15 << 16) | (x15 >> 16)) & _MASK
        x11 = (x11 + x15) & _MASK; x7 ^= x11; x7 = ((x7 << 12) | (x7 >> 20)) & _MASK
        x3 = (x3 + x7) & _MASK; x15 ^= x3; x15 = ((x15 << 8) | (x15 >> 24)) & _MASK
        x11 = (x11 + x15) & _MASK; x7 ^= x11; x7 = ((x7 << 7) | (x7 >> 25)) & _MASK

        # diagonal rounds
        x0 = (x0 + x5) & _MASK; x15 ^= x0; x15 = ((x15 << 16) | (x15 >> 16)) & _MASK
        x10 = (x10 + x15) & _MASK; x5 ^= x10; x5 = ((x5 << 12) | (x5 >> 20)) & _MASK
        x0 = (x0 + x5) & _MASK; x15 ^= x0; x15 = ((x15 << 8) | (x15 >> 24)) & _MASK
        x10 = (x10 + x15) & _MASK; x5 ^= x10; x5 = ((x5 << 7) | (x5 >> 25)) & _MASK

        x1 = (x1 + x6) & _MASK; x12 ^= x1; x12 = ((x12 << 16) | (x12 >> 16)) & _MASK
        x11 = (x11 + x12) & _MASK; x6 ^= x11; x6 = ((x6 << 12) | (x6 >> 20)) & _MASK
        x1 = (x1 + x6) & _MASK; x12 ^= x1; x12 = ((x12 << 8) | (x12 >> 24)) & _MASK
        x11 = (x11 + x12) & _MASK; x6 ^= x11; x6 = ((x6 << 7) | (x6 >> 25)) & _MASK

        x2 = (x2 + x7) & _MASK; x13 ^= x2; x13 = ((x13 << 16) | (x13 >> 16)) & _MASK
        x8 = (x8 + x13) & _MASK; x7 ^= x8; x7 = ((x7 << 12) | (x7 >> 20)) & _MASK
        x2 = (x2 + x7) & _MASK; x13 ^= x2; x13 = ((x13 << 8) | (x13 >> 24)) & _MASK
        x8 = (x8 + x13) & _MASK; x7 ^= x8; x7 = ((x7 << 7) | (x7 >> 25)) & _MASK

        x3 = (x3 + x4) & _MASK; x14 ^= x3; x14 = ((x14 << 16) | (x14 >> 16)) & _MASK
        x9 = (x9 + x14) & _MASK; x4 ^= x9; x4 = ((x4 << 12) | (x4 >> 20)) & _MASK
        x3 = (x3 + x4) & _MASK; x14 ^= x3; x14 = ((x14 << 8) | (x14 >> 24)) & _MASK
        x9 = (x9 + x14) & _MASK; x4 ^= x9; x4 = ((x4 << 7) | (x4 >> 25)) & _MASK

    return struct.pack(
        "<16I",
        (x0 + s0) & _MASK, (x1 + s1) & _MASK, (x2 + s2) & _MASK, (x3 + s3) & _MASK,
        (x4 + s4) & _MASK, (x5 + s5) & _MASK, (x6 + s6) & _MASK, (x7 + s7) & _MASK,
        (x8 + s8) & _MASK, (x9 + s9) & _MASK, (x10 + s10) & _MASK, (x11 + s11) & _MASK,
        (x12 + s12) & _MASK, (x13 + s13) & _MASK, (x14 + s14) & _MASK, (x15 + s15) & _MASK,
    )


def chacha20_xor(key, counter, nonce, data):
    """XOR `data` with the ChaCha20 keystream. key: 32 bytes, nonce: 12 bytes."""
    key_words = struct.unpack("<8I", key)
    nonce_words = struct.unpack("<3I", nonce)
    out = bytearray(len(data))
    for offset in range(0, len(data), 64):
        block = _chacha20_block(key_words, counter, nonce_words)
        counter += 1
        chunk = data[offset:offset + 64]
        for i, b in enumerate(chunk):
            out[offset + i] = b ^ block[i]
    return bytes(out)


# --------------------------------------------------------------------------
# Poly1305 (RFC 8439 section 2.5)
# --------------------------------------------------------------------------

_P1305 = (1 << 130) - 5


def poly1305_mac(key, msg):
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for offset in range(0, len(msg), 16):
        chunk = msg[offset:offset + 16]
        n = int.from_bytes(chunk + b"\x01", "little")
        acc = ((acc + n) * r) % _P1305
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(data):
    rem = len(data) % 16
    return b"" if rem == 0 else b"\x00" * (16 - rem)


def aead_encrypt(key, nonce, plaintext, aad=b""):
    """ChaCha20-Poly1305 AEAD. Returns ciphertext || 16-byte tag."""
    poly_key = _chacha20_block(struct.unpack("<8I", key), 0, struct.unpack("<3I", nonce))[:32]
    ciphertext = chacha20_xor(key, 1, nonce, plaintext)
    mac_data = (
        aad + _pad16(aad)
        + ciphertext + _pad16(ciphertext)
        + struct.pack("<QQ", len(aad), len(ciphertext))
    )
    return ciphertext + poly1305_mac(poly_key, mac_data)


class AuthenticationError(Exception):
    """The frame was forged, corrupted, replayed, or encrypted under another key."""


def aead_decrypt(key, nonce, blob, aad=b""):
    if len(blob) < 16:
        raise AuthenticationError("frame too short to carry a tag")
    ciphertext, tag = blob[:-16], blob[-16:]
    poly_key = _chacha20_block(struct.unpack("<8I", key), 0, struct.unpack("<3I", nonce))[:32]
    mac_data = (
        aad + _pad16(aad)
        + ciphertext + _pad16(ciphertext)
        + struct.pack("<QQ", len(aad), len(ciphertext))
    )
    if not hmac.compare_digest(poly1305_mac(poly_key, mac_data), tag):
        raise AuthenticationError("bad authentication tag")
    return chacha20_xor(key, 1, nonce, ciphertext)


# --------------------------------------------------------------------------
# X25519 (RFC 7748)
# --------------------------------------------------------------------------

_P25519 = (1 << 255) - 19
_A24 = 121665


def _cswap(swap, a, b):
    dummy = ((1 << 256) - 1) * swap & (a ^ b)
    return a ^ dummy, b ^ dummy


def _x25519_scalarmult(scalar, u):
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k = int.from_bytes(k, "little")
    x1 = int.from_bytes(u, "little") % (1 << 255) % _P25519

    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        x2, x3 = _cswap(swap, x2, x3)
        z2, z3 = _cswap(swap, z2, z3)
        swap = kt

        a = (x2 + z2) % _P25519
        aa = (a * a) % _P25519
        b = (x2 - z2) % _P25519
        bb = (b * b) % _P25519
        e = (aa - bb) % _P25519
        c = (x3 + z3) % _P25519
        d = (x3 - z3) % _P25519
        da = (d * a) % _P25519
        cb = (c * b) % _P25519
        x3 = pow((da + cb) % _P25519, 2, _P25519)
        z3 = (x1 * pow((da - cb) % _P25519, 2, _P25519)) % _P25519
        x2 = (aa * bb) % _P25519
        z2 = (e * ((aa + _A24 * e) % _P25519)) % _P25519

    x2, x3 = _cswap(swap, x2, x3)
    z2, z3 = _cswap(swap, z2, z3)
    return ((x2 * pow(z2, _P25519 - 2, _P25519)) % _P25519).to_bytes(32, "little")


_BASE_POINT = (9).to_bytes(32, "little")


def x25519_keypair():
    private = os.urandom(32)
    return private, _x25519_scalarmult(private, _BASE_POINT)


def x25519_shared(private, peer_public):
    shared = _x25519_scalarmult(private, peer_public)
    if shared == b"\x00" * 32:
        # All-zero output means the peer sent a low-order point, which would
        # pin the shared secret to a value they already know.
        raise AuthenticationError("peer offered a degenerate public key")
    return shared


# --------------------------------------------------------------------------
# Key derivation
# --------------------------------------------------------------------------

def derive_cluster_key(passphrase, cluster_name):
    """Stretch the human-typed passphrase once, at startup (~100 ms)."""
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=b"mousebridge/cluster/" + cluster_name.encode("utf-8"),
        n=1 << 14, r=8, p=1, dklen=32,
    )


def hkdf(secret, salt, info, length=32):
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]
