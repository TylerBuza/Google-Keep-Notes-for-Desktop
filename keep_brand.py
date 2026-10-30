"""Bundled Google Keep artwork for the header, tray, windows and Windows icons.

Source: https://www.gstatic.com/images/branding/product/2x/keep_2020q4_48dp.png
Retrieved 2026-10-29; SHA256:
c95da1279834351ba206716611597a5d9b3a6c0df8fa79ad4f5007e4151fc857

The Google Keep logo is Google's trademark, not this project's AGPL artwork.
Keep Desktop is an unofficial app, not affiliated with or endorsed by Google.
Embedding the original PNG avoids runtime downloads and SVG/resource loading.
"""

import base64
from functools import lru_cache

from PySide6.QtGui import QIcon, QPixmap


_LOGO_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAMAAADVRocKAAAAulBMVEUAAAD/vwD6vAX7vAT8vAP7vATxmgD/vwD7vATymQD7vAT6vAX7vAT7wyD92G3+6rD+8Mj+9+D+78T+6Kj91mX7wRj8yTP+67T//////vv+56T8xSf7vQj94pH//ff923n7vxD+7bz+7sD95Jn8zD/93oX/+OP+9tz+8tD8zkb81F7945X7wBT+9Nj7whz7vQT8uwP6vAX7vQT8vAP/vwD7vAT7vAT5uQb7vQT7vQT7vAT7vAT7vAT/vwAZpl8SAAAAPnRSTlMABGO73/9vGM//v2vv/////////////////////////////////////////////9fbo4NbEOOzKNN7w/NzCElS86kAAAFOSURBVHgBYhiMYBQAaJsDI4AAGAiCIMeh/3q1EJn5bWDXbT+qgWXmvKrH4XBXk7PhqS5nw94PZsPbD2ZD/QskHUg6kHQg6UDSgaQDSQeSDiQdSDqQdCDpQNKBpAOJBf2hPnbrGoFhGIbC8OStFFaYkzLf/2xdyrWLr6z/At9iS3qwHwcYYKDZanc0TTdM6ymA7dAm1/PhQBC6tFcUg4EkpaO8AAkEGZ3kIYGcJMU4oCBZkQ8DdJLmoQCb5LkWCChJkQkCOirAAAGVCtBBAKnSng1UIMBVATUI6KqANgjIVUAMAnoqwAcBoiZpqUABfTlQwADhkKSBwAHDEZ2kB0BAjE8EPcEufatNB+UJX3a3xwAdxMCXA/wPGGCAAQYYYGByEZg+BswuAvPHgMVFYLmaKSXmmzEQBuYc5JtvwU7UsgdLK2vyjLexZRiEYBQAAGNsnogduk1YAAAAAElFTkSuQmCC"
)


@lru_cache(maxsize=1)
def keep_logo():
    """Return the bundled official logo. Requires an existing QApplication."""
    pixmap = QPixmap()
    if not pixmap.loadFromData(base64.b64decode(_LOGO_PNG), "PNG"):
        raise RuntimeError("Bundled Keep logo could not be decoded.")
    return QIcon(pixmap)
