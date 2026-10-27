from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tooling import clean_workspace  # noqa: E402


def installer(version):
    return f"ElectricityToolbox-{version}-apple-silicon.dmg"


class InstallerVersionTests(unittest.TestCase):
    def test_only_versioned_installers_are_considered(self):
        names = [installer("0.1.23"), installer("0.1.22"), "电力工具箱_macOS_20261020.zip",
                 "ElectricityToolbox-0.1.22-apple-silicon.dmg.sha256"]
        self.assertEqual(
            set(clean_workspace.installer_versions(names)),
            {(0, 1, 23), (0, 1, 22)},
        )

    def test_newest_versions_survive_regardless_of_listing_order(self):
        names = [installer("0.1.9"), installer("0.1.23"), installer("0.1.10"),
                 installer("0.1.22")]
        self.assertEqual(
            clean_workspace.stale_installers(names, 2),
            [installer("0.1.10"), installer("0.1.9")],
        )

    def test_keeping_everything_deletes_nothing(self):
        names = [installer("0.1.22"), installer("0.1.23")]
        self.assertEqual(clean_workspace.stale_installers(names, 2), [])

    def test_zero_keep_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "至少为 1"):
            clean_workspace.stale_installers([installer("0.1.23")], 0)


class PlanTests(unittest.TestCase):
    def test_plan_keeps_patch_assets_and_download_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            dist = base / "dist"
            (dist / "updates").mkdir(parents=True)
            (dist / "updates" / "0.1.22-to-0.1.23.patch.tar.xz").write_bytes(b"p")
            (dist / "tauri-build").mkdir()
            (dist / "tauri-build" / "python-download").mkdir()
            for version in ("0.1.21", "0.1.22", "0.1.23"):
                (dist / installer(version)).write_bytes(b"d")
                (dist / f"{installer(version)}.sha256").write_bytes(b"s")
            release = dist / "release-v0.1.22"
            release.mkdir()
            (release / installer("0.1.22")).write_bytes(b"d")
            (release / "transfer").mkdir()
            (release / "published.json").write_text("{}", encoding="utf-8")

            target = base / "target"
            (target / "debug").mkdir(parents=True)
            payload = base / "payload"
            payload.mkdir()

            items = clean_workspace.plan(2, dist=dist, target=target, payload=payload)
            names = {item.relative_to(base).as_posix() for item in items}
            self.assertEqual(
                names,
                {
                    "dist/ElectricityToolbox-0.1.21-apple-silicon.dmg",
                    "dist/ElectricityToolbox-0.1.21-apple-silicon.dmg.sha256",
                    "dist/release-v0.1.22/ElectricityToolbox-0.1.22-apple-silicon.dmg",
                    "dist/release-v0.1.22/transfer",
                    "target/debug",
                    "payload",
                },
            )

    def test_apply_removes_the_plan_and_reports_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            dist = base / "dist"
            dist.mkdir()
            old = dist / installer("0.1.9")
            old.write_bytes(b"x" * 10)
            new = dist / installer("0.1.23")
            new.write_bytes(b"y" * 20)
            target = base / "target"
            target.mkdir()
            payload = base / "payload"
            payload.mkdir()
            (payload / "app").mkdir()
            (payload / "app" / "a.py").write_bytes(b"z" * 5)

            items = clean_workspace.plan(1, dist=dist, target=target, payload=payload)
            removed = clean_workspace.apply(items)
            self.assertFalse(old.exists())
            self.assertFalse(payload.exists())
            self.assertTrue(new.is_file())
            self.assertGreaterEqual(removed, 15)


if __name__ == "__main__":
    unittest.main()
