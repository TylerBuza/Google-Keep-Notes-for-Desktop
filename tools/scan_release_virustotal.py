"""Release-time VirusTotal API scans. Never imported by the installed app.

Uploads share the selected public binaries with VirusTotal's security community.
The API key is read only from VT_API_KEY; it is never written to a report.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, urlsplit

import requests


BASE = "https://www.virustotal.com/api/v3"
HOSTS = frozenset({"www.virustotal.com", "bigfiles.virustotal.com"})


class ScanError(RuntimeError):
    pass


def allowed_url(url):
    try:
        value = urlsplit(url)
        return (value.scheme == "https" and value.hostname in HOSTS
                and value.port in (None, 443) and not value.username and not value.password
                and "\\" not in url and not any(ord(c) < 32 for c in url))
    except (TypeError, ValueError):
        return False


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class VirusTotal:
    def __init__(self, key, *, session=None, interval=16, clock=time.monotonic, sleep=time.sleep):
        if not key or key != key.strip() or any(ord(c) < 33 for c in key):
            raise ScanError("Set VT_API_KEY outside Git before scanning release binaries.")
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"x-apikey": key, "User-Agent": "KeepDesktop-release-review/1"})
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.last_request = None

    def request(self, method, url, **kwargs):
        if not allowed_url(url):
            raise ScanError("VirusTotal returned an unapproved destination; upload refused.")
        if self.last_request is not None:
            delay = self.interval - (self.clock() - self.last_request)
            if delay > 0:
                self.sleep(delay)
        self.last_request = self.clock()
        try:
            response = self.session.request(method, url, allow_redirects=False,
                                            timeout=(20, 300), **kwargs)
        except requests.RequestException:
            # Request exceptions can include an ephemeral signed upload URL.
            raise ScanError("VirusTotal request failed; no key or upload URL logged.") from None
        if response.status_code != 200:
            raise ScanError(f"VirusTotal rejected the request (HTTP {response.status_code}); no response body logged.")
        try:
            return response.json()
        except ValueError:
            raise ScanError("VirusTotal returned malformed JSON.") from None

    def scan(self, path, *, timeout=1200):
        path = Path(path)
        digest = sha256(path)
        size = path.stat().st_size
        upload = BASE + "/files"
        if size > 32 * 1024 * 1024:
            upload = self.request("GET", BASE + "/files/upload_url").get("data")
            if not isinstance(upload, str) or not allowed_url(upload):
                raise ScanError("VirusTotal large-file upload URL failed validation.")
        with path.open("rb") as stream:
            result = self.request("POST", upload, files={"file": (path.name, stream, "application/octet-stream")})
        analysis_id = result.get("data", {}).get("id")
        if not isinstance(analysis_id, str) or not re.fullmatch(r"[A-Za-z0-9_=:-]{1,512}", analysis_id):
            raise ScanError("VirusTotal did not return a valid analysis identifier.")
        deadline = self.clock() + timeout
        analysis = None
        while self.clock() < deadline:
            result = self.request("GET", BASE + "/analyses/" + quote(analysis_id, safe=""))
            attributes = result.get("data", {}).get("attributes", {})
            if attributes.get("status") == "completed":
                analysis = attributes
                break
            if attributes.get("status") not in ("queued", "in-progress"):
                raise ScanError("VirusTotal returned an unknown analysis state.")
        if analysis is None:
            raise ScanError("VirusTotal analysis is still pending; no pass result recorded.")
        # Verify the completed report belongs to the exact local binary.
        file_result = self.request("GET", BASE + "/files/" + digest)
        if file_result.get("data", {}).get("id") != digest:
            raise ScanError("VirusTotal file report does not match the release binary hash.")
        reported_hash = result.get("meta", {}).get("file_info", {}).get("sha256", digest)
        if reported_hash != digest or sha256(path) != digest:
            raise ScanError("Release binary identity changed during analysis.")
        stats = analysis.get("stats")
        if (not isinstance(stats, dict) or not stats
                or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in stats.items())
                or sum(stats.values()) == 0):
            raise ScanError("VirusTotal completed without valid engine totals.")
        findings = []
        for engine, entry in analysis.get("results", {}).items():
            if entry.get("category") in ("malicious", "suspicious"):
                findings.append({"engine": str(engine)[:100], "category": entry["category"],
                                 "result": str(entry.get("result") or "")[:300]})
        detected = stats.get("malicious", 0) + stats.get("suspicious", 0) > 0
        return {"file": path.name, "sha256": digest, "bytes": size,
                "analysis_status": "completed", "analysis_date_unix": analysis.get("date"),
                "analysis_id": analysis_id, "stats": stats, "detections": findings,
                "verdict": "detections_reported" if detected else "no_malicious_or_suspicious_detections_reported",
                "report_url": f"https://www.virustotal.com/gui/file/{digest}/detection",
                "caveat": "Point-in-time multi-engine analysis, not a safety guarantee or digital signature."}


def verified_inputs(directory, version):
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ScanError("Invalid release version.")
    root = Path(directory)
    files = [(root / f"Google-Keep-Notes-for-Desktop-Setup-{version}.exe", "virustotal-installer.json"),
             (root / "scan-inputs" / "KeepDesktop.exe", "virustotal-application.json")]
    # Validate both files before making any upload request.
    for path, _ in files:
        try:
            expected = Path(str(path) + ".sha256").read_text(encoding="utf-8-sig").strip().split()[0]
            actual = sha256(path)
        except (OSError, IndexError):
            raise ScanError("A release binary or checksum sidecar is missing.") from None
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected) or expected.lower() != actual:
            raise ScanError("Release binary checksum mismatch; nothing uploaded.")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[a-f0-9]{40}", args.source_commit):
            raise ScanError("An exact source commit SHA is required.")
        files = verified_inputs(args.directory, args.version)
        client = VirusTotal(os.environ.get("VT_API_KEY"))
        args.output.mkdir(parents=True, exist_ok=True)
        detected = False
        for path, name in files:
            report = client.scan(path, timeout=args.timeout)
            report.update(version=args.version, source_commit=args.source_commit)
            (args.output / name).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({k: report[k] for k in ("file", "sha256", "verdict", "stats", "report_url")}), flush=True)
            detected |= report["verdict"] == "detections_reported"
        return 2 if detected else 0
    except ScanError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
