"""Synthetic-only typography, theme and content-height regressions."""

from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QFont

from config import KEEP_COLORS_DARK
from dashboard_theme import card_color
from note_manager import NoteManagerDialog
from note_model import KeepNote
from note_window import NoteWindow


def settle(qapp):
    for _ in range(12):
        qapp.processEvents()


@pytest.fixture
def popup(qapp):
    dialogs = []

    def create(note):
        dlg = NoteManagerDialog({note.id: note}, {})
        dialogs.append(dlg)
        dlg.show()
        editor = dlg.create_editor(note)
        dlg.show_editor()
        settle(qapp)
        return dlg, editor

    yield create
    for dlg in dialogs:
        if dlg._editor is not None:
            dlg._editor.accept()
        dlg.close()
        dlg.deleteLater()
    settle(qapp)


def test_short_popup_is_compact_grows_only_as_needed_and_shrinks(popup, qapp):
    note = KeepNote(id="synthetic", title="Quick thought", text="A short line")
    dlg, editor = popup(note)
    small = editor.height()
    assert 180 <= small < 300
    editor.text_edit.setPlainText("Fictional line\n" * 80)
    settle(qapp)
    assert editor.height() > small
    assert editor.height() <= dlg.height() - 48
    assert editor.text_edit.verticalScrollBar().maximum() > 0
    editor.text_edit.setPlainText("Short again")
    settle(qapp)
    assert editor.height() <= small + 4
    assert note.text == "A short line"  # Sizing never commits a draft.


def test_checklist_popup_grows_with_rows(popup, qapp):
    dlg, editor = popup(KeepNote(id="synthetic", is_list=True,
        list_items=[{"text": "Task", "checked": False}]))
    small = editor.height()
    for _ in range(15):
        editor.checklist_editor.insert_row_after(editor.checklist_editor._rows[-1])
    settle(qapp)
    assert editor.height() > small
    assert editor.height() <= dlg.height() - 48


@pytest.mark.parametrize("color", ["#FFFFFF", "#AECBFA", "#CCFF90", "#FDCFE8"])
def test_popup_matches_card_without_recoloring_cloud_note(popup, color):
    note = KeepNote(id="synthetic", text="Text", color_hex=color)
    _, editor = popup(note)
    assert editor._display_color == card_color(color)
    assert editor._display_color in editor.styleSheet()
    assert note.color_hex == color
    assert editor.changes() == {}
    assert KEEP_COLORS_DARK["White"] == card_color("#FFFFFF")


def test_fonts_are_larger_and_disable_colored_subpixel_antialiasing(popup):
    dlg, editor = popup(KeepNote(id="synthetic", title="Title", text="Text"))
    assert editor.text_edit.font().pixelSize() >= 17
    assert editor.font().styleStrategy() & QFont.StyleStrategy.NoSubpixelAntialias
    assert editor.text_edit.font().styleStrategy() & QFont.StyleStrategy.NoSubpixelAntialias
    assert dlg._cards["synthetic"].body.font().pixelSize() >= 17
    assert dlg._pinned_label.font().pixelSize() >= 14


def test_popup_hint_distinguishes_web_sync_from_local_save(popup):
    dlg, editor = popup(KeepNote(id="synthetic", text="Text"))
    dlg.set_sync_state(True, True)
    assert "two-way sync on" in editor._save_hint.text()
    assert "Two-way sync on" in dlg._status.text()
    dlg.set_sync_state(True, False)
    assert "web writes off" in editor._save_hint.text()
    assert "Read-only sync" in dlg._status.text()


@pytest.fixture
def floating(qapp, monkeypatch):
    saved = MagicMock()
    monkeypatch.setattr("note_window.get_position", lambda note_id: None)
    monkeypatch.setattr("note_window.set_position", saved)
    win = NoteWindow("synthetic-fit", title="Small note", text="One line", dark_mode=True)
    win.move(40, 40)
    win.show()
    settle(qapp)
    yield win, saved
    win.close()
    win.deleteLater()
    settle(qapp)


def test_desktop_note_fits_content_instead_of_default_empty_height(floating, qapp):
    win, saved = floating
    small = win.height()
    assert small < 300
    width = win.width()
    win.text_edit.setPlainText("Fictional line\n" * 80)
    settle(qapp)
    assert win.height() > small
    assert win.width() == width
    assert win.height() <= win.screen().availableGeometry().height()
    win.text_edit.setPlainText("Short again")
    settle(qapp)
    assert win.height() <= small + 4
    win.save_geometry()
    assert saved.call_args.kwargs["auto_size"] is True


def test_manual_desktop_height_is_respected_until_fit_is_reenabled(floating, qapp):
    win, saved = floating
    win.set_auto_size(False)
    win.resize(win.width(), 400)
    win.text_edit.setPlainText("Short text")
    settle(qapp)
    assert win.height() == 400
    win.save_geometry()
    assert saved.call_args.kwargs["auto_size"] is False
    win.set_auto_size(True)
    settle(qapp)
    assert win.height() < 300


def test_opening_and_autofitting_rich_note_is_not_a_content_edit(qapp, monkeypatch):
    monkeypatch.setattr("note_window.get_position", lambda note_id: None)
    monkeypatch.setattr("note_window.set_position", MagicMock())
    win = NoteWindow("synthetic-render", title="Title", text="Text https://google.com",
                     html="<p><b>Text</b> https://google.com</p>", dark_mode=True)
    changed = MagicMock()
    win.note_changed.connect(changed)
    try:
        win.show()
        settle(qapp)
        changed.assert_not_called()
    finally:
        win.close()
        win.deleteLater()
        settle(qapp)
