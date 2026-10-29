"""Content-height measurements for native editors, independent of note storage."""

import math

from PySide6.QtWidgets import QTextEdit


def body_height(widget):
    if isinstance(widget, QTextEdit):
        return max(38, math.ceil(widget.document().size().height()) + 16)
    host = widget.widget()
    return max(38, host.sizeHint().height() + 2 * widget.frameWidth())


def chrome_height(layout, body, width):
    margins = layout.contentsMargins()
    parent_margins = layout.parentWidget().contentsMargins()
    height = margins.top() + margins.bottom() + parent_margins.top() + parent_margins.bottom()
    inner_width = max(1, width - margins.left() - margins.right())
    count = 0
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is not None and widget.isHidden():
            continue
        if item.spacerItem() is not None:
            height += item.minimumSize().height()
            continue
        count += 1
        if widget is body:
            continue
        fitted = item.heightForWidth(inner_width) if item.hasHeightForWidth() else -1
        height += fitted if fitted >= 0 else item.sizeHint().height()
    return height + max(0, count - 1) * max(0, layout.spacing())
