<div align="center">

# Google Keep Notes for Desktop

**Your notes. Beyond the browser.**

A native Windows dashboard, floating desktop notes, and guarded Google Keep sync.

[![Download](https://img.shields.io/github/v/release/TylerBuza/Google-Keep-Notes-for-Desktop?label=Windows%20setup&color=fbbc04)](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/releases/latest)
[![Tests](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/actions/workflows/test.yml/badge.svg)](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/actions/workflows/test.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

[Download Windows setup](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/releases/latest) · [Release verification](docs/RELEASES.md) · [Security](SECURITY.md) · [Report an issue](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/issues)

![Native dashboard and floating notes](docs/images/overview.png)

</div>

> **Unofficial and experimental.** This is not a Google product or a supported
> Google API integration. Export a Google Takeout backup before enabling cloud
> writes. The installer is currently unsigned; an antivirus report is not a
> security guarantee. Review the release hashes, scan results, and known limits.

## A desktop home for your notes

Keep your notes in a familiar dark dashboard—or pull a few onto your desktop
while you work. Search, edit, organize, and switch between the two without
turning a browser tab into your workspace.

- **A native Keep-style dashboard.** Masonry cards, search, grid/list layouts,
  pinned sections, labels already present in the local model, and note colors.
- **Floating desktop notes.** Move and resize individual notes, keep them above
  other windows, hide them without deleting, and restore their placement later.
- **Editing that stays out of the way.** Card-colored in-app editors save locally
  when closed or clicked outside. Floating notes wrap titles and fit their
  content; text-formatting tools expand only when needed.
- **Familiar typography.** Bundled Google Sans titles and Roboto body text, with
  smooth native rendering and no app-interface font downloads.
- **Local-first behavior.** Work offline; local edits and pending sync state
  survive restarts. Nothing uploads until cloud writes are explicitly enabled.
- **Guarded two-way sync.** Text, formatting, checklists, colors, Keep pins, and
  ordering use conflict checks, encrypted recovery copies, and write-pause
  safeguards. Uncertain writes pause instead of being silently retried.
- **A quiet tray companion.** Open the dashboard, sync, sign in/out, and manage
  desktop visibility from the tray. No automatic installer downloads.

**Two different pins:** the thumbtack controls Google Keep pinning. The monitor
controls desktop visibility or local always-on-top behavior; it does not change
the note's Google Keep pin.

## Take a look

All images below are captured from the actual native interface using **fictional
demo notes and isolated application data**. The displayed sync state is also
fictional—not a capture of a personal account.

### Everything in one place

![Searchable dark dashboard with fictional notes](docs/images/dashboard.png)

### Edit without leaving the dashboard

![Embedded editor with a card-colored surface](docs/images/editor.png)

Click outside, use **Close**, press Escape, or select the header to save locally
and return to Notes. Storage errors and conflicting newer edits keep your draft
open rather than silently losing it.

### Keep a few notes close

![Floating text and checklist notes](docs/images/floating-notes.png)

Drag the grip above a title. Use the monitor to toggle always-on-top, the palette
to change color, and **×** to hide. Drag an edge or corner to resize; **More → Fit
height to content** restores automatic height. Hide and Delete are separate
actions, and deletion uses the existing confirmation and guarded sync path.

## Install

**Windows 10/11, x64.**

1. Open the [latest release](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/releases/latest).
2. Download `Google-Keep-Notes-for-Desktop-Setup-<version>.exe` and its `.sha256`
   file. Compare the hash and review the linked VirusTotal reports.
3. When upgrading, let sync finish, save edits, and use **tray → Quit** first.
4. Run the setup EXE. Installation is per-user; startup with Windows is optional.

```powershell
Get-FileHash .\Google-Keep-Notes-for-Desktop-Setup-2.3.0.exe -Algorithm SHA256
```

Release assets include exact-byte hashes, dependency-audit and packaging reports,
and the available antivirus results. Read [how releases are verified](docs/RELEASES.md).
Do not disable antivirus or dismiss a detection simply because this project
expects its binary to be safe. Inspect the evidence and report concerns.

For upgrade compatibility, the executable, shortcuts, installation folder, and
application-data folder still use the internal name **KeepDesktop**. A branding
change does not migrate or reset your saved notes, credentials, or sync consent.

## Start offline—or connect Google Keep

Choose **Use offline** to start without connecting an account. Sign in later
from the tray if you want Google Keep integration.

To connect:

1. Read the sign-in prompt and explicitly select **Remember on this PC**.
2. Continue to Google's page. Verify the visible HTTPS address is
   `accounts.google.com`; enter your email, password, and 2FA **only there**.
3. After a successful read, choose which notes should appear on the desktop.
4. Cloud writes start **OFF**. Back up your Google notes first, then explicitly
   enable **Two-way Google Keep sync (text, pins, checklists)** from the tray.

**Enabling writes includes all queued local edits**, not just the note currently
open. Consent persists for the same account. Sign-out, account mismatch, failed
reads, conflicts, or uncertain writes retain their fail-safe behavior.

### What remembered sign-in means

The app stores a powerful Google master token protected by Windows DPAPI for
your Windows user. Other software running as that same user can potentially
access it. There is no session-only desktop sign-in mode or fixed token-expiry
promise. Sign-out clears local credentials, but does not itself revoke Google's
server-side session; use Google Account security controls to revoke it.

## Privacy and safety, without the marketing shortcuts

- Credentials, note cache, and recovery data use per-user Windows encryption and
  atomic saves. App data is stored under `%APPDATA%\KeepDesktop\`.
- Runtime networking is restricted by application policy to reviewed Google
  destinations. No telemetry endpoint or automatic binary updater is enabled.
- Notes do not fetch linked images/resources or open external links. Google
  sign-in has a visible origin and rejects certificate errors and unapproved
  navigation; there is no TLS/sandbox bypass.
- Conflicts preserve both versions. Use **Resolve sync conflicts…** or **View
  saved recovery copies…** from the tray rather than overwriting uncertain state.
- A completed read and “writes enabled” status do **not** prove that every queued
  upload succeeded. Antivirus and dependency scans do **not** certify the app.

These are application-level controls—not an OS firewall, a Google endorsement,
or protection from software already running as your Windows user.
Read [SECURITY.md](SECURITY.md) and the [login review](security/LOGIN-REVIEW.md)
before entrusting important notes or credentials to the app.

## Current limits

| Area | Status |
| --- | --- |
| Text, titles, formatting, colors, checklists, Keep pins/order | Guarded experimental sync; opt-in writes |
| Desktop placement, visibility, always-on-top | Local to this PC |
| Archive/Trash browsing | Not implemented; navigation disabled |
| Label editing, attachments, reminders | Not implemented |
| Existing remote note-type conversion | Blocked; use the official Keep app |
| Automatic updates and code signing | Not available |
| macOS/Linux | Not supported by this Windows/DPAPI implementation |

Google can change its internal API without notice. A separate WebEngine shutdown
hang after sign-in remains under investigation. Sign-in/read access has been
manually verified, but live cloud-write correctness has not been comprehensively
verified. Do not rely on this as the only copy of important notes.

## Develop

Use **Python 3.13 on Windows**. No real Google account is needed for offline tests
or README renders.

```powershell
git clone https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop.git
cd Google-Keep-Notes-for-Desktop
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

### Test and render

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe tools\render_project.py --output docs\images
```

Tests use temporary app data and block live `requests` traffic. The image renderer
creates native widgets directly with fictional notes; it never starts a sync
controller or reads the installed account's note cache.

### Build a setup EXE

Use the [Build & Release workflow](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/actions/workflows/release.yml)
with an explicit version. It runs tests and a dependency audit, builds with
PyInstaller/Inno Setup, verifies frozen security modules and bundled font assets,
and uploads build artifacts. **It does not automatically publish a release.**
Publication happens only after review of the exact installer bytes and antivirus
results. Local build and publication instructions are in [docs/RELEASES.md](docs/RELEASES.md).

### Source map

| Component | Location |
| --- | --- |
| Dashboard and embedded editor | `note_manager.py`, `dashboard_theme.py` |
| Floating desktop notes | `note_window.py`, `content_sizing.py` |
| Tray, lifecycle, sync orchestration | `app_controller.py` |
| Keep protocol and sync | `keep_protocol/`, `keep_sync_v2.py` |
| Consent, conflicts, recovery, write pauses | `hardened_sync.py`, `sync_merge.py` |
| Sign-in and destination policy | `login_security.py`, `network_policy.py` |
| Windows encrypted storage | `secure_storage.py` |
| Fonts and branding provenance | `ui_typography.py`, `keep_brand.py`, `assets/fonts/` |

## Contribute

[Open an issue](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/issues)
or a pull request in **this repository**. Include the version, Windows version,
reproduction steps, and fictional examples where possible. Do not post tokens,
cookies, personal notes, or unredacted account logs. See [SECURITY.md](SECURITY.md)
for security concerns.

## Credits and license

Maintained by [TylerBuza](https://github.com/TylerBuza). Built on
[Keep-Desktop by LukeCGG](https://github.com/LukeCGG/Keep-Desktop) and its
contributors; original history, notices, and author credit are retained.
This is its own standalone repository, not a claim that all code was written
from scratch. See [CREDITS.md](CREDITS.md).

Application source is licensed under **[AGPL-3.0](LICENSE)**. Distribution and
covered network use must comply with that license, including applicable source
availability requirements. Bundled Google Sans/Roboto fonts have their own OFL
license and notices. Google Keep and its logo are Google's trademarks.

**Not affiliated with, endorsed by, or supported by Google.**
