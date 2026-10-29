from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInfo

from login_security import allowed_https_url, display_address, GuardedGooglePage, AuthRequestInterceptor


@pytest.mark.parametrize("url", [
    "https://accounts.google.com/EmbeddedSetup", "https://accounts.google.com:443/v3/signin",
])
def test_only_google_https_origins_are_allowed(url):
    assert allowed_https_url(url)


@pytest.mark.parametrize("url", [
    "http://accounts.google.com/", "https://evil.test/", "https://accounts.google.com.evil.test/",
    "https://evilaccounts.google.com/", "https://accounts.google.com:444/",
    "https://accounts.google.com@evil.test/", "https://evil@accounts.google.com/",
    "https://accounts.google.com./", "https://accounts%2egoogle.com/",
    "javascript:alert(1)", "data:text/html,hello", "file:///C:/Windows/win.ini",
    "https://accounts.google.com\\@evil.test/", "https://accounts.google.com:invalid/",
    "https://accounts.google.com/\nmalformed", "about:blank",
    "https://@accounts.google.com/", " https://accounts.google.com/",
])
def test_other_origins_schemes_credentials_and_ports_are_rejected(url):
    assert not allowed_https_url(url)


def test_visible_address_never_includes_oauth_parameters():
    url = QUrl("https://accounts.google.com/v3/signin?access_token=dummy-secret#oauth=dummy-secret")
    assert display_address(url) == "https://accounts.google.com/v3/signin"
    assert "dummy-secret" not in display_address(url)


def test_visible_address_encodes_directional_controls():
    assert display_address(QUrl("https://accounts.google.com/\u202eevil")) == (
        "https://accounts.google.com/%E2%80%AEevil")


def test_page_rejects_external_navigation_and_popups():
    page = SimpleNamespace(blocked=MagicMock())
    assert GuardedGooglePage.acceptNavigationRequest(page, QUrl("https://evil.test/"), 0, True) is False
    assert GuardedGooglePage.acceptNavigationRequest(page, QUrl("https://accounts.google.com/"), 0, True) is True
    assert GuardedGooglePage.createWindow(page, 0) is None


def test_certificate_error_cannot_be_bypassed():
    page = SimpleNamespace(blocked=MagicMock())
    error = MagicMock()
    GuardedGooglePage._reject_certificate(page, error)
    error.rejectCertificate.assert_called_once()
    error.acceptCertificate.assert_not_called()


@pytest.mark.parametrize("url,kind,blocked", [
    ("https://accounts.google.com/v3/signin", "ResourceTypeMainFrame", False),
    ("https://www.gstatic.com/script.js", "ResourceTypeScript", False),
    ("https://evil.test/script.js", "ResourceTypeScript", True),
    ("https://www.gstatic.com/frame.html", "ResourceTypeSubFrame", True),
    ("https://www.google.com/recaptcha/api2/anchor", "ResourceTypeSubFrame", False),
    ("https://www.google.com/search", "ResourceTypeSubFrame", True),
    ("https://www.google.com/recaptcha/api2/anchor", "ResourceTypeMainFrame", True),
    ("data:text/html,spoofed-login", "ResourceTypeMainFrame", True),
    ("data:application/javascript,code", "ResourceTypeScript", True),
    ("data:image/png;base64,aaaa", "ResourceTypeImage", False),
])
def test_resource_policy_blocks_external_executable_content(url, kind, blocked):
    request = MagicMock()
    request.requestUrl.return_value = QUrl(url)
    request.firstPartyUrl.return_value = QUrl("https://accounts.google.com/")
    request.resourceType.return_value = getattr(QWebEngineUrlRequestInfo.ResourceType, kind)
    interceptor = AuthRequestInterceptor()
    AuthRequestInterceptor.interceptRequest(interceptor, request)
    assert request.block.called is blocked


def test_login_checkbox_is_the_only_confirmation_and_browser_expands_after_continue(qapp, monkeypatch):
    from app_controller import LoginDialog
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QMessageBox
    navigate = MagicMock()
    monkeypatch.setattr(QWebEngineView, "setUrl", navigate)
    confirm = MagicMock(side_effect=AssertionError("No extra confirmation dialog"))
    monkeypatch.setattr(QMessageBox, "question", confirm)
    dialog = LoginDialog(first_launch=True)
    try:
        assert dialog._profile.isOffTheRecord()
        assert dialog._origin.isReadOnly()
        assert dialog.metaObject().indexOfSlot("_on_cookie(QNetworkCookie)") >= 0
        assert not dialog._remember_login
        assert "Remember on this PC" in dialog._risk_consent.text()
        assert "powerful master token, encrypted" in dialog._risk_consent.text()
        assert not hasattr(dialog, "_warning")
        assert not hasattr(dialog, "_account_email")
        assert "45" not in dialog._retention_details.text()
        assert "mal" + "ware" not in dialog._retention_details.text()
        dialog.show()
        qapp.processEvents()
        compact_height = dialog.height()
        assert compact_height < 350
        assert dialog._browser.isHidden()
        assert dialog._retention_details.isHidden()
        dialog._details_btn.click()
        assert not dialog._retention_details.isHidden()
        dialog._details_btn.click()
        assert dialog._retention_details.isHidden()
        assert not dialog._risk_consent.isChecked()
        navigate.assert_not_called()
        dialog._begin_signin()
        navigate.assert_not_called()
        confirm.assert_not_called()
        dialog._risk_consent.setChecked(True)
        dialog._begin_signin()
        navigate.assert_called_once_with(QUrl("https://accounts.google.com/EmbeddedSetup"))
        confirm.assert_not_called()
        assert dialog._remember_login is True
        assert dialog._risk_consent.isEnabled() is False
        assert not dialog._browser.isHidden()
        assert dialog.height() > compact_height + 250
        assert dialog._continue_btn.isHidden()
        # Address follows the actual allowed page, and never includes secret parameters.
        dialog._on_url_changed(QUrl("https://accounts.google.com/v3/signin?secret=hidden"))
        assert dialog._origin.text() == "https://accounts.google.com/v3/signin"
    finally:
        dialog.reject()
        import shiboken6
        shiboken6.delete(dialog)


def test_old_stored_credentials_do_not_bypass_new_consent():
    from app_controller import AppController
    controller = SimpleNamespace(config={"email": "test@example.test"}, sync=MagicMock())
    AppController._do_login(controller, "test@example.test")
    controller.sync.login.assert_not_called()


def test_cancel_after_token_exchange_still_signs_out():
    import threading
    from app_controller import LoginDialog
    dialog = SimpleNamespace(_browser=MagicMock(), _profile=MagicMock(),
                              _cancelled=threading.Event(), signed_in=True,
                              _keep_sync=MagicMock(is_authenticated=True), _captured_token="dummy",
                              _save_login_diagnostics=MagicMock(), _exchange_timer=MagicMock(),
                              _page_load_timer=MagicMock(), _worker_result_timer=MagicMock())
    LoginDialog._cleanup_signin(dialog, 0)  # Rejected, even though exchange finished.
    assert dialog._cancelled.is_set()
    assert dialog._captured_token is None
    dialog._keep_sync.logout.assert_called_once()
    # Resource enforcement remains in place until actual page destruction.
    dialog._profile.setUrlRequestInterceptor.assert_not_called()


def test_resource_hook_is_detached_only_when_page_is_destroyed():
    from login_security import release_auth_profile
    profile = MagicMock()
    release_auth_profile(profile)
    assert [call[0] for call in profile.method_calls] == ["setUrlRequestInterceptor", "deleteLater"]


def test_cookie_capture_is_disabled_before_consent_and_after_cancellation():
    import threading
    from app_controller import LoginDialog
    cookie = MagicMock()
    cookie.name.return_value = b"oauth_token"
    cookie.domain.return_value = "accounts.google.com"
    cookie.isSecure.return_value = True
    cookie.value.return_value = b"oauth2_4/dummy"
    dialog = SimpleNamespace(_login_started=False, _cancelled=threading.Event(),
                              _captured_token=None, _signals=MagicMock())
    LoginDialog._on_cookie(dialog, cookie)
    dialog._signals.token_found.emit.assert_not_called()
    dialog._login_started = True
    dialog._cancelled.set()
    LoginDialog._on_cookie(dialog, cookie)
    dialog._signals.token_found.emit.assert_not_called()


@pytest.mark.parametrize("kind", list(QWebEngineUrlRequestInfo.ResourceType))
def test_every_resource_category_blocks_non_google_destinations(kind):
    request = MagicMock()
    request.requestUrl.return_value = QUrl("https://not-google.test/resource?token=must-not-log")
    request.firstPartyUrl.return_value = QUrl("https://accounts.google.com/")
    request.resourceType.return_value = kind
    interceptor = AuthRequestInterceptor()
    messages = []
    interceptor.blocked.connect(messages.append)
    backgrounds = []
    interceptor.background_blocked.connect(lambda: backgrounds.append(True))
    interceptor.interceptRequest(request)
    request.block.assert_called_once_with(True)
    assert interceptor.observed_hosts() == []
    assert len(messages) + len(backgrounds) == 1
    assert all("must-not-log" not in text and "not-google" not in text for text in messages)


@pytest.mark.parametrize("kind", ["ResourceTypeWebSocket", "ResourceTypeWorker", "ResourceTypeSharedWorker",
    "ResourceTypeServiceWorker", "ResourceTypePing", "ResourceTypePrefetch", "ResourceTypeCspReport",
    "ResourceTypeObject", "ResourceTypeMedia", "ResourceTypeUnknown"])
def test_unneeded_background_channels_blocked_even_on_google(kind):
    request = MagicMock()
    request.requestUrl.return_value = QUrl("https://accounts.google.com/background")
    request.resourceType.return_value = getattr(QWebEngineUrlRequestInfo.ResourceType, kind)
    interceptor = AuthRequestInterceptor()
    interceptor.interceptRequest(request)
    request.block.assert_called_once_with(True)


@pytest.mark.parametrize("host", ["accounts.youtube.com", "www.recaptcha.net", "google-analytics.com",
    "www.googletagmanager.com", "github.com", "api.github.com", "evil.google.com", "127.0.0.1"])
def test_unlisted_hosts_have_no_resource_exception(host):
    request = MagicMock()
    request.requestUrl.return_value = QUrl(f"https://{host}/recaptcha/script.js")
    request.resourceType.return_value = QWebEngineUrlRequestInfo.ResourceType.ResourceTypeScript
    interceptor = AuthRequestInterceptor()
    interceptor.interceptRequest(request)
    request.block.assert_called_once_with(True)


def test_host_diagnostics_never_record_paths_queries_or_fragments():
    request = MagicMock()
    request.requestUrl.return_value = QUrl("https://www.gstatic.com/secret?token=dummy#secret")
    request.resourceType.return_value = QWebEngineUrlRequestInfo.ResourceType.ResourceTypeScript
    interceptor = AuthRequestInterceptor()
    interceptor.interceptRequest(request)
    assert interceptor.observed_hosts() == ["www.gstatic.com"]


def test_blocked_diagnostics_never_record_arbitrary_hostnames_or_url_values():
    request = MagicMock()
    request.requestUrl.return_value = QUrl("https://dummy-secret.evil.test/private?token=dummy-secret#private")
    request.resourceType.return_value = QWebEngineUrlRequestInfo.ResourceType.ResourceTypeScript
    interceptor = AuthRequestInterceptor()
    interceptor.interceptRequest(request)
    assert interceptor.blocked_requests() == [{
        "host": "unlisted destination", "type": "ResourceTypeScript", "reason": "destination"}]


def test_background_blocks_do_not_replace_page_status_with_a_red_error(qapp, monkeypatch):
    from app_controller import LoginDialog
    from PySide6.QtWebEngineWidgets import QWebEngineView
    monkeypatch.setattr(QWebEngineView, "setUrl", MagicMock())
    dialog = LoginDialog()
    try:
        dialog._risk_consent.setChecked(True)
        dialog._begin_signin()
        dialog._on_background_request_blocked()
        assert dialog.status_label.isHidden()
        assert "background requests were blocked" in dialog._retention_details.text()
        dialog._on_login_blocked("A sign-in page was blocked by the Google-only policy.")
        assert not dialog.status_label.isHidden()
        assert dialog._navigation_blocked is True
    finally:
        dialog.reject()
        import shiboken6
        shiboken6.delete(dialog)


def test_login_diagnostics_contain_only_safe_categories_and_no_token_values(qapp, monkeypatch):
    from app_controller import LoginDialog
    from PySide6.QtWebEngineWidgets import QWebEngineView
    save = MagicMock()
    monkeypatch.setattr("app_controller.save_json", save)
    monkeypatch.setattr(QWebEngineView, "setUrl", MagicMock())
    dialog = LoginDialog()
    try:
        dialog._risk_consent.setChecked(True)
        dialog._begin_signin()
        dialog._captured_token = "dummy-secret"
        dialog._email = "private@example.test"
        dialog._auth_failure = {"reason": "http", "http_status": 403,
                                "google_code": "dummy-secret", "private": "dummy-secret"}
        dialog._save_login_diagnostics()
        report = save.call_args.args[1]
        assert report["auth_failure"]["http_status"] == 403
        assert "dummy-secret" not in repr(report)
        assert "private@example.test" not in repr(report)
    finally:
        dialog.reject()
        import shiboken6
        shiboken6.delete(dialog)


def test_exchange_timeout_remains_cancellable_and_prevents_late_login(qapp):
    from app_controller import LoginDialog
    dialog = LoginDialog()
    try:
        dialog._exchange_attempted = True
        dialog._on_exchange_timeout()
        assert dialog._cancelled.is_set()
        assert dialog._exchange_timed_out is True
        assert "timed out" in dialog.status_label.text()
        dialog._on_login_result(True, "private@example.test")
        assert not dialog.signed_in
    finally:
        dialog.reject()
        import shiboken6
        shiboken6.delete(dialog)


def test_token_handoff_needs_no_duplicate_email_and_does_not_abort_google_terms_navigation(qapp, monkeypatch):
    from app_controller import LoginDialog
    from PySide6.QtWebEngineWidgets import QWebEngineView
    monkeypatch.setattr(QWebEngineView, "setUrl", MagicMock())
    worker = MagicMock()
    thread = MagicMock(return_value=worker)
    monkeypatch.setattr("app_controller.threading.Thread", thread)
    dialog = LoginDialog()
    try:
        dialog._risk_consent.setChecked(True)
        dialog._begin_signin()
        stop = MagicMock()
        monkeypatch.setattr(dialog._browser, "stop", stop)
        dialog._on_token_found("oauth2_4/dummy")
        assert thread.call_args.kwargs["args"] == ("oauth2_4/dummy",)
        worker.start.assert_called_once()
        stop.assert_not_called()
        assert dialog._exchange_timer.isActive()
        assert dialog.status_label.text() == "Finishing sign-in…"
    finally:
        dialog.reject()
        import shiboken6
        shiboken6.delete(dialog)


@pytest.mark.parametrize("email", [None, "", "not-an-email", "private@example.test\x00"])
def test_google_identity_must_be_present_and_valid_before_login(email):
    import threading
    from app_controller import LoginDialog
    engine = MagicMock()
    engine.exchange_oauth_for_master.return_value = {"Email": email, "Token": "dummy"}
    dialog = SimpleNamespace(_cancelled=threading.Event(), _keep_sync=engine, _queue_login_result=MagicMock())
    LoginDialog._exchange_worker(dialog, "oauth2_4/dummy")
    engine.login.assert_not_called()
    assert dialog._queue_login_result.call_args.args[0] is False
    assert "account identity" in dialog._queue_login_result.call_args.args[1]


def test_login_uses_only_the_identity_returned_by_google():
    import threading
    from app_controller import LoginDialog
    engine = MagicMock()
    engine.exchange_oauth_for_master.return_value = {"Email": "google-identity@example.test", "Token": "dummy"}
    engine.login.return_value = True
    dialog = SimpleNamespace(_cancelled=threading.Event(), _keep_sync=engine,
                             _queue_login_result=MagicMock(), _remember_login=True)
    LoginDialog._exchange_worker(dialog, "oauth2_4/dummy")
    engine.exchange_oauth_for_master.assert_called_once_with("oauth2_4/dummy")
    assert engine.login.call_args.args == ("google-identity@example.test",)
    assert dialog._queue_login_result.call_args.args == (True, "google-identity@example.test")


def test_worker_results_reach_widgets_only_when_consumed_on_the_gui_thread(qapp):
    import threading
    from app_controller import LoginDialog
    dialog = LoginDialog()
    try:
        assert dialog._signals.parent() is dialog
        worker = threading.Thread(target=dialog._queue_login_result, args=(False, "Sign-in did not finish."))
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert dialog.status_label.text() == ""
        dialog._consume_login_result()
        assert dialog.status_label.text() == "Sign-in did not finish."
        assert dialog._browser.isEnabled()
        dialog._cancelled.set()
        dialog._queue_login_result(True, "private@example.test")
        assert dialog._login_results.empty()
    finally:
        dialog.reject()
        import shiboken6
        shiboken6.delete(dialog)


def test_result_does_not_clear_google_cookies_during_live_terms_navigation(qapp, monkeypatch):
    from app_controller import LoginDialog
    dialog = LoginDialog()
    store = MagicMock()
    monkeypatch.setattr(dialog._profile, "cookieStore", lambda: store)
    try:
        dialog._browser.setEnabled(False)
        dialog._on_login_result(False, "Sign-in did not finish.")
        assert dialog._browser.isEnabled()
        store.deleteAllCookies.assert_not_called()
        dialog.reject()
        store.deleteAllCookies.assert_called_once()
    finally:
        import shiboken6
        shiboken6.delete(dialog)
