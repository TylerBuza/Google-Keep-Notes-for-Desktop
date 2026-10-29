# KeepDesktop

**Google Keep as floating sticky notes on your Windows desktop.** Each note gets its own draggable window — just like Windows Sticky Notes, but synced with Google Keep.

![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)

## Private security-hardened copy

Derived from [LukeCGG/Keep-Desktop](https://github.com/LukeCGG/Keep-Desktop),
upstream commit `60a70ffee9bed33fbc1aa373203a89a03df7dc3f` (v2.2.0).
Original history, attribution and AGPL-3.0 license are retained. This is a
private standalone repository because GitHub public forks cannot be private.

- **Protected credentials and notes:** per-Windows-user DPAPI encryption,
  atomic saves, migration of the old plaintext cache/token, and sign-out
  that clears both stored credentials and in-memory access. Login cookies
  use an off-the-record browser profile.
- **Cloud writes are OFF initially.** Export a Google Takeout backup first,
  then opt in via the tray's **Enable Google Keep writes** toggle.
- **Fail-safe sync:** reject malformed/incomplete responses, roll back failed
  paginated pulls, refuse writes against stale/unverified state, and pause
  after uncertain writes. A persistent write-intent marker also pauses after
  a crash during a request. No silent legacy-writer fallback.
- **Conflicts preserve both versions:** any concurrent change to a note's
  text, formatting, title, colors or checklist pauses its push. Choose a
  version from **Resolve sync conflicts…**, or copy text from **View saved
  recovery copies…**. The policy is intentionally conservative, including
  non-overlapping edits to the same note. No automatic local-wins merge.
- **No automatic installer updates:** unsigned upstream binaries cannot
  silently replace this build. Dependency versions used for testing are
  frozen in `requirements.lock`.

Google's internal API is **still unofficial**. It cannot be made supported
by a fork. See [SECURITY.md](SECURITY.md) for limits and recovery guidance.

## Download & Install

Run this copy from source using the commands below. Upstream installers do
**not** include these security changes. No installer has been security-certified.

## Features

- **One window per note** — drag them anywhere on screen
- **Remembers positions** — every window reopens exactly where you left it
- **Google Keep sync** — two-way sync keeps your notes in the cloud
- **System tray** — lives quietly in your taskbar tray
- **Auto-start** — optional "Start with Windows" toggle
- **Reviewed updates only** — automatic installer downloads disabled
- **Color-coded** — all 12 Google Keep colors + local dark mode varients
- **Pin notes** — toggle always-on-top per note
- **Minimal UI** — frameless, clean, stays out of your way

## Connecting Google Keep

1. Right-click the tray icon → **Sign in to Google Keep**
2. A Google sign-in window opens — enter your Google account credentials and complete any 2-Step Verification prompts as normal.
3. Once you're signed in, the window closes automatically and your Keep notes start appearing as floating windows.

Google handles the password/2FA page; the app retains a sensitive long-lived
master token, encrypted with Windows DPAPI in `%APPDATA%\KeepDesktop\keep_token.dat`.
Optional CLI login uses Windows Credential Manager. This is not a normal
limited-scope public OAuth integration. Revoke this device/session from
[Google Account → Security → Your devices](https://myaccount.google.com/device-activity)
if needed. Signing out locally does not revoke Google's server-side token.

## Usage

| Action | How |
|---|---|
| **New note** | Right-click tray → "New note" |
| **Move a note** | Drag the handle (≡) on the title bar |
| **Edit title** | Click the title text |
| **Resize** | Drag the bottom-right corner grip |
| **Change color** | Click 🎨 on the title bar |
| **Pin on top** | Click 📌 on the title bar |
| **Hide a note** | Click ✕ (note is hidden, not deleted) |
| **Delete a note** | Right-click the note body → "Delete note" |
| **Manage notes** | Double-click tray icon |
| **Sync now** | Right-click tray → "Sync now" |
| **Enable cloud changes** | Tray → "Enable Google Keep writes" (opt-in) |
| **Choose conflicting version** | Tray → "Resolve sync conflicts…" |
| **Recover older text** | Tray → "View saved recovery copies…" |
| **Auto-start** | Right-click tray → "Start with Windows" |

## Building from Source

### Run from Python

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

### Build the Windows installer

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php) on your `PATH`.

```powershell
pip install -r requirements.lock pyinstaller
pyinstaller KeepDesktop.spec
ISCC.exe installer.iss
```

The installer is written to `Output\KeepDesktop-Setup-<version>.exe`.

GitHub Actions does this automatically on every `v*` tag — see `.github/workflows/release.yml`.

## How It Works

- Config, positions, notes cache, and the auth token live in `%APPDATA%\KeepDesktop\`
- Sync talks directly to Google Keep's internal
  `notes-pa.clients6.google.com/notes/v1/changes` endpoint via
  `keep_protocol/`
- Auth uses [`gpsoauth`](https://github.com/simon-weber/gpsoauth) — your password is never stored, only the master token locally
- Offline edits, dirty flags and their confirmed sync baselines survive restarts
- Local notes sync only after cloud writes are explicitly enabled
- Changing an existing note between text/checklist is deliberately blocked;
  do that in the official Keep app instead

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point |
| `app_controller.py` | Tray icon, note lifecycle, sync orchestration |
| `note_window.py` | Individual sticky-note window (frameless Qt widget) |
| `keep_protocol/` | First-party Keep `/notes/v1/changes` client (auth, encode, decode, models) |
| `keep_sync_v2.py` | High-level sync engine on top of `keep_protocol` |
| `hardened_sync.py` | Write opt-in, conflict policy, recovery and pause journal |
| `secure_storage.py` | Windows DPAPI encryption and atomic file replacement |
| `keep_sync.py` | Legacy compatibility shim (KeepNote dataclass + colour maps) |
| `sync_merge.py` | Three-way local/remote merge decisions |
| `updater.py` | GitHub Releases auto-updater |
| `config.py` | Settings, positions, autostart, paths, version |
| `KeepDesktop.spec` | PyInstaller build spec |
| `installer.iss` | Inno Setup installer script |

## License

KeepDesktop is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0).

This means:

- You may use, copy, modify, and redistribute it freely.
- You may run it for any purpose, personal or internal-business.
- If you distribute it, modified or not, the **complete corresponding source code** must be made available under the same AGPL-3.0 license.
- If you run a modified version on a network server that users interact with, you must offer them the modified source code.
- You **may not** rebrand it, repackage it, or sell it as a closed-source / proprietary product.

See [LICENSE](LICENSE) for the full legal text.

## Contributing

Issues and pull requests welcome at <https://github.com/LukeCGG/Keep-Desktop>.

## Disclaimer

KeepDesktop uses an **unofficial** Google Keep API. Google may change or break this at any time. Use at your own risk; do not rely on this for mission-critical data. The project is not affiliated with or endorsed by Google.
