"""Build mousebridge.pyz -- the whole program as one runnable file.

Python can execute a zip archive directly, and mousebridge imports nothing
outside the standard library, so the entire program fits in a single file that
needs no installer, no virtualenv and no pip. Someone who has never used a
terminal for anything else can download one file and run it.

Uses only `zipapp` from the standard library, so building has no more
dependencies than running does.
"""

import argparse
import pathlib
import shutil
import sys
import tempfile
import zipapp

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY = ROOT / "tools" / "entry.py"   # shared with the Windows executable build


def build(output, interpreter="/usr/bin/env python3"):
    with tempfile.TemporaryDirectory() as tmp:
        staging = pathlib.Path(tmp) / "app"
        shutil.copytree(
            ROOT / "mb", staging / "mb",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copyfile(ENTRY, staging / "__main__.py")
        output.parent.mkdir(parents=True, exist_ok=True)
        zipapp.create_archive(staging, output, interpreter=interpreter)
    output.chmod(0o755)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default="dist/mousebridge.pyz",
                        type=pathlib.Path)
    parser.add_argument("--interpreter", default="/usr/bin/env python3",
                        help="shebang line; ignored on Windows")
    args = parser.parse_args()
    path = build(args.output, args.interpreter)
    print(f"built {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
