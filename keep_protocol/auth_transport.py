"""Small, endpoint-locked transport for the experimental Android auth flow.

The wire fields follow the open-source gpsoauth protocol documentation. No
password-login API, arbitrary scope, URL, proxy, redirect, or retry is exposed.
This does not turn the underlying master token into a Keep-only credential.
"""

from __future__ import annotations

from network_policy import AUTH_URL
import requests
from .transport import GoogleAuthAdapter, GoogleTransportPolicyError, google_session
KEEP_SCOPE = "oauth2:https://www.googleapis.com/auth/memento"
KEEP_APP = "com.google.android.keep"
KEEP_SIGNATURE = "38918a453d07199354f8b19af05ec6562ced5788"
MAX_RESPONSE_BYTES = 64 * 1024


class AuthError(RuntimeError):
    """Only fixed, non-secret diagnostics may cross the auth boundary."""

    REASONS = frozenset({"http", "google", "timeout", "tls", "connection", "policy",
                         "response", "cookie", "email", "master-missing", "bearer-missing", "storage", "unknown"})
    GOOGLE_CODES = frozenset({"BadAuthentication", "NeedsBrowser", "WebLoginRequired",
                              "DeviceManagementRequired", "CaptchaRequired", "ServiceDisabled"})

    def __init__(self, message, *, reason="unknown", status=None, google_code=None):
        super().__init__(message)
        self.reason = reason if isinstance(reason, str) and reason in self.REASONS else "unknown"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        self.google_code = google_code if isinstance(google_code, str) and google_code in self.GOOGLE_CODES else None

    def diagnostic(self, stage):
        return {"stage": stage if isinstance(stage, str) and stage in ("token-exchange", "keep-bearer", "storage") else "unknown",
                "reason": self.reason, "http_status": self.status, "google_code": self.google_code}


def safe_auth_failure(failure):
    if not isinstance(failure, dict):
        return None
    return AuthError("Sign-in failed.", reason=failure.get("reason"),
                     status=failure.get("http_status"), google_code=failure.get("google_code")).diagnostic(
                         failure.get("stage"))


def auth_failure_message(failure):
    """Never echo exception text, raw Google error values or response bodies."""
    failure = safe_auth_failure(failure)
    if failure is None:
        return "Sign-in did not finish."
    reason = failure.get("reason")
    if reason == "http" and type(failure.get("http_status")) is int:
        return f"Google sign-in returned HTTP {failure['http_status']}."
    if reason == "google":
        if failure.get("google_code") in ("NeedsBrowser", "WebLoginRequired", "DeviceManagementRequired"):
            return "Google requires a sign-in step this app cannot complete."
        return "Google rejected the sign-in token."
    return {
        "timeout": "The Google sign-in request timed out.",
        "tls": "Could not verify Google's HTTPS connection.",
        "connection": "Could not connect to Google's sign-in service.",
        "policy": "The app blocked the request under its Google-only policy.",
        "response": "Google returned an unsupported sign-in response.",
        "cookie": "Google did not provide a supported sign-in token.",
        "email": "Enter the email of the Google account you are signing into.",
        "master-missing": "Google did not issue a master token.",
        "bearer-missing": "Google did not issue Keep access.",
        "storage": "Could not save the encrypted sign-in credential.",
    }.get(reason, "Sign-in did not finish.")


def valid_account_email(email):
    if not isinstance(email, str) or not 3 <= len(email) <= 320 or email.count("@") != 1:
        return False
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char in "<>\\/" for char in email):
        return False
    local, domain = email.split("@")
    return bool(local and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def _post(form: dict) -> dict[str, str]:
    try:
        with google_session(auth=True) as session:
            session.headers.update({"User-Agent": "GoogleAuth/1.4", "Accept-Encoding": "identity"})
            with session.post(AUTH_URL, data=form, verify=True, allow_redirects=False,
                              timeout=(10, 30), stream=True) as response:
                if response.status_code != 200:
                    raise AuthError("Google sign-in request failed.", reason="http", status=response.status_code)
                raw = bytearray()
                for chunk in response.iter_content(4096):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise AuthError("Google returned an unsupported authentication response.", reason="response")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise AuthError("Google returned an unsupported authentication response.", reason="response") from None
        result = {}
        for line in text.splitlines():
            if not line:
                continue
            name, separator, value = line.partition("=")
            if not separator or not name.isascii() or not name.replace("_", "").isalnum() or name in result:
                raise AuthError("Google returned an unsupported authentication response.", reason="response")
            result[name] = value
        if result.get("Error"):
            raise AuthError("Google rejected the sign-in token.", reason="google", google_code=result["Error"])
        return result
    except AuthError:
        raise
    except GoogleTransportPolicyError:
        raise AuthError("Google-only request policy blocked authentication.", reason="policy") from None
    except requests.exceptions.SSLError:
        raise AuthError("Google HTTPS verification failed.", reason="tls") from None
    except requests.Timeout:
        raise AuthError("Google authentication timed out.", reason="timeout") from None
    except requests.ConnectionError:
        raise AuthError("Google authentication connection failed.", reason="connection") from None
    except Exception:
        # Requests exceptions can include request details. Never propagate
        # response text, submitted forms, cookies, or dependency exceptions.
        raise AuthError("Google authentication failed securely. Check the connection or try again.") from None


def exchange_web_token(email: str, web_token: str, android_id: str) -> dict[str, str]:
    if not valid_account_email(email):
        raise AuthError("Account email is required for this exchange.", reason="email")
    if not isinstance(web_token, str) or not web_token.startswith("oauth2_4/") or len(web_token) > 16384:
        raise AuthError("Unsupported Google sign-in token.", reason="cookie")
    result = _post({
        "accountType": "HOSTED_OR_GOOGLE", "Email": email,
        "has_permission": 1, "add_account": 1, "ACCESS_TOKEN": 1,
        "Token": web_token, "service": "ac2dm", "source": "android",
        "androidId": android_id, "device_country": "us", "operatorCountry": "us",
        "lang": "en", "sdk_version": 17, "google_play_services_version": 240913000,
        "client_sig": KEEP_SIGNATURE, "callerSig": KEEP_SIGNATURE, "droidguard_results": "dummy123",
    })
    if not result.get("Token"):
        raise AuthError("Google did not issue a master token.", reason="master-missing")
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
        raise AuthError("Google did not issue a Keep access token.", reason="bearer-missing")
    return result["Auth"]
