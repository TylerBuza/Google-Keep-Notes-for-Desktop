import ssl
from unittest.mock import MagicMock

import certifi
import pytest
import requests
from requests.adapters import HTTPAdapter

from keep_protocol.transport import GoogleEndpointAdapter, google_session
from network_policy import AUTH_URL, CHANGES_URL, CHANGES_URL_WRITE, RESOURCE_HOSTS


@pytest.mark.parametrize("auth,url", [(True, AUTH_URL), (False, CHANGES_URL), (False, CHANGES_URL_WRITE)])
def test_direct_google_transport_ignores_ambient_proxy_netrc_and_ca(monkeypatch, tmp_path, auth, url):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.not-google.test:8080")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.not-google.test:8080")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "untrusted-ca.pem"))
    netrc = tmp_path / "netrc"
    netrc.write_text("machine notes-pa.clients6.google.com login dummy password dummy-secret")
    monkeypatch.setenv("NETRC", str(netrc))
    send = MagicMock(return_value=requests.Response())
    monkeypatch.setattr(HTTPAdapter, "send", send)
    with google_session(auth=auth) as session:
        assert session.trust_env is False
        assert session.proxies == {}
        settings = session.merge_environment_settings(url, {}, False, True, None)
        assert not settings["proxies"]
        assert settings["verify"] is True
        prepared = session.prepare_request(requests.Request("POST", url, headers={"Authorization": "OAuth dummy"}))
        assert prepared.headers["Authorization"] == "OAuth dummy"  # not overridden by netrc
        session.get_adapter(url).send(prepared, **settings)
    kwargs = send.call_args.kwargs
    assert kwargs["proxies"] == {}
    assert kwargs["verify"] == certifi.where()
    assert kwargs["timeout"] == (10, 30)


@pytest.mark.parametrize("auth,url", [(True, CHANGES_URL), (False, AUTH_URL),
    (False, "https://evil.test/"), (False, "https://notes-pa.clients6.google.com.evil.test/notes/v1/changes"),
    (False, "https://notes-pa.clients6.google.com/other"), (False, "http://notes-pa.clients6.google.com/notes/v1/changes"),
    (False, "https://notes-pa.clients6.google.com:444/notes/v1/changes"),
    (False, CHANGES_URL + "?redirect=https://evil.test"),
    (False, "https://user@notes-pa.clients6.google.com/notes/v1/changes")])
def test_transport_rejects_unlisted_endpoints_before_any_socket(monkeypatch, auth, url):
    send = MagicMock()
    monkeypatch.setattr(HTTPAdapter, "send", send)
    # Assign directly so normalization cannot hide malformed URL variations.
    request = requests.PreparedRequest()
    request.method, request.url = "POST", url
    with pytest.raises(requests.RequestException):
        GoogleEndpointAdapter(auth=auth).send(request)
    send.assert_not_called()


@pytest.mark.parametrize("override", [{"verify": False}, {"verify": "untrusted.pem"},
    {"proxies": {"https": "http://proxy.test:8080"}}, {"cert": "client.pem"}])
def test_transport_cannot_relax_tls_or_add_proxy(monkeypatch, override):
    send = MagicMock()
    monkeypatch.setattr(HTTPAdapter, "send", send)
    request = requests.Request("POST", CHANGES_URL).prepare()
    with pytest.raises(requests.RequestException):
        GoogleEndpointAdapter().send(request, **override)
    send.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "CONNECT", "OPTIONS"])
def test_transport_only_accepts_the_reviewed_post_method(monkeypatch, method):
    send = MagicMock()
    monkeypatch.setattr(HTTPAdapter, "send", send)
    request = requests.Request(method, CHANGES_URL).prepare()
    with pytest.raises(requests.RequestException):
        GoogleEndpointAdapter().send(request)
    send.assert_not_called()


@pytest.mark.parametrize("auth,host", [(True, "android.clients.google.com"),
                                      (False, "notes-pa.clients6.google.com")])
def test_each_transport_checks_exact_hostname_with_no_retries(auth, host):
    adapter = GoogleEndpointAdapter(auth=auth)
    context = adapter.poolmanager.connection_pool_kw["ssl_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert adapter.poolmanager.connection_pool_kw["assert_hostname"] == host
    assert adapter.max_retries.total == 0


def test_browser_configuration_cannot_inherit_unsafe_flags_or_proxy(monkeypatch):
    from network_policy import configure_browser_network
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--ignore-certificate-errors --no-sandbox --proxy-server=evil.test")
    monkeypatch.setenv("QTWEBENGINE_REMOTE_DEBUGGING", "9222")
    monkeypatch.setenv("QTWEBENGINE_DISABLE_SANDBOX", "1")
    configure_browser_network()
    import os
    flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
    assert "--no-proxy-server" in flags
    assert "ignore-certificate-errors" not in flags
    assert "--no-sandbox" not in flags
    assert "evil.test" not in flags
    assert "QTWEBENGINE_REMOTE_DEBUGGING" not in os.environ
    assert "QTWEBENGINE_DISABLE_SANDBOX" not in os.environ
    assert all("EXCLUDE " + host in flags for host in RESOURCE_HOSTS)


@pytest.mark.parametrize("url", ["https://evil.test/image.png", "https://www.gstatic.com/image.png",
    "file:///C:/Windows/win.ini", "file://server/share/image.png", "data:image/png;base64,AAAA"])
def test_native_note_resources_cannot_fetch_or_read_files(qapp, url):
    from native_resources import LocalNoteDocument
    from note_window import NoteTextEdit
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument
    editor = NoteTextEdit()
    try:
        assert isinstance(editor.document(), LocalNoteDocument)
        assert editor.document().loadResource(QTextDocument.ResourceType.ImageResource, QUrl(url)).isEmpty()
        editor.setHtml(f'<p>kept text</p><img src="{url}">')
        assert "kept text" in editor.toPlainText()
        assert editor.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(url)).isEmpty()
    finally:
        editor.deleteLater()


@pytest.mark.parametrize("url", ["https://evil.test/", "https://keep.google.com/", "mailto:a@test.invalid", "tel:123"])
def test_note_links_never_launch_external_apps(qapp, monkeypatch, url):
    from note_window import NoteTextEdit
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QToolTip
    launch = MagicMock()
    monkeypatch.setattr(QDesktopServices, "openUrl", launch)
    monkeypatch.setattr(QToolTip, "showText", MagicMock())
    editor = NoteTextEdit()
    try:
        editor._open_link(url)
        launch.assert_not_called()
    finally:
        editor.deleteLater()
