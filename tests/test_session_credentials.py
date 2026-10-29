import threading
import time
from unittest.mock import MagicMock

import pytest

from keep_protocol.auth import AuthError, Credentials, SessionCredentials
from keep_protocol.client import KeepClient
from keep_sync_v2 import KeepSyncV2


@pytest.fixture
def login_policy(monkeypatch):
    mint = MagicMock(return_value="dummy-keep-bearer")
    save = MagicMock()
    clear = MagicMock()
    monkeypatch.setattr(Credentials, "mint_bearer", mint)
    monkeypatch.setattr("keep_sync_v2.save_token", save)
    monkeypatch.setattr("keep_sync_v2.clear_token", clear)
    return mint, save, clear


def test_session_only_does_not_retain_master_token_or_persist_credentials(login_policy):
    mint, save, clear = login_policy
    sync = KeepSyncV2()
    assert sync.login("test@example.test", master_token="dummy-master", consent=True)
    assert isinstance(sync._client.creds, SessionCredentials)
    assert not hasattr(sync._client.creds, "master_token")
    assert "dummy-keep-bearer" not in repr(sync._client.creds)
    assert sync._client._get_bearer() == "dummy-keep-bearer"
    save.assert_not_called()
    clear.assert_called_once_with("test@example.test")


def test_remembering_master_requires_explicit_choice(login_policy):
    mint, save, clear = login_policy
    sync = KeepSyncV2()
    assert sync.login("test@example.test", master_token="dummy-master", consent=True, remember=True)
    assert isinstance(sync._client.creds, Credentials)
    assert save.call_count == 1
    assert save.call_args.args == ("dummy-master", "test@example.test")
    assert save.call_args.kwargs["android_id"] == sync._client.creds.android_id
    clear.assert_not_called()


@pytest.mark.parametrize("consent", [False, None, "yes", 1])
def test_no_authentication_without_explicit_boolean_consent(login_policy, consent):
    mint, save, clear = login_policy
    assert KeepSyncV2().login("test@example.test", master_token="dummy-master", consent=consent) is False
    mint.assert_not_called()
    save.assert_not_called()


def test_expired_session_cannot_renew_or_return_a_cached_bearer():
    creds = SessionCredentials("test@example.test", "dummy-bearer", time.monotonic() - 1)
    client = KeepClient(creds)
    client._bearer = "previously-cached-bearer"
    client._bearer_exp = time.monotonic() + 99999
    with pytest.raises(AuthError):
        client._get_bearer()


def test_cancelling_during_exchange_prevents_persistence_and_login(login_policy):
    mint, save, clear = login_policy
    cancelled = threading.Event()
    def cancel_during_mint():
        cancelled.set()
        return "dummy-bearer"
    mint.side_effect = cancel_during_mint
    sync = KeepSyncV2()
    assert sync.login("test@example.test", master_token="dummy-master", consent=True,
                      remember=True, cancel_event=cancelled) is False
    assert not sync.is_authenticated
    save.assert_not_called()


def test_storage_cleanup_failure_cannot_silently_leave_a_remembered_token(login_policy):
    mint, save, clear = login_policy
    clear.side_effect = OSError("locked")
    sync = KeepSyncV2()
    assert sync.login("test@example.test", master_token="dummy-master", consent=True) is False
    assert not sync.is_authenticated
    save.assert_not_called()


def test_remembered_account_reuses_its_device_id(login_policy, monkeypatch):
    monkeypatch.setattr("keep_sync_v2.load_token_record",
                        lambda email: {"token": "dummy-master", "android_id": "same-device"})
    sync = KeepSyncV2()
    assert sync.login("test@example.test", consent=True, remember=True)
    assert sync._client.creds.android_id == "same-device"
