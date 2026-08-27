# mousebridge

[![tests](https://github.com/Kiwi172/mousebridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Kiwi172/mousebridge/actions/workflows/ci.yml)

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

Pick one. The first needs nothing but the ability to download a file.

### The one-file version

Every release ships a single file containing the whole program. There is
nothing to install, nothing to uninstall, and no folder full of dependencies —
delete the file and it is gone.

Grab it from the [releases page](https://github.com/Kiwi172/mousebridge/releases):

| You have | Download | Run it with |
|---|---|---|
| **Windows** | `mousebridge.exe` | Double-click, or `mousebridge.exe` in a terminal |
| **Linux** | `mousebridge.pyz` | `chmod +x mousebridge.pyz` then `./mousebridge.pyz` |

`mousebridge.exe` is self-contained — it does not need Python installed.
`mousebridge.pyz` needs Python 3.8 or newer, which every Linux desktop already
has.

Each release also carries `SHA256SUMS.txt`, if you want to check the download
arrived intact:

```bash
sha256sum -c SHA256SUMS.txt
```

Wherever the instructions below say `mousebridge`, type whichever of those you
downloaded instead. So `mousebridge doctor` becomes `mousebridge.exe doctor` or
`./mousebridge.pyz doctor`.

#### Windows will warn you the first time

Windows shows a blue **"Windows protected your PC"** box, and Defender
SmartScreen calls it an unrecognised app. To run it anyway: click **More info**,
then **Run anyway**.

That warning is not about anything found in the program. It appears because the
executable is not signed with a code-signing certificate, which costs a few
hundred pounds a year from a certificate authority. Every unsigned program gets
the same message, and it fades once enough people have downloaded a given file
for SmartScreen to build a reputation for it.

You do not have to take my word for that. The alternatives, in increasing order
of paranoia:

- Check the download against `SHA256SUMS.txt`, so you know you have the same
  file the release published.
- Look at [how it was built](.github/workflows/release.yml). The executable is
  compiled by GitHub's own Windows runners from the source in this repository,
  and the build log is public — you can read exactly what went into it.
- Skip the executable entirely and [run it from source](#from-source). It is a
  few hundred KB of Python you can read in an afternoon, and it pulls in no
  third-party packages that might hide something.

A browser may also refuse the download until you tell it to keep the file. Same
cause, same answer.

Some antivirus engines may flag it for a second reason, and this one is fair:
mousebridge installs a global keyboard hook and synthesises keystrokes, which is
a precise description of what a keylogger does. The difference is where the
keystrokes go — to one machine you own, over a link encrypted with a key only
you hold, and only while you have pushed the cursor onto that machine. That
distinction is invisible to a heuristic scanner. If your scanner objects, the
source is right here to check.

### From source

```bash
git clone https://github.com/Kiwi172/mousebridge
cd mousebridge
python3 -m mb doctor          # runs straight from the source tree
pip install -e .              # optional; gives you a `mousebridge` command
```

**Python 3.8 or newer, and nothing else.** No pip packages, no compiler, no
service to register. The X11 and Win32 bindings are hand-written `ctypes`, and
the cryptography is `hashlib` plus a few hundred lines of arithmetic.

> **Not available as a Docker image, and cannot be.** A container has no way to
> reach the Windows desktop session — Docker Desktop runs Linux containers in a
> virtual machine — so it could never install the input hooks or move the
> Windows cursor. The single file above is the easy path.

### Check it can work at all, before anything else

```bash
mousebridge doctor
```

```
platform     linux
session      x11  DISPLAY=:0  WAYLAND_DISPLAY=-
backend      x11
screen       1920 x 1080
keycodes     evdev (+8)

this machine can capture and inject input.
```

If the last line says anything else, fix that before going further. The most
common answer is that you are logged into a Wayland session — see
[What does not work](#what-does-not-work).

---

## Set it up

Two machines, ten minutes. The example is a Linux desktop called `desk` with a
Windows laptop called `laptop` sitting to its right. Substitute your own names;
they are just labels, though using each machine's real hostname saves confusion
later.

### 1. Find the address of each machine

You need the local network address of the **other** machine, which looks like
`192.168.x.x` or `10.x.x.x`.

```bash
# Linux
hostname -I | awk '{print $1}'
```
```powershell
# Windows (PowerShell)
(Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.InterfaceAlias -notmatch 'Loopback' }).IPAddress
```

Write both down. If the two machines are on different subnets, or one is on
Wi-Fi and the other on a guest network, they will not find each other.

### 2. Set up the first machine

On the Linux desktop:

```bash
mousebridge init --peer laptop=192.168.1.42 --side right
```

`--side right` means *the laptop sits to the right of this machine*. Use
`left`, `up` or `down` to match how the screens actually sit on your desk —
getting this wrong is the single most common setup mistake, and the symptom is
a cursor that refuses to cross.

That writes two files:

```
~/.config/mousebridge/config.json     the layout — safe to copy anywhere
~/.config/mousebridge/secret          the shared key — mode 600, keep it
```

### 3. Set up the second machine

```bash
mousebridge pair
```

This prints, ready to paste, the exact config for the other machine and the
secret to go with it — in both a Linux/macOS form and a Windows PowerShell
form. Copy the block for whichever the other machine runs, paste it there,
done. You never have to write a config file by hand.

The secret is what stops anything else on your network from typing on your
computers. Carry it across on a USB stick, a password manager, or by reading it
aloud — not through a group chat.

### 4. Start both

Run this on each machine:

```bash
mousebridge run
```

You should see, within a few seconds:

```
[14:22:01] desk up: 1920x1080, cluster 'default'
[14:22:01] layout: desk -> right: laptop
[14:22:03] connected to 192.168.1.42: laptop
[14:22:03] laptop: 2560x1440
```

That last line is the one that matters — it means the handshake succeeded and
both machines agree on each other's screen size. Now push your mouse off the
right-hand edge of the Linux screen.

### 5. Check it before trusting it

```bash
mousebridge status
```

shows the layout, every peer, and whether each one is actually answering.

---

## Using it

| To do this | Do this |
|---|---|
| Move to the next screen | Push the cursor off that edge |
| Jump to a screen directly | `ctrl+alt+←` `→` `↑` `↓` |
| Yank the cursor back to the machine in front of you | `ctrl+alt+home` |
| Copy between machines | Just copy — it syncs by itself |
| Take over with a different machine's mouse | Pick that mouse up and move it |

Hotkeys are configurable under `"hotkeys"` in `config.json`.

### Keep it running

**Linux (systemd user service).** Runs when you log in, restarts if it dies:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/mousebridge.service <<'EOF'
[Unit]
Description=mousebridge
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=%h/.local/bin/mousebridge run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now mousebridge
journalctl --user -u mousebridge -f      # watch it
```

Adjust `ExecStart` to wherever your copy lives — `%h/mousebridge.pyz run` if
you downloaded the single file.

**Windows (start at login).** Press `Win+R`, type `shell:startup`, and put a
shortcut to `mousebridge.exe run` in the folder that opens.

Do not run it as Administrator unless you need to control programs that are
themselves elevated. Windows blocks input injection from a lower integrity
level into a higher one, so if the far machine ignores your typing only inside
one particular app, that is why.

### Let it through the firewall

mousebridge listens on TCP **24800**. Both machines need to accept connections
from the other.

```bash
# Linux, ufw
sudo ufw allow from 192.168.1.0/24 to any port 24800 proto tcp
```
```powershell
# Windows, PowerShell as Administrator
New-NetFirewallRule -DisplayName "mousebridge" -Direction Inbound `
  -Protocol TCP -LocalPort 24800 -Action Allow -Profile Private
```

Restrict it to your local subnet, as both examples do. There is no reason to
expose it to the internet, and the secret is the only thing between a stranger
and your keyboard.

### When it does not work

| Symptom | Cause |
|---|---|
| `peers: laptop ... unreachable` | Firewall, wrong address, or the other machine is not running yet. `mousebridge status` on both. |
| `handshake failed` / `peer failed to prove the shared secret` | The two machines have different secrets. Re-run `mousebridge pair` and paste it again. |
| `rejected 'x' ...: not in this node's peer list` | The names in the two configs disagree. They must match exactly. |
| Cursor will not cross an edge | Wrong `--side`, or you are aiming at a corner — the outer 40px of each edge deliberately will not switch, so corner buttons stay reachable. Aim at the middle of the edge. |
| Cursor crosses, then bounces straight back | The two configs disagree about the layout. `mousebridge status` on both and compare. |
| Clipboard does not sync | Over the 4 MB limit, or it is an image going to Windows. Text always works. |
| Nothing at all, on Linux | `mousebridge doctor`. Almost always a Wayland session. |
| Windows says "protected your PC" | The executable is unsigned. **More info → Run anyway**, or see [above](#windows-will-warn-you-the-first-time). |
| Antivirus quarantines it | It hooks the keyboard, which looks like a keylogger to a heuristic. Same section explains why. |

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
- **The Windows side has never driven a second machine.** CI does run it on
  real Windows, where `SendInput` moves the actual cursor, both low-level hooks
  install, and the clipboard round-trips — so the parts that were pure
  guesswork are now measured. What no test covers is the thing you actually
  want: two physical machines, one capturing while a person types on it. The
  capture path in particular is only exercised by its structure, never by a
  real hand on a real keyboard. Treat your first two-machine run as the test.
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

CI runs the whole suite on Linux under Xvfb — so the grab, XTEST and clipboard
paths are genuinely exercised, not skipped — across Python 3.8 through 3.13, and
on Windows. A separate informational job runs `tests/test_live_win32.py`, which
drives the runner's real cursor and clipboard. It is allowed to fail without
breaking the build, because a failure there could equally mean a bug or a
limitation of the runner's window station, and those look identical from
outside. Read its log before trusting the Windows backend.

---

## Donations

Entirely optional, and nothing in mousebridge behaves differently either way.

Monero:

```
8ACg45xXbJDMn68kJZzofM8JcUzbtasRXed45SnKXpfdXFSay19GSYd4kngj6ex6uqFBmxE3d81JtD3qpPq9ydzBDHbETXf
```

---

## Licence

MIT — see [LICENSE](LICENSE).
