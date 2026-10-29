"""Verify that a frozen distribution contains the exact local font bundle."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui_typography import verify_font_assets


def verify(directory):
    directory = Path(directory)
    for root in (directory / "_internal", directory):
        fonts = root / "assets" / "fonts"
        if fonts.is_dir():
            return verify_font_assets(fonts)
    raise RuntimeError("Frozen font bundle is missing.")


if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1]), indent=2))
