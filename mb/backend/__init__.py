"""Backend selection."""

import sys


def create(clipboard_config=None):
    """Return the backend for this platform, or explain why there isn't one."""
    if sys.platform == "win32":
        from .win32 import Win32Backend
        return Win32Backend(clipboard_config)

    if sys.platform.startswith("linux") or "bsd" in sys.platform:
        import os
        if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            raise RuntimeError(
                "This looks like a pure Wayland session.\n"
                "Wayland deliberately refuses to let one application capture "
                "global input or synthesise it into another, so the X11 method "
                "mousebridge uses cannot work here.\n"
                "Options: log in to an X11/Xorg session, or run under Xwayland "
                "with DISPLAY set (input capture will still be limited to "
                "X11 clients)."
            )
        from .x11 import X11Backend
        return X11Backend(clipboard_config)

    raise RuntimeError(
        f"no mousebridge backend for {sys.platform!r}; "
        f"supported platforms are Linux/X11 and Windows"
    )
