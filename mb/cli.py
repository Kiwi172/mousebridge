"""Command line."""

import argparse
import json
import os
import socket
import sys

from . import config as config_module
from . import keymap
from .config import ConfigError

USAGE = """mousebridge -- one keyboard and mouse across Linux and Windows

  mousebridge init --peer NAME=ADDRESS --side {left,right,up,down}
                                 set this machine up and describe the next one
  mousebridge pair               print what to run on the other machine
  mousebridge run                start the daemon
  mousebridge status             show config, peers, and whether they answer
  mousebridge doctor             check this machine can capture and inject
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mousebridge", add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter, description=USAGE)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--config", help="path to config.json")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", add_help=True)
    p_init.add_argument("--name", help="name for this machine (default: hostname)")
    p_init.add_argument("--cluster", default="default")
    p_init.add_argument("--peer", action="append", default=[],
                        metavar="NAME=ADDRESS", help="another machine, repeatable")
    p_init.add_argument("--side", action="append", default=[],
                        choices=list(config_module.DIRECTIONS),
                        help="which side of this machine each --peer sits on")
    p_init.add_argument("--port", type=int, default=config_module.DEFAULT_PORT)
    p_init.add_argument("--force", action="store_true", help="overwrite an existing config")

    sub.add_parser("pair", add_help=True)
    sub.add_parser("run", add_help=True)
    sub.add_parser("status", add_help=True)
    sub.add_parser("doctor", add_help=True)

    args = parser.parse_args(argv)
    if args.help or not args.command:
        print(USAGE)
        return 0

    try:
        return {
            "init": cmd_init, "pair": cmd_pair, "run": cmd_run,
            "status": cmd_status, "doctor": cmd_doctor,
        }[args.command](args)
    except ConfigError as exc:
        print(f"mousebridge: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


# --------------------------------------------------------------------- init
def cmd_init(args):
    path = args.config or config_module.config_path()
    if os.path.exists(path) and not args.force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 2

    if len(args.side) not in (0, len(args.peer)):
        print("give one --side for each --peer", file=sys.stderr)
        return 2
    sides = args.side or ["right"] * len(args.peer)

    name = args.name or socket.gethostname().split(".")[0]
    cfg = json.loads(json.dumps(config_module.DEFAULTS))
    cfg["cluster"] = args.cluster
    cfg["node"] = name
    cfg["port"] = args.port
    cfg["layout"] = {name: {}}

    for spec, side in zip(args.peer, sides):
        if "=" not in spec:
            print(f"--peer wants NAME=ADDRESS, got {spec!r}", file=sys.stderr)
            return 2
        peer_name, address = spec.split("=", 1)
        cfg["peers"][peer_name] = address
        cfg["layout"][name][side] = peer_name
        cfg["layout"].setdefault(peer_name, {})[config_module.OPPOSITE[side]] = name

    config_module.validate(cfg)
    config_module.save(cfg, path)

    secret_file = config_module.secret_path()
    if os.path.exists(secret_file):
        secret = config_module.load_secret()
        print(f"kept the existing shared secret in {secret_file}")
    else:
        secret = config_module.generate_secret()
        config_module.save_secret(secret)
        print(f"wrote a new shared secret to {secret_file} (mode 600)")

    print(f"wrote {path}")
    print()
    print(f"this machine is {name!r}", end="")
    if args.peer:
        print(", with " + ", ".join(
            f"{p.split('=')[0]} on the {s}" for p, s in zip(args.peer, sides)))
    else:
        print(" and has no peers yet -- add them to `peers` and `layout`")
    print()
    print("next: run `mousebridge pair` and follow it on the other machine")
    return 0


# --------------------------------------------------------------------- pair
def cmd_pair(args):
    cfg = config_module.load(args.config)
    secret = config_module.load_secret()
    me = cfg["node"]
    others = [n for n in cfg["layout"] if n != me]

    if not others:
        print("No other machines in the layout yet -- add them with `mousebridge init`.")
        return 0

    for other in others:
        their_cfg = json.loads(json.dumps(cfg))
        their_cfg["node"] = other
        their_cfg["peers"] = {
            name: address for name, address in cfg["peers"].items() if name != other
        }
        their_cfg["peers"][me] = _my_address(cfg)
        blob = json.dumps(their_cfg, indent=2)

        print(f"=== on {other} ===")
        print()
        print("  Linux / macOS")
        print("  -------------")
        print("    mkdir -p ~/.config/mousebridge")
        # The heredoc body and its terminator must start at column zero, or
        # bash never sees the EOF and the user's shell hangs on a paste.
        print("    cat > ~/.config/mousebridge/config.json <<'EOF'")
        print(blob)
        print("EOF")
        print(f"    printf '%s\\n' '{secret}' > ~/.config/mousebridge/secret")
        print("    chmod 600 ~/.config/mousebridge/secret")
        print()
        print("  Windows (PowerShell)")
        print("  --------------------")
        print("    mkdir -Force $env:APPDATA\\mousebridge | Out-Null")
        # Same for PowerShell: a here-string's closing '@ must be at column zero.
        print("    $cfg = @'")
        print(blob)
        print("'@")
        print("    $cfg | Set-Content -Encoding utf8 "
              "$env:APPDATA\\mousebridge\\config.json")
        print(f"    Set-Content -Encoding utf8 $env:APPDATA\\mousebridge\\secret '{secret}'")
        print()
        print("  then, either way:  mousebridge run")
        print()

    print("The shared secret is what stops anything else on your network from")
    print("typing on these machines. Carry it over something you trust.")
    print()
    print("On Windows the secret file cannot be permission-checked the way it is")
    print("on Linux, so keep that account to yourself.")
    return 0





def _my_address(cfg):
    """Best guess at the address peers should dial back on."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))     # TEST-NET-1: routed nowhere, never sends
        address = probe.getsockname()[0]
        probe.close()
        return address
    except OSError:
        return socket.gethostbyname(socket.gethostname())


# ---------------------------------------------------------------------- run
def cmd_run(args):
    from .node import Node
    cfg = config_module.load(args.config)
    secret = config_module.load_secret()
    node = Node(cfg, secret)
    node.run()
    return 0


# ------------------------------------------------------------------- status
def cmd_status(args):
    cfg = config_module.load(args.config)
    print(f"config   {args.config or config_module.config_path()}")
    print(f"cluster  {cfg['cluster']}")
    print(f"node     {cfg['node']}")
    print(f"listen   {cfg['bind']}:{cfg['port']}")
    try:
        config_module.load_secret()
        print("secret   present")
    except ConfigError as exc:
        print(f"secret   MISSING -- {exc.args[0].splitlines()[0]}")

    print()
    print("layout")
    for name in sorted(cfg["layout"]):
        edges = cfg["layout"][name]
        arrows = ", ".join(f"{d} -> {edges[d]}" for d in config_module.DIRECTIONS if d in edges)
        marker = " (this machine)" if name == cfg["node"] else ""
        print(f"  {name}{marker}: {arrows or 'no neighbours'}")

    print()
    print("peers")
    for name in sorted(cfg["peers"]):
        host, port = config_module.peer_address(cfg, name)
        try:
            with socket.create_connection((host, port), timeout=1.5):
                state = "listening"
        except OSError as exc:
            state = f"unreachable ({exc.strerror or exc})"
        print(f"  {name:<16} {host}:{port:<6} {state}")

    print()
    print("hotkeys")
    for action, spec in cfg["hotkeys"].items():
        if spec:
            print(f"  {action:<14} {spec}")
    return 0


# ------------------------------------------------------------------- doctor
def cmd_doctor(args):
    problems = []
    print(f"platform     {sys.platform}")
    print(f"python       {sys.version.split()[0]}")

    if sys.platform.startswith("linux"):
        session = os.environ.get("XDG_SESSION_TYPE", "?")
        display = os.environ.get("DISPLAY")
        wayland = os.environ.get("WAYLAND_DISPLAY")
        print(f"session      {session}  DISPLAY={display or '-'}  WAYLAND_DISPLAY={wayland or '-'}")
        if wayland and not display:
            problems.append(
                "Pure Wayland session: global input capture and injection are "
                "blocked by design. Use an Xorg session.")
        elif wayland:
            print("             note: under Xwayland, capture only covers X11 clients")

    try:
        from . import backend as backend_module
        instance = backend_module.create({"max_bytes": 1 << 20})
    except Exception as exc:
        print(f"backend      FAILED -- {exc}")
        problems.append(str(exc))
        instance = None
    else:
        width, height = instance.screen_size()
        print(f"backend      {instance.name}")
        print(f"screen       {width} x {height}")
        print(f"cursor       {instance.cursor_position()}")
        if instance.name == "x11":
            style = "evdev (+8)" if instance._keycode_of is None else "keysym fallback"
            print(f"keycodes     {style}")
        try:
            instance.stop()
        except Exception:
            pass

    try:
        cfg = config_module.load(args.config)
        print(f"config       ok ({cfg['node']}, {len(cfg['peers'])} peer(s))")
    except ConfigError as exc:
        print(f"config       {exc.args[0].splitlines()[0]}")

    try:
        config_module.load_secret()
        print("secret       ok")
    except ConfigError as exc:
        print(f"secret       {exc.args[0].splitlines()[0]}")

    print()
    if problems:
        for problem in problems:
            print(f"problem: {problem}")
        return 1
    print("this machine can capture and inject input.")
    return 0
