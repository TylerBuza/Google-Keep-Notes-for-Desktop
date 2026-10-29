"""Local-only Keep-style colors and vector icons. No web fonts or resources."""

from functools import lru_cache

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from config import KEEP_COLORS, KEEP_COLORS_DARK

BACKGROUND = "#202124"
FOREGROUND = "#e8eaed"
MUTED = "#9aa0a6"
BORDER = "#5f6368"
ACCENT = "#fbbc04"


def card_color(wire_color):
    name = next((k for k, v in KEEP_COLORS.items()
                 if v.casefold() == str(wire_color).casefold()), "White")
    return BACKGROUND if name == "White" else KEEP_COLORS_DARK[name]


STYLE = """
QDialog { background: #202124; color: #e8eaed; }
QWidget { color: #e8eaed; font-family: 'Segoe UI'; font-size: 16px; }
QLabel { background: transparent; }
QPushButton { background: transparent; border: none; border-radius: 5px;
              padding: 8px 14px; text-align: left; }
QPushButton:hover { background: rgba(255,255,255,0.07); }
QPushButton:focus { border: 1px solid #9aa0a6; }
QPushButton:disabled { color: #6e7074; }
QPushButton[iconButton="true"] { border-radius: 18px; padding: 6px; }
QPushButton[nav="true"] { border-radius: 0; border-top-right-radius: 24px;
                         border-bottom-right-radius: 24px; padding: 12px 22px; }
QPushButton[nav="true"]:checked { background: #41331c; }
QLineEdit { background: transparent; border: none; padding: 8px; }
QLineEdit:focus { border: none; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 8px; background: transparent; }
QScrollBar::handle:vertical { background: #5f6368; min-height: 32px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: #303134; color: #e8eaed; border: 1px solid #5f6368; padding: 5px; }
QMenu::item { padding: 8px 22px; }
QMenu::item:selected { background: #454649; }
QMenu::separator { height: 1px; background: #5f6368; margin: 4px; }
QComboBox { background: #303134; color: #e8eaed; padding: 8px; border: 1px solid #5f6368; }
QComboBox QAbstractItemView { background: #303134; color: #e8eaed; selection-background-color: #41331c; }
QToolTip { background: #3c4043; color: #e8eaed; border: none; padding: 5px; }
"""


@lru_cache(maxsize=64)
def icon(name, color=FOREGROUND):
    """Draw bundled line icons at 2x; never depend on emoji fonts or URLs."""
    pix = QPixmap(48, 48)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(2, 2)
    p.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine,
                 Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

    def lines(points):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        p.drawPath(path)

    if name == "menu":
        for y in (6, 12, 18):
            lines([(4, y), (20, y)])
    elif name == "search":
        p.drawEllipse(QRectF(4, 4, 12, 12))
        lines([(16, 16), (21, 21)])
    elif name == "desktop":
        p.drawRoundedRect(QRectF(3, 4, 18, 13), 1, 1)
        lines([(12, 17), (12, 21)])
        lines([(8, 21), (16, 21)])
    elif name == "pin":
        lines([(9, 3), (15, 3), (14, 9), (18, 13), (18, 15),
               (6, 15), (6, 13), (10, 9), (9, 3)])
        lines([(12, 15), (12, 21)])
    elif name == "check":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 1, 1)
        lines([(8, 12), (11, 15), (17, 8)])
    elif name == "notes":
        p.drawEllipse(QRectF(6, 2, 12, 12))
        lines([(8, 13), (9, 17), (15, 17), (16, 13)])
        lines([(9, 21), (15, 21)])
    elif name == "label":
        lines([(3, 5), (15, 5), (22, 12), (15, 19), (3, 19), (3, 5)])
        p.drawEllipse(QRectF(6, 11, 2, 2))
    elif name == "archive":
        p.drawRect(QRectF(3, 3, 18, 5))
        p.drawRect(QRectF(4, 8, 16, 13))
        lines([(9, 12), (15, 12)])
    elif name == "trash":
        lines([(3, 6), (21, 6)])
        lines([(9, 3), (15, 3)])
        lines([(5, 6), (6, 21), (18, 21), (19, 6)])
        for x in (10, 14):
            lines([(x, 10), (x, 17)])
    elif name == "refresh":
        p.drawArc(QRectF(4, 4, 16, 16), 45 * 16, 290 * 16)
        lines([(20, 3), (20, 10), (13, 10)])
    elif name == "grid":
        for x in (3, 14):
            for y in (3, 14):
                p.drawRect(QRectF(x, y, 7, 7))
    elif name == "list":
        for y in (4, 10, 16):
            p.drawRect(QRectF(3, y, 18, 4))
    elif name == "palette":
        p.drawArc(QRectF(3, 3, 18, 18), 0, 300 * 16)
        lines([(21, 12), (16, 14), (14, 17), (16, 20)])
        for x, y in ((8, 8), (13, 6), (17, 9), (6, 13)):
            p.drawEllipse(QRectF(x, y, 1, 1))
    elif name == "settings":
        for y, x in ((6, 9), (12, 16), (18, 7)):
            lines([(3, y), (21, y)])
            p.setBrush(QColor(BACKGROUND))
            p.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "more":
        for y in (5, 12, 19):
            p.drawEllipse(QRectF(11, y - 1, 2, 2))
    elif name == "close":
        lines([(6, 6), (18, 18)])
        lines([(6, 18), (18, 6)])
    p.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)
