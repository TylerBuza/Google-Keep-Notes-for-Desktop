"""Check PyInstaller's generated module inventory, without executing the app."""

import ast
import json
from pathlib import Path
import sys


def module_names(value):
    if isinstance(value, (tuple, list)):
        if (len(value) == 3 and isinstance(value[0], str) and isinstance(value[2], str)
                and value[2] in ("PYMODULE", "PYSOURCE")):
            yield value[0]
        for child in value:
            yield from module_names(child)


def verify(path):
    names = set(module_names(ast.literal_eval(Path(path).read_text(encoding="utf-8"))))
    forbidden = {name for name in names if any(name == root or name.startswith(root + ".")
                                              for root in ("keep_sync", "gkeepapi", "gpsoauth", "Cryptodome"))}
    if forbidden:
        raise RuntimeError("Legacy login code present in frozen build: " + ", ".join(sorted(forbidden)))
    required = {"login_security", "keep_protocol.auth_transport", "note_model", "hardened_sync"}
    if not required.issubset(names):
        raise RuntimeError("Frozen build is missing a required security module.")
    return {"legacy_auth_modules_absent": True, "required_security_modules_present": True,
            "python_module_count": len(names)}


if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1]), indent=2))
