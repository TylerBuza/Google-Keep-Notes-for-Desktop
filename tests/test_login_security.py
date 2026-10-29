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
    interceptor = SimpleNamespace(_reported=set(), blocked=MagicMock())
    AuthRequestInterceptor.interceptRequest(interceptor, request)
    assert request.block.called is blocked


def test_login_requires_consent_and_defaults_to_no_credential_retention(qapp, monkeypatch):
    from app_controller import LoginDialog
    from PySide6.QtWebEngineWidgets import QWebEngineView
    navigate = MagicMock()
    monkeypatch.setattr(QWebEngineView, "setUrl", navigate)
    dialog = LoginDialog(first_launch=True)
    try:
        assert dialog._profile.isOffTheRecord()
        assert dialog._origin.isReadOnly()
        assert not dialog._remember.isChecked()
        assert not dialog._risk_consent.isChecked()
        navigate.assert_not_called()
        dialog._begin_signin()
        navigate.assert_not_called()
        dialog._risk_consent.setChecked(True)
        dialog._begin_signin()
        navigate.assert_called_once_with(QUrl("https://accounts.google.com/EmbeddedSetup"))
        assert dialog._remember_login is False
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
                              _keep_sync=MagicMock(is_authenticated=True), _captured_token="dummy")
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
