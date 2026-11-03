from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging/tauri"))

import transfer_asset  # noqa: E402


def fake_gh(payload):
    """Stand-in for ``gh`` that answers release view and records every call."""

    calls = []

    def run(args, **_kwargs):
        calls.append(list(args))
        if args[:3] == ["gh", "release", "view"]:
            return SimpleNamespace(stdout=json.dumps(payload), returncode=0)
        return SimpleNamespace(stdout="{}", returncode=0)

    return run, calls


class SplitJoinTests(unittest.TestCase):
    def write_installer(self, base: Path, size: int = 1000) -> Path:
        installer = base / "ElectricityToolbox-0.1.27-apple-silicon.dmg"
        installer.write_bytes(bytes(range(256)) * (size // 256) + b"tail")
        return installer

    def test_split_and_join_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            installer = self.write_installer(base)
            work = base / "logs/transfer"

            manifest = transfer_asset.split(installer, work, parts=4)
            self.assertEqual(manifest["tag"], "v0.1.27")
            self.assertEqual(manifest["canonical"], installer.name)
            self.assertEqual(manifest["size"], installer.stat().st_size)
            self.assertEqual(len(manifest["parts"]), 4)
            self.assertEqual(sum(part["size"] for part in manifest["parts"]),
                             installer.stat().st_size)
            self.assertEqual(
                [part["name"][-7:] for part in manifest["parts"]],
                ["00.part", "01.part", "02.part", "03.part"],
            )

            restored = transfer_asset.join(work, base / "joined.dmg")
            self.assertEqual(restored.read_bytes(), installer.read_bytes())

    def test_split_clears_previous_parts_of_the_same_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            installer = self.write_installer(base)
            work = base / "work"
            transfer_asset.split(installer, work, parts=3)
            manifest = transfer_asset.split(installer, work, parts=2)
            parts = sorted(work.glob("*.part"))
            self.assertEqual(len(parts), 2)
            self.assertEqual({part.name for part in parts},
                             {entry["name"] for entry in manifest["parts"]})

    def test_split_refuses_to_write_inside_dist(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            installer = self.write_installer(base)
            with mock.patch.object(transfer_asset, "DIST", (base / "dist").resolve()):
                with self.assertRaisesRegex(transfer_asset.TransferError, "不能写进 dist"):
                    transfer_asset.split(installer, base / "dist/transfer", parts=2)

    def test_split_bounds_and_installer_name(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            installer = self.write_installer(base)
            for parts in (1, 65):
                with self.assertRaisesRegex(transfer_asset.TransferError, "2 到 64"):
                    transfer_asset.split(installer, base / "work", parts=parts)
            other = base / "random.dmg"
            other.write_bytes(b"x")
            with self.assertRaisesRegex(transfer_asset.TransferError, "不是版本化安装包"):
                transfer_asset.split(other, base / "work2", parts=2)

    def test_join_rejects_a_tampered_part(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            installer = self.write_installer(base)
            work = base / "work"
            manifest = transfer_asset.split(installer, work, parts=2)
            victim = work / manifest["parts"][0]["name"]
            victim.write_bytes(b"tampered" + victim.read_bytes()[8:])
            with self.assertRaisesRegex(transfer_asset.TransferError, "分片校验失败"):
                transfer_asset.join(work, base / "joined.dmg")
            self.assertFalse((base / "joined.dmg").exists())

    def test_manifest_loader_requires_exactly_one(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            with self.assertRaisesRegex(transfer_asset.TransferError, "恰有一个 manifest"):
                transfer_asset.load_manifest(work)


class UploadGuardTests(unittest.TestCase):
    def prepare(self, base: Path):
        installer = base / "ElectricityToolbox-0.1.27-apple-silicon.dmg"
        installer.write_bytes(b"a" * 400)
        work = base / "work"
        manifest = transfer_asset.split(installer, work, parts=2)
        return manifest, work

    def test_upload_refuses_a_published_release(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            _, work = self.prepare(base)
            run, _ = fake_gh({"isDraft": False, "assets": []})
            with mock.patch.object(transfer_asset, "run", run):
                with self.assertRaisesRegex(transfer_asset.TransferError, "已发布"):
                    transfer_asset.upload(work, "v0.1.27", "owner/repo")

    def test_upload_skips_parts_already_on_the_release(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            manifest, work = self.prepare(base)
            assets = [{"name": part["name"], "size": part["size"],
                       "digest": f"sha256:{part['sha256']}"} for part in manifest["parts"]]
            run, calls = fake_gh({"isDraft": True, "assets": assets})
            with mock.patch.object(transfer_asset, "run", run):
                transfer_asset.upload(work, "v0.1.27", "owner/repo")
            uploads = [call for call in calls if call[:3] == ["gh", "release", "upload"]]
            # Only the manifest still needs to go up.
            self.assertEqual(len(uploads), 1)
            self.assertTrue(uploads[0][4].endswith("manifest.json"))


class CleanGuardTests(unittest.TestCase):
    def prepare(self, base: Path):
        installer = base / "ElectricityToolbox-0.1.27-apple-silicon.dmg"
        installer.write_bytes(b"b" * 400)
        work = base / "work"
        manifest = transfer_asset.split(installer, work, parts=2)
        return manifest, work

    def test_clean_keeps_parts_while_the_full_installer_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            manifest, work = self.prepare(base)
            run, _ = fake_gh({"isDraft": True, "assets": []})
            with mock.patch.object(transfer_asset, "run", run):
                with self.assertRaisesRegex(transfer_asset.TransferError, "还没有"):
                    transfer_asset.clean(work, "v0.1.27", "owner/repo")
            for part in manifest["parts"]:
                self.assertTrue((work / part["name"]).is_file())

    def test_clean_rejects_a_mismatched_full_installer(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            manifest, work = self.prepare(base)
            payload = {"isDraft": False, "assets": [
                {"id": 1, "name": manifest["canonical"], "size": manifest["size"],
                 "digest": "sha256:" + "0" * 64},
            ]}
            run, _ = fake_gh(payload)
            with mock.patch.object(transfer_asset, "run", run):
                with self.assertRaisesRegex(transfer_asset.TransferError, "不一致"):
                    transfer_asset.clean(work, "v0.1.27", "owner/repo")

    def test_clean_removes_parts_and_assets_after_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            manifest, work = self.prepare(base)
            assets = [{"id": 100, "name": manifest["canonical"], "size": manifest["size"],
                       "digest": f"sha256:{manifest['sha256']}"}]
            assets += [{"id": index, "name": part["name"], "size": part["size"],
                        "digest": f"sha256:{part['sha256']}"}
                       for index, part in enumerate(manifest["parts"])]
            manifest_path = Path(manifest["manifest_path"])
            assets.append({"id": 99, "name": manifest_path.name,
                           "size": manifest_path.stat().st_size,
                           "digest": f"sha256:{transfer_asset.digest_file(manifest_path)}"})
            run, calls = fake_gh({"isDraft": False, "assets": assets})
            with mock.patch.object(transfer_asset, "run", run):
                removed = transfer_asset.clean(work, "v0.1.27", "owner/repo")
            self.assertEqual(removed, len(manifest["parts"]) + 1)
            self.assertEqual(sorted(work.glob("*.part")), [])
            deletes = [call for call in calls if call[:3] == ["gh", "api", "--method"]]
            self.assertEqual(len(deletes), len(manifest["parts"]) + 1)
            self.assertTrue(all("releases/assets/" in call[4] for call in deletes))


if __name__ == "__main__":
    unittest.main()
