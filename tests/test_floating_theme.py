"""Fictional-only floating-note chrome, actions, sizing and refresh regressions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QLineEdit, QPushButton

from app_controller import AppController
from config import KEEP_COLORS
from dashboard_theme import BORDER, FOREGROUND, card_color
from keep_sync_v2 import html_to_styled_doc
from note_model import KeepNote
from note_window import NoteWindow


def settle(qapp):
    for _ in range(16):
        qapp.processEvents()


@pytest.fixture
def floating(qapp, monkeypatch):
    monkeypatch.setattr("note_window.get_position", lambda note_id: None)
    monkeypatch.setattr("note_window.set_position", MagicMock())
    windows = []

    def create(**kwargs):
        options = dict(title="Weekend inspiration", text="A little space for a good idea.",
                       color_hex=KEEP_COLORS["Purple"], dark_mode=True)
        options.update(kwargs)
        win = NoteWindow("fictional-theme", **options)
        windows.append(win)
        win.move(100, 100)
        win.show()
        settle(qapp)
        return win

    yield create
    for win in windows:
        win.close()
        win.deleteLater()
    settle(qapp)


@pytest.mark.parametrize("color", [KEEP_COLORS["White"], KEEP_COLORS["Purple"], KEEP_COLORS["Blue"]])
def test_single_surface_matches_dashboard_without_inherited_chrome_bands(floating, color):
    win = floating(color_hex=color)
    style = win.container.styleSheet()
    assert "QWidget#floatingNoteSurface" in style
    assert "QWidget {" not in style
    assert card_color(color) in style
    assert f"border: 1px solid {BORDER}" in style
    assert FOREGROUND in win.text_edit.styleSheet()
    assert "transparent" in win.title_bar.styleSheet()
    assert "transparent" in win.fmt_toolbar.styleSheet()
    assert win.title_bar.color_btn.parentWidget() is win.action_bar
    assert win.title_bar.color_btn.isVisible()
    assert win.fmt_toolbar.isHidden()


def test_literal_title_wraps_and_retains_native_edit_api_without_body_edits(floating, qapp):
    title = "Weekend <b>ideas</b> & plans"
    win = floating(title=title)
    changed = MagicMock()
    win.note_changed.connect(changed)
    field = win.title_bar.title_edit
    assert isinstance(field, QLineEdit)
    assert win.get_title() == title
    assert field.text() == title
    assert field.heightForWidth(120) > field.heightForWidth(800)
    assert field.heightForWidth(12) <= field.fontMetrics().lineSpacing() * 3 + 4
    win.set_title("A remotely refreshed title")
    settle(qapp)
    changed.assert_not_called()
    field.setFocus()
    field.selectAll()
    QTest.keyClicks(field, "A new title")
    settle(qapp)
    assert win.get_title() == "A new title"
    assert win.windowTitle().startswith("A new title")
    assert win.get_text() == "A little space for a good idea."
    assert changed.called


def test_wrapped_header_refits_when_width_changes_but_never_changes_content(floating, qapp):
    win = floating(title="A thoughtful title for the weekend")
    changed = MagicMock()
    win.note_changed.connect(changed)
    short_width_height = win.title_bar.height()
    win.resize(800, win.height())
    settle(qapp)
    assert win.title_bar.height() < short_width_height
    win.resize(260, win.height())
    settle(qapp)
    assert win.title_bar.height() >= short_width_height
    changed.assert_not_called()


def test_desktop_pin_and_hide_are_local_not_note_content_or_deletion(floating, qapp):
    win = floating()
    changed, hidden, deleted = MagicMock(), MagicMock(), MagicMock()
    win.note_changed.connect(changed)
    win.note_hidden.connect(hidden)
    win.note_deleted.connect(deleted)
    QTest.mouseClick(win.title_bar.pin_btn, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert win.title_bar.pin_btn.isChecked()
    assert win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert "desktop only" in win.title_bar.pin_btn.toolTip()
    QTest.mouseClick(win.title_bar.pin_btn, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert not win.title_bar.pin_btn.isChecked()
    assert not win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    QTest.mouseClick(win.title_bar.close_btn, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert not win.isVisible()
    hidden.assert_called_once_with(win.note_id)
    deleted.assert_not_called()
    changed.assert_not_called()


def test_footer_palette_is_accessible_onscreen_and_keeps_canonical_wire_color(floating, qapp):
    win = floating()
    changed = MagicMock()
    win.note_changed.connect(changed)
    button = win.title_bar.color_btn
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    settle(qapp)
    picker = win.title_bar._color_popup
    assert picker.isVisible()
    assert picker.screen().availableGeometry().contains(picker.geometry())
    changed.assert_not_called()
    swatch = next(b for b in picker.findChildren(QPushButton) if b.toolTip() == "Blue (dark)")
    QTest.mouseClick(swatch, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert not picker.isVisible()
    assert win.color_hex == KEEP_COLORS["Blue"]
    assert win.dark_mode is True
    changed.assert_called_once_with(win.note_id)


def test_formatting_expands_only_on_request_and_refits_without_a_content_edit(floating, qapp):
    win = floating()
    changed = MagicMock()
    win.note_changed.connect(changed)
    small_height = win.height()
    QTest.mouseClick(win.format_btn, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert win.fmt_toolbar.isVisible()
    assert win.format_btn.isChecked()
    assert win.height() > small_height
    QTest.mouseClick(win.format_btn, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert win.fmt_toolbar.isHidden()
    assert win.height() == small_height
    changed.assert_not_called()


def test_expanding_formatting_keeps_selection_and_format_actions_still_edit(floating, qapp):
    win = floating(text="A good idea")
    win.text_edit.setFocus()
    cursor = win.text_edit.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
    win.text_edit.setTextCursor(cursor)
    QTest.mouseClick(win.format_btn, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert win.text_edit.textCursor().selectedText() == "A good"
    changed = MagicMock()
    win.note_changed.connect(changed)
    QTest.mouseClick(win.fmt_toolbar.bold_btn, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert win.get_text() == "A good idea"
    cursor = QTextCursor(win.text_edit.document())
    cursor.setPosition(2)
    assert cursor.charFormat().fontWeight() >= QFont.Weight.Bold
    assert changed.called


@pytest.mark.parametrize("representation", ["text", "html", "styled", "checklist"])
def test_sync_refresh_preserves_formatting_panel_preference(floating, qapp, representation):
    win = floating()
    note = KeepNote(id=win.note_id, title="Fresh fictional title", text="Fresh text")
    if representation == "html":
        note.html = "<p><b>Fresh text</b></p>"
    elif representation == "styled":
        note.styled_doc = html_to_styled_doc("<p><b>Fresh text</b></p>")
    elif representation == "checklist":
        note.is_list = True
        note.list_items = [{"text": "Fresh item", "checked": False}]
    controller = SimpleNamespace(windows={note.id: win}, _notes={note.id: note},
                                 _rendered_doc={}, _pending_widget_refresh={note.id})
    changed = MagicMock()
    win.note_changed.connect(changed)
    before = deepcopy(note)
    for expanded in (False, True, False):
        win.set_formatting_expanded(expanded)
        AppController._refresh_window(controller, note.id)
        settle(qapp)
        assert win.fmt_toolbar.isVisible() == (expanded and not note.is_list)
        assert win.format_btn.isVisible() == (not note.is_list)
        assert win._formatting_expanded == expanded
        assert win.char_count.text() == "10 chars"
        assert note == before
    changed.assert_not_called()


def test_checklist_transition_does_not_discard_formatting_preference(floating, qapp):
    win = floating(list_items=[{"text": "A small task", "checked": False}])
    assert win.char_count.text() == "12 chars"
    win.set_formatting_expanded(True)
    assert win.fmt_toolbar.isHidden()
    assert win.format_btn.isHidden()
    win._toggle_checklist_mode()
    settle(qapp)
    assert win.fmt_toolbar.isVisible()
    assert win.get_text() == "A small task"
    win._toggle_checklist_mode()
    settle(qapp)
    assert win.fmt_toolbar.isHidden()
    assert win.get_list_items()[0]["text"] == "A small task"


def test_more_actions_keep_hide_and_delete_distinct_and_offer_no_type_conversion(floating, qapp):
    win = floating()
    changed, hidden, deleted = MagicMock(), MagicMock(), MagicMock()
    win.note_changed.connect(changed)
    win.note_hidden.connect(hidden)
    win.note_deleted.connect(deleted)
    menu = win._note_actions_menu()
    actions = {a.text(): a for a in menu.actions() if not a.isSeparator()}
    assert set(actions) == {"Hide desktop note", "Fit height to content", "Delete note"}
    assert "#303134" in menu.styleSheet()
    actions["Fit height to content"].trigger()
    settle(qapp)
    assert not win._auto_size
    actions["Hide desktop note"].trigger()
    hidden.assert_called_once_with(win.note_id)
    deleted.assert_not_called()
    actions["Delete note"].trigger()
    deleted.assert_called_once_with(win.note_id)
    changed.assert_not_called()
    menu.deleteLater()


def test_grip_and_wrapped_header_actions_are_not_covered_by_resize_handles(floating, qapp):
    win = floating(title="A longer fictional title that wraps across several lines")
    win.set_auto_size(False)
    win.resize(260, 480)
    settle(qapp)
    win._position_resize_handles()
    grip_point = win.title_drag_sliver.mapTo(win, QPoint(win.title_drag_sliver.width() // 2, 6))
    assert win.childAt(grip_point) is win.title_drag_sliver
    for button in (win.title_bar.pin_btn, win.title_bar.close_btn, win.title_bar.color_btn):
        point = button.mapTo(win, button.rect().center())
        assert win.childAt(point) is button
    header_bottom = win.title_bar.geometry().bottom()
    assert win._resize_handles["w"].y() > header_bottom
    assert win._resize_handles["e"].y() > header_bottom
    assert win.childAt(QPoint(1, header_bottom + 12)) is win._resize_handles["w"]


def test_restyling_colors_and_local_mode_never_emit_content_changes(floating, qapp):
    win = floating(html="<p><b>A good idea</b> and a little text.</p>")
    changed = MagicMock()
    win.note_changed.connect(changed)
    text = win.get_text()
    doc = html_to_styled_doc(win.get_html())
    for dark in (False, True):
        win.set_dark_mode(dark)
        for color in KEEP_COLORS.values():
            win._apply_color(color)
            settle(qapp)
            assert win.get_text() == text
            assert html_to_styled_doc(win.get_html()) == doc
    changed.assert_not_called()
