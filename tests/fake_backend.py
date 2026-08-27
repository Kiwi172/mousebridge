"""A backend that pretends, so the node logic can be tested off-hardware."""

import threading

from mb.backend.base import Backend


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, width=1920, height=1080):
        self.width, self.height = width, height
        self.cursor = (width // 2, height // 2)
        self.capturing = False
        self.hotkeys = {}
        self.handler = lambda *a: None
        self.clipboard_listener = None
        self.clipboard = None
        self.mods = 0
        self.injected = []
        self.released = 0
        self.lock = threading.Lock()

    # geometry
    def screen_size(self):
        return self.width, self.height

    def cursor_position(self):
        return self.cursor

    def warp_cursor(self, x, y):
        self.cursor = (int(x), int(y))

    def modifier_state(self):
        return self.mods

    # lifecycle
    def start(self, handler):
        self.handler = handler

    def stop(self):
        pass

    def set_capturing(self, capturing):
        self.capturing = bool(capturing)

    def register_hotkeys(self, hotkeys):
        self.hotkeys = dict(hotkeys)

    # injection -- recorded, and the cursor follows motion like a real one
    def inject_motion(self, x, y):
        with self.lock:
            self.cursor = (int(x), int(y))
            self.injected.append(("motion", int(x), int(y)))

    def inject_button(self, code, pressed):
        with self.lock:
            self.injected.append(("button", code, bool(pressed)))

    def inject_scroll(self, dx, dy):
        with self.lock:
            self.injected.append(("scroll", dx, dy))

    def inject_key(self, code, pressed):
        with self.lock:
            self.injected.append(("key", code, bool(pressed)))

    def release_all(self):
        with self.lock:
            self.released += 1

    # clipboard
    def clipboard_read(self):
        return self.clipboard

    def clipboard_write(self, mime, data):
        self.clipboard = (mime, data)

    def set_clipboard_listener(self, callback):
        self.clipboard_listener = callback

    # test helpers
    def feed(self, kind, *args):
        self.handler(kind, *args)

    def local_copy(self, text):
        self.clipboard = ("text/plain", text.encode())
        if self.clipboard_listener:
            self.clipboard_listener(*self.clipboard)

    def count(self, kind=None):
        with self.lock:
            return len([e for e in self.injected if kind is None or e[0] == kind])

    def drain(self, kind=None):
        with self.lock:
            out = [e for e in self.injected if kind is None or e[0] == kind]
            self.injected = []
        return out
