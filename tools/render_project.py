"""Render README images from native widgets, fictional notes and isolated data."""

import argparse
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/images"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    temp_root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "opencode"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="keep-readme-", dir=temp_root) as data:
        os.environ["APPDATA"] = data
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtGui import QColor, QFont, QImage, QPainter
        from PySide6.QtWidgets import QApplication
        from note_manager import NoteManagerDialog
        from note_window import NoteWindow
        from tools.preview_dashboard import fictional_notes
        from ui_typography import crisp_font

        app = QApplication([])
        app.setFont(crisp_font(QFont("Roboto", 12)))
        samples = fictional_notes()
        dashboard = NoteManagerDialog({n.id: n for n in samples}, {"demo-1": True})
        dashboard.set_sync_state(True, True)  # Fictional state; no controller or connection.
        dashboard.resize(1180, 900)
        dashboard.show()

        text_note = NoteWindow("render-text", title="Weekend inspiration",
                               text="A little space for a good idea.", color_hex="#D7AEFB",
                               dark_mode=True, pinned=True)
        list_note = NoteWindow("render-list", title="A few small things", color_hex="#AECBFA",
                               list_items=[{"text": "Make something useful", "checked": False},
                                           {"text": "Take a screen break", "checked": False},
                                           {"text": "Clear the desk", "checked": True}], dark_mode=True)
        text_note.resize(300, 240)
        list_note.resize(300, 280)
        text_note.show()
        list_note.show()

        def settle():
            for _ in range(16):
                app.processEvents()

        def save(image, name):
            if not image.save(str(output / name)):
                raise RuntimeError("Could not save fictional project render.")

        settle()
        list_note.title_bar.title_edit.clearFocus()
        text_note.text_edit.clearFocus()
        dashboard._search_edit.clearFocus()
        settle()
        dashboard_image = dashboard.grab().toImage()
        text_image = text_note.grab().toImage()
        list_image = list_note.grab().toImage()
        save(dashboard_image, "dashboard.png")

        def draw_scaled(painter, image, x, y, width):
            height = round(image.height() * width / image.width())
            painter.drawImage(QRect(x, y, width, height), image)
            return height

        floating = QImage(800, 340, QImage.Format.Format_ARGB32)
        floating.fill(QColor("#17181b"))
        painter = QPainter(floating)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        draw_scaled(painter, text_image, 30, 45, 350)
        draw_scaled(painter, list_image, 420, 45, 350)
        painter.end()
        save(floating, "floating-notes.png")

        editor = dashboard.create_editor(samples[0])
        dashboard.show_editor()
        settle()
        save(dashboard.grab().toImage(), "editor.png")
        editor.accept()
        settle()

        hero = QImage(1600, 1160, QImage.Format.Format_ARGB32)
        hero.fill(QColor("#17181b"))
        painter = QPainter(hero)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(QColor("#fbbc04"))
        font = crisp_font(QFont("Roboto"))
        font.setPixelSize(16)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        painter.setFont(font)
        painter.drawText(48, 52, "UNOFFICIAL WINDOWS CLIENT")
        font = crisp_font(QFont("Google Sans"), title=True)
        font.setPixelSize(54)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor("#e8eaed"))
        painter.drawText(48, 125, "Your notes. Beyond the browser.")
        font = crisp_font(QFont("Roboto"))
        font.setPixelSize(23)
        painter.setFont(font)
        painter.setPen(QColor("#9aa0a6"))
        painter.drawText(50, 174, "A native dashboard, floating desktop notes, and guarded Google Keep sync.")
        draw_scaled(painter, dashboard_image, 48, 222, 1080)
        draw_scaled(painter, text_image, 1170, 260, 370)
        draw_scaled(painter, list_image, 1170, 535, 370)
        font.setPixelSize(16)
        painter.setFont(font)
        painter.drawText(50, 1120, "Actual native interface · fictional demo notes · not affiliated with Google")
        painter.end()
        save(hero, "overview.png")
        for window in (text_note, list_note, dashboard):
            window.close()
        settle()
        print("Rendered 4 native screenshots with fictional data only.")


if __name__ == "__main__":
    main()
