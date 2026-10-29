# Experimental login review — 2026-10-28

## Decision and scope

The user selected **Retain experimental sync** after being offered removal of
the master-token login. This review documents reduced exposure, not a claim
that the master-token risk or all supply-chain risks were eliminated.
The user subsequently requested **remembered-only desktop sign-in** and a
visible Google URL. The previous session-only UI/duration cap has been removed,
not silently extended. Persistent storage still requires consent and confirmation.

Google's [Keep API overview](https://developers.google.com/workspace/keep/api/guides)
describes enterprise-admin access, not a supported personal-Keep replacement
for this app's native two-way sync. Google's [OAuth browser policy](https://developers.google.com/identity/protocols/oauth2/policies#browsers)
prohibits developer-controlled embedded user-agents for supported OAuth. This
unofficial Android flow is not Google-approved. If Google blocks it, stop.

## Implemented controls

- Explicit risk consent before loading the sign-in page; visible allowed
  HTTPS origin/path; no secret URL query/fragment displayed or logged.
- Exact `accounts.google.com` top-level navigation allowlist and separate restricted
  authentication/static-resource allowlist. No arbitrary third-party frames,
  executable data URLs, popups, downloads or certificate-error overrides.
  Google's `/recaptcha/` challenge frames are the only cross-origin frame exception.
- Off-record browser profile; memory-only cookies/cache/permissions. No
  JavaScript credential-field capture or manual cookie-paste/CLI bypass.
- Fixed Google auth endpoint, hostname/certificate verification, TLS >=1.2,
  explicit CA bundle, redirects/retries disabled, no environment proxy/netrc,
  bounded network calls and response bodies, redacted errors.
- Request only the Keep memento bearer scope, not reminders. Desktop login is
  remembered-only, with no fixed app duration cap. The encrypted master token
  renews Keep access across restarts until sign-out or Google's rejection/revocation.
- Saving the master token requires unchecked-by-default risk consent plus a
  second confirmation defaulting to No. No/Cancel stays offline; there is no
  automatic fallback to session-only storage. Credentials remain DPAPI protected.
- Cancellation, including cancellation immediately after exchange, signs out.
  Browser pages are discarded before teardown; the resource policy is removed
  only after page destruction, preventing a Qt/Python IO-thread shutdown hang.
- Cloud writes reset to OFF after each login. A non-secret `auth-validation.json`
  records bearer issuance/API-read status, requested scope and retention mode;
  it never records tokens, passwords, email addresses, note text or note counts.
- The Google-only list now excludes YouTube/recaptcha.net aliases. Both auth
  and Keep sync use endpoint-locked sessions without ambient proxies/netrc/CA
  overrides. Browser background channels, DNS prefetch and hyperlink auditing
  are denied; startup uses no system proxy and reviewed Chromium flags.
- Native notes cannot load linked resources or launch external links. About
  attribution is not clickable. Runtime updater networking code is removed.
  See [SECURITY.md](../SECURITY.md) for exact hosts and the app/OS boundary.

## Dependency checks actually performed

- `pip-audit 2.10.1`: **27 pinned packages, 0 known vulnerabilities, 0 skips**.
  Raw result: [dependency-audit.json](dependency-audit.json).
- Exact SHA256 wheel hashes generated from PyPI version metadata and verified
  with pip's hash-checking dry run for all 27 Windows x64 / CPython 3.13 pins.
- Local Qt: **6.12.0**; Qt WebEngine wheel: **6.12.0.140**, the latest versions
  reported by PyPI during this review. Chromium base reports **140.0.7339.225**;
  Qt's separate security-backport version reports **152.0.7977.83**.
- The legacy gkeepapi/gpsoauth/Cryptodome Python modules are excluded from the
  installer. Historical compatibility tests may still install them in the
  development environment. CI checks the actual frozen module inventory.
- CI also scans the full installed Python build environment before packaging.
  Its raw audit and frozen-inventory result accompany the installer artifact.

These are vulnerability-feed/provenance checks and focused auth-code review,
not an independent line-by-line review of every dependency or native library.
Zero published advisories is not proof of safety. Wheel hashes verify artifact
identity, not whether an upstream publisher or build runner was compromised.

## Live checks and remaining user step

The isolated anonymous Windows probe successfully loaded
`https://accounts.google.com` with an off-record profile, no saved-credential consent,
no credentials entered and no sync engine attached. Page/profile destruction and
application shutdown completed cleanly after the teardown fix. The automated
test suite also exercises spoofed origins, bad certificates, cancellation,
short-lived session expiry, transport failures and secret redaction.

The Google-only/remembered-only revision passed **510 offline tests** and three
consecutive anonymous Windows runs with clean application shutdown. The only
allowed resource hosts observed were `accounts.google.com`, `fonts.gstatic.com`
and `www.gstatic.com`. This is page-request observation, not a packet capture.
The WebEngine cookie handler now has an explicit `QNetworkCookie` Qt slot/type
registration before network startup; without the early binding import,
anonymous process teardown intermittently hung. No credential or note was used
in diagnosing this.

**Authenticated end-to-end login is still pending user participation.** No
Google password/2FA prompt has been completed by the agent. To complete it:

1. Install the new private build and leave cloud writes OFF. Decide whether
   you accept saving the powerful master token; otherwise stay offline.
2. Read/accept the experimental warning, verify the Google origin, and enter
   credentials/2FA yourself. Prefer a secondary account. Do not paste tokens
   into chat or bypass any Google warning.
3. Confirm that read-only Keep sync succeeds. The agent may inspect only the
   non-secret validation booleans/retention mode, not notes or credentials.
4. After a successful remembered sign-in, verify reconnect after a graceful
   restart, then sign out and verify saved credentials are cleared and startup
   no longer signs in. Never show the token file or its decrypted contents.

Until that user step passes, this report must not be described as a completed
authenticated-login test or a certification that the app is safe for a primary
Google account.
