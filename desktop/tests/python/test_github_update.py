from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from github_update import Cancelled, GitHubUpdater, UpdateError, parse_release, version_parts

REPO = "a792275829-cpu/electricity-toolbox-scripts"
PAYLOAD = b"test installer bytes"


def release():
    return {"tag_name": "v1.2.0", "html_url": f"https://github.com/{REPO}/releases/tag/v1.2.0",
            "draft": False, "prerelease": False, "body": "测试更新说明", "assets": [
                {"name": "ElectricityToolbox-1.2.0-apple-silicon.pkg", "size": len(PAYLOAD),
                 "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
                 "browser_download_url": f"https://github.com/{REPO}/releases/download/v1.2.0/test.pkg"}]}


class Response(io.BytesIO):
    status = 200


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
            self.assertEqual(fractions[-1], 1)
        finally:
            shutil.rmtree(path.parent)
        for field, value in (("size", len(PAYLOAD) + 1), ("size", len(PAYLOAD) - 1),
                             ("digest", "sha256:" + "0" * 64), ("digest", None),
                             ("browser_download_url", "http://github.com/test.pkg")):
            data = release()
            data["assets"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(UpdateError):
                updater.download(parse_release(data, "0.1.3"), threading.Event(), lambda _: None)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            updater.download(candidate, cancelled, lambda _: None)

    def test_publication_requires_matching_pkg_and_checksum(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packaging/tauri"))
        from validate_installer import validate
        data = release()
        data["assets"].append({"name": data["assets"][0]["name"] + ".sha256"})
        validate(data)
        for field, value in (("digest", None), ("size", 0), ("name", "wrong.pkg")):
            invalid = copy.deepcopy(data)
            invalid["assets"][0][field] = value
            with self.assertRaises(ValueError):
                validate(invalid)


if __name__ == "__main__":
    unittest.main()
