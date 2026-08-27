"""The daemon: peers, focus, and the rules for who is driving.

Exactly one machine in the cluster holds *focus* -- the cursor is on its
screen -- and at most one machine is *controlling* -- its physical mouse and
keyboard are being captured and forwarded. Usually those are different
machines, which is the whole point.

    focus == me, not controlling     idle; my own input is my own
    focus != me, controlling         I am the keyboard; everything goes to focus
    focus == me, someone controlling I am being typed into

Focus moves when the controlling node's virtual cursor runs off an edge, when
a hotkey says so, or when someone picks up the mouse attached to whichever
machine currently has the cursor. That last case is what makes the cluster feel
symmetrical rather than master/slave.
"""

import hashlib
import socket
import struct
import sys
import threading
import time

from . import config as config_module
from . import crypto, keymap, wire
from .layout import Cluster

RECLAIM_IDLE_SECONDS = 0.3      # quiet period before local motion counts as "the user grabbed this mouse"
RECLAIM_DISTANCE = 20           # px the cursor must jump for the same
EDGE_POLL_HZ = 120


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


class Peer:
    def __init__(self, name, address):
        self.name = name
        self.address = address
        self.channel = None
        self.send_lock = threading.Lock()
        self.screen = None
        self.connected_at = None

    @property
    def alive(self):
        return self.channel is not None

    def send(self, msg_type, payload):
        channel = self.channel
        if channel is None:
            return False
        try:
            with self.send_lock:
                channel.send(msg_type, payload)
            return True
        except (OSError, ValueError):
            return False


class Node:
    def __init__(self, cfg, secret, backend=None):
        self.cfg = cfg
        self.name = cfg["node"]
        self.cluster_key = crypto.derive_cluster_key(secret, cfg["cluster"])

        from . import backend as backend_module
        self.backend = backend or backend_module.create(cfg["clipboard"])

        width, height = self.backend.screen_size()
        self.cluster = Cluster(
            cfg["layout"], {self.name: (width, height)},
            corner_size=cfg["edge"]["corner_size"],
        )

        self.peers = {
            name: Peer(name, config_module.peer_address(cfg, name))
            for name in cfg["peers"]
        }

        self.lock = threading.RLock()
        self.focus = self.name
        self.controlling = False
        self.controlled_by = None
        self.locked = False
        # An edge only counts once the cursor has been seen *away* from it.
        # Without this the cursor lands on the neighbour's edge, that machine
        # sees "cursor is at an edge" and bounces it straight back, forever.
        self._edge_armed = False
        self.virtual = (width // 2, height // 2)
        self.running = False

        self._last_injection = 0.0
        self._expected_cursor = None
        self._clipboard_seq = 0
        self._clipboard_digest = None
        self._pending_offers = {}
        self._switch_armed_at = None
        self._threads = []

    # ---------------------------------------------------------------- run
    def run(self):
        self.running = True
        hotkeys = {}
        for name, spec in self.cfg["hotkeys"].items():
            if spec:
                hotkeys[name] = keymap.parse_hotkey(spec)
        self.backend.register_hotkeys(hotkeys)
        self.backend.set_clipboard_listener(self._on_local_clipboard)
        self.backend.start(self._on_input)

        self._spawn(self._listen_loop, "listener")
        for peer in self.peers.values():
            # Only the lexicographically smaller name dials, so a pair of nodes
            # ends up with exactly one connection rather than racing to open two.
            if self.name < peer.name:
                self._spawn(self._dial_loop, f"dial-{peer.name}", peer)
        self._spawn(self._edge_loop, "edge")

        width, height = self.backend.screen_size()
        log(f"{self.name} up: {width}x{height}, cluster {self.cfg['cluster']!r}")
        log(f"layout: {self.cluster.describe(self.name)}")
        if not self.peers:
            log("no peers configured -- add some to `peers` and `layout`")

        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        self.running = False
        with self.lock:
            if self.controlling:
                self._release_control()
        for peer in self.peers.values():
            if peer.channel:
                peer.channel.close()
        self.backend.stop()
        log("stopped")

    def _spawn(self, target, name, *args):
        thread = threading.Thread(target=self._guard(target), name=name, args=args, daemon=True)
        thread.start()
        self._threads.append(thread)
        return thread

    def _guard(self, function):
        def wrapper(*args):
            try:
                function(*args)
            except Exception as exc:
                if self.running:
                    log(f"{threading.current_thread().name} failed: {exc!r}")
        return wrapper

    # ------------------------------------------------------------ transport
    def _listen_loop(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.cfg["bind"], self.cfg["port"]))
        server.listen(8)
        while self.running:
            try:
                client, address = server.accept()
            except OSError:
                break
            self._spawn(self._accept, f"accept-{address[0]}", client, address)

    def _accept(self, client, address):
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client.settimeout(10)
        try:
            channel, peer_name = wire.handshake(
                client, self.cluster_key, self.name, initiator=False)
        except (crypto.AuthenticationError, OSError) as exc:
            log(f"rejected connection from {address[0]}: {exc}")
            client.close()
            return
        client.settimeout(None)
        peer = self.peers.get(peer_name)
        if peer is None:
            log(f"rejected {peer_name!r} from {address[0]}: not in this node's peer list")
            channel.close()
            return
        if peer.alive:
            log(f"{peer_name} is already connected; dropping the duplicate")
            channel.close()
            return
        self._attach(peer, channel, f"from {address[0]}")

    def _dial_loop(self, peer):
        delay = self.cfg["reconnect_seconds"]
        while self.running:
            if peer.alive:
                time.sleep(delay)
                continue
            try:
                sock = socket.create_connection(peer.address, timeout=5)
            except OSError:
                time.sleep(delay)
                continue
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(10)
            try:
                channel, peer_name = wire.handshake(
                    sock, self.cluster_key, self.name, initiator=True)
            except (crypto.AuthenticationError, OSError) as exc:
                log(f"handshake with {peer.name} failed: {exc}")
                sock.close()
                time.sleep(delay)
                continue
            sock.settimeout(None)
            if peer_name != peer.name:
                log(f"{peer.address[0]} calls itself {peer_name!r}, expected {peer.name!r}")
                channel.close()
                time.sleep(delay)
                continue
            self._attach(peer, channel, f"to {peer.address[0]}")
            time.sleep(delay)

    def _attach(self, peer, channel, how):
        peer.channel = channel
        peer.connected_at = time.time()
        log(f"connected {how}: {peer.name}")
        width, height = self.backend.screen_size()
        peer.send(wire.MSG_INFO, {"screen": [width, height], "focus": self.focus})
        self._spawn(self._reader, f"read-{peer.name}", peer)

    def _reader(self, peer):
        channel = peer.channel
        try:
            while self.running and peer.channel is channel:
                message = channel.recv()
                if message is None:
                    break
                self._on_message(peer, *message)
        except crypto.AuthenticationError as exc:
            log(f"{peer.name}: dropping connection, {exc}")
        except (OSError, ValueError, struct.error) as exc:
            if self.running:
                log(f"{peer.name}: {exc}")
        finally:
            channel.close()
            if peer.channel is channel:
                peer.channel = None
                log(f"disconnected: {peer.name}")
                with self.lock:
                    # If the machine holding the cursor just vanished, take it
                    # back rather than leaving the user typing into nothing.
                    if self.focus == peer.name:
                        self._take_focus_locally()

    def _send_to(self, name, msg_type, payload):
        peer = self.peers.get(name)
        if peer is None or not peer.send(msg_type, payload):
            return False
        return True

    def _broadcast(self, msg_type, payload, skip=None):
        for peer in self.peers.values():
            if peer.name != skip and peer.alive:
                peer.send(msg_type, payload)

    # ------------------------------------------------------- inbound messages
    def _on_message(self, peer, msg_type, payload):
        if msg_type == wire.MSG_INFO:
            screen = payload.get("screen")
            if screen:
                self.cluster.set_screen(peer.name, *screen)
                log(f"{peer.name}: {screen[0]}x{screen[1]}")
            return

        if msg_type == wire.MSG_MOTION:
            self._injected()
            self.backend.inject_motion(*payload)
        elif msg_type == wire.MSG_BUTTON:
            self._injected()
            self.backend.inject_button(payload[0], bool(payload[1]))
        elif msg_type == wire.MSG_SCROLL:
            self._injected()
            self.backend.inject_scroll(*payload)
        elif msg_type == wire.MSG_KEY:
            self._injected()
            self.backend.inject_key(payload[0], bool(payload[1]))
        elif msg_type == wire.MSG_ENTER:
            self._on_enter(peer, *payload)
        elif msg_type == wire.MSG_LEAVE:
            with self.lock:
                if self.controlled_by == peer.name:
                    self.controlled_by = None
            self.backend.release_all()
        elif msg_type == wire.MSG_FOCUS:
            with self.lock:
                self.focus = payload["focus"]
                if self.controlling and payload["focus"] != self.name and \
                        payload.get("by") != self.name:
                    # Someone else took over. Stop capturing so two machines
                    # are never both forwarding input at once.
                    self._release_control()
        elif msg_type == wire.MSG_LOCK:
            self.locked = bool(payload.get("locked"))
        elif msg_type == wire.MSG_CLIPBOARD_OFFER:
            self._on_clipboard_offer(peer, payload)
        elif msg_type == wire.MSG_CLIPBOARD_REQUEST:
            self._on_clipboard_request(peer, payload)
        elif msg_type == wire.MSG_CLIPBOARD_DATA:
            self._on_clipboard_data(peer, payload)
        elif msg_type == wire.MSG_PING:
            peer.send(wire.MSG_PONG, payload)

    def _on_enter(self, peer, x, y, mods):
        with self.lock:
            self.focus = self.name
            self.controlled_by = peer.name
            self._edge_armed = False
        self.backend.release_all()
        self.backend.inject_motion(x, y)
        self._injected()
        self._expected_cursor = (x, y)
        # Re-assert modifiers that were already held when the cursor crossed,
        # so shift-dragging across the boundary keeps shifting.
        for mask, key in keymap.MOD_REPRESENTATIVE:
            if mods & mask:
                self.backend.inject_key(key, True)
        log(f"cursor arrived from {peer.name} at ({x}, {y})")

    def _injected(self):
        self._last_injection = time.monotonic()

    # -------------------------------------------------------- local input
    def _on_input(self, kind, *args):
        if kind == "hotkey":
            self._on_hotkey(args[0])
            return
        if kind == "capture_failed":
            log(f"capture failed: {args[0]}")
            with self.lock:
                self._take_focus_locally()
            return
        if not self.controlling:
            return

        target = self.focus
        if kind == "motion":
            self._on_motion(*args)
        elif kind == "button":
            self._send_to(target, wire.MSG_BUTTON, (args[0], int(args[1])))
        elif kind == "scroll":
            self._send_to(target, wire.MSG_SCROLL, args)
        elif kind == "key":
            self._send_to(target, wire.MSG_KEY, (args[0], int(args[1])))

    def _on_motion(self, dx, dy):
        with self.lock:
            x, y = self.virtual[0] + dx, self.virtual[1] + dy
            node, x, y, crossed = self.cluster.step(self.focus, x, y)
            self.virtual = (x, y)
            if crossed:
                self._move_focus(node, x, y)
                return
            target = self.focus
        self._send_to(target, wire.MSG_MOTION, (x, y))

    def _on_hotkey(self, name):
        with self.lock:
            if name == "reclaim":
                if self.focus != self.name:
                    log("reclaiming the cursor")
                    self._take_focus_locally()
                return
            direction = name.replace("switch_", "")
            if direction not in config_module.DIRECTIONS:
                return
            x, y = self.virtual
            node, x, y, moved = self.cluster.jump(self.focus, direction, x, y)
            if moved:
                self.virtual = (x, y)
                self._move_focus(node, x, y)

    # ------------------------------------------------------- focus changes
    def _move_focus(self, node, x, y):
        """Caller holds self.lock. Hand the cursor to `node` at (x, y)."""
        previous = self.focus
        if node == previous:
            return
        if previous != self.name:
            self._send_to(previous, wire.MSG_LEAVE, ())
        self.focus = node
        self.virtual = (x, y)

        if node == self.name:
            self._release_control()
            self.controlled_by = None
            self._edge_armed = False
            self.backend.warp_cursor(x, y)
            log(f"cursor came home to ({x}, {y})")
        else:
            if not self.controlling:
                self._take_control()
            mods = self.backend.modifier_state()
            self._send_to(node, wire.MSG_ENTER, (x, y, mods))
            log(f"cursor moved to {node} at ({x}, {y})")
        self._broadcast(wire.MSG_FOCUS, {"focus": node, "by": self.name})

    def _take_control(self):
        self.controlling = True
        self.backend.set_capturing(True)

    def _release_control(self):
        if self.controlling:
            self.controlling = False
            self.backend.set_capturing(False)

    def _take_focus_locally(self):
        """Caller holds self.lock. Bring the cursor back to this machine."""
        if self.focus != self.name:
            self._send_to(self.focus, wire.MSG_LEAVE, ())
        if self.controlled_by is not None:
            self._send_to(self.controlled_by, wire.MSG_LEAVE, ())
            self.controlled_by = None
        self._release_control()
        self.focus = self.name
        self._edge_armed = False
        width, height = self.backend.screen_size()
        x = min(max(self.virtual[0], 0), width - 1)
        y = min(max(self.virtual[1], 0), height - 1)
        self.virtual = (x, y)
        self.backend.warp_cursor(x, y)
        self._broadcast(wire.MSG_FOCUS, {"focus": self.name, "by": self.name})

    # ---------------------------------------------------------- edge polling
    def _edge_loop(self):
        """Watch this machine's own cursor while it is not captured.

        Two jobs: notice the cursor arriving at an edge that leads somewhere,
        and notice the user physically grabbing this machine's mouse while
        another machine is driving.
        """
        interval = 1.0 / EDGE_POLL_HZ
        while self.running:
            time.sleep(interval)
            if self.locked:
                continue
            with self.lock:
                controlling, controlled = self.controlling, self.controlled_by
                focus = self.focus
            if controlling:
                # Our own cursor is parked and hidden; the virtual one in
                # _on_motion is the one that matters.
                continue

            try:
                x, y = self.backend.cursor_position()
            except Exception:
                continue

            if controlled is not None or focus != self.name:
                # Somebody else is driving. The only thing worth watching for
                # is the user physically grabbing this machine's own mouse.
                self._maybe_reclaim(x, y)
                continue

            with self.lock:
                if self.controlling or self.controlled_by is not None:
                    continue
                direction = self.cluster.at_edge(self.name, x, y)
                if direction is None:
                    self._switch_armed_at = None
                    self._edge_armed = True
                    self.virtual = (x, y)
                    continue
                if not self._edge_armed:
                    continue
                delay = self.cfg["edge"]["switch_delay_ms"] / 1000.0
                if delay > 0:
                    now = time.monotonic()
                    if self._switch_armed_at is None:
                        self._switch_armed_at = now
                        continue
                    if now - self._switch_armed_at < delay:
                        continue
                self._switch_armed_at = None
                self.virtual = (x, y)
                node, nx, ny = self.cluster.cross(self.name, direction, x, y)
                self._move_focus(node, nx, ny)

    def _maybe_reclaim(self, x, y):
        """Another machine is driving, but this machine's own mouse just moved,
        so the user physically picked it up. Take the cursor back.

        The test has to distinguish a real hand on a real mouse from the
        injected motion we were asked to perform, hence both a quiet period
        since the last injection and a minimum distance.
        """
        if self._expected_cursor is None:
            self._expected_cursor = (x, y)
            return
        if time.monotonic() - self._last_injection < RECLAIM_IDLE_SECONDS:
            self._expected_cursor = (x, y)
            return
        if abs(x - self._expected_cursor[0]) + abs(y - self._expected_cursor[1]) \
                < RECLAIM_DISTANCE:
            return
        self._expected_cursor = (x, y)
        with self.lock:
            if self.focus == self.name and self.controlled_by is None:
                return
            log("local mouse moved; taking the cursor back")
            self.virtual = (x, y)
            self._take_focus_locally()

    # ------------------------------------------------------------ clipboard
    def _on_local_clipboard(self, mime, data):
        if not self.cfg["clipboard"]["enabled"] or not data:
            return
        digest = hashlib.sha256(data).digest()
        if digest == self._clipboard_digest:
            return              # this is the copy we just pasted in from a peer
        self._clipboard_digest = digest
        self._clipboard_seq += 1
        self._local_clipboard = (mime, data)
        log(f"clipboard: {len(data)} bytes of {mime}, offering to peers")
        self._broadcast(wire.MSG_CLIPBOARD_OFFER, {
            "seq": self._clipboard_seq, "mime": mime, "size": len(data),
        })

    _local_clipboard = None

    def _on_clipboard_offer(self, peer, payload):
        if not self.cfg["clipboard"]["enabled"]:
            return
        limits = self.cfg["clipboard"]
        if payload["size"] > limits["max_bytes"]:
            log(f"clipboard from {peer.name} is {payload['size']} bytes; over the limit")
            return
        if payload["mime"].startswith("image/") and not limits["images"]:
            return
        if payload["mime"].startswith("text/") and not limits["text"]:
            return
        peer.send(wire.MSG_CLIPBOARD_REQUEST, {"seq": payload["seq"]})

    def _on_clipboard_request(self, peer, payload):
        held = self._local_clipboard
        if held is None or payload.get("seq") != self._clipboard_seq:
            return
        mime, data = held
        peer.send(wire.MSG_CLIPBOARD_DATA,
                  {"seq": self._clipboard_seq, "mime": mime, "data": data})

    def _on_clipboard_data(self, peer, payload):
        data, mime = payload["data"], payload["mime"]
        if len(data) > self.cfg["clipboard"]["max_bytes"]:
            return
        # Remember what we are about to write, so our own clipboard listener
        # does not see it change and offer it straight back.
        self._clipboard_digest = hashlib.sha256(data).digest()
        self._local_clipboard = (mime, data)
        self.backend.clipboard_write(mime, data)
        log(f"clipboard: {len(data)} bytes of {mime} from {peer.name}")

    # --------------------------------------------------------------- status
    def status(self):
        return {
            "node": self.name,
            "focus": self.focus,
            "controlling": self.controlling,
            "screen": list(self.backend.screen_size()),
            "peers": {
                name: {
                    "address": f"{peer.address[0]}:{peer.address[1]}",
                    "connected": peer.alive,
                    "screen": list(self.cluster.screens.get(name, ())) or None,
                }
                for name, peer in self.peers.items()
            },
        }
