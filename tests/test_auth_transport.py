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
    result = transport.exchange_web_token("oauth2_4/dummy-web-token", "device")
    assert result == {"Token": "dummy-master", "Email": "test@example.test", "AndroidId": "device"}
    assert session.trust_env is False
    args, kwargs = session.post.call_args
    assert args == ("https://android.clients.google.com/auth",)
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (10, 30)
    assert kwargs["stream"] is True
    assert session.post.call_count == 1
    assert "Email" not in kwargs["data"]


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
        transport.exchange_web_token("oauth2_4/dummy", "device")
    assert "dummy-secret" not in str(error.value)
    assert session.post.call_count == 1
    response.iter_content.assert_not_called()
    assert error.value.diagnostic("token-exchange") == {
        "stage": "token-exchange", "reason": "http", "http_status": status, "google_code": None}
    assert str(status) in transport.auth_failure_message(error.value.diagnostic("token-exchange"))


@pytest.mark.parametrize("raw", [b"Token=dummy-secret\nToken=duplicate\n", b"not-a-key-value-response",
                                  b"Error=dummy-secret\n", b"\xff\xff", b"x" * 65537])
def test_invalid_or_oversized_responses_fail_without_echoing_secrets(connection, raw):
    session, response = connection
    response.iter_content.return_value = [raw]
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("oauth2_4/dummy", "device")
    assert "dummy-secret" not in str(error.value)


def test_auth_timeout_is_bounded_and_does_not_echo_dependency_exception(connection):
    session, response = connection
    session.post.side_effect = requests.Timeout("dummy-secret-in-dependency-exception")
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("oauth2_4/dummy", "device")
    assert "dummy-secret" not in str(error.value)
    assert session.post.call_count == 1


def test_manual_or_malformed_web_tokens_never_reach_google(connection):
    session, response = connection
    with pytest.raises(transport.AuthError):
        transport.exchange_web_token("unsupported-cookie", "device")
    session.post.assert_not_called()


@pytest.mark.parametrize("error,reason", [
    (requests.Timeout("dummy-secret"), "timeout"),
    (requests.exceptions.SSLError("dummy-secret"), "tls"),
    (requests.ConnectionError("dummy-secret"), "connection"),
    (transport.GoogleTransportPolicyError("dummy-secret"), "policy"),
])
def test_auth_transport_reports_specific_non_secret_failure_categories(connection, error, reason):
    session, response = connection
    session.post.side_effect = error
    with pytest.raises(transport.AuthError) as result:
        transport.exchange_web_token("oauth2_4/dummy", "device")
    diagnostic = result.value.diagnostic("token-exchange")
    assert diagnostic["reason"] == reason
    assert diagnostic["http_status"] is None
    assert "dummy-secret" not in repr(diagnostic)
    assert "dummy-secret" not in transport.auth_failure_message(diagnostic)


@pytest.mark.parametrize("code,expected", [("BadAuthentication", "BadAuthentication"),
    ("NeedsBrowser", "NeedsBrowser"), ("dummy-secret-google-value", None)])
def test_only_known_google_rejection_codes_reach_diagnostics(connection, code, expected):
    session, response = connection
    response.iter_content.return_value = [f"Error={code}\n".encode()]
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("oauth2_4/dummy", "device")
    details = error.value.diagnostic("token-exchange")
    assert details["reason"] == "google"
    assert details["google_code"] == expected
    assert "dummy-secret" not in repr(details)


def test_sync_exposes_safe_exchange_failure_without_claiming_google_rejected_the_token(monkeypatch):
    from keep_sync_v2 import KeepSyncV2
    def fail(*args):
        raise transport.AuthError("dummy-secret-error", reason="tls")
    monkeypatch.setattr("keep_sync_v2.exchange_web_token", fail)
    sync = KeepSyncV2()
    assert sync.exchange_oauth_for_master("oauth2_4/dummy") is None
    assert sync.last_auth_failure["reason"] == "tls"
    assert "HTTPS" in transport.auth_failure_message(sync.last_auth_failure)
    assert "dummy-secret" not in repr(sync.last_auth_failure)


def test_invalid_failure_metadata_is_sanitized_before_display_or_storage():
    details = transport.safe_auth_failure({"reason": {"secret": "dummy-secret"},
        "stage": "dummy-secret", "google_code": ["dummy-secret"], "http_status": "dummy-secret"})
    assert details == {"stage": "unknown", "reason": "unknown", "http_status": None, "google_code": None}
    assert transport.auth_failure_message(details) == "Sign-in did not finish."


@pytest.mark.parametrize("email", [None, "", " ", "not-an-email", "test@example.test\x00", "a@b", "a@@example.test"])
def test_google_must_return_an_account_identity_without_any_local_email_fallback(connection, email):
    session, response = connection
    fields = b"Token=dummy-master\n"
    if email is not None:
        fields += f"Email={email}\n".encode()
    response.iter_content.return_value = [fields]
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("oauth2_4/dummy", "device")
    assert error.value.reason == "identity"
    assert "Email" not in session.post.call_args.kwargs["data"]


def test_unused_google_metadata_and_identical_repeats_do_not_reject_valid_credentials(connection):
    session, response = connection
    response.iter_content.return_value = [
        b"\xef\xbb\xbfToken=dummy-master\r\nToken=dummy-master\r\nEmail=test@example.test\r\n"
        b"services=unused\r\nservices=more-unused\r\nunused.field=extension\r\n  \r\nextra metadata\r\n"]
    assert transport.exchange_web_token("oauth2_4/dummy", "device") == {
        "Token": "dummy-master", "Email": "test@example.test", "AndroidId": "device"}


@pytest.mark.parametrize("raw,kind", [
    (b"<html>Token=dummy-secret</html>", "html"),
    (b'{"Token":"dummy-secret"}', "json"),
    (b"Token=dummy-secret\nToken=conflicting\n", "conflicting-fields"),
    (b"\xff\xff", "invalid-encoding"),
    (b"no supported fields", "invalid-fields"),
])
def test_response_format_failures_are_classified_without_exposing_the_body(connection, raw, kind):
    session, response = connection
    response.iter_content.return_value = [raw]
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("oauth2_4/dummy", "device")
    details = error.value.diagnostic("token-exchange")
    assert details["response_kind"] == kind
    assert details["http_status"] == 200
    assert "dummy-secret" not in repr(details)
    assert "dummy-secret" not in transport.auth_failure_message(details)


@pytest.mark.parametrize("value", [b"", b"token with spaces", b"dummy\x00secret", "secret\u202e".encode()])
def test_credential_fields_remain_strict_even_when_unused_metadata_is_ignored(connection, value):
    session, response = connection
    response.iter_content.return_value = [b"Token=" + value + b"\nEmail=test@example.test\n"]
    with pytest.raises(transport.AuthError) as error:
        transport.exchange_web_token("oauth2_4/dummy", "device")
    assert error.value.diagnostic("token-exchange")["response_kind"] == "invalid-fields"
