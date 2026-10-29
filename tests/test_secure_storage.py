from pathlib import Path
from unittest.mock import MagicMock

import pytest

import config
import secure_storage as storage
from keep_protocol.auth import Credentials


def test_dpapi_roundtrip_has_no_plaintext_on_disk(tmp_path):
    path = tmp_path / "secret.dat"
    value = {"token": "dummy-secret-not-a-real-google-token", "text": "private ☀ notes"}
    storage.save_protected(path, value)
    assert value["token"].encode() not in path.read_bytes()
    assert value["text"].encode() not in path.read_bytes()
    assert storage.load_protected(path) == value
    assert list(tmp_path.iterdir()) == [path]


def test_tampered_ciphertext_is_not_replaced_with_empty_data(tmp_path):
    path = tmp_path / "notes.dat"
    storage.save_protected(path, {"notes": "important"})
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    with pytest.raises(storage.StorageError):
        storage.load_protected(path)
    assert path.read_bytes() == data


def test_plaintext_is_not_accepted_as_protected_data(tmp_path):
    path = tmp_path / "plain.dat"
    path.write_text('{"token": "dummy"}')
    with pytest.raises(storage.StorageError):
        storage.load_protected(path)


def test_encryption_failure_never_writes_plaintext(tmp_path, monkeypatch):
    path = tmp_path / "token.dat"
    def fail(*args, **kwargs):
        raise storage.StorageError("locked")
    monkeypatch.setattr(storage, "_crypt", fail)
    with pytest.raises(storage.StorageError):
        storage.save_protected(path, {"token": "dummy"})
    assert not path.exists()
    assert not list(tmp_path.iterdir())


def test_atomic_replace_failure_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "state.dat"
    path.write_bytes(b"original")
    monkeypatch.setattr(storage.os, "replace", MagicMock(side_effect=OSError("locked")))
    with pytest.raises(OSError):
        storage.atomic_write(path, b"new")
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


@pytest.fixture
def token_path(tmp_path, monkeypatch):
    path = tmp_path / "keep_token.dat"
    monkeypatch.setattr(config, "TOKEN_FILE", str(path))
    monkeypatch.setattr(config, "load_config", lambda: {"email": "test@example.test"})
    return path


def test_legacy_token_migrates_to_dpapi(token_path):
    token_path.write_text("dummy-legacy-token")
    assert config.load_token("test@example.test") == "dummy-legacy-token"
    assert b"dummy-legacy-token" not in token_path.read_bytes()
    assert config.load_token("other@example.test") is None


def test_legacy_token_needs_the_configured_account(token_path):
    token_path.write_text("dummy-legacy-token")
    assert config.load_token() is None
    assert config.load_token("other@example.test") is None
    assert token_path.read_text() == "dummy-legacy-token"


def test_protected_token_account_binding(token_path):
    config.save_token("dummy", "test@example.test")
    assert config.load_token("test@example.test") == "dummy"
    assert config.load_token("other@example.test") is None


def test_clear_token_removes_both_credential_stores(token_path, monkeypatch):
    import keep_protocol.auth as auth
    clear = MagicMock()
    monkeypatch.setattr(auth, "clear_credentials", clear)
    config.save_token("dummy", "test@example.test")
    config.clear_token("test@example.test")
    clear.assert_called_once_with("test@example.test")
    assert not token_path.exists()


def test_token_file_is_still_removed_if_keyring_clear_fails(token_path, monkeypatch):
    import keep_protocol.auth as auth
    monkeypatch.setattr(auth, "clear_credentials", MagicMock(side_effect=OSError("locked")))
    config.save_token("dummy", "test@example.test")
    with pytest.raises(RuntimeError):
        config.clear_token("test@example.test")
    assert not token_path.exists()


def test_note_cache_migrates_and_is_encrypted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOTES_FILE", str(tmp_path / "notes.dat"))
    monkeypatch.setattr(config, "LEGACY_NOTES_FILE", str(tmp_path / "notes.json"))
    Path(config.LEGACY_NOTES_FILE).write_text('{"note": {"text": "private"}}')
    assert config.load_notes() == {"note": {"text": "private"}}
    assert not Path(config.LEGACY_NOTES_FILE).exists()
    assert b"private" not in Path(config.NOTES_FILE).read_bytes()


def test_damaged_legacy_notes_are_not_silently_emptied(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(config, "NOTES_FILE", str(tmp_path / "notes.dat"))
    monkeypatch.setattr(config, "LEGACY_NOTES_FILE", str(tmp_path / "notes.json"))
    Path(config.LEGACY_NOTES_FILE).write_text("broken JSON")
    with pytest.raises(json.JSONDecodeError):
        config.load_notes()
    assert not Path(config.NOTES_FILE).exists()
    assert Path(config.LEGACY_NOTES_FILE).read_text() == "broken JSON"


def test_credentials_repr_never_includes_token():
    assert "dummy-secret" not in repr(Credentials("test@example.test", "dummy-secret", "id"))


def test_google_auth_error_never_echoes_response(monkeypatch):
    import keep_protocol.auth as auth
    monkeypatch.setattr(auth.gpsoauth, "perform_oauth", lambda *args: {"Token": "dummy-secret"})
    with pytest.raises(auth.AuthError) as result:
        Credentials("test@example.test", "dummy-secret", "id").mint_bearer()
    assert "dummy-secret" not in str(result.value)
