"""Public release identity, verification gates and fictional documentation assets."""

from pathlib import Path
import re

from PySide6.QtGui import QImage

from config import APP_NAME, AUTO_UPDATE_ENABLED, GITHUB_REPO
from tools.preview_dashboard import fictional_notes


ROOT = Path(__file__).resolve().parents[1]


def test_public_identity_preserves_storage_and_disables_runtime_updates():
    assert GITHUB_REPO == "TylerBuza/Google-Keep-Notes-for-Desktop"
    assert APP_NAME == "KeepDesktop"
    assert AUTO_UPDATE_ENABLED is False
    installer = (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert '#define MyAppDisplayName "Google Keep Notes for Desktop"' in installer
    assert 'OutputBaseFilename=Google-Keep-Notes-for-Desktop-Setup-{#MyAppVersion}' in installer
    assert 'AppId={{E7720D5B-8CF2-4C7A-A6DA-6CA4B9A6A4DD}}' in installer
    assert 'LicenseFile=LICENSE' in installer
    assert 'Source: "CREDITS.md";' in installer


def test_build_cannot_automatically_publish_unreviewed_release_bytes():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "action-gh-release" not in workflow
    assert "gh release create" not in workflow
    assert "scan-inputs\\KeepDesktop.exe" in workflow
    assert "security-build-audit.json" in workflow
    assert "security-frozen-inventory.json" in workflow
    assert "security-fonts.json" in workflow


def test_readme_images_exist_and_are_real_nonempty_renders(qapp):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    images = re.findall(r"!\[[^\]]*\]\((docs/images/[^)]+)\)", readme)
    assert len(images) == 4
    for name in images:
        image = QImage(str(ROOT / name))
        assert not image.isNull()
        assert image.width() >= 800
        assert image.height() >= 300
    assert "fictional" in readme
    assert "VirusTotal" in readme
    assert "not a" in readme and "security guarantee" in readme


def test_render_samples_are_fictional_models_not_an_installed_cache():
    notes = fictional_notes()
    assert len(notes) == 6
    assert all(note.id.startswith("demo-") for note in notes)
    assert {note.title for note in notes} >= {"A little inspiration", "This week", "Weekend plans"}


def test_binary_review_is_disposable_offline_and_checks_exact_installed_bytes():
    workflow = (ROOT / ".github/workflows/verify-release.yml").read_text(encoding="utf-8")
    assert "runs-on: windows-latest" in workflow
    assert "$appHash -ne $scanHash" in workflow
    assert '"start_offline":true' in workflow
    assert '"cloud_writes_enabled":false' in workflow
    assert "KeepDesktop-DPAPI-v1" in workflow
    assert "security-binary-smoke.json" in workflow
    assert "contents: write" not in workflow


def test_publication_retains_credit_and_documents_reviewed_scanner_findings():
    credit = (ROOT / "CREDITS.md").read_text(encoding="utf-8")
    review = (ROOT / "security/PUBLICATION-REVIEW.md").read_text(encoding="utf-8")
    assert "LukeCGG" in credit and "AGPL-3.0" in credit
    assert "gcp-api-key" in review
    assert "not silently suppressed" in review
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (ROOT / "LICENSE").read_text(encoding="utf-8")
