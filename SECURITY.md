# Security model and remaining limits

## What this copy changes

The desktop entry point uses `HardenedKeepSync`, never the legacy gkeepapi
writer. Tokens and the local notes cache use current-user Windows DPAPI
(not machine-wide protection). Secure-storage failure is an error; there is
no plaintext fallback. Optional CLI credentials explicitly use Windows
Credential Manager, not arbitrary keyring backends. Token responses are
not included in logs or exception messages, and credentials have redacted
representations. Browser login uses a non-persistent context.

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
Dependency versions are frozen, not cryptographically audited. Keep the Qt/
Chromium runtime updated through deliberate, tested dependency upgrades.

## What it cannot guarantee

- The Keep endpoint and gpsoauth authentication remain unofficial. Google
  can break or block them. A supported personal Keep API cannot be added
  just by changing this app. Web/phone edits occurring after preflight
  still depend on Google's revision/conflict semantics. There is no claim
  of zero data loss or compatibility with arbitrary future protocol changes.
- A Google master token is sensitive and potentially more powerful than a
  Keep-only token. Local sign-out does not revoke it on Google's servers.
  Revoke the associated device/session in Google Account Security if exposed.
- DPAPI protects files at rest, not against malware running as you, an
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
- No live Google account or production-note writes are exercised by tests.
  Test with a secondary account before trusting important notes.

## Safe first run and recovery

1. Export Keep with Google Takeout. Use a secondary account initially.
2. Run from this repository, not an upstream installer. Sign in and verify
   read-only synchronization before enabling cloud writes in the tray.
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
