"""Credential types and legacy credential cleanup; no browser/cookie CLI.

Desktop login is guarded by the consent/origin UI. Session-only mode keeps
only a short-lived Keep bearer after the initial exchange. Remembering a
master token requires a separate explicit opt-in and encrypted storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
import time
from typing import Optional

import keyring

from .auth_transport import AuthError, mint_keep_bearer

AUTH_CONSENT_VERSION = 1
SESSION_SECONDS = 45 * 60
_KR_SERVICE = "keep-protocol"
_KR_ACCOUNT_EMAIL = "active-email"
_KR_PREFIX_MASTER = "master:"
_KR_PREFIX_ANDROID = "android:"


@dataclass
class Credentials:
    email: str
    master_token: str = field(repr=False)
    android_id: str

    def mint_bearer(self) -> str:
        return mint_keep_bearer(self.email, self.master_token, self.android_id)


@dataclass
class SessionCredentials:
    email: str
    bearer: str = field(repr=False)
    expires_at: float

    def mint_bearer(self) -> str:
        if time.monotonic() >= self.expires_at:
            raise AuthError("Your session-only Keep login expired. Sign in again.")
        return self.bearer


def _keyring():
    if sys.platform != "win32":
        raise AuthError("This build requires Windows Credential Manager.")
    from keyring.backends.Windows import WinVaultKeyring
    return WinVaultKeyring()


def _get_active_email() -> Optional[str]:
    return _keyring().get_password(_KR_SERVICE, _KR_ACCOUNT_EMAIL)


def load_credentials(email: Optional[str] = None) -> Credentials:
    """Legacy tools only. The desktop never auto-imports these credentials."""
    email = email or _get_active_email()
    if not email:
        raise AuthError("No stored Google account.")
    master = _keyring().get_password(_KR_SERVICE, _KR_PREFIX_MASTER + email)
    android = _keyring().get_password(_KR_SERVICE, _KR_PREFIX_ANDROID + email)
    if not master or not android:
        raise AuthError("No complete stored Google credential.")
    return Credentials(email, master, android)


def clear_credentials(email: Optional[str] = None) -> None:
    email = email or _get_active_email()
    if not email:
        return
    for name in (_KR_PREFIX_MASTER + email, _KR_PREFIX_ANDROID + email):
        try:
            _keyring().delete_password(_KR_SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass
    if _get_active_email() == email:
        try:
            _keyring().delete_password(_KR_SERVICE, _KR_ACCOUNT_EMAIL)
        except keyring.errors.PasswordDeleteError:
            pass


if __name__ == "__main__":
    # Do not ship a consent/origin-check bypass or a token-printing command.
    print("Use the desktop application's guarded experimental sign-in. CLI token capture is disabled.")
    sys.exit(2)
