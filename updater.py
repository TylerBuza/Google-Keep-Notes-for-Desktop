"""No-network compatibility API for the private, Google-only desktop build.

There is deliberately no updater HTTP/download/installer-launch code to
re-enable through a preference or flag. Builds are installed separately.
"""

from PySide6.QtCore import QObject, Signal


def fetch_latest_release(timeout=10.0):
    return None


def download_installer(url, progress_cb=None):
    return None


def run_installer_and_quit(installer_path):
    return False


class UpdateChecker(QObject):
    update_available = Signal(object)
    no_update = Signal()
    error = Signal(str)

    def check_async(self):
        self.no_update.emit()


def prompt_and_install(parent, info):
    return None
