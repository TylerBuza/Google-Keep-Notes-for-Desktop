"""Reviewed runtime destinations. No suffix/wildcard or third-party fallback."""

from urllib.parse import urlsplit

AUTH_URL = "https://android.clients.google.com/auth"
CHANGES_URL = "https://notes-pa.clients6.google.com/notes/v1/changes"
_KEEP_WEB_API_KEY = "AIzaSyDE7NHMUZfMoJVu-YNkK-7AXFSuL1Q9gKE"
CHANGES_URL_WRITE = f"{CHANGES_URL}?alt=json&key={_KEEP_WEB_API_KEY}"

AUTH_HOSTS = frozenset({"accounts.google.com"})
RESOURCE_HOSTS = AUTH_HOSTS | frozenset({
    "ssl.gstatic.com", "www.gstatic.com", "fonts.gstatic.com",
    "www.google.com", "recaptcha.google.com", "apis.google.com",
})
RUNTIME_HOSTS = RESOURCE_HOSTS | frozenset({
    "android.clients.google.com", "notes-pa.clients6.google.com",
})


def allowed_https_url(value, hosts=AUTH_HOSTS):
    # QUrl is accepted without making the protocol transport import Qt.
    text = value.toString() if hasattr(value, "toString") else str(value)
    try:
        url = urlsplit(text)
        return (text == text.strip() and url.scheme.lower() == "https" and url.hostname in hosts
                and url.port in (None, 443) and url.username is None and url.password is None
                and "\\" not in text and not any(ord(c) < 32 or ord(c) == 127 for c in text))
    except (ValueError, TypeError):
        return False


def configure_browser_network():
    """Before QApplication: no ambient proxy or Chromium background services.

    Resource interception is the primary page policy. These flags also reduce
    browser-initiated background traffic; this is not an OS packet firewall.
    """
    import os
    # Do not accept ambient flags that could disable TLS/sandboxing, enable
    # remote debugging, substitute a proxy, or relax the destination policy.
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join((
        "--no-proxy-server", "--disable-background-networking",
        "--disable-component-update", "--disable-domain-reliability", "--disable-sync",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        '--host-resolver-rules="MAP * ~NOTFOUND, ' +
        ", ".join("EXCLUDE " + host for host in sorted(RESOURCE_HOSTS)) + '"',
    ))
    os.environ.pop("QTWEBENGINE_REMOTE_DEBUGGING", None)
    os.environ.pop("QTWEBENGINE_DISABLE_SANDBOX", None)
    # Chromium's direct-network flag is authoritative for the embedded browser;
    # Python protocol calls independently use proxy-free locked sessions.
