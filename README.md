# KeepDesktop

**Google Keep as floating sticky notes on your Windows desktop.** Each note gets its own draggable window — just like Windows Sticky Notes, but synced with Google Keep.

![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)

## Private security-hardened copy

Derived from [LukeCGG/Keep-Desktop](https://github.com/LukeCGG/Keep-Desktop),
upstream commit `60a70ffee9bed33fbc1aa373203a89a03df7dc3f` (v2.2.0).
Original history, attribution and AGPL-3.0 license are retained. This is a
private standalone repository because GitHub public forks cannot be private.

- **Remembered Google sign-in:** the requested desktop mode saves a powerful
  master token with Windows encryption after checkbox consent. It
  renews Keep access across restarts, with no fixed app time limit.
- **Protected remembered credentials and notes:** per-Windows-user DPAPI encryption,
  atomic saves, migration of the old plaintext cache/token, and sign-out
  that clears both stored credentials and in-memory access. Login cookies
  use an off-the-record browser profile.
- **Cloud writes are OFF initially.** Export a Google Takeout backup first,
  then opt in via **Two-way Google Keep sync (text, pins, checklists)** in the tray.
  New explicit consent is remembered for the same account across reconnects;
  sign-out, read failures and uncertain writes still disable writing.
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
  frozen with exact wheel SHA256 hashes in `requirements.lock`.
- **Guarded experimental login:** visible HTTPS origin/path, strict navigation
  and resource allowlists, rejected certificate errors, no popups/downloads,
  no manual-cookie/CLI capture bypass, and no automatic import of old logins.
  See [the login review](security/LOGIN-REVIEW.md) for tested checks and limits.
- **Google-only runtime networking:** exact Google-owned destination lists,
  direct HTTPS without environment/system proxies, and no runtime updater.
  Notes cannot fetch linked resources or launch external links. This is an
  app-level restriction, not an OS firewall or protection from compromised software.

Google’s internal API is **still unofficial**. It cannot be made supported
by a fork. See [SECURITY.md](SECURITY.md) for limits and recovery guidance.

## Download & Install

Run this copy from source using the commands below. Upstream installers do
**not** include these security changes. No installer has been security-certified.

## Features

- **One window per note** — drag them anywhere on screen
- **Remembers positions** — preserves desktop placement and manual sizes; auto-height follows content
- **Experimental Google Keep sync** — read/import, with guarded writes opt-in only
- **System tray** — lives quietly in your taskbar tray
- **Auto-start** — optional "Start with Windows" toggle
- **Reviewed updates only** — automatic installer downloads disabled
- **Color-coded** — all 12 Google Keep colors + local dark mode varients
- **Pin notes** — toggle always-on-top per note
- **Minimal UI** — frameless, clean, stays out of your way

## Keep-style dashboard

Open **Manage notes** from the tray for the native dark dashboard: searchable
masonry cards, Pinned/Other sections, grid/list view, note colors, and a separate
**Desktop pins** view. No website, remote fonts, or linked images are loaded.
The **Keep Desktop** header uses Google's original Keep logo, bundled locally;
the app retains its distinct K icon in the tray/taskbar. Google Keep and its logo
are Google's trademarks. This unofficial app is not affiliated with or endorsed
by Google. Asset provenance is recorded in `keep_brand.py`.

- Click a card to open an **in-app editor popup**, not another window. Clicking
  outside, clicking the logo, **Close**, or Escape saves locally and returns to
  Notes. **Cancel** confirms before discarding changes. A storage failure or
  conflicting newer edit keeps the popup/draft open rather than losing work.
- Click the **Keep Desktop** logo/title to return to all Notes and clear search.
  An unchanged popup closes without creating a phantom edit or empty new note.
- **Take a note…** and the checklist button create drafts without opening a
  floating window. Empty untouched drafts do not create notes.
- **Thumbtack** changes Keep pinning/order. **Monitor** opens an always-on-top
  desktop note; clicking it again hides that window without changing Keep's pin.
  With cloud writes off, thumbtack changes remain local and a notice says so.
  Enabling writes in the tray opts into uploading **all pending edits**, not
  just pins. Pin-only uploads use guarded metadata writes, not body rewrites.
- Color display uses local dark equivalents without changing cloud note colors.
  Explicit color selection is a normal edit, still subject to cloud-write opt-in.
- Popups match the card's dark color and fit their content: short notes stay
  compact, growing notes expand up to the app's available height and then scroll.
  Desktop notes also auto-fit height without changing their width/placement.
  Manually dragging a top/bottom/corner resize handle keeps that height; turn
  **Fit height to content** back on from the desktop note's context menu.
- Default card/editor/floating-note text is larger. Google Sans titles and Roboto
  bodies/controls use smooth grayscale antialiasing without forced full pixel-grid
  hinting. The original OFL-licensed fonts and notices are bundled locally, not
  installed system-wide or downloaded at runtime. See `assets/fonts/README.md`.
- More actions include reorder, delete (with confirmation), and type conversion
  for local notes only. Failed/blocked deletes leave the card present.

Archive and Trash browsing, label editing, attachments, and reminders are **not
implemented**. Archive/Trash navigation is disabled; any labels already in the
local note model can be viewed/filtered but are not a claim of label sync support.
Account and cloud-write controls remain in the tray menu. The dashboard does not
enable writes or change remembered-login security.

## Connecting Google Keep

1. On first open, the compact sign-in prompt appears automatically. It also
   returns after a cancelled/failed sign-in. **Use offline** explicitly skips
   future prompts; sign in later from the tray. Remembered sign-in reconnects
   automatically when still valid.
2. Select **Remember on this PC (powerful master token, encrypted)**, then click
   **Continue to Google**. Enter your email only on Google's sign-in page.
   The checkbox is the only storage confirmation. The compact window expands
   only after Continue; optional explanation is under **Security details**.
   Choose Cancel to stay offline; there is no session-only desktop option.
   The app requires Google to return the account identity; it does not scrape
   the sign-in form or fall back to a separately entered email.
3. Verify the displayed address starts with `https://accounts.google.com`.
   Complete Google's password/2FA prompts yourself. If Google blocks the embedded
   browser, stop—this app offers no security-warning bypass.
4. On success, the window closes and notes sync into the manager. Cloud writes
   default to OFF. A new explicit two-way-sync opt-in persists only for the same
   account; older session-only write flags do not silently become persistent.
   Review/back up queued edits before opting in. Uncertain writes still pause.
   The manager opens after the first verified download, so you can choose which
   Google notes appear as desktop windows. Later background syncs do not reopen it.

This is **not** a Google-approved public OAuth integration. After checkbox consent,
the master token is saved with Windows DPAPI in
`%APPDATA%\KeepDesktop\keep_token.dat` and reused on later starts. Keep access
tokens are renewed as needed. This continues until local sign-out or Google's
rejection/revocation, not for a guaranteed number of days. The previous
session-only duration cap is not used by the desktop login.

Other software running as your Windows user can access the saved credential. Python
cannot guarantee forensic erasure of transient strings from memory.
Legacy credential-manager copies are cleaned up on sign-out. CLI cookie
capture and bearer-printing commands are disabled. Revoke the device/session from
[Google Account → Security → Your devices](https://myaccount.google.com/device-activity)
if needed. Signing out locally does not revoke Google's server-side token.

If sign-in fails, `login-validation.json` records only fixed failure categories,
HTTP status, known Google error codes and safe request metadata. It contains no
email, password, cookies, tokens, note text or full URLs. Background resource
blocks appear under Security details rather than as a red login failure.
Google sign-in and read access were verified in a manual test. The final-page
import fix in private.6 passed a fresh app read/decode check and the user confirmed
notes appeared. Live cloud writes remain unverified. A separate browser shutdown
hang remains under investigation.

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
| **Enable cloud changes** | Tray → "Two-way Google Keep sync (text, pins, checklists)" (opt-in) |
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
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.lock
.\.venv\Scripts\python.exe -m pip install pyinstaller==6.22.3 pip-audit==2.10.1
.\.venv\Scripts\python.exe -m PyInstaller KeepDesktop.spec
mkdir Output -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pip_audit --local --format json --output Output/security-build-audit.json
.\.venv\Scripts\python.exe tools/verify_frozen_security.py build/KeepDesktop/Analysis-00.toc | Out-File Output/security-frozen-inventory.json -Encoding utf8
ISCC.exe installer.iss
```

The installer is written to `Output\KeepDesktop-Setup-<version>.exe`.

GitHub Actions does this automatically on every `v*` tag — see `.github/workflows/release.yml`.

## How It Works

- Config, positions, notes cache, and the auth token live in `%APPDATA%\KeepDesktop\`
- Sync talks directly to Google Keep's internal
  `notes-pa.clients6.google.com/notes/v1/changes` endpoint via
  `keep_protocol/`
- Auth uses a small endpoint-locked transport derived from the documented
  gpsoauth protocol. The legacy gpsoauth/gkeepapi code remains for historical
  tests only and is explicitly excluded from the packaged desktop app.
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
| `updater.py` | No-network compatibility stub; runtime updater removed |
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
