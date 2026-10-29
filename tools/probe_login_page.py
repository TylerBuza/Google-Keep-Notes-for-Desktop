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
        from network_policy import configure_browser_network
        configure_browser_network()
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
                "remember_mode": "requires-explicit-confirmation",
                "allowed_resource_hosts_observed": dialog._interceptor.observed_hosts(),
                "credentials_entered": False, "sync_engine_attached": False,
                "authenticated_login_tested": False,
            }
            dialog.reject()
            dialog.deleteLater()
            QTimer.singleShot(250, app.quit)

        dialog._browser.loadFinished.connect(finish)
        QTimer.singleShot(15000, lambda: finish(False))
        dialog.show()
        # Navigate only the anonymous page under the production resource policy.
        # Do not accept saved-credential consent: no sync engine is attached.
        from PySide6.QtCore import QUrl
        from app_controller import EMBEDDED_SETUP_URL
        dialog._login_started = True
        dialog._browser.setUrl(QUrl(EMBEDDED_SETUP_URL))
        app.exec()
        import shiboken6
        result["page_closed_cleanly"] = not shiboken6.isValid(page)
        result["profile_closed_cleanly"] = not shiboken6.isValid(profile)
        print(json.dumps(result, indent=2), flush=True)
        shiboken6.delete(app)
        print("Anonymous application shutdown completed.", flush=True)
        faulthandler.cancel_dump_traceback_later()
        return 0 if all(result.get(key) for key in (
            "anonymous_https_page_loaded", "page_closed_cleanly", "profile_closed_cleanly")) else 1


if __name__ == "__main__":
    sys.exit(main())
