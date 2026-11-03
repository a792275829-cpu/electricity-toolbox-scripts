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
    def build_dist(self, base: Path) -> Path:
        """A dist with real products mixed with release staging debris."""

        dist = base / "dist"
        (dist / "updates").mkdir(parents=True)
        (dist / "updates" / "0.1.22-to-0.1.23.patch.tar.xz").write_bytes(b"p")
        (dist / "tauri-build" / "python-download").mkdir(parents=True)
        (dist / "样例.zip").write_bytes(b"z")
        for version in ("0.1.21", "0.1.22", "0.1.23"):
            (dist / installer(version)).write_bytes(b"d")
            (dist / f"{installer(version)}.sha256").write_bytes(b"s")
        staging = dist / "release-v0.1.22"
        staging.mkdir()
        (staging / "published.json").write_text("{}", encoding="utf-8")
        (staging / "transfer").mkdir()
        (dist / "build-v0.1.4.log").write_bytes(b"l")
        return dist

    def test_plan_keeps_products_and_drops_release_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            dist = self.build_dist(base)
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
                    "dist/release-v0.1.22",
                    "dist/build-v0.1.4.log",
                    "target/debug",
                    "payload",
                },
            )

    def test_plan_never_touches_patch_index_cache_or_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            dist = self.build_dist(base)
            items = clean_workspace.plan(1, dist=dist, target=base / "t", payload=base / "p")
            names = {item.relative_to(base).as_posix() for item in items}
            for kept in ("dist/updates", "dist/tauri-build", "dist/样例.zip"):
                self.assertNotIn(kept, names)

    def test_apply_removes_the_plan_and_reports_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            dist = base / "dist"
            dist.mkdir()
            old = dist / installer("0.1.9")
            old.write_bytes(b"x" * 10)
            new = dist / installer("0.1.23")
            new.write_bytes(b"y" * 20)
            stale = dist / "release-v0.1.9"
            stale.mkdir()
            (stale / "notes.md").write_bytes(b"n" * 7)
            target = base / "target"
            target.mkdir()
            payload = base / "payload"
            payload.mkdir()
            (payload / "app").mkdir()
            (payload / "app" / "a.py").write_bytes(b"z" * 5)

            items = clean_workspace.plan(1, dist=dist, target=target, payload=payload)
            removed = clean_workspace.apply(items)
            self.assertFalse(old.exists())
            self.assertFalse(stale.exists())
            self.assertFalse(payload.exists())
            self.assertTrue(new.is_file())
            self.assertGreaterEqual(removed, 22)


if __name__ == "__main__":
    unittest.main()
