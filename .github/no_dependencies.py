"""Fail if anything under mb/ imports a third-party module.

"Nothing but the standard library" is a promise the README makes, and it is
the kind of promise that erodes one convenient import at a time.
"""

import ast
import pathlib
import sys

if sys.version_info >= (3, 10):
    stdlib = set(sys.stdlib_module_names)
else:
    # sys.stdlib_module_names arrived in 3.10; on older interpreters fall back
    # to asking whether the module resolves without site-packages.
    import importlib.util
    import sysconfig
    site = sysconfig.get_paths().get("purelib", "")
    stdlib = None

external = set()
for path in sorted(pathlib.Path("mb").rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                external.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            external.add(node.module.split(".")[0])

def is_third_party(name):
    if name == "mb":
        return False
    if stdlib is not None:
        return name not in stdlib
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    return bool(spec and spec.origin and site and spec.origin.startswith(site))

offenders = sorted(name for name in external if is_third_party(name))
if offenders:
    print("third-party imports found in mb/:", ", ".join(offenders))
    sys.exit(1)
print(f"mb/ imports {len(external)} modules, all standard library")
