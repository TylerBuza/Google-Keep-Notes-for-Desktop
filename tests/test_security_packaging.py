from pathlib import Path

import pytest

from tools.verify_frozen_security import verify


def inventory(extra=()):
    names = ("login_security", "keep_protocol.auth_transport", "note_model", "hardened_sync")
    return [(name, "dummy-path.py", "PYMODULE") for name in (*names, *extra)]


def test_frozen_security_inventory_requires_guarded_login(tmp_path):
    path = tmp_path / "analysis.toc"
    path.write_text(repr(inventory()), encoding="utf-8")
    assert verify(path)["legacy_auth_modules_absent"] is True


@pytest.mark.parametrize("name", ["keep_sync", "gkeepapi", "gkeepapi.node", "gpsoauth", "Cryptodome.Cipher.AES"])
def test_frozen_security_inventory_rejects_legacy_login_modules(tmp_path, name):
    path = tmp_path / "analysis.toc"
    path.write_text(repr(inventory([name])), encoding="utf-8")
    with pytest.raises(RuntimeError):
        verify(path)


def test_all_application_and_test_dependencies_have_exact_wheel_hashes():
    import re
    root = Path(__file__).resolve().parents[1]
    lines = (root / "requirements.lock").read_text(encoding="utf-8").splitlines()
    pins = [line for line in lines if line and not line.startswith("#")]
    assert len(pins) == 27
    assert all(re.fullmatch(r"[A-Za-z0-9_.-]+==[^\s]+ --hash=sha256:[0-9a-f]{64}", line) for line in pins)
