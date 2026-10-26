from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import sys
import threading
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from github_update import Cancelled, GitHubUpdater, UpdateError, parse_release, version_parts

REPO = "a792275829-cpu/electricity-toolbox-scripts"
PAYLOAD = b"test installer bytes"


def release():
    return {"tag_name": "v1.2.0", "html_url": f"https://github.com/{REPO}/releases/tag/v1.2.0",
            "draft": False, "prerelease": False, "body": "测试更新说明", "assets": [
                {"name": "ElectricityToolbox-1.2.0-apple-silicon.dmg", "size": len(PAYLOAD),
                 "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
                 "browser_download_url": f"https://github.com/{REPO}/releases/download/v1.2.0/test.dmg"}]}


class Response(io.BytesIO):
    status = 200

    def __init__(self, data=b"", url="", headers=None):
        super().__init__(data)
        self.url = url
        self.headers = headers or {}

    def geturl(self):
        return self.url


class GitHubUpdateTests(unittest.TestCase):
    def test_numeric_versions_and_invalid_tags(self):
        self.assertGreater(version_parts("v1.10.0"), version_parts("1.9.9"))
        self.assertEqual(version_parts("1.2"), version_parts("1.2.0.0"))
        for tag in ("latest", "1.2-beta", "1..2", "１.２", "1", "1.2.3.4.5"):
            with self.subTest(tag=tag), self.assertRaises(UpdateError):
                version_parts(tag)

    def test_same_old_draft_and_preview_are_ignored(self):
        for field, value in (("tag_name", "v0.1.0"), ("tag_name", "v0.1.3"), ("draft", True), ("prerelease", True)):
            data = release()
            data[field] = value
            self.assertIsNone(parse_release(data, "0.1.3"))

    def test_notes_asset_and_untrusted_page(self):
        data = release()
        result = parse_release(data, "0.1.3")
        self.assertEqual(result.notes, "测试更新说明")
        self.assertIsNotNone(result.asset)
        data["html_url"] = "https://github.com.evil.example/release"
        with self.assertRaises(UpdateError):
            parse_release(data, "0.1.3")
        data = release()
        data["assets"] = []
        self.assertIsNone(parse_release(data, "0.1.3").asset)
        data = release()
        data["assets"][0]["name"] = "ElectricityToolbox-1.2.0-aarch64.app.tar.gz"
        self.assertIsNone(parse_release(data, "0.1.3").asset)

    def test_api_request_and_http_errors(self):
        requests = []
        def opener(request, timeout):
            requests.append(request)
            return Response(json.dumps(release()).encode())
        updater = GitHubUpdater("0.1.3", opener=opener)
        self.assertEqual(updater.check().version, "v1.2.0")
        self.assertTrue(requests[0].full_url.endswith("/releases/latest"))
        self.assertEqual(requests[0].get_header("Cache-control"), "no-cache")
        import urllib.error
        for status in (403, 404, 429, 500):
            def fail(request, timeout):
                raise urllib.error.HTTPError(request.full_url, status, "error", {}, None)
            with self.subTest(status=status), self.assertRaises(UpdateError):
                GitHubUpdater("0.1.3", opener=fail).check()

    def test_download_verifies_payload_size_digest_and_cancellation(self):
        updater = GitHubUpdater("0.1.3", opener=lambda request, timeout: Response(PAYLOAD))
        candidate = parse_release(release(), "0.1.3")
        fractions = []
        path = updater.download(candidate, threading.Event(), fractions.append)
        try:
            self.assertEqual(path.read_bytes(), PAYLOAD)
            self.assertEqual(path.suffix, ".dmg")
            self.assertEqual(fractions[-1], 1)
        finally:
            shutil.rmtree(path.parent)
        for field, value in (("size", len(PAYLOAD) + 1), ("size", len(PAYLOAD) - 1),
                             ("digest", "sha256:" + "0" * 64), ("digest", None),
                             ("browser_download_url", "http://github.com/test.dmg")):
            data = release()
            data["assets"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(UpdateError):
                updater.download(parse_release(data, "0.1.3"), threading.Event(), lambda _: None)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            updater.download(candidate, cancelled, lambda _: None)

    def public_opener(self, status=403, page=None, digest=None, size="123", assets=True):
        data = release()
        base = f"https://github.com/{REPO}/releases"
        calls = []
        def opener(request, timeout):
            calls.append((request.full_url, request.get_method()))
            if request.full_url.startswith("https://api.github.com/"):
                raise urllib.error.HTTPError(request.full_url, status, "rate limit", {}, None)
            if request.full_url == base + "/latest":
                return Response(b'<div class="markdown-body"><h2>Changes</h2><div><p>Fixed &amp; tested</p></div></div>',
                                page or base + "/tag/v1.2.0")
            if "/expanded_assets/" in request.full_url:
                html = "".join(f'<li><a href="/REPO/releases/download/v1.2.0/{name}">file</a>'
                               f'<clipboard-copy value="{digest if digest is not None else data["assets"][0]["digest"]}"></clipboard-copy></li>'
                               for name in (data["assets"][0]["name"], "update-manifest.json")) if assets else ""
                return Response(html.replace("/REPO/", f"/{REPO}/").encode())
            self.assertEqual(request.get_method(), "HEAD")
            return Response(headers={"Content-Length": size})
        return opener, calls

    def test_rate_limit_falls_back_to_public_release_with_verified_metadata(self):
        for status in (403, 429):
            opener, calls = self.public_opener(status)
            result = GitHubUpdater("0.1.3", opener=opener).check()
            self.assertEqual(result.version, "v1.2.0")
            self.assertIn("Fixed & tested", result.notes)
            self.assertEqual(result.asset["size"], 123)
            self.assertEqual(result.asset["digest"], release()["assets"][0]["digest"])
            self.assertEqual(result.update_asset["name"], "update-manifest.json")
            self.assertEqual(sum(method == "HEAD" for _, method in calls), 2)

    def test_public_fallback_rejects_invalid_metadata_and_preserves_first_error(self):
        for options in ({"page": "https://evil.example/tag/v1.2.0"},
                        {"page": f"https://github.com/{REPO}/releases/tag/v1.2-beta"},
                        {"digest": ""}, {"size": "740 MB"}, {"size": "0"}, {"assets": False}):
            with self.subTest(options=options), self.assertRaisesRegex(UpdateError, "HTTP 403.*备用检测失败"):
                GitHubUpdater("0.1.3", opener=self.public_opener(**options)[0]).check()

    def test_public_fallback_same_version_needs_no_assets(self):
        opener, calls = self.public_opener()
        self.assertIsNone(GitHubUpdater("1.2.0", opener=opener).check())
        self.assertEqual(len(calls), 2)

    def test_publication_requires_matching_dmg_and_checksum(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packaging/tauri"))
        from validate_installer import validate
        data = release()
        data["assets"][0]["name"] = "ElectricityToolbox-1.2.0-apple-silicon.dmg"
        data["assets"].append({"name": data["assets"][0]["name"] + ".sha256"})
        validate(data)
        for name in ("legacy.app.tar.gz", "legacy.app.tar.gz.sig", "latest.json", "legacy.app.zip"):
            invalid = copy.deepcopy(data)
            invalid["assets"].append({"name": name})
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate(invalid)
        for field, value in (("digest", None), ("size", 0), ("name", "wrong.pkg")):
            invalid = copy.deepcopy(data)
            invalid["assets"][0][field] = value
            with self.assertRaises(ValueError):
                validate(invalid)


if __name__ == "__main__":
    unittest.main()
