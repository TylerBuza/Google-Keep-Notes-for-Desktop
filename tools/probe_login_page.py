"""Anonymous HTTPS/origin probe. Never enters credentials or exchanges tokens."""

import json
import os
from pathlib import Path
import sys
import tempfile
import faulthandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    faulthandler.enable()
    faulthandler.dump_traceback_later(25, exit=True)
    with tempfile.TemporaryDirectory(prefix="keepdesktop-public-login-", dir=os.environ.get("KEEPDESKTOP_TEST_TEMP")) as directory:
        os.environ["APPDATA"] = directory
        headed = "--headed" in sys.argv
        os.environ["QT_QPA_PLATFORM"] = "windows" if headed else "offscreen"
        # Headless Windows graphics only; do not weaken sandbox/TLS or hide
        # automation from Google. Production login uses the normal Qt backend.
        if not headed:
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-software-rasterizer"
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from app_controller import LoginDialog
        from login_security import allowed_https_url

        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        dialog = LoginDialog(first_launch=True)
        profile = dialog._profile
        page = dialog._browser.page()
        dialog.setWindowTitle("Anonymous login security check — input disabled")
        dialog.setEnabled(False)
        # No sync engine is attached. A token exchange cannot run even if a
        # cookie were unexpectedly issued to this brand-new anonymous profile.
        done = False
        result = {}

        def finish(loaded):
            nonlocal done, result
            if done:
                return
            done = True
            url = dialog._browser.url()
            result = {
                "anonymous_https_page_loaded": bool(loaded and allowed_https_url(url)),
                "origin": f"{url.scheme()}://{url.host()}",
                "off_the_record": dialog._profile.isOffTheRecord(),
                "remember_default": dialog._remember.isChecked(),
                "credentials_entered": False, "sync_engine_attached": False,
                "authenticated_login_tested": False,
            }
            dialog.reject()
            dialog.deleteLater()
            QTimer.singleShot(250, app.quit)

        dialog._browser.loadFinished.connect(finish)
        QTimer.singleShot(15000, lambda: finish(False))
        dialog._risk_consent.setChecked(True)
        dialog.show()
        dialog._begin_signin()
        app.exec()
        import shiboken6
        result["page_closed_cleanly"] = not shiboken6.isValid(page)
        result["profile_closed_cleanly"] = not shiboken6.isValid(profile)
        shiboken6.delete(app)
        print(json.dumps(result, indent=2))
        faulthandler.cancel_dump_traceback_later()
        return 0 if result.get("anonymous_https_page_loaded") else 1


if __name__ == "__main__":
    sys.exit(main())
