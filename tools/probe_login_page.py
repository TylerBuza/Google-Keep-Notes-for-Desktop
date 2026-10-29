"""Anonymous Google-page check; optional synthetic hand-off, never account login."""

import faulthandler
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class SyntheticSync:
    """No HTTP, storage, or note APIs. Only exercises the dialog's worker path."""

    is_authenticated = False
    last_auth_failure = None

    def exchange_oauth_for_master(self, email, token):
        assert email == "synthetic@example.test" and token == "oauth2_4/synthetic-test-only"
        time.sleep(0.1)  # Synthetic worker latency, not a wait for an external job.
        return {"Email": email, "Token": "synthetic-master", "AndroidId": "synthetic-device"}

    def login(self, email, **kwargs):
        assert email == "synthetic@example.test" and kwargs["master_token"] == "synthetic-master"
        self.is_authenticated = not kwargs["cancel_event"].is_set()
        return self.is_authenticated

    def logout(self):
        self.is_authenticated = False


def main():
    faulthandler.enable()
    faulthandler.dump_traceback_later(25, exit=True)
    with tempfile.TemporaryDirectory(prefix="keepdesktop-public-login-", dir=os.environ.get("KEEPDESKTOP_TEST_TEMP")) as directory:
        os.environ["APPDATA"] = directory
        os.environ["QT_QPA_PLATFORM"] = "windows" if "--headed" in sys.argv else "offscreen"
        from network_policy import configure_browser_network
        configure_browser_network()
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtWidgets import QApplication
        from app_controller import LoginDialog, EMBEDDED_SETUP_URL
        from login_security import allowed_https_url

        simulated = "--simulate-handoff" in sys.argv
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        dialog = LoginDialog(first_launch=True)
        profile, page = dialog._profile, dialog._browser.page()
        dialog.setWindowTitle("Anonymous sign-in check — input disabled")
        dialog.setEnabled(False)
        if simulated:
            dialog.set_keep_sync(SyntheticSync())
        done = False
        result = {}
        ticks = []
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(True))

        def close_dialog(result_code):
            result["synthetic_handoff_completed"] = bool(simulated and dialog.signed_in)
            result["gui_heartbeat_during_handoff"] = bool(ticks) if simulated else None
            result["blocked_requests"] = dialog._interceptor.blocked_requests()
            dialog.deleteLater()
            QTimer.singleShot(500, app.quit)

        dialog.finished.connect(close_dialog)

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
                "remember_mode": "checkbox-consent",
                "allowed_resource_hosts_observed": dialog._interceptor.observed_hosts(),
                "credentials_entered": False, "real_sync_engine_attached": False,
                "authenticated_login_tested": False, "synthetic_handoff_requested": simulated,
            }
            if simulated and result["anonymous_https_page_loaded"]:
                heartbeat.start()
                # A dummy value goes only to SyntheticSync above, never Google.
                dialog._on_token_found("oauth2_4/synthetic-test-only")
            else:
                dialog.reject()

        dialog._browser.loadFinished.connect(finish)
        QTimer.singleShot(15000, lambda: finish(False))
        dialog.show()
        if simulated:
            dialog._account_email.setText("synthetic@example.test")
            dialog._risk_consent.setChecked(True)
            dialog._begin_signin()
        else:
            # Anonymous page only, without accepting persistent storage consent.
            dialog._browser.show()
            dialog.resize(560, 820)
            dialog._login_started = True
            dialog._browser.setUrl(QUrl(EMBEDDED_SETUP_URL))
        app.exec()
        heartbeat.stop()
        import shiboken6
        result["page_closed_cleanly"] = not shiboken6.isValid(page)
        result["profile_closed_cleanly"] = not shiboken6.isValid(profile)
        print(json.dumps(result, indent=2), flush=True)
        shiboken6.delete(app)
        print("Anonymous application shutdown completed.", flush=True)
        faulthandler.cancel_dump_traceback_later()
        required = ["anonymous_https_page_loaded", "page_closed_cleanly", "profile_closed_cleanly"]
        if simulated:
            required += ["synthetic_handoff_completed", "gui_heartbeat_during_handoff"]
        return 0 if all(result.get(key) for key in required) else 1


if __name__ == "__main__":
    sys.exit(main())
