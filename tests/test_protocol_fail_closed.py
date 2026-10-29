from copy import deepcopy
from unittest.mock import MagicMock

import pytest
import requests

from keep_protocol.client import KeepClient, KeepError, CHANGES_URL, CHANGES_URL_WRITE
from keep_protocol.models import Note


@pytest.fixture
def keep_connection(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    monkeypatch.setattr("keep_protocol.client.google_session", lambda: session)
    return session.post


def client():
    creds = MagicMock()
    creds.mint_bearer.return_value = "dummy-bearer"
    instance = KeepClient(creds)
    instance.notes["1.original"] = Note.from_server({"id": "1.original", "type": "NOTE", "text": "original"})
    instance.target_version = "before"
    return instance


@pytest.mark.parametrize("data", [None, [], {}, {"nodes": None, "toVersion": "v"},
    {"nodes": [], "toVersion": 123}, {"nodes": [None], "toVersion": "v"},
    {"nodes": [{"id": 123}], "toVersion": "v"}, {"nodes": None, "toVersion": "v", "truncated": False},
    {"toVersion": "v", "truncated": True}, {"toVersion": "v"},
    {"toVersion": "v", "truncated": "false"}, {"toVersion": "", "truncated": False},
    {"truncated": False}, {"toVersion": 123, "truncated": False},
    {"nodes": [], "toVersion": "v", "truncated": "false"}])
def test_changed_schema_cannot_replace_cache_or_advance_cursor(data):
    instance = client()
    original = deepcopy(instance.notes)
    instance._post = MagicMock(return_value=data)
    with pytest.raises(KeepError):
        instance.sync(full=True)
    assert instance.notes == original
    assert instance.target_version == "before"


def test_second_page_failure_rolls_back_whole_pull():
    instance = client()
    original = deepcopy(instance.notes)
    instance._post = MagicMock(side_effect=[
        {"nodes": [{"id": "2.partial", "type": "NOTE", "text": "partial"}],
         "toVersion": "middle", "truncated": True}, KeepError("offline")])
    with pytest.raises(KeepError):
        instance.sync(full=True)
    assert instance.notes == original
    assert instance.target_version == "before"


def test_repeating_pagination_is_bounded():
    instance = client()
    instance._post = MagicMock(return_value={"nodes": [], "toVersion": "same", "truncated": True})
    with pytest.raises(KeepError):
        instance.sync(full=True)
    assert instance._post.call_count == 2
    assert instance.target_version == "before"


def test_repeating_force_resync_is_bounded():
    instance = client()
    instance._post = MagicMock(return_value={"forceFullResync": True})
    with pytest.raises(KeepError):
        instance.sync(full=True)
    assert instance._post.call_count == 2


@pytest.mark.parametrize("failure", [requests.Timeout("dummy-secret"), requests.ConnectionError("dummy-secret")])
def test_uncertain_write_is_never_automatically_replayed(keep_connection, failure):
    post = keep_connection
    post.side_effect = failure
    with pytest.raises(KeepError) as error:
        client()._post(CHANGES_URL_WRITE, {"nodes": []})
    assert post.call_count == 1
    assert "dummy-secret" not in str(error.value)


@pytest.mark.parametrize("status", [302, 403, 429, 500, 503])
def test_http_errors_do_not_retry_or_log_response_body(keep_connection, status):
    response = MagicMock(status_code=status, text="dummy-secret-response")
    post = keep_connection
    post.return_value = response
    with pytest.raises(KeepError) as error:
        client()._post(CHANGES_URL_WRITE, {"nodes": []})
    assert post.call_count == 1
    assert post.call_args.kwargs["allow_redirects"] is False
    assert post.call_args.kwargs["verify"] is True
    assert "dummy-secret" not in str(error.value)


def test_only_definite_401_rejection_retries_once(keep_connection):
    success = MagicMock(status_code=200)
    success.json.return_value = {"nodes": [], "toVersion": "ok"}
    post = keep_connection
    post.side_effect = [MagicMock(status_code=401), success]
    assert client()._post(CHANGES_URL, {}) == success.json.return_value
    assert post.call_count == 2


@pytest.mark.parametrize("payload", [None, [], {}, {"error": "failure"}, {"nodes": []}])
def test_missing_write_acknowledgment_is_not_success(keep_connection, payload):
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    keep_connection.return_value = response
    with pytest.raises(KeepError):
        client()._post(CHANGES_URL_WRITE, {"nodes": [{"id": "1.original"}]})


def test_untrusted_endpoint_never_receives_a_google_token(keep_connection):
    post = keep_connection
    with pytest.raises(KeepError):
        client()._post("https://evil.test/notes", {})
    post.assert_not_called()


def test_keep_requests_use_only_the_locked_shared_session(keep_connection):
    response = MagicMock(status_code=200)
    response.json.return_value = {"nodes": [], "toVersion": "ok"}
    keep_connection.return_value = response
    assert client()._post(CHANGES_URL, {}) == response.json.return_value
    assert keep_connection.call_args.kwargs["verify"] is True
    assert keep_connection.call_args.kwargs["allow_redirects"] is False
    assert keep_connection.call_args.kwargs["timeout"] == (10, 30)
    response.close.assert_called_once()
