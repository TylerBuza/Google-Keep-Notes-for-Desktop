"""Local Keep-like typography, registered for this process only; no networking."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication

BODY_FAMILY = "Roboto"
TITLE_FAMILY = "Google Sans"
FONT_FILES = (
    "GoogleSans-Regular.ttf", "GoogleSans-Medium.ttf", "Roboto-Regular.ttf",
    "Roboto-Medium.ttf", "Roboto-Bold.ttf", "Roboto-Italic.ttf", "Roboto-BoldItalic.ttf",
)


def font_directory():
    # __file__ resolves inside PyInstaller's _internal directory too. Never
    # use the working directory, a note-supplied path, or a remote font URL.
    return Path(__file__).resolve().parent / "assets" / "fonts"


def verify_font_assets(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if set(manifest["files"]) != set(FONT_FILES):
        raise RuntimeError("Bundled font inventory is incomplete.")
    for name in FONT_FILES:
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest["files"][name]["sha256"]:
            raise RuntimeError("Bundled font integrity check failed.")
    for name in ("OFL.txt", "GoogleSans-TRADEMARKS.md"):
        if not (directory / name).is_file():
            raise RuntimeError("Bundled font license notice is missing.")
    license_text = (directory / "OFL.txt").read_text(encoding="utf-8")
    if not all(value in license_text for value in (
            "SIL OPEN FONT LICENSE Version 1.1", "Google Sans Project Authors", "Roboto Project Authors")):
        raise RuntimeError("Bundled font license notice is incomplete.")
    return {"font_count": len(FONT_FILES), "sha256_verified": True, "licenses_present": True}


@lru_cache(maxsize=1)
def load_app_fonts():
    if QGuiApplication.instance() is None:
        raise RuntimeError("Font registration requires a Qt application.")
    directory = font_directory()
    verify_font_assets(directory)
    ids = []
    for name in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(directory / name))
        expected = TITLE_FAMILY if name.startswith("GoogleSans-") else BODY_FAMILY
        if font_id < 0 or expected not in QFontDatabase.applicationFontFamilies(font_id):
            raise RuntimeError("Bundled interface font could not be loaded.")
        ids.append(font_id)
    return tuple(ids)


def crisp_font(font, *, title=False):
    load_app_fonts()
    result = QFont(font)
    result.setFamily(TITLE_FAMILY if title else BODY_FAMILY)
    result.setStyleStrategy(QFont.StyleStrategy.PreferAntialias |
                            QFont.StyleStrategy.NoSubpixelAntialias)
    # Full grid fitting made strokes look rigid compared with Chromium's
    # natural metrics. Keep fractional outlines and smooth grayscale coverage.
    result.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return result
