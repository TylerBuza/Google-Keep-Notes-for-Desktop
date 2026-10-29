"""Native Keep-style dashboard and isolated edit drafts; no browser or networking."""

from copy import deepcopy
import uuid

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QWidget, QFrame, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QHBoxLayout, QStackedWidget, QSizePolicy, QMenu, QMessageBox,
    QComboBox,
)

from config import KEEP_COLORS
from app_icon import make_icon
from keep_brand import keep_logo
from dashboard_theme import STYLE, BACKGROUND, MUTED, ACCENT, card_color, icon
from note_window import NoteTextEdit, ChecklistEditor, FormattingToolbar


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

    def __init__(self, note, *, is_new=False, parent=None):
        super().__init__(parent)
        self.setObjectName("noteEditor")
        self.setStyleSheet(STYLE)
        self.setWindowTitle("New note" if is_new else "Edit note")
        self.setWindowIcon(make_icon())
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(600, 460)
        self.setMinimumSize(380, 300)
        self.note_id = note.id
        self.base = edit_snapshot(note)
        self.is_new = is_new
        self._is_list = note.is_list
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        self.title_edit = QLineEdit(note.title)
        self.title_edit.setPlaceholderText("Title")
        self.title_edit.setAccessibleName("Note title")
        self.title_edit.setStyleSheet("font-size: 20px; padding: 4px 8px;")
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
            "padding: 8px; selection-color: #ffffff; selection-background-color: #394b68; }")
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
        self.toolbar = FormattingToolbar(self.text_edit, "#FFFFFF", self)
        self.toolbar.set_dark(True)
        self.toolbar.setVisible(not note.is_list)
        layout.addWidget(self.toolbar)
        self.error = plain_label()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #f28b82;")
        self.error.hide()
        layout.addWidget(self.error)
        footer = QHBoxLayout()
        hint = plain_label("Save stores locally · cloud writes require separate opt-in")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        footer.addWidget(hint, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(lambda: self.save_requested.emit(self.changes()))
        footer.addWidget(save)
        layout.addLayout(footer)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=save.click)

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

    def reject(self):
        if self.changes():
            answer = QMessageBox.question(self, "Discard draft?", "Discard your unsaved changes?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()


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
        self.title.setStyleSheet("font-size: 16px; font-weight: 500;")
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
        self.body.setStyleSheet("font-size: 14px;")
        self.body.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.body)
        self.labels = plain_label()
        self.labels.setWordWrap(True)
        self.labels.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.labels.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.labels)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        palette = icon_button("palette", "Change note color", self)
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

    def __init__(self, notes, visibility, font_scale=1.0, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboard")
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
        self._editor = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        header.setStyleSheet("QFrame#header { border-bottom: 1px solid #5f6368; }")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(16, 8, 20, 8)
        bar.setSpacing(12)
        menu = icon_button("menu", "Toggle navigation")
        menu.clicked.connect(lambda: self._sidebar.setVisible(not self._sidebar.isVisible()))
        bar.addWidget(menu)
        brand_icon = QLabel()
        brand_icon.setObjectName("keepLogo")
        brand_icon.setAccessibleName("Google Keep logo")
        brand_icon.setPixmap(keep_logo().pixmap(QSize(36, 36), self.devicePixelRatioF()))
        bar.addWidget(brand_icon)
        brand = plain_label("Keep Desktop")
        brand.setObjectName("brandLabel")
        brand.setStyleSheet("font-size: 23px;")
        brand.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
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
        self._label_layout = QVBoxLayout(self._label_host)
        self._label_layout.setContentsMargins(0, 8, 0, 8)
        self._label_layout.setSpacing(0)
        self._nav_layout.addWidget(self._label_host)
        for text, name in (("Archive", "archive"), ("Trash", "trash")):
            button = self._add_nav(name, text, name)
            button.setEnabled(False)
            button.setToolTip("Not available yet — requires additional safe sync support")
        self._nav_layout.addStretch()
        body.addWidget(self._sidebar)
        self._pages = QStackedWidget()
        body.addWidget(self._pages, 1)
        notes_page = QWidget()
        page = QVBoxLayout(notes_page)
        page.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
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
        self._empty.setStyleSheet(f"color: {MUTED}; font-size: 20px; padding: 60px 12px;")
        content.addWidget(self._empty)
        content.addStretch()
        scroll.setWidget(scroll_host)
        page.addWidget(scroll)
        self._pages.addWidget(notes_page)
        self._pages.addWidget(self._build_settings_page(font_scale))
        root.addLayout(body, 1)
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
        label.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 600; padding-left: 8px;")
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
        heading.setStyleSheet("font-size: 22px;")
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
                           "Archive, Trash browsing, and label editing are not available yet. "
                           "Existing labels, when present in the local model, are read-only.\n\n"
                           "Cloud-write opt-in and account controls remain in the tray menu.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(info)
        layout.addStretch()
        return page

    def set_sync_state(self, authenticated, writes_enabled):
        if not authenticated:
            text = "Offline · edits are saved locally"
        elif not writes_enabled:
            text = "Cloud writes off · edits are saved locally · enable writes separately in the tray menu"
        else:
            text = "Cloud writes enabled · changes use guarded Keep sync"
        self._status.setText(text)

    def _show_settings(self):
        self._pages.setCurrentIndex(1 - self._pages.currentIndex())

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

    def refresh(self, notes, visibility):
        self._all_notes = dict(notes)
        # Controller state is authoritative, including closing a floating note
        # outside this dashboard. Filtering must never reset its visibility.
        self.result_visibility = {nid: visibility.get(nid, False) for nid in notes}
        names = sorted({label for note in notes.values() for label in note.labels
                        if isinstance(label, str) and label})
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
        for nid in list(self._cards):
            if nid not in notes:
                card = self._cards.pop(nid)
                card.hide()
                card.deleteLater()
        query = self._search_text.strip().casefold()
        matches = []
        for note in notes.values():
            visible = self.result_visibility[note.id]
            match = not note.trashed and (not query or query in
                (note.title + "\n" + note.text + "\n" + "\n".join(
                    label for label in note.labels if isinstance(label, str))).casefold())
            if self._view == "desktop":
                match = match and visible
            elif self._view.startswith("label:"):
                match = match and self._view[6:] in note.labels
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
        pinned = [self._cards[n.id] for n in matches if n.pinned]
        others = [self._cards[n.id] for n in matches if not n.pinned]
        self._pinned_grid.set_cards(pinned, self._list_view)
        self._others_grid.set_cards(others, self._list_view)
        self._pinned_label.setVisible(bool(pinned))
        self._pinned_grid.setVisible(bool(pinned))
        self._others_label.setVisible(bool(pinned and others))
        self._others_grid.setVisible(bool(others))
        self._empty.setVisible(not matches)
        self._empty.setText("No matching notes" if query else
            "Pin a note to your desktop to see it here" if self._view == "desktop" else "Notes you add appear here")

    def _on_desktop_toggle(self, note_id, visible):
        self.result_visibility[note_id] = visible
        self.visibility_changed.emit(note_id, visible)
        self.refresh(self._all_notes, self.result_visibility)

    def set_checkbox(self, note_id, checked):
        """Compatibility with floating-window hide notifications; no signal echo."""
        self.result_visibility[note_id] = checked
        self.refresh(self._all_notes, self.result_visibility)

    def _delete_note(self, note_id):
        answer = QMessageBox.question(self, "Delete note?",
            "Delete this note? A synced note is moved to Google Keep’s Trash only "
            "when cloud writes are enabled. The note stays here until deletion succeeds.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.note_deleted.emit(note_id)

    def _show_color_menu(self, note_id, widget, pos):
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
