# mousebridge

**One keyboard and mouse, two operating systems, and a clipboard that follows
the cursor.**

You have a Linux box and a Windows box on the same desk. Mouse Without Borders
solves this beautifully and runs on Windows only. `mousebridge` does the same
job across the boundary: push the pointer off the right-hand edge of the Linux
screen and it appears on the Windows one, your typing goes with it, and
whatever you copied on one machine pastes on the other.

```
        Linux  (1920x1080)                Windows  (2560x1440)
   ┌───────────────────────────┐     ┌─────────────────────────────────┐
   │                           │     │                                 │
   │            ·············· │ ──▶ │ ▸                               │
   │                           │     │                                 │
   │   capturing, cursor hidden│     │   XTEST / SendInput drives this │
   └───────────────────────────┘     └─────────────────────────────────┘
             desk                                 laptop
                    ChaCha20-Poly1305 over TCP/24800
```

---

## Install

**Python 3.8 or newer. Nothing else.** No pip wheels, no compiler, no service
to register. The X11 and Win32 bindings are hand-written `ctypes`, and the
cryptography is `hashlib` and a few hundred lines of arithmetic.

```bash
pip install -e .          # gives you a `mousebridge` command
```

Or with no install at all:

```bash
python3 -m mb doctor
```

On Linux you need X11 (`libX11`, `libXtst`, `libXfixes` — already present on
any desktop install). On Windows you need nothing.

---

## Set it up

On the machine you want to type from:

```bash
mousebridge init --peer laptop=192.168.1.42 --side right
```

That writes `~/.config/mousebridge/config.json`, generates a shared secret in
a `0600` file next to it, and records that `laptop` sits to the right.

Then:

```bash
mousebridge pair
```

which prints the exact config and secret to paste on the other machine. Run
`mousebridge run` on both. That is the whole setup.

```bash
mousebridge status      # who is configured, who is reachable
mousebridge doctor      # can this machine capture and inject at all?
```

---

## What works

| | |
|---|---|
| **Mouse** | Motion, all five buttons, vertical and horizontal wheel. Sub-pixel wheel deltas from trackpads accumulate rather than rounding to nothing. |
| **Keyboard** | Every key, by *physical position*. Modifiers held across the boundary stay held, so shift-dragging from one screen to the next works. |
| **Clipboard** | Text both ways, automatically, on copy. Images (PNG) between Linux machines. |
| **Switching** | Push the cursor off an edge, or `ctrl+alt+←/→/↑/↓`, or `ctrl+alt+home` to yank it back. |
| **Layout** | Up to four neighbours per machine, any number of machines. Screens of different sizes line up proportionally. |
| **Security** | X25519 key agreement authenticated by your shared secret, ChaCha20-Poly1305 per frame, replay-rejecting counters. |

Keys travel as positions, not characters. Press the key right of `L` on a UK
keyboard and the US-layout machine types what *its* layout puts there — which
is what you want, because you are looking at your own keyboard.

### The details that make it usable

- **Corners are yours.** The outer 40px of each edge will not switch screens,
  so you can still reach the corner UI elements on your own machine. Tunable.
- **Edges arm before they fire.** The cursor has to be seen away from an edge
  before that edge will hand off again. Without this the cursor lands on the
  neighbour's edge, that machine sees "cursor at an edge", and it bounces back
  forever.
- **Any machine's mouse can drive.** Grab the mouse physically attached to
  whichever machine currently has the cursor and it takes over. This is a
  cluster, not a master and a slave.
- **Nothing gets stuck.** When the cursor leaves, the far machine releases
  every key and button it was holding. If the machine holding your cursor
  disappears from the network, your cursor comes home instead of vanishing.

---

## Security

The link carries every keystroke you type on the far machine, so it is
encrypted unconditionally — there is no plaintext mode to turn on by accident.

Each connection does an X25519 exchange authenticated by the pre-shared secret,
which means a passive observer gets nothing, an active attacker on your LAN
cannot machine-in-the-middle it, and recording today's traffic does not help
anyone who learns your secret later. Frames carry a strictly increasing
counter, so a captured click cannot be replayed.

The secret lives in `~/.config/mousebridge/secret`, mode `0600`, deliberately
**not** in `config.json` — the config is meant to be copied between machines and
pasted into terminals, and the key to your keyboard is not. `mousebridge` will
refuse to start if the secret file is group- or world-readable.

Move the secret between machines over something you trust. Anything that can
speak the protocol and knows it can type on your computer.

---

## What does not work

Stated plainly, because the gap between this and Mouse Without Borders is real:

- **Wayland.** Not a bug and not a to-do. Wayland deliberately refuses to let
  one application capture global input or synthesise it into another, which is
  exactly what this program is. Use an Xorg session. `mousebridge doctor` will
  tell you which one you are in.
- **The Windows side is written but unverified by me.** The hooks, `SendInput`
  calls and struct layouts are correct by construction — every structure is
  asserted against its documented Windows x64 size, and every canonical keycode
  round-trips through the Windows representation in the test suite — but I have
  no Windows machine here and have not watched it move a real cursor. Treat the
  first run as a test.
- **Clipboard images stop at Windows.** PNG moves between Linux machines. The
  Windows clipboard wants DIB, and converting would mean an image decoder,
  which would mean a dependency. Text works everywhere.
- **No file drag-and-drop.** Mouse Without Borders has it; this does not.
- **No macOS.** The backend interface is the only thing that would need writing.
- **Multi-monitor is one rectangle.** A machine's monitors are treated as a
  single bounding box, so an L-shaped arrangement has dead space at the edges.

---

## How it is put together

```
mb/crypto.py          ChaCha20-Poly1305, X25519, scrypt. RFC test vectors in tests/.
mb/wire.py            Framing, the message codec, and the authenticated handshake.
mb/keymap.py          One keycode space: evdev codes, which AT set-1 scancodes
                      already match, so Linux <-> Windows is mostly identity.
mb/layout.py          Screen graph; where the cursor goes when it runs off an edge.
mb/node.py            Focus, capture, hand-off, clipboard sync, reconnection.
mb/backend/x11.py     Grab to capture, XTEST to inject, ICCCM for the clipboard.
mb/backend/win32.py   Low-level hooks to capture, SendInput to inject.
```

Exactly one machine has **focus** (the cursor is on its screen) and at most one
is **controlling** (its physical input is being captured and forwarded). Usually
those are different machines, which is the entire point.

Input events are `struct`-packed into 4–9 bytes because they are sent hundreds
of times a second; control messages are JSON because being able to read a
packet dump matters more than forty bytes.

Measured on loopback, capture thread to injection on the far node:

```
median  1.50 ms      p95  1.77 ms      p99  2.04 ms
```

Add your LAN's round trip to that. The cryptography costs about 190 µs per
event per direction, which is not the bottleneck.

---

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

69 tests: RFC vectors for every primitive, tamper and replay rejection, keycode
round-trips across all three representations, cursor geometry, config
validation, and a two-node integration test that runs both daemons over real
sockets with real crypto and only the hardware faked.

On an X11 machine it also runs `tests/test_live_x11.py`, which has a second
node drive this machine's actual cursor and actual clipboard over a real
encrypted socket, then checks with an independent X client that the clipboard
really is pasteable. Skipped automatically elsewhere.

---

## Donations

Entirely optional, and nothing in mousebridge behaves differently either way.

Monero:

```
8ACg45xXbJDMn68kJZzofM8JcUzbtasRXed45SnKXpfdXFSay19GSYd4kngj6ex6uqFBmxE3d81JtD3qpPq9ydzBDHbETXf
```

---

## Licence

MIT.
