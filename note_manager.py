"""Native Keep-style dashboard and isolated edit drafts; no browser or networking."""

from copy import deepcopy
import uuid

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QWidget, QFrame, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QHBoxLayout, QStackedWidget, QSizePolicy, QMenu, QMessageBox,
    QComboBox, QTextEdit,
)

from config import KEEP_COLORS
from app_icon import make_icon
from keep_brand import keep_logo
from dashboard_theme import STYLE, BACKGROUND, MUTED, ACCENT, card_color, icon
from note_window import NoteTextEdit, ChecklistEditor, FormattingToolbar
from ui_typography import crisp_font
from content_sizing import body_height, chrome_height


def plain_label(text="", parent=None):
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def icon_button(name, tooltip, parent=None):
    button = QPushButton(parent)
    button.setAutoDefault(False)
    button.setProperty("iconButton", True)
    button.setIcon(icon(name))
    button.setIconSize(QSize(20, 20))
    button.setFixedSize(36, 36)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def edit_snapshot(note):
    return deepcopy({key: getattr(note, key) for key in
                     ("title", "text", "html", "is_list", "list_items")})


class NoteEditorDialog(QDialog):
    """Draft-only editor. The controller validates concurrent edits before saving."""

    save_requested = Signal(object)
    dismiss_requested = Signal()
    content_size_changed = Signal()

    def __init__(self, note, *, is_new=False, embedded=False, parent=None):
        super().__init__(parent)
        self._embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setObjectName("noteEditor")
        self.setFont(crisp_font(self.font()))
        self._display_color = card_color(note.color_hex)
        self.setStyleSheet(STYLE + f"QDialog#noteEditor {{ background: {self._display_color};"
                          "border: 1px solid #5f6368; border-radius: 8px; }")
        self.setWindowTitle("New note" if is_new else "Edit note")
        self.setWindowIcon(make_icon())
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(600, 220)
        self.setMinimumSize(380, 180)
        self.note_id = note.id
        self.base = edit_snapshot(note)
        self.is_new = is_new
        self._is_list = note.is_list
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        self.title_edit = QLineEdit(note.title)
        self.title_edit.setPlaceholderText("Title")
        self.title_edit.setAccessibleName("Note title")
        self.title_edit.setStyleSheet("font-family: 'Google Sans'; font-size: 22px; font-weight: 500; padding: 4px 8px;")
        layout.addWidget(self.title_edit)

        self.text_edit = NoteTextEdit(self)
        # Use the same resource-blocking document and formatted renderer as
        # floating notes. Never render note HTML in WebEngine or QLabel.
        if getattr(note, "styled_doc", None) is not None:
            self.text_edit.set_styled_doc(note.styled_doc)
        elif note.html:
            self.text_edit.setHtml(note.html)
        else:
            self.text_edit.setPlainText(note.text)
        self.text_edit.setStyleSheet(
            "QTextEdit { background: transparent; color: #e8eaed; border: none;"
            "font-size: 17px; padding: 8px; selection-color: #ffffff; selection-background-color: #394b68; }")
        self.text_edit.setPlaceholderText("Take a note…")
        self.text_edit.setAccessibleName("Note text")
        self._rendered_body = (self.text_edit.toPlainText(), self.text_edit.toHtml())
        layout.addWidget(self.text_edit, 1)

        self.checklist_editor = ChecklistEditor(self)
        self.checklist_editor.set_dark(True)
        self.checklist_editor.set_items(deepcopy(note.list_items))
        self._rendered_items = self.checklist_editor.get_items()
        layout.addWidget(self.checklist_editor, 1)
        self.checklist_editor.setVisible(note.is_list)
        self.text_edit.setVisible(not note.is_list)
        self.toolbar = FormattingToolbar(self.text_edit, self._display_color, self)
        self.toolbar.set_dark(True)
        self.toolbar.setVisible(not note.is_list)
        layout.addWidget(self.toolbar)
        self.error = plain_label()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #f28b82;")
        self.error.hide()
        layout.addWidget(self.error)
        footer = QHBoxLayout()
        hint = plain_label("Click outside or Close to save locally · two-way sync is on by default"
                           if embedded else "Save stores locally · two-way sync follows your tray setting")
        self._save_hint = hint
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        footer.addWidget(hint, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = QPushButton("Close" if embedded else "Save")
        save.setDefault(True)
        if embedded:
            save.clicked.connect(self.dismiss_requested.emit)
        else:
            save.clicked.connect(lambda: self.save_requested.emit(self.changes()))
        footer.addWidget(save)
        layout.addLayout(footer)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=save.click)
        self.text_edit.document().documentLayout().documentSizeChanged.connect(
            lambda size: self.content_size_changed.emit())
        self.checklist_editor.changed.connect(self.content_size_changed.emit)
        self.title_edit.textChanged.connect(lambda text: self.content_size_changed.emit())

    def fit_height(self, maximum):
        body = self.checklist_editor if self._is_list else self.text_edit
        chrome = chrome_height(self.layout(), body, self.width())
        height = min(maximum, max(180, chrome + body_height(body)))
        body.setFixedHeight(max(32, height - chrome))
        self.setFixedHeight(height)

    def set_sync_state(self, authenticated, writes_enabled):
        action = "Click outside or Close to save" if self._embedded else "Save changes"
        self._save_hint.setText(action + (" · two-way sync on" if authenticated and writes_enabled
                                          else " locally · web writes off"))
        self.content_size_changed.emit()

    def changes(self):
        changes = {}
        if self.title_edit.text() != self.base["title"]:
            changes["title"] = self.title_edit.text()
        if self._is_list:
            items = self.checklist_editor.get_items()
            if items != self._rendered_items:
                changes["list_items"] = items
                changes["text"] = "\n".join(
                    ("☑ " if item["checked"] else "☐ ") + item["text"] for item in items)
                changes["html"] = ""
        elif (self.text_edit.toPlainText(), self.text_edit.toHtml()) != self._rendered_body:
            changes["text"] = self.text_edit.toPlainText()
            changes["html"] = self.text_edit.toHtml()
        return changes

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()
        self.content_size_changed.emit()

    def reject(self):
        if self.changes():
            answer = QMessageBox.question(self, "Discard draft?", "Discard your unsaved changes?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()

    def close_for_navigation(self):
        """Leave a bubble only after an explicit save/discard decision."""
        changes = self.changes()
        if changes:
            answer = QMessageBox.question(self, "Leave this note?", "Save your changes before leaving this note?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Save:
                self.save_requested.emit(changes)
                # A failed secure save or concurrent-edit conflict keeps the
                # draft visible. Do not navigate away or suppress that error.
                return not self.isVisible()
            if answer != QMessageBox.StandardButton.Discard:
                self.raise_()
                self.activateWindow()
                return False
        super().reject()  # Clean draft or explicitly discarded, no second prompt.
        return True

    def keyPressEvent(self, event):
        if self._embedded and event.key() == Qt.Key.Key_Escape:
            self.dismiss_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class NoteEditorOverlay(QWidget):
    """A dimmed in-app backdrop, not a native top-level note window."""

    dismiss_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("editorBackdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#editorBackdrop { background: rgba(0,0,0,0.50); }")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.editor = None
        self._pressed_outside = False
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(24, 24, 24, 24)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_editor)

    def set_editor(self, editor):
        self.editor = editor
        self._layout.addWidget(editor, 0, Qt.AlignmentFlag.AlignCenter)
        editor.dismiss_requested.connect(self.dismiss_requested.emit)
        editor.content_size_changed.connect(lambda: self._fit_timer.start(0))
        self._fit_editor()

    def _fit_editor(self):
        if self.editor is not None:
            self.editor.setFixedWidth(min(600, max(380, self.width() - 48)))
            self.editor.fit_height(max(180, self.height() - 48))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_editor()

    def mousePressEvent(self, event):
        self._pressed_outside = (event.button() == Qt.MouseButton.LeftButton and
            self.editor is not None and not self.editor.geometry().contains(event.position().toPoint()))
        event.accept()

    def mouseReleaseEvent(self, event):
        outside = (self.editor is not None and
                   not self.editor.geometry().contains(event.position().toPoint()))
        close = event.button() == Qt.MouseButton.LeftButton and self._pressed_outside and outside
        self._pressed_outside = False
        if close:
            self.dismiss_requested.emit()
        event.accept()


class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoteCard(QFrame):
    opened = Signal(str)
    pin_requested = Signal(str)
    desktop_requested = Signal(str, bool)
    menu_requested = Signal(str, object, object)
    color_requested = Signal(str, object, object)

    def __init__(self, note, visible, parent=None):
        super().__init__(parent)
        self.setObjectName("noteCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.note_id = note.id
        self._desktop = visible
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.menu_requested.emit(self.note_id, self, pos))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 6)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.title = plain_label()
        self.title.setWordWrap(True)
        self.title.setMaximumHeight(72)
        self.title.setMinimumWidth(0)
        self.title.setStyleSheet("font-family: 'Google Sans'; font-size: 22px; font-weight: 500;")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top.addWidget(self.title, 1)
        # A hidden (untitled) QLabel contributes no stretch. Keep an expanding
        # spacer even then, rather than centering the lone pin in the row.
        top.addStretch()
        self.pin = icon_button("pin", "Pin to top of Keep", self)
        self.pin.clicked.connect(lambda: self.pin_requested.emit(self.note_id))
        top.addWidget(self.pin, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self.body = plain_label()
        self.body.setWordWrap(True)
        self.body.setMaximumHeight(230)
        self.body.setMinimumWidth(0)
        self.body.setStyleSheet("font-size: 17px;")
        self.body.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.body)
        self.labels = plain_label()
        self.labels.setWordWrap(True)
        self.labels.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        self.labels.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.labels)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        palette = icon_button("palette", "Change note color", self)
        self.palette = palette
        palette.clicked.connect(lambda: self.color_requested.emit(
            self.note_id, palette, palette.rect().bottomLeft()))
        actions.addWidget(palette)
        self.desktop = icon_button("desktop", "Pin to desktop (always on top)", self)
        self.desktop.clicked.connect(lambda: self.desktop_requested.emit(self.note_id, not self._desktop))
        actions.addWidget(self.desktop)
        actions.addStretch()
        more = icon_button("more", "More note actions", self)
        more.clicked.connect(lambda: self.menu_requested.emit(self.note_id, more, more.rect().bottomLeft()))
        actions.addWidget(more)
        layout.addLayout(actions)
        self.update_note(note, visible)

    def update_note(self, note, visible):
        self._desktop = visible
        for button in (self.pin, self.desktop, self.palette):
            button.setVisible(not note.archived)
        bg = card_color(note.color_hex)
        self.setStyleSheet(f"QFrame#noteCard {{ background: {bg}; border: 1px solid #5f6368;"
                          "border-radius: 8px; } QFrame#noteCard:hover { border-color: #9aa0a6; }")
        self.title.setText(note.title)
        self.title.setVisible(bool(note.title))
        if note.is_list:
            lines = [("☑  " if item.get("checked") else "☐  ") + item.get("text", "")
                     for item in note.list_items[:10]]
            if len(note.list_items) > 10:
                lines.append(f"+ {len(note.list_items) - 10} more items")
            text = "\n".join(lines)
        else:
            text = note.text[:900]
            if len(note.text) > 900:
                text += "…"
        self.body.setText(text or ("" if note.title else "Empty note"))
        self.body.setVisible(bool(self.body.text()))
        self.labels.setText("  ·  ".join(label for label in note.labels if isinstance(label, str)))
        self.labels.setVisible(bool(self.labels.text()))
        self.pin.setIcon(icon("pin", ACCENT if note.pinned else MUTED))
        self.pin.setToolTip("Unpin from top of Keep" if note.pinned else "Pin to top of Keep")
        self.desktop.setIcon(icon("desktop", ACCENT if visible else MUTED))
        self.desktop.setToolTip("Hide desktop note" if visible else "Pin to desktop (always on top)")
        self.setAccessibleName(note.title or "Untitled note")
        self.updateGeometry()

    def heightForWidth(self, width):
        return max(112, self.layout().totalHeightForWidth(width))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.opened.emit(self.note_id)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.opened.emit(self.note_id)
            event.accept()
        else:
            super().keyPressEvent(event)


class _Masonry(QWidget):
    """Keep's independent-height columns, without rebuilding cards on resize."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = []
        self.list_view = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_cards(self, cards, list_view):
        self.cards = cards
        self.list_view = list_view
        for card in cards:
            if card.parent() is not self:
                card.setParent(self)
            card.show()
        self.relayout()

    def relayout(self):
        gap = 16
        width = max(1, self.width())
        count = 1 if self.list_view else max(1, (width + gap) // 256)
        card_width = (width - gap * (count - 1)) // count
        if self.list_view:
            card_width = min(600, width)
        heights = [0] * count
        for card in self.cards:
            column = min(range(count), key=heights.__getitem__)
            height = card.heightForWidth(card_width)
            x = (width - card_width) // 2 if self.list_view else column * (card_width + gap)
            card.setGeometry(x, heights[column], card_width, height)
            heights[column] += height + gap
        height = max(0, max(heights) - gap)
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()


class NoteManagerDialog(QDialog):
    """Non-modal native dashboard. Desktop visibility never changes Keep pinning."""

    note_deleted = Signal(str)
    note_create_requested = Signal()
    checklist_create_requested = Signal()
    note_edit_requested = Signal(str)
    visibility_changed = Signal(str, bool)
    checklist_toggle_requested = Signal(str)
    pin_toggle_requested = Signal(str)
    color_change_requested = Signal(str, str)
    reorder_requested = Signal(str, str)
    font_scale_changed = Signal(float)
    sync_requested = Signal()
    _FONT_SCALE_PRESETS = [0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]

    def __init__(self, notes, visibility, font_scale=1.0, parent=None, *, cloud_labels=None):
        super().__init__(parent)
        self.setObjectName("dashboard")
        self.setFont(crisp_font(self.font()))
        self.setWindowTitle("KeepDesktop")
        self.setWindowIcon(make_icon())
        self.setStyleSheet(STYLE)
        self.resize(1180, 800)
        self.setMinimumSize(640, 480)
        self.result_visibility = dict(visibility)
        self._all_notes = {}
        self._cards = {}
        self._view = "notes"
        self._search_text = ""
        self._list_view = False
        self._label_buttons = {}
        self._label_names = []
        self._cloud_labels = list(cloud_labels or [])
        self._editor = None
        self._editor_overlay = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        self._header = header
        header.setObjectName("header")
        header.setStyleSheet("QFrame#header { border-bottom: 1px solid #5f6368; }")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(16, 8, 20, 8)
        bar.setSpacing(12)
        menu = icon_button("menu", "Toggle navigation")
        menu.clicked.connect(lambda: self._sidebar.setVisible(not self._sidebar.isVisible()))
        bar.addWidget(menu)
        brand = QPushButton("Keep Desktop")
        brand.setObjectName("brandHome")
        brand.setAutoDefault(False)
        brand.setIcon(keep_logo())
        brand.setIconSize(QSize(36, 36))
        brand.setStyleSheet("QPushButton { font-family: 'Google Sans'; font-size: 23px; padding: 0 6px 0 0; }")
        brand.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        brand.setCursor(Qt.CursorShape.PointingHandCursor)
        brand.setToolTip("Back to Notes")
        brand.setAccessibleName("Keep Desktop — back to Notes")
        brand.clicked.connect(self._return_to_notes)
        self._home_button = brand
        bar.addWidget(brand)
        search_box = QFrame()
        search_box.setObjectName("searchBox")
        search_box.setStyleSheet("QFrame#searchBox { background: #525355; border-radius: 8px; }")
        search = QHBoxLayout(search_box)
        search.setContentsMargins(12, 0, 6, 0)
        search_icon = QLabel()
        search_icon.setPixmap(icon("search").pixmap(20, 20))
        search.addWidget(search_icon)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search")
        self._search_edit.setAccessibleName("Search notes")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumHeight(46)
        self._search_edit.textChanged.connect(self._on_search_changed)
        search.addWidget(self._search_edit)
        bar.addWidget(search_box, 1)
        sync = icon_button("refresh", "Sync now")
        sync.clicked.connect(self.sync_requested.emit)
        bar.addWidget(sync)
        self._layout_button = icon_button("list", "List view")
        self._layout_button.clicked.connect(self._toggle_layout)
        bar.addWidget(self._layout_button)
        settings = icon_button("settings", "Settings")
        settings.clicked.connect(self._show_settings)
        bar.addWidget(settings)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._sidebar = QWidget()
        self._sidebar.setFixedWidth(224)
        self._nav_layout = QVBoxLayout(self._sidebar)
        self._nav_layout.setContentsMargins(0, 12, 8, 0)
        self._nav_layout.setSpacing(0)
        self._nav_buttons = {}
        self._add_nav("notes", "Notes", "notes")
        self._add_nav("desktop", "Desktop pins", "desktop")
        self._label_host = QWidget()
        self._label_host.setObjectName("labelHost")
        self._label_host.setStyleSheet(f"QWidget#labelHost {{ background: {BACKGROUND}; }}")
        self._label_layout = QVBoxLayout(self._label_host)
        self._label_layout.setContentsMargins(0, 8, 0, 8)
        self._label_layout.setSpacing(0)
        self._label_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        label_scroll = QScrollArea()
        label_scroll.setWidgetResizable(True)
        label_scroll.setFrameShape(QFrame.Shape.NoFrame)
        label_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        label_scroll.setWidget(self._label_host)
        label_scroll.viewport().setObjectName("labelViewport")
        label_scroll.viewport().setStyleSheet(f"QWidget#labelViewport {{ background: {BACKGROUND}; }}")
        label_scroll.viewport().setAutoFillBackground(False)
        self._label_scroll = label_scroll
        self._nav_layout.addWidget(label_scroll, 1)
        archive = self._add_nav("archive", "Archive", "archive")
        archive.setToolTip("Cloud archive · read-only; manage archives in the official Keep app")
        trash = self._add_nav("trash", "Trash", "trash")
        trash.setEnabled(False)
        trash.setToolTip("Not available yet — requires additional safe sync support")
        self._nav_layout.addStretch()
        body.addWidget(self._sidebar)
        self._pages = QStackedWidget()
        body.addWidget(self._pages, 1)
        notes_page = QWidget()
        page = QVBoxLayout(notes_page)
        page.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        self._notes_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_host = QWidget()
        scroll_host.setObjectName("scrollHost")
        scroll_host.setStyleSheet(f"QWidget#scrollHost {{ background: {BACKGROUND}; }}")
        content = QVBoxLayout(scroll_host)
        content.setContentsMargins(28, 28, 28, 24)
        content.setSpacing(16)
        composer_row = QHBoxLayout()
        composer_row.addStretch()
        composer = QFrame()
        self._composer = composer
        composer.setObjectName("composer")
        composer.setMaximumWidth(600)
        composer.setStyleSheet("QFrame#composer { border: 1px solid #5f6368; border-radius: 8px; }")
        compose = QHBoxLayout(composer)
        compose.setContentsMargins(8, 4, 8, 4)
        new = QPushButton("Take a note…")
        new.setMinimumHeight(38)
        new.clicked.connect(self.note_create_requested.emit)
        compose.addWidget(new, 1)
        checklist = icon_button("check", "New checklist")
        checklist.clicked.connect(self.checklist_create_requested.emit)
        compose.addWidget(checklist)
        composer_row.addWidget(composer, 6)
        composer_row.addStretch()
        content.addLayout(composer_row)
        content.addSpacing(10)
        self._pinned_label = self._section_label("PINNED")
        content.addWidget(self._pinned_label)
        self._pinned_grid = _Masonry()
        content.addWidget(self._pinned_grid)
        self._others_label = self._section_label("OTHERS")
        content.addWidget(self._others_label)
        self._others_grid = _Masonry()
        content.addWidget(self._others_grid)
        self._empty = plain_label("Notes you add appear here")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(f"color: {MUTED}; font-family: 'Google Sans'; font-size: 20px; padding: 60px 12px;")
        content.addWidget(self._empty)
        content.addStretch()
        scroll.setWidget(scroll_host)
        page.addWidget(scroll)
        self._pages.addWidget(notes_page)
        self._pages.addWidget(self._build_settings_page(font_scale))
        root.addLayout(body, 1)
        self._pin_status = plain_label()
        self._pin_status.setWordWrap(True)
        self._pin_status.setStyleSheet(f"color: {ACCENT}; font-size: 11px; padding: 6px 24px;")
        self._pin_status.hide()
        root.addWidget(self._pin_status)
        self._status = plain_label()
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 11px; padding: 8px 24px;")
        root.addWidget(self._status)
        self.set_sync_state(False, False)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._search_edit.setFocus)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.note_create_requested.emit)
        self.refresh(notes, visibility)

    @staticmethod
    def _section_label(text):
        label = plain_label(text)
        label.setStyleSheet(f"color: {MUTED}; font-size: 14px; font-weight: 600; padding-left: 8px;")
        return label

    def _add_nav(self, key, text, image):
        button = QPushButton(text)
        button.setIcon(icon(image))
        button.setIconSize(QSize(22, 22))
        button.setProperty("nav", True)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self._set_view(key))
        self._nav_layout.addWidget(button)
        self._nav_buttons[key] = button
        return button

    def _build_settings_page(self, font_scale):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 32, 36, 24)
        heading = plain_label("Settings")
        heading.setStyleSheet("font-family: 'Google Sans'; font-size: 22px;")
        layout.addWidget(heading)
        layout.addSpacing(20)
        layout.addWidget(plain_label("Floating note text size"))
        hint = plain_label("Applies immediately to floating desktop notes. The dashboard uses Keep’s dark theme.")
        hint.setStyleSheet(f"color: {MUTED};")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._font_scale_combo = _NoWheelComboBox()
        closest = min(self._FONT_SCALE_PRESETS, key=lambda p: abs(p - font_scale))
        for preset in self._FONT_SCALE_PRESETS:
            self._font_scale_combo.addItem(f"{round(preset * 100)}%", preset)
        self._font_scale_combo.setCurrentIndex(self._FONT_SCALE_PRESETS.index(closest))
        self._font_scale_combo.currentIndexChanged.connect(
            lambda index: self.font_scale_changed.emit(float(self._font_scale_combo.itemData(index))))
        self._font_scale_combo.setMaximumWidth(180)
        layout.addWidget(self._font_scale_combo)
        layout.addSpacing(24)
        info = plain_label("Keep pin: move a note into the Pinned section.\n\n"
                           "Desktop pin: open a floating note above other windows. "
                           "Hide it without changing its Keep pin.\n\n"
                           "Archive browsing and existing cloud labels are read-only. "
                           "Manage archives, labels and Trash in the official Keep app.\n\n"
                           "Two-way sync and account controls remain in the tray menu.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(info)
        layout.addStretch()
        return page

    def set_sync_state(self, authenticated, writes_enabled, writes_requested=False):
        self._authenticated = authenticated
        self._writes_enabled = writes_enabled
        if authenticated and writes_enabled:
            self._pin_status.hide()
        if not authenticated:
            text = "Offline · edits are saved locally"
        elif not writes_enabled:
            text = ("Two-way sync waiting or safety-paused · edits stay queued locally" if writes_requested else
                    "Read-only sync · edits stay local · enable two-way sync in the tray menu to upload")
        else:
            text = "Two-way sync on · text, checklists, Keep pins and colors sync with Google Keep"
        self._status.setText(text)
        if self._editor is not None:
            self._editor.set_sync_state(authenticated, writes_enabled)

    def show_pin_notice(self, message):
        self._pin_status.setText(message)
        self._pin_status.show()

    def _show_settings(self):
        self._pages.setCurrentIndex(1 - self._pages.currentIndex())

    def _return_to_notes(self):
        editor = self._editor
        if editor is not None:
            changes = editor.changes()
            if changes:
                editor.save_requested.emit(changes)
                if editor.isVisible():
                    return  # Secure-save failure/conflict: retain the bubble and draft.
            else:
                editor.accept()
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        self._search_text = ""
        self._set_view("notes")
        self._notes_scroll.verticalScrollBar().setValue(0)

    def create_editor(self, note, *, is_new=False):
        overlay = NoteEditorOverlay(self)
        overlay.setGeometry(self.rect())
        editor = NoteEditorDialog(note, is_new=is_new, embedded=True, parent=overlay)
        editor.set_sync_state(self._authenticated, self._writes_enabled)
        overlay.set_editor(editor)
        overlay.dismiss_requested.connect(self._return_to_notes)
        self._editor = editor
        self._editor_overlay = overlay

        def finished(_result):
            self._editor = None
            self._editor_overlay = None
            overlay.hide()
            for widget in (self._header, self._sidebar, self._pages):
                widget.setEnabled(True)
            overlay.deleteLater()

        editor.finished.connect(finished)
        return editor

    def show_editor(self):
        for widget in (self._header, self._sidebar, self._pages):
            widget.setEnabled(False)
        self._editor_overlay.show()
        self._editor_overlay.raise_()
        self._editor.show()
        if self._editor._is_list:
            self._editor.checklist_editor.focus_first_empty()
        else:
            self._editor.text_edit.setFocus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        overlay = getattr(self, "_editor_overlay", None)
        if overlay is not None:
            overlay.setGeometry(self.rect())

    def reject(self):
        # Non-modal drafts must not disappear behind a closed dashboard.
        if self._editor is not None and not self._editor.close_for_navigation():
            return
        super().reject()

    def _set_view(self, view):
        self._view = view
        self._pages.setCurrentIndex(0)
        self.refresh(self._all_notes, self.result_visibility)

    def _toggle_layout(self):
        self._list_view = not self._list_view
        self._layout_button.setIcon(icon("grid" if self._list_view else "list"))
        self._layout_button.setToolTip("Grid view" if self._list_view else "List view")
        self.refresh(self._all_notes, self.result_visibility)

    def _on_search_changed(self, text):
        self._search_text = text
        self._pages.setCurrentIndex(0)
        self.refresh(self._all_notes, self.result_visibility)

    def refresh(self, notes, visibility, cloud_labels=None):
        self._all_notes = dict(notes)
        if cloud_labels is not None:
            self._cloud_labels = list(cloud_labels)
        # Controller state is authoritative, including closing a floating note
        # outside this dashboard. Filtering must never reset its visibility.
        self.result_visibility = {nid: visibility.get(nid, False) for nid in notes}
        names = sorted({label for note in notes.values() for label in note.labels
                        if isinstance(label, str) and label}
                       | {label for label in self._cloud_labels if isinstance(label, str) and label},
                       key=str.casefold)
        if names != self._label_names:
            for button in self._label_buttons.values():
                self._label_layout.removeWidget(button)
                button.deleteLater()
            self._label_buttons.clear()
            self._label_names = names
            for name in names:
                button = QPushButton(name)
                button.setProperty("nav", True)
                button.setCheckable(True)
                button.setIcon(icon("label"))
                button.clicked.connect(lambda checked=False, label=name: self._set_view("label:" + label))
                self._label_layout.addWidget(button)
                self._label_buttons[name] = button
        if self._view.startswith("label:") and self._view[6:] not in names:
            self._view = "notes"
        for key, button in self._nav_buttons.items():
            button.setChecked(key == self._view)
        for name, button in self._label_buttons.items():
            button.setChecked(self._view == "label:" + name)
        self._label_host.setVisible(bool(names))
        self._label_scroll.setVisible(bool(names))
        self._label_host.ensurePolished()
        self._label_scroll.setMaximumHeight(max(1, self._label_host.sizeHint().height()))
        for nid in list(self._cards):
            if nid not in notes:
                card = self._cards.pop(nid)
                card.hide()
                card.deleteLater()
        query = self._search_text.strip().casefold()
        self._composer.setVisible(self._view != "archive")
        matches = []
        for note in notes.values():
            visible = self.result_visibility[note.id]
            match = not note.trashed and (not query or query in
                (note.title + "\n" + note.text + "\n" + "\n".join(
                    label for label in note.labels if isinstance(label, str))).casefold())
            if self._view == "desktop":
                match = match and visible and not note.archived
            elif self._view == "archive":
                match = match and note.archived
            elif self._view.startswith("label:"):
                match = match and self._view[6:] in note.labels
            elif not query:
                match = match and not note.archived
            card = self._cards.get(note.id)
            if match:
                if card is None:
                    card = NoteCard(note, visible)
                    card.opened.connect(self.note_edit_requested.emit)
                    card.pin_requested.connect(self.pin_toggle_requested.emit)
                    card.desktop_requested.connect(self._on_desktop_toggle)
                    card.menu_requested.connect(self._show_row_menu)
                    card.color_requested.connect(self._show_color_menu)
                    self._cards[note.id] = card
                else:
                    card.update_note(note, visible)
                matches.append(note)
            elif card is not None:
                card.hide()
        matches.sort(key=lambda n: (not n.pinned,
            (0, n.local_order) if n.local_order else (1, -int(n.sort_key or 0))))
        pinned = [self._cards[n.id] for n in matches if n.pinned and not n.archived]
        others = [self._cards[n.id] for n in matches if not n.pinned or n.archived]
        self._pinned_grid.set_cards(pinned, self._list_view)
        self._others_grid.set_cards(others, self._list_view)
        self._pinned_label.setVisible(bool(pinned))
        self._pinned_grid.setVisible(bool(pinned))
        self._others_label.setVisible(bool(pinned and others))
        self._others_grid.setVisible(bool(others))
        self._empty.setVisible(not matches)
        self._empty.setText("No matching notes" if query else
            "Pin a note to your desktop to see it here" if self._view == "desktop" else
            "Your cloud archive is empty" if self._view == "archive" else
            "No notes with this label" if self._view.startswith("label:") else "Notes you add appear here")

    def _on_desktop_toggle(self, note_id, visible):
        note = self._all_notes.get(note_id)
        if note is None or note.archived:
            return
        self.result_visibility[note_id] = visible
        self.visibility_changed.emit(note_id, visible)
        self.refresh(self._all_notes, self.result_visibility)

    def set_checkbox(self, note_id, checked):
        """Compatibility with floating-window hide notifications; no signal echo."""
        self.result_visibility[note_id] = checked
        self.refresh(self._all_notes, self.result_visibility)

    def _delete_note(self, note_id):
        note = self._all_notes.get(note_id)
        if note is None or note.archived:
            return
        answer = QMessageBox.question(self, "Delete note?",
            "Delete this note? A synced note is moved to Google Keep’s Trash only "
            "when cloud writes are enabled. The note stays here until deletion succeeds.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.note_deleted.emit(note_id)

    def _show_color_menu(self, note_id, widget, pos):
        note = self._all_notes.get(note_id)
        if note is None or note.archived:
            return
        menu = QMenu(self)
        for name, color in KEEP_COLORS.items():
            action = menu.addAction("Default" if name == "White" else name)
            action.triggered.connect(lambda checked=False, value=color:
                                     self.color_change_requested.emit(note_id, value))
        menu.exec(widget.mapToGlobal(pos))

    def _show_row_menu(self, note_id, widget, pos):
        note = self._all_notes.get(note_id)
        if note is None:
            return
        menu = QMenu(self)
        if note.archived:
            menu.addAction("View archived note (read-only)", lambda: self.note_edit_requested.emit(note_id))
            menu.exec(widget.mapToGlobal(pos))
            return
        menu.addAction("Edit note", lambda: self.note_edit_requested.emit(note_id))
        menu.addAction("Unpin from Keep" if note.pinned else "Pin to top of Keep",
                       lambda: self.pin_toggle_requested.emit(note_id))
        menu.addAction("Hide desktop note" if self.result_visibility.get(note_id) else "Pin to desktop (always on top)",
                       lambda: self._on_desktop_toggle(note_id, not self.result_visibility.get(note_id, False)))
        menu.addSeparator()
        convert = menu.addAction("Convert to text" if note.is_list else "Convert to checklist",
                                 lambda: self.checklist_toggle_requested.emit(note_id))
        try:
            uuid.UUID(note_id)
        except (ValueError, TypeError, AttributeError):
            convert.setEnabled(False)
            convert.setToolTip("Change existing Google note types in the official Keep app")
        for label, action in (("Move to top", "top"), ("Move up", "up"),
                              ("Move down", "down"), ("Move to bottom", "bottom")):
            menu.addAction(label, lambda checked=False, value=action: self.reorder_requested.emit(note_id, value))
        menu.addSeparator()
        menu.addAction("Delete note", lambda: self._delete_note(note_id))
        menu.exec(widget.mapToGlobal(pos))

    def show_archived_note(self, note, *, pending_edits=False):
        """Read-only, resource-free preview. No archive mutation endpoints."""
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("Archived note — read-only")
        dialog.setWindowIcon(make_icon())
        dialog.setStyleSheet(STYLE)
        dialog.resize(600, 520)
        layout = QVBoxLayout(dialog)
        title = plain_label(note.title or "Untitled note")
        title.setWordWrap(True)
        layout.addWidget(title)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setAcceptRichText(False)
        body.setPlainText(note.text)
        layout.addWidget(body, 1)
        layout.addWidget(plain_label(" · ".join(note.labels)))
        info = plain_label("Cloud archive · read-only. Edit or unarchive this note in Google Keep.")
        if pending_edits:
            info.setText("This copy contains pending local edits. They are retained here, not uploaded. "
                         "View the current archived version in Google Keep.")
        info.setWordWrap(True)
        layout.addWidget(info)
        close = QPushButton("Close")
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        dialog.show()
        return dialog
