"""Mocked release-time antivirus API tests; no real API key or network traffic."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from tools.scan_release_virustotal import BASE, ScanError, VirusTotal, allowed_url, verified_inputs


def response(value, status=200):
    result = MagicMock(status_code=status)
    result.json.return_value = value
    return result


@pytest.fixture
def binary(tmp_path):
    path = tmp_path / "KeepDesktop.exe"
    path.write_bytes(b"fictional executable fixture, not a PE")
    return path


def api(responses):
    session = MagicMock()
    session.request.side_effect = responses
    return VirusTotal("dummy-test-key-never-log", session=session, interval=0), session


def completed(path, *, malicious=0):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"data": {"attributes": {"status": "completed", "date": 123,
            "stats": {"malicious": malicious, "suspicious": 0, "undetected": 70},
            "results": {"ExampleEngine": {"category": "malicious" if malicious else "undetected",
                                           "result": "Example.Test" if malicious else None}}}},
            "meta": {"file_info": {"sha256": digest}}}


@pytest.mark.parametrize("url", ["http://www.virustotal.com/api/v3/files", "https://evil.example/upload",
                                 "https://www.virustotal.com.evil.example/upload", "https://key@www.virustotal.com/",
                                 "https://www.virustotal.com:444/upload", "https://www.virustotal.com/\n"])
def test_unapproved_upload_destinations_are_rejected(url):
    assert not allowed_url(url)


def test_https_official_api_and_signed_upload_paths_are_allowed():
    assert allowed_url(BASE + "/files")
    assert allowed_url("https://www.virustotal.com/_ah/upload/example")
    assert allowed_url("https://bigfiles.virustotal.com/_ah/upload/example")


def test_completed_scan_matches_exact_hash_and_never_includes_key(binary):
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    client, session = api([response({"data": {"id": "example-analysis"}}),
                           response(completed(binary)), response({"data": {"id": digest}})])
    report = client.scan(binary)
    assert report["sha256"] == digest
    assert report["analysis_status"] == "completed"
    assert report["verdict"] == "no_malicious_or_suspicious_detections_reported"
    assert "dummy-test-key-never-log" not in json.dumps(report)
    assert session.trust_env is False
    assert session.request.call_args_list[0].args == ("POST", BASE + "/files")
    assert all(c.kwargs["allow_redirects"] is False for c in session.request.call_args_list)


def test_detections_are_reported_not_silently_dismissed(binary):
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    client, _ = api([response({"data": {"id": "analysis"}}), response(completed(binary, malicious=1)),
                     response({"data": {"id": digest}})])
    report = client.scan(binary)
    assert report["verdict"] == "detections_reported"
    assert report["detections"] == [{"engine": "ExampleEngine", "category": "malicious", "result": "Example.Test"}]


def test_wrong_file_report_never_receives_a_pass(binary):
    client, _ = api([response({"data": {"id": "analysis"}}), response(completed(binary)),
                     response({"data": {"id": "0" * 64}})])
    with pytest.raises(ScanError, match="does not match"):
        client.scan(binary)


def test_large_binary_uses_official_large_file_upload_endpoint(binary):
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    upload = "https://bigfiles.virustotal.com/_ah/upload/example-signed-upload"
    client, session = api([response({"data": upload}), response({"data": {"id": "analysis"}}),
                           response(completed(binary)), response({"data": {"id": digest}})])
    with patch("pathlib.Path.stat", return_value=SimpleNamespace(st_size=33 * 1024 * 1024)):
        report = client.scan(binary)
    assert report["analysis_status"] == "completed"
    assert session.request.call_args_list[0].args == ("GET", BASE + "/files/upload_url")
    assert session.request.call_args_list[1].args == ("POST", upload)


def test_untrusted_large_file_upload_url_is_never_contacted(binary):
    client, session = api([response({"data": "https://evil.example/upload"})])
    with patch("pathlib.Path.stat", return_value=SimpleNamespace(st_size=33 * 1024 * 1024)):
        with pytest.raises(ScanError, match="failed validation"):
            client.scan(binary)
    assert session.request.call_count == 1


def test_pending_analysis_is_not_a_pass(binary):
    now = [0.0]
    session = MagicMock()
    session.request.side_effect = [response({"data": {"id": "analysis"}}),
                                   response({"data": {"attributes": {"status": "queued"}}})]
    def sleep(seconds):
        now[0] += seconds
    client = VirusTotal("dummy-key", session=session, clock=lambda: now[0], sleep=sleep)
    with pytest.raises(ScanError, match="still pending"):
        client.scan(binary, timeout=1)


def test_network_failures_do_not_echo_key_or_signed_upload_url():
    client, _ = api([requests.RequestException("secret=example-sensitive-upload-token")])
    with pytest.raises(ScanError) as caught:
        client.request("GET", BASE + "/files/upload_url")
    assert "example-sensitive-upload-token" not in str(caught.value)
    assert "dummy-test-key-never-log" not in str(caught.value)


def test_api_redirect_is_not_followed():
    client, session = api([response({}, 302)])
    with pytest.raises(ScanError, match="HTTP 302"):
        client.request("GET", BASE + "/files/upload_url")
    assert session.request.call_count == 1


def test_rate_limiter_waits_between_calls():
    now = [0.0]
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds
    session = MagicMock()
    session.request.return_value = response({})
    client = VirusTotal("dummy-key", session=session, clock=lambda: now[0], sleep=sleep)
    client.request("GET", BASE + "/files/upload_url")
    client.request("GET", BASE + "/files/upload_url")
    assert waits == [16]


def test_both_release_inputs_are_verified_before_upload(tmp_path):
    (tmp_path / "scan-inputs").mkdir()
    for name in ("Google-Keep-Notes-for-Desktop-Setup-2.3.0.exe", "scan-inputs/KeepDesktop.exe"):
        path = tmp_path / name
        path.write_bytes(b"fictional build fixture")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (tmp_path / (name + ".sha256")).write_text(digest + "  fixture.exe\n", encoding="utf-8")
    assert len(verified_inputs(tmp_path, "2.3.0")) == 2
    (tmp_path / "scan-inputs/KeepDesktop.exe").write_bytes(b"changed")
    with pytest.raises(ScanError, match="nothing uploaded"):
        verified_inputs(tmp_path, "2.3.0")
