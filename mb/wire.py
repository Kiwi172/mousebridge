"""Framing and message codec.

Two shapes of message share one channel:

  * Input events are hot. They are fixed-layout `struct` records of 5-13 bytes,
    because they are sent up to a few hundred times a second and every
    microsecond between your hand and the other screen is felt.
  * Control messages (hello, clipboard, focus hand-off) are rare and are JSON,
    because being able to read a packet dump matters more than 40 bytes.

Every frame is sealed with ChaCha20-Poly1305 under a per-direction key and a
per-direction counter. The counter is the nonce, and the receiver requires it
to strictly increase, so a replayed or reordered frame is dropped rather than
being re-injected as a phantom click.
"""

import json
import socket
import struct

from . import crypto

PROTOCOL_VERSION = 1
MAX_FRAME = 8 << 20  # a clipboard image can be large; anything past this is a lie

# Hot path -- input events.
MSG_MOTION = 0x01     # absolute position on the receiving node's screen
MSG_BUTTON = 0x02
MSG_SCROLL = 0x03
MSG_KEY = 0x04
MSG_ENTER = 0x05      # cursor arrives; carries held modifiers so they survive the hop
MSG_LEAVE = 0x06      # cursor departs; receiver releases everything still held

# Control path -- JSON.
MSG_HELLO = 0x30
MSG_INFO = 0x31
MSG_FOCUS = 0x32
MSG_LOCK = 0x33
MSG_CLIPBOARD_OFFER = 0x40
MSG_CLIPBOARD_REQUEST = 0x41
MSG_CLIPBOARD_DATA = 0x42
MSG_PING = 0x50
MSG_PONG = 0x51

_HOT = {MSG_MOTION, MSG_BUTTON, MSG_SCROLL, MSG_KEY, MSG_ENTER, MSG_LEAVE}

_MOTION = struct.Struct("!ii")
_BUTTON = struct.Struct("!HB")
_SCROLL = struct.Struct("!hh")
_KEY = struct.Struct("!HB")
_ENTER = struct.Struct("!iiI")


def encode(msg_type, payload):
    """Turn a message into bytes. `payload` is a tuple for hot types, a dict otherwise."""
    if msg_type == MSG_MOTION:
        return bytes([msg_type]) + _MOTION.pack(*payload)
    if msg_type == MSG_BUTTON:
        return bytes([msg_type]) + _BUTTON.pack(*payload)
    if msg_type == MSG_SCROLL:
        return bytes([msg_type]) + _SCROLL.pack(*payload)
    if msg_type == MSG_KEY:
        return bytes([msg_type]) + _KEY.pack(*payload)
    if msg_type == MSG_ENTER:
        return bytes([msg_type]) + _ENTER.pack(*payload)
    if msg_type == MSG_LEAVE:
        return bytes([msg_type])
    if msg_type == MSG_CLIPBOARD_DATA:
        # Kept out of JSON so clipboard bytes are never base64-inflated.
        mime = payload["mime"].encode("utf-8")
        head = json.dumps({"seq": payload["seq"], "mime": payload["mime"]}).encode("utf-8")
        return bytes([msg_type]) + struct.pack("!H", len(head)) + head + payload["data"]
    return bytes([msg_type]) + json.dumps(payload).encode("utf-8")


def decode(raw):
    """Inverse of encode. Returns (msg_type, payload)."""
    if not raw:
        raise ValueError("empty message")
    msg_type, body = raw[0], raw[1:]
    if msg_type == MSG_MOTION:
        return msg_type, _MOTION.unpack(body)
    if msg_type == MSG_BUTTON:
        return msg_type, _BUTTON.unpack(body)
    if msg_type == MSG_SCROLL:
        return msg_type, _SCROLL.unpack(body)
    if msg_type == MSG_KEY:
        return msg_type, _KEY.unpack(body)
    if msg_type == MSG_ENTER:
        return msg_type, _ENTER.unpack(body)
    if msg_type == MSG_LEAVE:
        return msg_type, ()
    if msg_type == MSG_CLIPBOARD_DATA:
        (head_len,) = struct.unpack("!H", body[:2])
        head = json.loads(body[2:2 + head_len].decode("utf-8"))
        head["data"] = body[2 + head_len:]
        return msg_type, head
    return msg_type, json.loads(body.decode("utf-8"))


def is_input(msg_type):
    return msg_type in _HOT


class Channel:
    """An encrypted, framed message channel over a connected TCP socket.

    Not safe for concurrent senders; `Peer` serialises sends behind a lock.
    """

    def __init__(self, sock, send_key, recv_key):
        self.sock = sock
        self._send_key = send_key
        self._recv_key = recv_key
        self._send_counter = 0
        self._recv_counter = 0
        self._buf = bytearray()

    @staticmethod
    def _nonce(counter):
        return b"\x00\x00\x00\x00" + counter.to_bytes(8, "big")

    def send(self, msg_type, payload):
        frame = crypto.aead_encrypt(
            self._send_key, self._nonce(self._send_counter), encode(msg_type, payload)
        )
        self._send_counter += 1
        self.sock.sendall(struct.pack("!I", len(frame)) + frame)

    def recv(self):
        """Block for the next message. Returns (msg_type, payload), or None on clean EOF."""
        header = self._read_exactly(4)
        if header is None:
            return None
        (length,) = struct.unpack("!I", header)
        if length > MAX_FRAME or length < 16:
            raise crypto.AuthenticationError(f"implausible frame length {length}")
        frame = self._read_exactly(length)
        if frame is None:
            return None
        plain = crypto.aead_decrypt(self._recv_key, self._nonce(self._recv_counter), frame)
        self._recv_counter += 1
        return decode(plain)

    def _read_exactly(self, n):
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except (ConnectionResetError, OSError):
                return None
            if not chunk:
                return None
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------

def _read_blob(sock, n):
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise crypto.AuthenticationError("peer hung up during handshake")
        out += chunk
    return out


def handshake(sock, cluster_key, node_id, initiator):
    """Authenticated X25519 exchange, with the pre-shared cluster key as the
    authenticator. Returns (Channel, peer_node_id).

    Wire order is fixed by role, so both sides agree without extra round trips:
    the initiator speaks first. Each side sends its ephemeral public key and
    node id, then a confirmation MAC over the full transcript. A peer without
    the cluster key cannot produce the MAC, which is what stops a machine on
    your coffee-shop LAN from becoming the thing typing into your laptop.
    """
    private, public = crypto.x25519_keypair()
    me = node_id.encode("utf-8")[:255]
    mine = bytes([PROTOCOL_VERSION, len(me)]) + me + public

    if initiator:
        sock.sendall(mine)
        theirs_head = _read_blob(sock, 2)
    else:
        theirs_head = _read_blob(sock, 2)
        sock.sendall(mine)

    version, id_len = theirs_head[0], theirs_head[1]
    if version != PROTOCOL_VERSION:
        raise crypto.AuthenticationError(
            f"peer speaks protocol v{version}, this node speaks v{PROTOCOL_VERSION}"
        )
    rest = _read_blob(sock, id_len + 32)
    peer_id = rest[:id_len].decode("utf-8", "replace")
    peer_public = rest[id_len:]
    theirs = theirs_head + rest

    shared = crypto.x25519_shared(private, peer_public)
    transcript = (mine + theirs) if initiator else (theirs + mine)
    master = crypto.hkdf(shared, cluster_key, b"mousebridge/v1" + transcript, 32)

    init_key = crypto.hkdf(master, b"mousebridge/dir", b"initiator->acceptor")
    acc_key = crypto.hkdf(master, b"mousebridge/dir", b"acceptor->initiator")
    init_mac = crypto.hkdf(master, b"mousebridge/confirm", b"initiator", 32)
    acc_mac = crypto.hkdf(master, b"mousebridge/confirm", b"acceptor", 32)

    if initiator:
        sock.sendall(init_mac)
        if not _constant_eq(_read_blob(sock, 32), acc_mac):
            raise crypto.AuthenticationError("peer failed to prove the shared secret")
        return Channel(sock, init_key, acc_key), peer_id

    got = _read_blob(sock, 32)
    if not _constant_eq(got, init_mac):
        raise crypto.AuthenticationError("peer failed to prove the shared secret")
    sock.sendall(acc_mac)
    return Channel(sock, acc_key, init_key), peer_id


def _constant_eq(a, b):
    import hmac
    return hmac.compare_digest(a, b)
