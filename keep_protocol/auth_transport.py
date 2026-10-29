"""Small, endpoint-locked transport for the experimental Android auth flow.

The wire fields follow the open-source gpsoauth protocol documentation. No
password-login API, arbitrary scope, URL, proxy, redirect, or retry is exposed.
This does not turn the underlying master token into a Keep-only credential.
"""

from __future__ import annotations

import ssl

import certifi
import requests
from requests.adapters import HTTPAdapter

AUTH_URL = "https://android.clients.google.com/auth"
KEEP_SCOPE = "oauth2:https://www.googleapis.com/auth/memento"
KEEP_APP = "com.google.android.keep"
KEEP_SIGNATURE = "38918a453d07199354f8b19af05ec6562ced5788"
MAX_RESPONSE_BYTES = 64 * 1024


class AuthError(RuntimeError):
    """Only fixed, non-secret diagnostics may cross the auth boundary."""


class _GoogleTLSContext(ssl.SSLContext):
    def set_alpn_protocols(self, protocols):
        # Google's Android endpoint rejects ALPN in this legacy flow. This
        # compatibility setting does NOT disable certificate/hostname checks.
        pass


class GoogleAuthAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = _GoogleTLSContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(certifi.where())
        context.options &= ~ssl.OP_NO_TICKET
        kwargs["ssl_context"] = context
        kwargs["assert_hostname"] = "android.clients.google.com"
        super().init_poolmanager(*args, **kwargs)


def _post(form: dict) -> dict[str, str]:
    try:
        with requests.Session() as session:
            # Do not inherit proxy credentials, .netrc, or environment-supplied
            # CA paths. Requests still verifies against its trusted CA bundle.
            session.trust_env = False
            session.mount("https://", GoogleAuthAdapter(max_retries=0))
            session.headers.update({"User-Agent": "GoogleAuth/1.4", "Accept-Encoding": "identity"})
            with session.post(AUTH_URL, data=form, verify=True, allow_redirects=False,
                              timeout=(10, 30), stream=True) as response:
                if response.status_code != 200:
                    raise AuthError("Google rejected the authentication request.")
                raw = bytearray()
                for chunk in response.iter_content(4096):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise AuthError("Google returned an unsupported authentication response.")
        text = raw.decode("utf-8")
        result = {}
        for line in text.splitlines():
            if not line:
                continue
            name, separator, value = line.partition("=")
            if not separator or not name.isascii() or not name.replace("_", "").isalnum() or name in result:
                raise AuthError("Google returned an unsupported authentication response.")
            result[name] = value
        if result.get("Error"):
            raise AuthError("Google did not authorize this experimental login. Use official Keep instead.")
        return result
    except AuthError:
        raise
    except Exception:
        # Requests exceptions can include request details. Never propagate
        # response text, submitted forms, cookies, or dependency exceptions.
        raise AuthError("Google authentication failed securely. Check the connection or try again.") from None


def exchange_web_token(email: str, web_token: str, android_id: str) -> dict[str, str]:
    if not isinstance(web_token, str) or not web_token.startswith("oauth2_4/") or len(web_token) > 16384:
        raise AuthError("Unsupported Google sign-in token.")
    result = _post({
        "accountType": "HOSTED_OR_GOOGLE", "Email": email,
        "has_permission": 1, "add_account": 1, "ACCESS_TOKEN": 1,
        "Token": web_token, "service": "ac2dm", "source": "android",
        "androidId": android_id, "device_country": "us", "operatorCountry": "us",
        "lang": "en", "sdk_version": 17, "google_play_services_version": 240913000,
        "client_sig": KEEP_SIGNATURE, "callerSig": KEEP_SIGNATURE, "droidguard_results": "dummy123",
    })
    if not result.get("Token"):
        raise AuthError("Google did not issue a master token.")
    # Discard SID/LSID and any other broad session credentials in the response.
    return {"Token": result["Token"], "Email": result.get("Email") or email, "AndroidId": android_id}


def mint_keep_bearer(email: str, master_token: str, android_id: str) -> str:
    result = _post({
        "accountType": "HOSTED_OR_GOOGLE", "Email": email, "has_permission": 1,
        "EncryptedPasswd": master_token, "service": KEEP_SCOPE, "source": "android",
        "androidId": android_id, "app": KEEP_APP, "client_sig": KEEP_SIGNATURE,
        "device_country": "us", "operatorCountry": "us", "lang": "en",
        "sdk_version": 17, "google_play_services_version": 240913000,
    })
    if not result.get("Auth"):
        raise AuthError("Google did not issue a Keep access token.")
    return result["Auth"]
