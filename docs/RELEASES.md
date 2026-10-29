# Releases and verification

## For users

Download only from this project's
[GitHub releases](https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop/releases).
The Windows x64 setup EXE is named
`Google-Keep-Notes-for-Desktop-Setup-<version>.exe`.

Each reviewed public release includes:

- The installer and a SHA256 checksum sidecar.
- `security-build-audit.json`: the Python build-environment dependency audit.
- `security-frozen-inventory.json`: required security modules present and legacy
  authentication modules excluded from the frozen application.
- `security-fonts.json`: packaged font hashes and license-notice checks.
- Antivirus report summaries/permalinks for the exact installer and packaged
  application EXE, when those analyses have completed. The release notes name
  any missing, pending, unsupported, or detected result explicitly.

Check hashes with `Get-FileHash <installer> -Algorithm SHA256`. A hash identifies
bytes; it is not a digital signature or proof of safety. The app is currently
unsigned. VirusTotal results are a snapshot from multiple engines, not a
certification; no detection should be dismissed automatically as a false positive.
No scan result is invented or carried over from a differently hashed build.

The packaged app EXE needs its accompanying Qt/Python files and is **not** a
portable single-file download. Install using the setup EXE.

## Maintainer build

The `release.yml` workflow can be dispatched with a version, or run for a `v*`
tag. It creates artifacts only, with read-only repository permissions; it never
automatically makes binaries into a published GitHub release.

```powershell
gh workflow run release.yml --repo TylerBuza/Google-Keep-Notes-for-Desktop --ref main -f version=2.3.0
```

For a local build, use Python 3.13 on Windows and Inno Setup 6. CI is preferred
for public releases so the audit and exact source revision are recorded.

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.lock
.\.venv\Scripts\python.exe -m pip install pyinstaller==6.22.3 pip-audit==2.10.1
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest tests -q
mkdir Output -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pip_audit --local --format json --output Output/security-build-audit.json
$env:QT_QPA_PLATFORM = 'windows'
.\.venv\Scripts\python.exe tools/generate_icon.py
.\.venv\Scripts\python.exe -m PyInstaller KeepDesktop.spec
.\.venv\Scripts\python.exe tools/verify_frozen_security.py build/KeepDesktop/Analysis-00.toc | Out-File Output/security-frozen-inventory.json -Encoding utf8
.\.venv\Scripts\python.exe tools/verify_frozen_fonts.py dist/KeepDesktop | Out-File Output/security-fonts.json -Encoding utf8
ISCC.exe /DMyAppVersion=2.3.0 /DMyAppNumericVersion=2.3.0 installer.iss
```

CI also stamps `config.APP_VERSION`. For a local release, stamp the same version
before freezing rather than shipping the development sentinel `0.0.0`.

## Publication checklist

1. Review tracked files and Git history for credentials and private data before
   changing repository visibility. Record reviewed scanner findings rather than
   pretending that a known exception was never detected.
2. Freeze the source revision. Run the full offline tests and synthetic headed
   UI tests. Render README images only from fictional, isolated data.
3. Download the successful build's artifact. Verify its source SHA, version,
   installer/application hashes, dependency audit, frozen inventory and fonts.
4. Scan the **exact final installer** and **packaged application EXE** with
   VirusTotal. Public VirusTotal uploads share the submitted binaries with the
   security community. Never upload credentials, personal notes, or app-data
   directories. Use an authenticated API key or the official upload interface;
   do not bypass an access challenge. Any API key stays outside Git and logs.
5. Wait for the analyses, record totals, detections and permanent report links
   beside the SHA256. Inspect actual findings; stop to investigate rather than
   modifying the binary to evade detection. Pending analysis is not a pass.
6. Perform an isolated offline binary smoke test. Do not use a real account for
   tests or enable cloud writes as part of release preparation.
7. Create the release against the exact build commit, attach installer, hashes,
   verification reports, scan summaries and release notes, then publish. Provide
   corresponding source through that exact tag, retaining LICENSE and CREDITS.
8. Never replace a published asset with different bytes under the same version
   and scan claim. Rebuilds get a new version and fresh hashes/analyses.

Maintainer antivirus tools are release-time tooling only. No VirusTotal traffic
or API credential is added to the installed app's Google-only runtime networking.
