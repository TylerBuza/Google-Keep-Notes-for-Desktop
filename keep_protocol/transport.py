"""Exact-endpoint, direct HTTPS sessions shared by auth and Keep requests."""

import ssl

import certifi
import requests
from requests.adapters import HTTPAdapter

from network_policy import AUTH_URL, CHANGES_URL, CHANGES_URL_WRITE


class _GoogleTLSContext(ssl.SSLContext):
    def set_alpn_protocols(self, protocols):
        # Android's legacy auth endpoint rejects ALPN. Certificate/hostname
        # validation remains enabled; Keep uses an ordinary TLS context.
        pass


class GoogleEndpointAdapter(HTTPAdapter):
    def __init__(self, *, auth=False):
        self._auth = auth
        self._urls = frozenset({AUTH_URL} if auth else {CHANGES_URL, CHANGES_URL_WRITE})
        super().__init__(max_retries=0)

    def init_poolmanager(self, *args, **kwargs):
        context_type = _GoogleTLSContext if self._auth else ssl.SSLContext
        context = context_type(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(certifi.where())
        if self._auth:
            context.options &= ~ssl.OP_NO_TICKET
        kwargs["ssl_context"] = context
        kwargs["assert_hostname"] = ("android.clients.google.com" if self._auth
                                     else "notes-pa.clients6.google.com")
        super().init_poolmanager(*args, **kwargs)

    def send(self, request, **kwargs):
        # Runs before DNS/TLS/socket creation, including on redirected or
        # directly prepared requests. No alternate endpoint or proxy adapter.
        if (request.method != "POST" or request.url not in self._urls
                or kwargs.get("proxies") or kwargs.get("cert")
                or kwargs.get("verify", True) not in (True, certifi.where())):
            raise requests.RequestException("Blocked a request outside the Google transport policy.")
        kwargs["verify"] = certifi.where()
        kwargs["proxies"] = {}
        kwargs["timeout"] = (10, 30)
        return super().send(request, **kwargs)


class GoogleAuthAdapter(GoogleEndpointAdapter):
    def __init__(self, max_retries=0):
        if max_retries != 0:
            raise ValueError("Automatic auth retries are disabled.")
        super().__init__(auth=True)


def google_session(*, auth=False):
    session = requests.Session()
    # Ignore proxies, netrc and custom CA settings from the environment.
    session.trust_env = False
    session.proxies.clear()
    adapter = GoogleAuthAdapter() if auth else GoogleEndpointAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)  # fails closed rather than using a default adapter
    session.headers.update({"Accept-Encoding": "identity"})
    return session
