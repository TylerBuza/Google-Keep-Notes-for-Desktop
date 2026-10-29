import ssl
from unittest.mock import MagicMock

import pytest
import requests

from keep_protocol import auth_transport as transport


@pytest.fixture
def connection(monkeypatch):
    response = MagicMock(status_code=200)
    response.iter_content.return_value = [b"Token=dummy-master\nEmail=test@example.test\nSID=discard-this\n"]
    session = MagicMock()
    session.__enter__.return_value = session
    session.post.return_value.__enter__.return_value = response
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(requests, "Session", factory)
    return session, response


def test_master_exchange_uses_locked_tls_transport_and_discards_extra_session_tokens(connection):
    session, response = connection
    result = transport.exchange_web_token("", "oauth2_4/dummy-web-token", "device")
    assert result == {"Token": "dummy-master", "Email": "test@example.test", "AndroidId": "device"}
    assert session.trust_env is False
    args, kwargs = session.post.call_args
    assert args == ("https://android.clients.google.com/auth",)
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (10, 30)
    assert kwargs["stream"] is True
    assert session.post.call_count == 1


def test_tls_adapter_requires_certificate_and_hostname_validation():
    adapter = transport.GoogleAuthAdapter(max_retries=0)
    context = adapter.poolmanager.connection_pool_kw["ssl_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert adapter.poolmanager.connection_pool_kw["assert_hostname"] == "android.clients.google.com"
    assert adapter.max_retries.total == 0


def test_bearer_request_is_keep_only_without_reminders_scope(connection):
    session, response = connection
    response.iter_content.return_value = [b"Auth=dummy-keep-bearer\nSID=discard-this\n"]
    assert transport.mint_keep_bearer("test@example.test", "dummy-master", "device") == "dummy-keep-bearer"
    form = session.post.call_args.kwargs["data"]
    assert form["service"] == "oauth2:https://www.googleapis.com/auth/memento"
    assert "reminders" not in form["service"]
    assert "Passwd" not in form
    assert form["app"] == "com.google.android.keep"


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 429, 500])
def test_rejected_requests_never_redirect_retry_or_expose_response(connection, status):
    session, response = connection
    response.status_code = status
    response.text = "dummy-secret-response"
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("", "oauth2_4/dummy", "device")
    assert "dummy-secret" not in str(error.value)
    assert session.post.call_count == 1
    response.iter_content.assert_not_called()


@pytest.mark.parametrize("raw", [b"Token=dummy-secret\nToken=duplicate\n", b"not-a-key-value-response",
                                  b"Error=dummy-secret\n", b"\xff\xff", b"x" * 65537])
def test_invalid_or_oversized_responses_fail_without_echoing_secrets(connection, raw):
    session, response = connection
    response.iter_content.return_value = [raw]
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("", "oauth2_4/dummy", "device")
    assert "dummy-secret" not in str(error.value)


def test_auth_timeout_is_bounded_and_does_not_echo_dependency_exception(connection):
    session, response = connection
    session.post.side_effect = requests.Timeout("dummy-secret-in-dependency-exception")
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("", "oauth2_4/dummy", "device")
    assert "dummy-secret" not in str(error.value)
    assert session.post.call_count == 1


def test_manual_or_malformed_web_tokens_never_reach_google(connection):
    session, response = connection
    with pytest.raises(transport.AuthError):
        transport.exchange_web_token("", "unsupported-cookie", "device")
    session.post.assert_not_called()
