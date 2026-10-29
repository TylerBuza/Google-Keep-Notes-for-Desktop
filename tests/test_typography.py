"""Bundled font selection, smoothing and display-only normalization."""

from copy import deepcopy
import json
import shutil
from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QTextCursor

from hardened_sync import snapshot
from note_manager import NoteManagerDialog, NoteEditorDialog
from note_model import KeepNote
from note_window import NoteWindow
from ui_typography import (BODY_FAMILY, TITLE_FAMILY, FONT_FILES, crisp_font,
                           font_directory, load_app_fonts, verify_font_assets)
from tools.verify_frozen_fonts import verify


def settle(qapp):
    for _ in range(8):
        qapp.processEvents()


def test_complete_original_font_bundle_and_licenses_are_verified():
    report = verify_font_assets(font_directory())
    assert report == {"font_count": 7, "sha256_verified": True, "licenses_present": True}
    license_text = (font_directory() / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "Google Sans Project Authors" in license_text
    assert "Roboto Project Authors" in license_text
    manifest = json.loads((font_directory() / "manifest.json").read_text(encoding="utf-8"))
    assert all(entry["source"].startswith("https://fonts.gstatic.com/")
               for entry in manifest["files"].values())


def test_fonts_register_once_for_this_application_and_resolve_real_faces(qapp):
    ids = load_app_fonts()
    assert len(ids) == len(FONT_FILES)
    assert ids is load_app_fonts()
    assert all(font_id >= 0 for font_id in ids)
    assert BODY_FAMILY in QFontDatabase.families()
    assert TITLE_FAMILY in QFontDatabase.families()
    for title in (False, True):
        font = crisp_font(QFont("Segoe UI", 12), title=title)
        family = TITLE_FAMILY if title else BODY_FAMILY
        assert font.family() == family
        assert QFontInfo(font).family().startswith(family)
        assert font.hintingPreference() == QFont.HintingPreference.PreferNoHinting
        assert font.styleStrategy() & QFont.StyleStrategy.PreferAntialias
        assert font.styleStrategy() & QFont.StyleStrategy.NoSubpixelAntialias
    assert {"Regular", "Medium", "Bold", "Italic", "Bold Italic"}.issubset(
        set(QFontDatabase.styles(BODY_FAMILY)))


def test_font_lookup_is_independent_of_working_directory(qapp, tmp_path, monkeypatch):
    original = font_directory()
    monkeypatch.chdir(tmp_path)
    assert font_directory() == original
    assert QFontInfo(crisp_font(QFont())).family() == BODY_FAMILY


@pytest.mark.parametrize("problem", ["missing-font", "modified-font", "missing-license", "empty-license"])
def test_incomplete_or_modified_assets_are_not_silently_replaced(tmp_path, problem):
    root = tmp_path / "fonts"
    shutil.copytree(font_directory(), root)
    if problem == "missing-font":
        (root / FONT_FILES[0]).unlink()
    elif problem == "modified-font":
        path = root / FONT_FILES[0]
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    elif problem == "missing-license":
        (root / "OFL.txt").unlink()
    else:
        (root / "OFL.txt").write_text("", encoding="utf-8")
    with pytest.raises((OSError, RuntimeError)):
        verify_font_assets(root)


@pytest.mark.parametrize("internal", [False, True])
def test_frozen_font_inventory_verifies_exact_packaged_bytes(tmp_path, internal):
    root = tmp_path / "distribution"
    destination = (root / "_internal" if internal else root) / "assets" / "fonts"
    shutil.copytree(font_directory(), destination)
    assert verify(root)["sha256_verified"] is True


def test_missing_frozen_fonts_fail_the_build(tmp_path):
    with pytest.raises(RuntimeError, match="missing"):
        verify(tmp_path)


def test_titles_and_body_use_distinct_keep_like_families(qapp):
    note = KeepNote(id="synthetic-font", title="A little inspiration", text="One small idea")
    manager = NoteManagerDialog({note.id: note}, {})
    editor = NoteEditorDialog(note)
    try:
        manager.show()
        editor.show()
        settle(qapp)
        card = manager._cards[note.id]
        assert card.title.font().family() == TITLE_FAMILY
        assert card.title.font().weight() == QFont.Weight.Medium
        assert card.body.font().family() == BODY_FAMILY
        assert card.body.font().weight() == QFont.Weight.Normal
        assert editor.title_edit.font().family() == TITLE_FAMILY
        assert editor.text_edit.font().family() == BODY_FAMILY
        assert editor.changes() == {}
    finally:
        editor.accept()
        editor.deleteLater()
        manager.close()
        manager.deleteLater()


def test_legacy_ui_font_is_display_only_not_a_note_or_formatting_edit(qapp):
    original_html = '<p style="font-family:Segoe UI;font-size:12pt"><b>Bold idea</b> and text</p>'
    note = KeepNote(id="synthetic-font", title="Title", text="Bold idea and text", html=original_html)
    before = deepcopy(note)
    editor = NoteEditorDialog(note)
    try:
        editor.show()
        settle(qapp)
        assert editor.text_edit.toPlainText() == note.text
        assert editor.changes() == {}
        assert note == before
        cursor = QTextCursor(editor.text_edit.document())
        cursor.setPosition(2)
        assert cursor.charFormat().fontFamilies() == [BODY_FAMILY]
        assert cursor.charFormat().fontWeight() >= QFont.Weight.Bold
        rendered = deepcopy(note)
        rendered.html = editor.text_edit.toHtml()
        assert snapshot(rendered)["styled_doc"] == snapshot(note)["styled_doc"]
        editor.title_edit.setText("New title")
        assert editor.changes() == {"title": "New title"}
        assert note.html == original_html
    finally:
        editor.accept()
        editor.deleteLater()


def test_opening_legacy_note_in_new_fonts_never_emits_an_edit(qapp, monkeypatch):
    monkeypatch.setattr("note_window.get_position", lambda note_id: None)
    monkeypatch.setattr("note_window.set_position", MagicMock())
    win = NoteWindow("synthetic-font", title="Title", text="Bold idea and text",
                     html='<p style="font-family:Segoe UI"><b>Bold idea</b> and text</p>', dark_mode=True)
    changed = MagicMock()
    win.note_changed.connect(changed)
    try:
        win.show()
        settle(qapp)
        changed.assert_not_called()
        assert win.title_bar.title_edit.font().family() == TITLE_FAMILY
        assert win.text_edit.toPlainText() == "Bold idea and text"
    finally:
        win.close()
        win.deleteLater()
        settle(qapp)


def test_explicit_non_ui_typefaces_are_not_replaced(qapp):
    note = KeepNote(id="synthetic-font", text="Custom text",
                    html='<p style="font-family:Times New Roman"><i>Custom text</i></p>')
    editor = NoteEditorDialog(note)
    try:
        cursor = QTextCursor(editor.text_edit.document())
        cursor.setPosition(2)
        assert cursor.charFormat().fontFamilies() == ["Times New Roman"]
        assert cursor.charFormat().fontItalic()
        assert editor.changes() == {}
    finally:
        editor.accept()
        editor.deleteLater()
