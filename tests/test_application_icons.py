"""Bundled Keep artwork stays identical across Windows app-icon surfaces."""

import base64
import hashlib
from pathlib import Path
import struct
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from app_icon import make_icon, save_ico
from config import KEEP_COLORS
from keep_brand import _LOGO_PNG, keep_logo
from note_manager import NoteEditorDialog, NoteManagerDialog
from note_model import KeepNote
from note_window import NoteWindow


def settle(qapp):
    for _ in range(12):
        qapp.processEvents()


@pytest.mark.parametrize("color", [None, "#FFF475", "#42275E", "#202124"])
def test_window_icon_is_exactly_the_tray_logo_without_note_tinting(qapp, color):
    icon = make_icon(color)
    assert not icon.isNull()
    assert icon.cacheKey() == keep_logo().cacheKey()


@pytest.mark.parametrize("tracked", [False, True])
def test_windows_ico_contains_correct_logo_at_small_taskbar_and_large_sizes(qapp, tmp_path, tracked):
    if tracked:
        path = Path(__file__).resolve().parents[1] / "icon.ico"
    else:
        path = tmp_path / "generated.ico"
        save_ico(str(path))
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    assert count == 10
    source = QPixmap()
    original = base64.b64decode(_LOGO_PNG)
    assert hashlib.sha256(original).hexdigest() == "c95da1279834351ba206716611597a5d9b3a6c0df8fa79ad4f5007e4151fc857"
    assert source.loadFromData(original, "PNG")
    sizes = []
    offset = 6 + 16 * count
    for index in range(count):
        width, height, colors, zero, planes, depth, length, start = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        size = width or 256
        assert (height or 256) == size
        assert (colors, zero, planes, depth) == (0, 0, 1, 32)
        assert start == offset
        assert data[start:start + 8] == b"\x89PNG\r\n\x1a\n"
        actual = QImage.fromData(data[start:start + length], "PNG")
        expected = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation).toImage()
        assert actual.size() == expected.size()
        # The tracked ICO can be generated on a different CPU from the runner.
        # Qt's SIMD resampling rounds a few edge pixels differently. Compare
        # premultiplied channels (the visible contribution) with a tiny bound,
        # not unpremultiplied RGB where near-transparent colors amplify it.
        actual_pm = actual.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        expected_pm = expected.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        actual_pixels = bytes(actual_pm.constBits())
        expected_pixels = bytes(expected_pm.constBits())
        deltas = [abs(a - b) for a, b in zip(actual_pixels, expected_pixels)]
        assert len(actual_pixels) == len(expected_pixels)
        assert max(deltas) <= 2, f"Wrong logo pixels at {size}px"
        assert sum(deltas) / len(deltas) <= 0.25, f"Wrong logo artwork at {size}px"
        offset += length
        sizes.append(size)
    assert sizes == [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    assert offset == len(data)


def test_dashboard_and_editor_use_the_same_logo_as_tray(qapp):
    note = KeepNote(id="fictional-icon", title="A small idea", text="Fictional body")
    manager = NoteManagerDialog({note.id: note}, {})
    editor = NoteEditorDialog(note)
    try:
        for widget in (manager, editor):
            assert widget.windowIcon().cacheKey() == keep_logo().cacheKey()
        assert editor.changes() == {}
    finally:
        editor.accept()
        editor.deleteLater()
        manager.close()
        manager.deleteLater()
        settle(qapp)


def test_floating_taskbar_icon_survives_palette_and_visibility_changes_without_edits(qapp, monkeypatch):
    monkeypatch.setattr("note_window.get_position", lambda note_id: None)
    monkeypatch.setattr("note_window.set_position", MagicMock())
    win = NoteWindow("fictional-icon", title="A small idea", text="Fictional body", dark_mode=True)
    changed = MagicMock()
    win.note_changed.connect(changed)
    try:
        win.show()
        settle(qapp)
        for dark in (False, True):
            win.set_dark_mode(dark)
            for color in KEEP_COLORS.values():
                win._apply_color(color)
                settle(qapp)
                assert win.windowIcon().cacheKey() == keep_logo().cacheKey()
        for show in (True, False, True):
            win.set_taskbar_visible(show)
            settle(qapp)
            assert win.windowIcon().cacheKey() == keep_logo().cacheKey()
        assert win.get_text() == "Fictional body"
        assert win.get_title() == "A small idea"
        changed.assert_not_called()
    finally:
        win.close()
        win.deleteLater()
        settle(qapp)
