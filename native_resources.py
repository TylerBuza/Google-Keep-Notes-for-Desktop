"""Text formatting never needs to read linked files or fetch resources."""

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QTextDocument


class LocalNoteDocument(QTextDocument):
    def loadResource(self, resource_type, name):
        # No remote images/fonts/styles, file/UNC paths, or data/blob resources.
        return QByteArray()
