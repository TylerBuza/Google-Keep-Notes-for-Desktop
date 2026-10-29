"""Authentication for Google Keep.

Three pieces:

1. **First-run login**: launch Chromium to Google's `EmbeddedSetup` page so the
   user signs in with their normal flow (password / 2FA / passkey / security
   key — all handled by Google, we never see credentials). When the page
   finishes, Google sets an `oauth_token` cookie which we extract.

2. **Master token exchange**: hand the `oauth_token` to `gpsoauth.exchange_token`
   to get a long-lived `aas_et/...` master token. Store it in the OS keyring.

3. **Bearer minting**: per session, exchange the master token for a short-lived
   OAuth bearer via `gpsoauth.perform_oauth`. That bearer goes into the
   `Authorization: OAuth <token>` header of every Keep API request.

CLI:
    python -m keep_protocol.auth login
    python -m keep_protocol.auth logout
    python -m keep_protocol.auth status
    python -m keep_protocol.auth bearer    # print a fresh bearer (debugging)
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import gpsoauth
import keyring

# Constants for the gpsoauth dance against Keep.
# Values mirror what the Android Keep app sends — these are public knowledge
# and reused by every unofficial Keep client (gkeepapi, kim, etc).
_KEEP_SERVICE = "oauth2:https://www.googleapis.com/auth/memento https://www.googleapis.com/auth/reminders"
_KEEP_APP = "com.google.android.keep"
_KEEP_CLIENT_SIG = "38918a453d07199354f8b19af05ec6562ced5788"

# Keyring service / account names — all our secrets land here.
_KR_SERVICE = "keep-protocol"
_KR_ACCOUNT_EMAIL = "active-email"
_KR_PREFIX_MASTER = "master:"   # actual key: "master:<email>"
_KR_PREFIX_ANDROID = "android:" # actual key: "android:<email>"

# Where Google issues the oauth_token cookie after sign-in. This URL is what
# the official `gpsoauth-java` README's "Second way" tutorial uses.
_EMBEDDED_SETUP_URL = "https://accounts.google.com/EmbeddedSetup"


class AuthError(RuntimeError):
    pass


@dataclass
class Credentials:
    email: str
    master_token: str = field(repr=False)
    android_id: str

    def mint_bearer(self) -> str:
        """Trade the master token for a short-lived OAuth bearer (~1h TTL)."""
        try:
            resp = gpsoauth.perform_oauth(
                self.email, self.master_token, self.android_id,
                _KEEP_SERVICE, _KEEP_APP, _KEEP_CLIENT_SIG,
            )
        except Exception:
            raise AuthError("Google authentication failed; sign in again.") from None
        token = resp.get("Auth") if isinstance(resp, dict) else None
        if not isinstance(token, str) or not token:
            raise AuthError("Google did not issue a Keep access token; sign in again.")
        return token


# ---------------------------------------------------------------- keyring I/O

def _keyring():
    # Never accept a third-party plaintext/fallback keyring backend.
    if sys.platform != "win32":
        raise AuthError("This build requires Windows Credential Manager.")
    from keyring.backends.Windows import WinVaultKeyring
    return WinVaultKeyring()

def _get_active_email() -> Optional[str]:
    return _keyring().get_password(_KR_SERVICE, _KR_ACCOUNT_EMAIL)


def _set_active_email(email: str) -> None:
    _keyring().set_password(_KR_SERVICE, _KR_ACCOUNT_EMAIL, email)


def _store_credentials(email: str, master_token: str, android_id: str) -> None:
    _keyring().set_password(_KR_SERVICE, _KR_PREFIX_MASTER + email, master_token)
    _keyring().set_password(_KR_SERVICE, _KR_PREFIX_ANDROID + email, android_id)
    _set_active_email(email)


def load_credentials(email: Optional[str] = None) -> Credentials:
    """Load stored credentials. Pass `email` to pick a specific account, else
    use the active one."""
    if email is None:
        email = _get_active_email()
    if not email:
        raise AuthError("no account is logged in — run: python -m keep_protocol.auth login")
    master = _keyring().get_password(_KR_SERVICE, _KR_PREFIX_MASTER + email)
    android = _keyring().get_password(_KR_SERVICE, _KR_PREFIX_ANDROID + email)
    if not master or not android:
        raise AuthError(f"no stored master token for {email!r} — run: python -m keep_protocol.auth login")
    return Credentials(email=email, master_token=master, android_id=android)


def clear_credentials(email: Optional[str] = None) -> None:
    if email is None:
        email = _get_active_email()
    if not email:
        return
    for key in (_KR_PREFIX_MASTER + email, _KR_PREFIX_ANDROID + email):
        try:
            _keyring().delete_password(_KR_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass
    if _get_active_email() == email:
        try:
            _keyring().delete_password(_KR_SERVICE, _KR_ACCOUNT_EMAIL)
        except keyring.errors.PasswordDeleteError:
            pass


# ------------------------------------------------------------- browser flow

def _new_android_id() -> str:
    """Random 16-hex-char Android ID. gpsoauth doesn't care what it is so long
    as we use the same one consistently against the same account."""
    return secrets.token_hex(8)


def _interactive_browser_signin() -> tuple[str, str]:
    """Open Chromium at EmbeddedSetup; wait until oauth_token cookie appears.
    Returns (email, oauth_token).

    This optional CLI uses an ephemeral Playwright context. The desktop app
    uses an off-the-record Qt profile instead. Neither reuses a login profile.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise AuthError(
            "playwright is not installed. run:\n"
            "    pip install -r requirements.txt\n"
            "    python -m playwright install chromium"
        ) from e

    print("[auth] opening sign-in window...")
    print("[auth] sign in normally (password / 2FA / passkey all work)")
    print("[auth] this window will close automatically once we detect")
    print("[auth] the oauth_token cookie. do NOT close it manually.\n")

    # Locked-down launch: --app= strips the URL bar, tabs and most chrome,
    # giving a single-purpose dialog window. Other flags suppress first-run
    # noise, password prompts, sync popups and translate bars.
    chromium_args = [
        f"--app={_EMBEDDED_SETUP_URL}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-features=Translate,MediaRouter,OptimizationHints,InterestFeedContentSuggestions,PasswordManagerOnboarding,AutofillServerCommunication",
        "--disable-blink-features=AutomationControlled",
        "--disable-default-apps",
        "--no-service-autorun",
        "--password-store=basic",       # don't poke Windows Credential Vault
        "--use-mock-keychain",          # ditto on macOS, harmless on Win
        "--window-size=460,640",
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            ignore_default_args=["--enable-automation"],
            args=chromium_args,
        )
        ctx = browser.new_context()
        # --app=URL already navigated; reuse that page.
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # Defensive: if the app-mode page somehow didn't navigate, force it.
        try:
            if not (page.url or "").startswith("https://accounts.google.com"):
                page.goto(_EMBEDDED_SETUP_URL)
        except Exception:
            pass

        oauth_token: Optional[str] = None
        email: Optional[str] = None
        deadline = time.monotonic() + 600  # 10 minutes
        while time.monotonic() < deadline:
            try:
                cookies = ctx.cookies("https://accounts.google.com")
            except Exception:
                cookies = []
            for c in cookies:
                if c.get("name") == "oauth_token":
                    val = c.get("value", "")
                    if val and val.startswith("oauth2_4/"):
                        oauth_token = val
                        break
            if oauth_token:
                # Try to read the email Google placed on the page.
                try:
                    email = page.evaluate(
                        "() => document.querySelector('[data-email],[data-identifier]')?.getAttribute('data-email') "
                        "  || document.querySelector('[data-identifier]')?.getAttribute('data-identifier') "
                        "  || ''"
                    )
                except Exception:
                    email = None
                break
            try:
                page.wait_for_timeout(500)
            except Exception:
                break

        try:
            ctx.close()
            browser.close()
        except Exception:
            pass

    if not oauth_token:
        raise AuthError("did not see oauth_token cookie within 10 minutes")

    if not email:
        # Fall back: prompt the user. Required for gpsoauth.
        print()
        email = input("[auth] enter the Google email you just signed in with: ").strip()
        if not email:
            raise AuthError("no email provided")

    return email, oauth_token


# ------------------------------------------------------------- login flow

def login() -> Credentials:
    """Run the full first-time login. Stores credentials, returns them."""
    email, oauth_token = _interactive_browser_signin()
    android_id = _new_android_id()
    print(f"[auth] exchanging oauth_token for master token (account: {email})...")
    try:
        resp = gpsoauth.exchange_token(email, oauth_token, android_id)
    except Exception:
        raise AuthError("Google token exchange failed.") from None
    master = resp.get("Token") if isinstance(resp, dict) else None
    if not master:
        raise AuthError("Google did not issue a master token.")
    try:
        _store_credentials(email, master, android_id)
    except Exception:
        raise AuthError("Windows Credential Manager could not save the login.") from None
    print(f"[auth] success — credentials stored in keyring for {email}")
    return load_credentials(email)


# --------------------------------------------------------------------- CLI

def _cmd_login(_args) -> int:
    try:
        creds = login()
        # Verify the master token works once after login. If this fails,
        # the user needs to know immediately, not later when sync breaks.
        try:
            bearer = creds.mint_bearer()
            print(f"[auth] verified — bearer ok ({len(bearer)} chars)")
        except AuthError as e:
            print(f"[auth] WARNING: stored ok but bearer failed: {e}", file=sys.stderr)
            print("[auth] try `python -m keep_protocol.auth login` again", file=sys.stderr)
            return 3
    except AuthError as e:
        print(f"[auth] error: {e}", file=sys.stderr)
        return 2
    return 0


def _cmd_logout(args) -> int:
    email = args.email or _get_active_email()
    if not email:
        print("[auth] nothing to log out of")
        return 0
    from config import clear_token
    clear_token(email)
    print(f"[auth] cleared credentials for {email}")
    return 0


def _cmd_status(_args) -> int:
    email = _get_active_email()
    if not email:
        print("[auth] not logged in")
        return 1
    try:
        creds = load_credentials(email)
        bearer = creds.mint_bearer()
    except AuthError as e:
        print(f"[auth] logged in as {email} but bearer mint failed: {e}")
        return 2
    print(f"[auth] logged in as {email}")
    print(f"[auth] master token: present ({len(creds.master_token)} chars)")
    print(f"[auth] bearer ok    ({len(bearer)} chars)")
    return 0


def _cmd_bearer(_args) -> int:
    creds = load_credentials()
    print(creds.mint_bearer())
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m keep_protocol.auth")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="interactive first-time login")
    p_logout = sub.add_parser("logout", help="forget stored credentials")
    p_logout.add_argument("--email", help="account to log out (default: active)")
    sub.add_parser("status", help="check if logged in and bearer mints ok")
    sub.add_parser("bearer", help="print a fresh bearer (debug)")
    args = p.parse_args(argv)
    return {
        "login": _cmd_login,
        "logout": _cmd_logout,
        "status": _cmd_status,
        "bearer": _cmd_bearer,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
