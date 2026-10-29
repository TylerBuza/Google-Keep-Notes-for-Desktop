"""Fail-closed navigation and resource policy for experimental Google sign-in."""

from urllib.parse import urlsplit

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineSettings, QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestInfo,
)

AUTH_HOSTS = frozenset({"accounts.google.com"})
RESOURCE_HOSTS = AUTH_HOSTS | frozenset({
    "accounts.youtube.com", "ssl.gstatic.com", "www.gstatic.com", "fonts.gstatic.com",
    "www.google.com", "www.recaptcha.net", "recaptcha.google.com", "apis.google.com",
})


def release_auth_profile(profile):
    # Called after the page's complete WebContents teardown. There is no live
    # page whose resource requests could escape the allowlist at this point.
    # Detaching the Python IO hook before profile destruction also avoids a
    # GIL deadlock in Qt's network-thread shutdown.
    profile.setUrlRequestInterceptor(None)
    profile.deleteLater()


def allowed_https_url(value, hosts=AUTH_HOSTS):
    text = value.toString() if isinstance(value, QUrl) else str(value)
    try:
        url = urlsplit(text)
        return (text == text.strip() and url.scheme.lower() == "https" and url.hostname in hosts
                and url.port in (None, 443) and url.username is None and url.password is None
                and "\\" not in text and not any(ord(c) < 32 for c in text))
    except (ValueError, TypeError):
        return False


def display_address(url):
    if not allowed_https_url(url):
        return "No allowed Google HTTPS page loaded"
    # OAuth URLs can carry credentials/state in their query or fragment.
    # Display the real origin and path, never those sensitive components.
    path = url.path() if isinstance(url, QUrl) else urlsplit(str(url)).path
    return "https://accounts.google.com" + path


def allowed_frame_url(url):
    if allowed_https_url(url):
        return True
    # Google may require a real CAPTCHA during sign-in. Permit only Google's
    # challenge frames, never arbitrary Google/static-site frames or main pages.
    challenge_hosts = frozenset({"www.google.com", "www.recaptcha.net"})
    if not allowed_https_url(url, challenge_hosts):
        return False
    path = url.path() if isinstance(url, QUrl) else urlsplit(str(url)).path
    return path.startswith("/recaptcha/") and ".." not in path


class AuthRequestInterceptor(QWebEngineUrlRequestInterceptor):
    blocked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reported = set()

    def interceptRequest(self, info):
        url = info.requestUrl()
        kind = info.resourceType()
        resource = QWebEngineUrlRequestInfo.ResourceType
        if kind == resource.ResourceTypeMainFrame:
            allowed = allowed_https_url(url)
        elif kind == resource.ResourceTypeSubFrame:
            allowed = allowed_frame_url(url)
        else:
            allowed = allowed_https_url(url, RESOURCE_HOSTS)
            # Inline images/fonts are not navigable HTML or executable scripts.
            if (url.scheme() in ("data", "blob") and allowed_https_url(info.firstPartyUrl())
                    and kind in (resource.ResourceTypeImage, resource.ResourceTypeFontResource,
                                 resource.ResourceTypeFavicon)):
                allowed = True
        if not allowed:
            info.block(True)
            host = url.host() or url.scheme() or "unknown"
            if host not in self._reported:
                self._reported.add(host)
                self.blocked.emit("Blocked a sign-in request outside the allowed Google origins.")


class GuardedGooglePage(QWebEnginePage):
    blocked = Signal(str)

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        settings = self.settings()
        attribute = QWebEngineSettings.WebAttribute
        for name in ("JavascriptCanOpenWindows", "JavascriptCanAccessClipboard",
                     "LocalContentCanAccessRemoteUrls", "LocalContentCanAccessFileUrls",
                     "ScreenCaptureEnabled", "FullScreenSupportEnabled", "ErrorPageEnabled"):
            settings.setAttribute(getattr(attribute, name), False)
        self.certificateError.connect(self._reject_certificate)
        self.permissionRequested.connect(lambda permission: permission.deny())

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if allowed_https_url(url) or (not is_main_frame and allowed_frame_url(url)):
            return True
        self.blocked.emit("Navigation blocked: sign-in may only stay on https://accounts.google.com.")
        return False

    def createWindow(self, window_type):
        self.blocked.emit("Sign-in popup blocked. This experimental flow cannot open other sites.")
        return None

    def _reject_certificate(self, error):
        error.rejectCertificate()
        self.blocked.emit("Certificate verification failed. Sign-in was stopped; no bypass is available.")
