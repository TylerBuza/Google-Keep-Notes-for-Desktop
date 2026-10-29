# Security model and remaining limits

## What this copy changes

The desktop entry point uses `HardenedKeepSync`, never the legacy gkeepapi
writer. Remembered master tokens and the local notes cache use current-user
Windows DPAPI (not machine-wide protection). Secure-storage failure is an error;
there is no plaintext fallback. At the user's request the desktop login now
offers only **Remember on this PC**, with no fixed app duration cap. Saving the
powerful master token requires checking the explicit Remember box before
Continue; the requested UI has no second confirmation. Leaving it unchecked stays offline, not a different
credential-retention mode. Old credentials cannot auto-login without the
new consent version and explicit remember preference. Credential-manager access
is limited to Windows' native backend for legacy cleanup. Token responses are
not included in logs or exception messages; credentials have redacted reprs.
The account email is provided explicitly before Continue; it is needed by the
exchange protocol and is not read from Google's password/verification fields.

The experimental login window does not load Google's page until consent. It
displays the HTTPS origin and path (not secret query/fragment values), permits
top-level navigation only to `accounts.google.com`, and restricts resource requests to
an explicit set of Google authentication/static-resource hosts. The only
cross-origin frames allowed are Google's specific `/recaptcha/` challenges. Popups,
downloads, clipboard access from JavaScript and device permissions are denied.
Certificate failures are rejected with no override. The profile is off-record,
with memory-only cookies/cache/permissions; cancellation discards the page and
clears cookies. Manual-cookie paste and command-line token capture are removed.
The network interceptor remains active until page destruction, then detaches
before profile deletion to prevent Qt/Python shutdown deadlocks.

Before Continue, the browser is hidden and the window sizes to the compact
form. Continue expands it and loads Google without another confirmation. There
is no warning banner; credential-retention facts stay in the visible checkbox
and optional Security details. Routine background resource blocks remain
blocked but do not overwrite the page status with a red failure; blocked
frames/navigation and certificate errors still produce visible errors.

Token capture no longer immediately stops Google's active terms/consent
navigation. A worker performs the exchange while the UI stays cancellable;
page-load and exchange watchdogs report stalls without bypassing Google.
Cancellation/timeout prevents a late worker from retaining credentials.

`login-validation.json` contains booleans for page/cookie/exchange stages, safe
HTTP/failure categories, known Google error codes and bounded blocked-resource
metadata. Full URLs, arbitrary hostnames, emails, cookies, passwords, token
values, notes and note counts are excluded. Blocked hostnames are reported only
from a fixed set of known Google hosts; other names become "unlisted destination".
Transport errors distinguish TLS/connection/timeout/local-policy failures from
actual Google HTTP/structured rejections. A generic failure is not described
as proof that Google rejected the user's account.

The auth transport only POSTs to `https://android.clients.google.com/auth`, with
TLS >=1.2, certificate and hostname validation, a pinned CA-bundle dependency,
no redirects/retries/environment proxies/netrc, bounded timeouts/response size,
and fixed error messages. Only Keep's memento scope is requested for the bearer;
the unused reminders scope is no longer requested. This is not a read-only
OAuth scope: cloud-write restrictions remain an application policy.

## Google-only runtime network policy

`network_policy.py` is the destination list; no wildcard subdomains or third-party
fallbacks are allowed. Browser resources may use only these Google-owned hosts:
`accounts.google.com`, `ssl.gstatic.com`, `www.gstatic.com`, `fonts.gstatic.com`,
`www.google.com`, `recaptcha.google.com`, and `apis.google.com`. The previous
YouTube and recaptcha.net aliases are removed. Top-level navigation is still
only `accounts.google.com`; Google challenge frames are limited to `/recaptcha/`.
If this prevents a Google login flow, stop rather than silently widen the list.

Both protocol transports now use the same TLS-verified, exact-endpoint adapter:
auth can only POST to `android.clients.google.com/auth`, and note sync only to
`notes-pa.clients6.google.com/notes/v1/changes` (the fixed read/write variants).
Neither inherits environment proxies, netrc, or CA overrides; the adapter
rejects proxies, unsafe TLS settings and other endpoints before socket creation.
Redirects are disabled. The one allowed retry is after a definite Keep HTTP 401
rejection, not an uncertain write.

Browser settings disable DNS prefetch and hyperlink auditing; request policy
also denies WebSockets, workers/service workers, prefetch, pings/beacons, CSP
reports, plugins/media, unknown types and navigation-preload channels. Qt uses
no system proxy. Startup replaces ambient Chromium flags with reviewed flags
that reduce background networking, restrict DNS resolution to the resource
list and restrict non-proxied WebRTC UDP; remote debugging and sandbox-disable
environment overrides are removed. These settings are defense in depth, not
proof of packet-level isolation of Chromium internals.

Note rendering and HTML conversion cannot load linked resources (including
remote images, fonts, file and UNC paths). Note links never launch a browser,
mailer or dialer, even for Google links, since an external app could fetch other
resources. About uses selectable source/license attribution, not active links.
The runtime updater's HTTP/download/installer code is removed entirely, not
just hidden behind a setting. Source/build tools can still contact GitHub,
PyPI and advisory services separately; they receive no Google credentials.

The anonymous probe reports only allowed resource hostnames, never full URLs,
query parameters, passwords, tokens, email or note content. This observes
intercepted page requests, not all OS packets. Windows/DNS, certificate/OS
services, user-launched apps and a compromised OS/dependency are outside this
app-level control. Strict process-wide destination enforcement would require
a separately managed OS/network firewall; it is not claimed here.

## Sync safety

Cloud writes start disabled. Before writing an existing note, the app pulls
authoritative state and compares it against a persisted baseline. Concurrent
changes, including formatting, title and checklist changes, produce an
encrypted conflict copy rather than an automatic winner. Both sides can be
viewed through the tray's recovery viewer; selecting the Google version
also saves any additional desktop typing that occurred after the conflict.
A newer Google edit after a conflict decision triggers another conflict.

Before every write the app saves an encrypted recovery snapshot and a
durable write-intent marker. A write interrupted by a crash/timeout pauses
future writes until explicit acknowledgement. New-note server ids are
checkpointed locally before another create is allowed. Failed cloud deletes
leave the local note intact. Unsupported/malformed responses, stalled or
excessive pagination, stale snapshots, and unverified pre-write pulls block
writes. A failed multi-page pull rolls back its cursor and cache together.
Incoming cloud changes also get a recovery copy before replacing local
content, including old caches with no reliable dirty flag from upstream.

The private build never checks for or installs upstream executable updates.
Application/test dependencies are pinned to exact Windows x64 / CPython 3.13
wheel hashes, verified by pip. CI audits the installed Python build environment
with pip-audit and verifies that the frozen module inventory excludes legacy
gkeepapi/gpsoauth login paths. Hashes/CVE scans are not a full source-code audit.
Keep Qt/Chromium updated through deliberate, tested dependency upgrades.

## What it cannot guarantee

- The Keep endpoint and gpsoauth authentication remain unofficial. Google
  can break or block them. A supported personal Keep API cannot be added
  just by changing this app. Web/phone edits occurring after preflight
  still depend on Google's revision/conflict semantics. There is no claim
  of zero data loss or compatibility with arbitrary future protocol changes.
- A Google master token is sensitive and potentially more powerful than a
  Keep-only token. Local sign-out does not revoke it on Google's servers.
  Revoke the associated device/session in Google Account Security if exposed.
- The user explicitly chose to retain experimental native sync rather than
  remove this credential flow. That accepts the remaining master-token risk;
  it does not eliminate it. Transient strings cannot be guaranteed erased
  from Python memory. Remember mode deliberately retains the encrypted token.
- Google prohibits developer-controlled embedded user-agents for supported
  OAuth authorization. This experimental Android flow is not Google-approved
  and may be blocked. The app must not spoof its way around Google's blocks.
- Advisory scans do not prove that every dependency, native Qt/Chromium
  component, compiler or OS service is vulnerability-free. No independent
  line-by-line audit or external penetration test is claimed.
- DPAPI protects files at rest, not against other software running as you, an
  administrator, a compromised OS, or a compromised Python dependency. It
  is not a separate-password vault. Your Windows account is the boundary.
- DPAPI files generally cannot be opened by another Windows user or after
  losing the original Windows profile/key material. They are recovery
  snapshots, **not portable backups**. Export Google Takeout backups too.
- Window positions, visibility and preferences remain plaintext metadata.
  Google still stores cloud notes. Earlier upstream logs/browser profiles,
  filesystem history and old external backups are not securely erased by
  this copy. Migration deletes the old live plaintext file only after a
  successful encrypted save; filesystem forensic erasure is not promised.
- Text/checklist conversion of existing cloud notes is intentionally blocked
  rather than using a known lossy legacy fallback. Images, attachments,
  reminders and collaboration are not certified for lossless round trips.
- Recovery snapshots are retained locally without automatic pruning. The
  viewer shows the most recent 200; older encrypted files remain under
  `%APPDATA%\KeepDesktop\recovery`. Treat them as sensitive note history.
- Automated tests never enter Google credentials or write production notes.
  An isolated anonymous probe verifies the real Google HTTPS page and clean
  browser/profile shutdown; it is NOT an authenticated-account test. A real
  end-to-end login requires the user to complete Google's prompts manually.
  Test with a secondary account before trusting important notes.

## Safe first run and recovery

1. Export Keep with Google Takeout. Use a secondary account initially.
2. Run from this repository, not an upstream installer. Sign in and verify
   read-only synchronization before enabling cloud writes in the tray. Google
   sign-in now requires accepting remembered-master-token storage; choose
   Cancel/offline if you do not accept that risk.
3. For conflicts, open **View saved recovery copies** to view/copy both
   versions, then **Resolve sync conflicts** to explicitly choose. A removed
   or archived original is not automatically resurrected; copy recovered
   text into a new note if desired.
4. After a timeout/crash, inspect Keep on the web before re-enabling writes,
   particularly after creating a note: the server may already have applied
   a request whose response was lost. The pause marker includes a confirmed
   server id if creation completed but local checkpointing was interrupted.
5. Keep one Google account per local notebook. To switch, sign out of the
   previous account and choose **Clear live local notes**. Encrypted recovery
   history is retained; it is not silently uploaded to another account.

## Tests

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest tests -q
```

The test process uses a temporary APPDATA and blocks live `requests` network
traffic. Credential-store tests mock keyring operations. DPAPI round-trip
tests protect only dummy data in temporary files.

## License and upstream

This is a private standalone copy of LukeCGG/Keep-Desktop, retaining the
complete upstream Git history and AGPL-3.0 license. Private personal use does
not require publishing the repository. Distribution or covered network use
of modified versions must comply with AGPL-3.0; see LICENSE.
