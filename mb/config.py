"""Configuration: one file you can copy verbatim to every machine.

The layout is written once and is identical on all nodes -- only the `node`
field differs, and even that defaults to the hostname. That means "set up the
second machine" is `scp` plus editing nothing, which is the part of Mouse
Without Borders that people actually like.

The shared secret is deliberately *not* in the config file. It lives beside it
in a 0600 file, so you can paste a config into a chat window or commit it to
your dotfiles without leaking the key to your own keyboard.
"""

import json
import os
import secrets
import socket
import sys

DEFAULT_PORT = 24800

DIRECTIONS = ("left", "right", "up", "down")
OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}


def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "mousebridge")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mousebridge")


def config_path():
    return os.path.join(config_dir(), "config.json")


def secret_path():
    return os.path.join(config_dir(), "secret")


DEFAULTS = {
    "cluster": "default",
    "node": None,                    # None -> hostname
    "bind": "0.0.0.0",
    "port": DEFAULT_PORT,
    "peers": {},                     # node name -> "host" or "host:port"
    "layout": {},                    # node name -> {direction: neighbour node}
    "clipboard": {
        "enabled": True,
        "max_bytes": 4 << 20,
        "text": True,
        "images": True,
    },
    "hotkeys": {
        "switch_left": "ctrl+alt+left",
        "switch_right": "ctrl+alt+right",
        "switch_up": "ctrl+alt+up",
        "switch_down": "ctrl+alt+down",
        "reclaim": "ctrl+alt+home",  # yank the cursor back to this machine
    },
    "edge": {
        "corner_size": 40,           # px at each corner that will not switch screens
        "switch_delay_ms": 0,        # dwell time at the edge before crossing
        "wrap": False,               # can the cursor run off the far end and come back?
    },
    "reconnect_seconds": 3,
}


class ConfigError(Exception):
    pass


def _merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path=None):
    path = path or config_path()
    if not os.path.exists(path):
        raise ConfigError(
            f"no config at {path}\n"
            f"run `mousebridge init` to create one"
        )
    with open(path, "r", encoding="utf-8") as handle:
        try:
            raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    cfg = _merge(DEFAULTS, raw)
    if not cfg["node"]:
        cfg["node"] = socket.gethostname().split(".")[0]
    validate(cfg)
    return cfg


def validate(cfg):
    node = cfg["node"]
    layout = cfg["layout"]
    peers = cfg["peers"]

    if layout and node not in layout:
        raise ConfigError(
            f"this node is called {node!r} but the layout only describes "
            f"{sorted(layout)}.\nEither rename the node or add it to the layout."
        )
    for owner, edges in layout.items():
        for direction, neighbour in edges.items():
            if direction not in DIRECTIONS:
                raise ConfigError(f"layout.{owner}: {direction!r} is not one of {DIRECTIONS}")
            if neighbour == owner:
                raise ConfigError(f"layout.{owner}.{direction} points at itself")
            if neighbour not in layout:
                raise ConfigError(
                    f"layout.{owner}.{direction} points at {neighbour!r}, "
                    f"which has no layout entry of its own"
                )
            back = layout[neighbour].get(OPPOSITE[direction])
            if back != owner:
                raise ConfigError(
                    f"layout is asymmetric: {owner}.{direction} = {neighbour}, but "
                    f"{neighbour}.{OPPOSITE[direction]} = {back!r}.\n"
                    f"The cursor must be able to come back the way it went."
                )
    for name in layout:
        if name != node and name not in peers:
            raise ConfigError(
                f"layout mentions {name!r} but peers has no address for it.\n"
                f'Add  "peers": {{"{name}": "192.168.x.y"}}'
            )
    for spec in cfg["hotkeys"].values():
        if spec:
            from . import keymap
            keymap.parse_hotkey(spec)   # raises ValueError on nonsense
    return cfg


def peer_address(cfg, name):
    spec = cfg["peers"][name]
    if ":" in spec and not spec.startswith("["):
        host, _, port = spec.rpartition(":")
        return host, int(port)
    return spec, cfg["port"]


# --------------------------------------------------------------------------
# Secret handling
# --------------------------------------------------------------------------

def load_secret():
    path = secret_path()
    env = os.environ.get("MOUSEBRIDGE_SECRET")
    if env:
        return env
    if not os.path.exists(path):
        raise ConfigError(
            f"no shared secret at {path}\n"
            f"run `mousebridge init` here, then `mousebridge pair` to print the "
            f"secret to type into the other machine"
        )
    if sys.platform != "win32":
        mode = os.stat(path).st_mode & 0o777
        if mode & 0o077:
            raise ConfigError(
                f"{path} is mode {mode:o}; other users on this machine can read the "
                f"key to your keyboard.\nFix it with: chmod 600 {path}"
            )
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def save_secret(secret):
    os.makedirs(config_dir(), exist_ok=True)
    path = secret_path()
    # Create with restrictive permissions from the outset rather than
    # chmod-ing after the fact, which would leave a readable window.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret + "\n")
    return path


def generate_secret():
    """Six short words worth of entropy (~77 bits), easy to read aloud."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)
    )


def save(cfg, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2, sort_keys=False)
        handle.write("\n")
    return path
