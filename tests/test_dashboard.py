"""Offline dashboard, edit-draft, desktop pinning and resource regressions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest
from PySide6.QtCore import Qt, QPoint, QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest

from app_controller import AppController
from dashboard_theme import BACKGROUND, card_color, icon
from native_resources import LocalNoteDocument
from note_manager import NoteManagerDialog, NoteEditorDialog, edit_snapshot
from note_model import KeepNote


@pytest.fixture
def notes():
    samples = [
        KeepNote(id="1.a", title="Pinned", text="Alpha", pinned=True, sort_key=100, color_hex="#FFFFFF"),
        KeepNote(id="2.b", title="Shopping", text="Milk\nTea", sort_key=300, labels=["Errands"]),
        KeepNote(id="3.c", text="Idea STRASSE", sort_key=200, labels=["Ideas"]),
    ]
    return {note.id: note for note in samples}


@pytest.fixture
def dashboard(qapp, notes):
    dlg = NoteManagerDialog(notes, {"1.a": True, "2.b": False, "3.c": False})
    dlg.show()
    qapp.processEvents()
    yield dlg
    dlg.close()
    dlg.deleteLater()
    qapp.processEvents()


def displayed(dlg):
    return [card.note_id for card in dlg._pinned_grid.cards + dlg._others_grid.cards]


def test_theme_does_not_mutate_cloud_colors_or_desktop_preferences(dashboard, notes):
    before = deepcopy(notes)
    dashboard.refresh(notes, {"1.a": False})
    dashboard._toggle_layout()
    assert notes == before
    assert card_color("#FFFFFF") == BACKGROUND
    assert card_color("#AECBFA") == "#1E3A5F"
    assert card_color("url(https://untrusted.test)") == BACKGROUND


def test_keep_pin_sections_and_sort_order(dashboard):
    assert [card.note_id for card in dashboard._pinned_grid.cards] == ["1.a"]
    assert [card.note_id for card in dashboard._others_grid.cards] == ["2.b", "3.c"]


@pytest.mark.parametrize("query,expected", [("shopping", ["2.b"]), ("tea", ["2.b"]),
    ("errands", ["2.b"]), ("straße", ["3.c"]), ("nonexistent", [])])
def test_search_title_body_labels_and_unicode(dashboard, query, expected):
    dashboard._search_edit.setText(query)
    assert displayed(dashboard) == expected


def test_search_and_navigation_never_change_desktop_visibility(dashboard):
    changed = MagicMock()
    dashboard.visibility_changed.connect(changed)
    before = dict(dashboard.result_visibility)
    dashboard._search_edit.setText("missing")
    dashboard._search_edit.clear()
    dashboard._set_view("desktop")
    assert displayed(dashboard) == ["1.a"]
    dashboard._set_view("label:Ideas")
    assert displayed(dashboard) == ["3.c"]
    assert dashboard.result_visibility == before
    changed.assert_not_called()


def test_external_hide_updates_even_while_card_filtered_out(dashboard):
    dashboard._search_edit.setText("Shopping")
    dashboard.set_checkbox("1.a", False)
    dashboard._search_edit.clear()
    dashboard._set_view("desktop")
    assert displayed(dashboard) == []


def test_controller_visibility_wins_over_stale_dashboard_state(dashboard, notes):
    dashboard.refresh(notes, {"2.b": True})
    assert dashboard.result_visibility == {"1.a": False, "2.b": True, "3.c": False}


def test_card_edit_keep_pin_and_desktop_pin_are_different_signals(dashboard, qapp):
    edit, pin, desktop = MagicMock(), MagicMock(), MagicMock()
    dashboard.note_edit_requested.connect(edit)
    dashboard.pin_toggle_requested.connect(pin)
    dashboard.visibility_changed.connect(desktop)
    card = dashboard._cards["2.b"]
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    edit.assert_called_once_with("2.b")
    card.pin.click()
    pin.assert_called_once_with("2.b")
    card.desktop.click()
    desktop.assert_called_once_with("2.b", True)
    assert dashboard._all_notes["2.b"].pinned is False


def test_disabled_management_is_not_fake_functionality(dashboard):
    assert not dashboard._nav_buttons["archive"].isEnabled()
    assert not dashboard._nav_buttons["trash"].isEnabled()
    assert "safe sync" in dashboard._nav_buttons["archive"].toolTip()


def test_delete_keeps_card_until_controller_confirms(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr("note_manager.QMessageBox.question", lambda *args: QMessageBox.StandardButton.Yes)
    deleted = MagicMock()
    dashboard.note_deleted.connect(deleted)
    dashboard._delete_note("1.a")
    deleted.assert_called_once_with("1.a")
    assert "1.a" in dashboard._cards
    assert "1.a" in dashboard._all_notes


def test_refresh_removes_deleted_card_and_preserves_others(dashboard, notes, qapp):
    retained = dashboard._cards["2.b"]
    dashboard.refresh({key: value for key, value in notes.items() if key != "1.a"}, {})
    qapp.processEvents()
    assert "1.a" not in dashboard._cards
    assert dashboard._cards["2.b"] is retained
    assert dashboard._pinned_grid.cards == []


@pytest.mark.parametrize("width", [640, 900, 1500])
def test_masonry_reflows_without_overlaps_or_card_rebuilds(dashboard, width, qapp):
    cards = dict(dashboard._cards)
    dashboard.resize(width, 800)
    qapp.processEvents()
    qapp.processEvents()
    assert dashboard._cards == cards
    for grid in (dashboard._pinned_grid, dashboard._others_grid):
        for index, card in enumerate(grid.cards):
            assert card.x() >= 0
            assert card.geometry().right() < grid.width()
            assert card.geometry().bottom() < grid.height()
            for other in grid.cards[index + 1:]:
                assert not card.geometry().intersects(other.geometry())


def test_list_view_uses_one_centered_column(dashboard, qapp):
    dashboard._toggle_layout()
    qapp.processEvents()
    cards = dashboard._others_grid.cards
    assert len({card.x() for card in cards}) == 1
    assert all(card.width() <= 600 for card in cards)


def test_clear_search_on_empty_notebook_is_safe(qapp):
    dlg = NoteManagerDialog({}, {})
    try:
        dlg._search_edit.setText("nothing")
        dlg._search_edit.clear()
        assert displayed(dlg) == []
        assert dlg._empty.text() == "Notes you add appear here"
    finally:
        dlg.deleteLater()


@pytest.mark.parametrize("is_list", [False, True])
def test_opening_editor_is_not_an_edit_and_never_changes_model(qapp, is_list):
    note = KeepNote(id="1.test", title="Title", text="Body", html="<p><b>Body</b></p>",
                    is_list=is_list, list_items=[{"text": "Task", "checked": False, "cbx_id": "saved"}] if is_list else [])
    before = deepcopy(note)
    editor = NoteEditorDialog(note)
    try:
        editor.show()
        qapp.processEvents()
        qapp.processEvents()
        assert editor.changes() == {}
        assert note == before
        assert isinstance(editor.text_edit.document(), LocalNoteDocument)
    finally:
        editor.accept()
        editor.deleteLater()


def test_title_only_edit_does_not_reserialize_body_or_formatting(qapp):
    note = KeepNote(id="1.test", title="Title", html="<h1>Heading</h1><p><b>Bold</b></p>", text="Heading\nBold")
    editor = NoteEditorDialog(note)
    try:
        qapp.processEvents()
        editor.title_edit.setText("New title")
        assert editor.changes() == {"title": "New title"}
    finally:
        editor.accept()
        editor.deleteLater()


def test_checklist_edits_preserve_item_identity_and_indent(qapp):
    items = [{"text": "Task", "checked": False, "cbx_id": "cbx-synthetic", "indent": 1}]
    editor = NoteEditorDialog(KeepNote(id="1.test", is_list=True, list_items=items))
    try:
        editor.checklist_editor._rows[0].checkbox.setChecked(True)
        changed = editor.changes()
        assert changed["list_items"] == [{**items[0], "checked": True}]
        assert changed["text"] == "☑ Task"
    finally:
        editor.accept()
        editor.deleteLater()


def test_untrusted_note_html_cannot_fetch_resources(qapp):
    note = KeepNote(id="1.test", html='<img src="https://untrusted.test/a.png"><p>Safe</p>')
    editor = NoteEditorDialog(note)
    try:
        document = editor.text_edit.document()
        assert not document.loadResource(QTextDocument.ResourceType.ImageResource,
                                         QUrl("https://untrusted.test/a.png"))
        assert editor.changes() == {}
    finally:
        editor.accept()
        editor.deleteLater()


def controller(note):
    return SimpleNamespace(_notes={note.id: note}, _visibility={note.id: False}, _dirty=set(),
        _last_edit_time={}, _save_notes_to_disk=MagicMock(), _refresh_window=MagicMock(),
        _refresh_manager_if_open=MagicMock(), sync=SimpleNamespace(is_authenticated=True),
        _sync_debounce=MagicMock())


def draft(note, *, is_new=False):
    return SimpleNamespace(note_id=note.id, base=edit_snapshot(note), is_new=is_new,
                           accept=MagicMock(), show_error=MagicMock())


def test_saving_dashboard_draft_preserves_formatted_body_for_title_change():
    note = KeepNote(id="1.test", title="Old", text="Body", html="<p><b>Body</b></p>")
    note.styled_doc = {"synthetic": True}
    ctl, editor = controller(note), draft(note)
    AppController._save_dashboard_edit(ctl, editor, {"title": "New"})
    saved = ctl._notes[note.id]
    assert saved is note  # In-flight create/push references must stay current.
    assert saved.title == "New"
    assert saved.html == note.html
    assert saved.styled_doc == note.styled_doc
    assert ctl._dirty == {note.id}
    assert ctl._visibility == {note.id: False}
    editor.accept.assert_called_once()


@pytest.mark.parametrize("change", [{"text": "Typing"}, {"html": "<p>Restyled</p>"},
                                    {"is_list": True}, {"list_items": [{"text": "Task"}]}])
def test_concurrent_body_changes_refuse_overwrite_and_keep_draft(change):
    note = KeepNote(id="1.test", title="Old", text="Before")
    ctl, editor = controller(note), draft(note)
    for key, value in change.items():
        setattr(note, key, value)
    AppController._save_dashboard_edit(ctl, editor, {"text": "Draft", "html": "<p>Draft</p>"})
    assert ctl._notes[note.id] is note
    assert ctl._dirty == set()
    editor.show_error.assert_called_once()
    editor.accept.assert_not_called()


def test_title_draft_merges_with_unrelated_remote_body_change():
    note = KeepNote(id="1.test", title="Old", text="Before")
    ctl, editor = controller(note), draft(note)
    note.text = "New from sync"
    AppController._save_dashboard_edit(ctl, editor, {"title": "Draft title"})
    assert ctl._notes[note.id].text == "New from sync"
    assert ctl._notes[note.id].title == "Draft title"
    editor.accept.assert_called_once()


def test_removed_note_cannot_be_recreated_by_stale_editor():
    note = KeepNote(id="1.test", text="Before")
    ctl, editor = controller(note), draft(note)
    ctl._notes.clear()
    AppController._save_dashboard_edit(ctl, editor, {"text": "Draft"})
    assert ctl._notes == {}
    editor.accept.assert_not_called()
    editor.show_error.assert_called_once()


def test_dashboard_only_merge_does_not_leave_widget_baseline_held():
    ctl = SimpleNamespace(windows={}, _notes={"1.test": KeepNote(id="1.test")},
        _pending_widget_refresh={"1.test"}, _refresh_manager_if_open=MagicMock())
    AppController._refresh_window_when_idle(ctl, "1.test")
    assert ctl._pending_widget_refresh == set()
    ctl._refresh_manager_if_open.assert_called_once()


def test_failed_secure_save_restores_model_and_retains_draft():
    note = KeepNote(id="1.test", title="Before")
    ctl, editor = controller(note), draft(note)
    ctl._save_notes_to_disk.side_effect = RuntimeError("synthetic storage failure")
    AppController._save_dashboard_edit(ctl, editor, {"title": "Draft"})
    assert ctl._notes[note.id] is note
    assert note.title == "Before"
    assert ctl._dirty == set()
    editor.accept.assert_not_called()
    editor.show_error.assert_called_once()


def test_visibility_save_failure_keeps_new_draft_uncommitted(monkeypatch):
    note = KeepNote(id=str(uuid.uuid4()))
    ctl, editor = controller(note), draft(note, is_new=True)
    ctl._notes.clear()
    ctl._visibility.clear()
    monkeypatch.setattr("app_controller._save_visibility", MagicMock(side_effect=OSError("synthetic")))
    AppController._save_dashboard_edit(ctl, editor, {"title": "Draft"})
    assert ctl._notes == {}
    assert ctl._visibility == {}
    assert ctl._dirty == set()
    ctl._save_notes_to_disk.assert_not_called()
    editor.show_error.assert_called_once()
    editor.accept.assert_not_called()


@pytest.mark.parametrize("discard", [False, True])
def test_editor_cancel_confirms_unsaved_draft(qapp, monkeypatch, discard):
    from PySide6.QtWidgets import QMessageBox
    editor = NoteEditorDialog(KeepNote(id="1.test", title="Original"))
    try:
        editor.show()
        editor.title_edit.setText("Unsaved")
        monkeypatch.setattr("note_manager.QMessageBox.question", lambda *args:
            QMessageBox.StandardButton.Discard if discard else QMessageBox.StandardButton.Cancel)
        editor.reject()
        assert editor.isVisible() is not discard
    finally:
        editor.accept()
        editor.deleteLater()


def test_dark_checklist_row_is_readable_after_toggle(qapp):
    editor = NoteEditorDialog(KeepNote(id="1.test", is_list=True,
        list_items=[{"text": "Task", "checked": False}]))
    try:
        row = editor.checklist_editor._rows[0]
        assert "#e8eaed" in row.line.styleSheet()
        row.checkbox.setChecked(True)
        assert "#9aa0a6" in row.line.styleSheet()
        assert row.line.font().strikeOut()
    finally:
        editor.accept()
        editor.deleteLater()


def test_new_dashboard_note_does_not_open_floating_window_or_enable_writes(monkeypatch):
    note = KeepNote(id=str(uuid.uuid4()))
    ctl, editor = controller(note), draft(note, is_new=True)
    ctl._notes.clear()
    ctl._visibility.clear()
    monkeypatch.setattr("app_controller._save_visibility", MagicMock())
    AppController._save_dashboard_edit(ctl, editor, {"text": "New draft", "html": "<p>New draft</p>"})
    saved = ctl._notes[note.id]
    assert saved.color_hex == "#FFFFFF"
    assert saved.dark_mode is True
    assert ctl._visibility[note.id] is False
    assert saved.pinned is False
    editor.accept.assert_called_once()


def test_blank_new_draft_is_discarded_without_creating_empty_note():
    note = KeepNote(id=str(uuid.uuid4()))
    ctl, editor = controller(note), draft(note, is_new=True)
    ctl._notes.clear()
    AppController._save_dashboard_edit(ctl, editor, {})
    assert ctl._notes == {}
    ctl._save_notes_to_disk.assert_not_called()


def test_desktop_pin_sets_always_on_top_without_dirtying_google_metadata(monkeypatch):
    note = KeepNote(id="1.test", pinned=False)
    window = MagicMock()
    ctl = SimpleNamespace(_notes={note.id: note}, _visibility={}, windows={},
        _create_window=MagicMock(return_value=window), _regroup_window=MagicMock(), _dirty=set())
    monkeypatch.setattr("app_controller._save_visibility", MagicMock())
    monkeypatch.setattr("app_controller.QTimer.singleShot", MagicMock())
    AppController._on_manager_visibility_changed(ctl, note.id, True)
    window._on_pin_toggle.assert_called_once_with(True)
    window.show.assert_called_once()
    assert note.pinned is False
    assert ctl._dirty == set()
    AppController._on_manager_visibility_changed(ctl, note.id, False)
    window.hide.assert_called_once()
    assert note.pinned is False


def test_icons_are_bundled_vectors(qapp):
    for name in ("menu", "search", "desktop", "pin", "notes", "check", "archive", "trash", "settings"):
        assert not icon(name).isNull()


def test_full_offline_controller_dashboard_edit_and_float_flow(qapp, monkeypatch):
    import app_controller
    engine = MagicMock(is_authenticated=False, writes_enabled=False)
    monkeypatch.setattr("hardened_sync.HardenedKeepSync", lambda **kwargs: engine)
    monkeypatch.setattr(app_controller, "load_config", lambda: {"start_offline": True})
    monkeypatch.setattr(app_controller, "load_notes", lambda: {})
    monkeypatch.setattr(app_controller, "save_notes", MagicMock())
    monkeypatch.setattr(app_controller, "save_config", MagicMock())
    monkeypatch.setattr(app_controller, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(app_controller, "_load_visibility", lambda: {})
    monkeypatch.setattr(app_controller, "_save_visibility", MagicMock())
    monkeypatch.setattr(app_controller, "_force_foreground", MagicMock())
    monkeypatch.setattr(AppController, "_start_initial_session", lambda self: None)
    monkeypatch.setattr(AppController, "_regroup_window", lambda *args: None)
    monkeypatch.setattr("note_window.set_position", MagicMock())
    ctl = AppController()
    try:
        ctl._show_note_manager()
        ctl._open_dashboard_editor()
        editor = ctl._manager_dlg._editor
        assert not ctl._notes
        editor.title_edit.setText("Synthetic note")
        editor.text_edit.insertPlainText("Synthetic text")
        editor.save_requested.emit(editor.changes())
        qapp.processEvents()
        note = next(iter(ctl._notes.values()))
        assert note.title == "Synthetic note"
        assert ctl._visibility[note.id] is False
        assert not ctl.windows
        assert ctl._manager_dlg._editor is None
        ctl._manager_dlg._cards[note.id].desktop.click()
        qapp.processEvents()
        win = ctl.windows[note.id]
        assert win.isVisible()
        assert bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        assert note.pinned is False
        ctl._open_dashboard_editor(note.id)
        editor = ctl._manager_dlg._editor
        editor.title_edit.setText("Edited from dashboard")
        editor.save_requested.emit(editor.changes())
        qapp.processEvents()
        assert win.get_title() == "Edited from dashboard"
        assert win.get_text() == "Synthetic text"
        assert ctl._notes[note.id].text == "Synthetic text"
        engine.create_note.assert_not_called()
        engine.push_note.assert_not_called()
    finally:
        ctl._save_debounce.stop()
        ctl._sync_debounce.stop()
        ctl._sync_timer.stop()
        ctl.tray.hide()
        if ctl._manager_dlg:
            ctl._manager_dlg.close()
        for win in ctl.windows.values():
            win.close()
            win.deleteLater()
        qapp.processEvents()
