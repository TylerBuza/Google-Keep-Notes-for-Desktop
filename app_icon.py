"""One bundled Keep logo for the app, native windows, taskbar and EXE.

Artwork provenance and trademark notices live in keep_brand.py. Icon generation
only resizes that local PNG; it never draws a replacement letter or fetches art.
"""

from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QIcon

from keep_brand import keep_logo


# Include Windows' small/taskbar/DPI sizes as well as large Explorer/Alt-Tab sizes.
_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def make_icon(color_hex: str | None = None) -> QIcon:
    """Return the same official artwork as the tray, without per-note tinting.

    The old optional color argument is accepted for compatibility only. Changing
    note colors must not replace or recolor the application's Windows icon.
    """
    return keep_logo()


def save_ico(path: str) -> None:
    """Write a multi-resolution Windows ICO using the bundled logo only."""
    # Explicit DPR=1 keeps ICO dimensions deterministic on fractional-DPI hosts.
    source = keep_logo().pixmap(QSize(256, 256), 1.0)
    frames = []
    for size in _ICON_SIZES:
        pixmap = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(1.0)
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise RuntimeError("Could not prepare the bundled application icon.")
        try:
            if not pixmap.save(buffer, "PNG"):
                raise RuntimeError("Could not encode the bundled application icon.")
        finally:
            buffer.close()
        frames.append((size, bytes(data)))

    # ICO entries point to standard PNG payloads supported by Windows 10/11.
    offset = 6 + 16 * len(frames)
    entries = []
    for size, data in frames:
        encoded_size = size if size < 256 else 0
        entries.append(struct.pack("<BBBBHHII", encoded_size, encoded_size,
                                   0, 0, 1, 32, len(data), offset))
        offset += len(data)
    Path(path).write_bytes(struct.pack("<HHH", 0, 1, len(frames))
                           + b"".join(entries) + b"".join(data for _, data in frames))
