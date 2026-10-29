"""Print a platform-specific, hash-locked wheel set for reviewed pinned versions.

Does not install packages, change versions, or overwrite the input. Run from
the Windows/Python version used to build the app, then review the output.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename


def main():
    pins = []
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)(?:\s+--hash=sha256:[0-9a-f]{64})?", line)
        if not match:
            raise ValueError("Only reviewed, exactly pinned versions are accepted.")
        pins.append(match.groups())
    ranks = {tag: i for i, tag in enumerate(sys_tags())}

    def resolve(pin):
        name, version = pin
        url = f"https://pypi.org/pypi/{quote(name, safe='')}/{quote(version, safe='')}/json"
        with urlopen(Request(url, headers={"User-Agent": "KeepDesktop-lock-review/1"}), timeout=30) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Unexpectedly large package metadata.")
        data = json.loads(raw)
        choices = []
        for artifact in data["urls"]:
            if artifact["packagetype"] != "bdist_wheel" or artifact.get("yanked"):
                continue
            _, _, _, tags = parse_wheel_filename(artifact["filename"])
            matches = [ranks[tag] for tag in tags if tag in ranks]
            digest = artifact["digests"]["sha256"]
            if matches and re.fullmatch(r"[0-9a-f]{64}", digest):
                choices.append((min(matches), artifact["filename"], digest))
        if not choices:
            raise ValueError(f"No supported wheel for {name}=={version}; no source-build fallback.")
        _, filename, digest = min(choices)
        return f"# {filename}\n{name}=={version} --hash=sha256:{digest}"

    with ThreadPoolExecutor(max_workers=8) as executor:
        locked = list(executor.map(resolve, pins))
    print("# Reviewed Windows x64 / CPython 3.13 build-and-test environment.")
    print("# Hashes identify exact wheels, not proof that their source is vulnerability-free.")
    print("\n".join(locked))


if __name__ == "__main__":
    main()
