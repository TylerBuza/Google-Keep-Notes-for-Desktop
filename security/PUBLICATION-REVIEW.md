# Public repository preparation

Reviewed on 2026-10-29 before the first public release of this maintained project.
This is a publication check, not a comprehensive security certification.

## Scope

- Tracked source and retained Git history, using Gitleaks 8.30.1 with its default
  rules and `git --all` history scope. The tool's Windows download was checked
  against its published release SHA256 list.
- Git-tracked file inventory; local application state, virtual environments,
  build outputs and OpenChamber artifacts are excluded by `.gitignore`.
- README images are generated only from fictional notes in isolated app data.
  No user's note cache, credentials, or personal screenshots are included.
- Prior CI builds use isolated offline tests and contain no signed-in app data.

## Reviewed findings

Gitleaks reported two `gcp-api-key` findings in retained history: an older
`keep_protocol/client.py` revision and the later `network_policy.py` constant.
They identify the same embedded Google Keep web-client key already included in
the public upstream revision `60a70ffee9bed33fbc1aa373203a89a03df7dc3f`.

That client identifier is not a user's OAuth bearer/master token, password,
cookie, or personally provisioned Google Cloud key. Requests still require user
authentication. These findings were reviewed as pre-existing public protocol
material, not silently suppressed as a blanket exception for future keys.
No personal credential was identified by this scan. Scanner coverage is limited
and does not prove the absence of secrets or vulnerabilities.

## Retained safeguards

The app's runtime network allowlist, encrypted storage, sign-in consent, guarded
cloud-write opt-in, conflict/recovery policies and updater restrictions are
unchanged by public repository/release preparation. The original AGPL license,
author credit, source history and third-party notices remain present.
