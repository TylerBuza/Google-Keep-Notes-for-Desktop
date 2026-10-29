"""Shared native font rendering: readable sizing without colored subpixel fringes."""

from PySide6.QtGui import QFont


def crisp_font(font):
    result = QFont(font)
    result.setStyleStrategy(QFont.StyleStrategy.PreferAntialias |
                            QFont.StyleStrategy.NoSubpixelAntialias)
    result.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return result
