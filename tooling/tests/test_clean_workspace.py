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


class LogArchiveTests(unittest.TestCase):
    def build_logs(self, base: Path) -> Path:
        logs = base / "logs"
        (logs / "release-archive").mkdir(parents=True)
        (logs / "release-archive" / "old.md").write_text("archived", encoding="utf-8")
        (logs / "tests.json").write_text("{}", encoding="utf-8")
        (logs / "ci-tests.json").write_text("{}", encoding="utf-8")
        for version in ("0.1.10", "0.1.22", "0.1.26"):
            folder = logs / f"release-v{version}"
            folder.mkdir()
            (folder / "notes.md").write_text(version, encoding="utf-8")
        (logs / "逐模块代码审查-2026-10-27.md").write_text("report", encoding="utf-8")
        (logs / "module-switch").mkdir()
        (logs / "module-switch" / "result.md").write_text("x", encoding="utf-8")
        return logs

    def test_plan_keeps_archive_reports_and_newest_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = self.build_logs(Path(folder))
            names = {item.name for item in clean_workspace.plan_logs(logs=logs, keep_release=1)}
            self.assertEqual(names, {"release-v0.1.10", "release-v0.1.22",
                                     "逐模块代码审查-2026-10-27.md", "module-switch"})

    def test_zero_keep_release_moves_every_release_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = self.build_logs(Path(folder))
            names = {item.name for item in clean_workspace.plan_logs(logs=logs, keep_release=0)}
            self.assertIn("release-v0.1.26", names)
            self.assertNotIn("release-archive", names)

    def test_negative_keep_release_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能为负数"):
            clean_workspace.kept_release_dirs(["release-v0.1.26"], -1)

    def test_archive_moves_without_deleting_or_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = self.build_logs(Path(folder))
            items = clean_workspace.plan_logs(logs=logs, keep_release=1)
            moved = clean_workspace.archive(items, logs=logs)

            self.assertEqual(len(moved), 4)
            self.assertFalse((logs / "release-v0.1.10").exists())
            self.assertTrue((logs / "release-v0.1.26" / "notes.md").is_file())
            self.assertTrue((logs / "release-archive" / "old.md").is_file())
            self.assertTrue((logs / "release-archive" / "release-v0.1.22" / "notes.md").is_file())
            self.assertEqual(
                (logs / "release-archive" / "逐模块代码审查-2026-10-27.md").read_text(encoding="utf-8"),
                "report",
            )
            self.assertEqual(clean_workspace.plan_logs(logs=logs, keep_release=1), [])

    def test_archive_skips_paths_outside_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            logs = self.build_logs(base)
            outside = base / "keep.md"
            outside.write_text("x", encoding="utf-8")
            moved = clean_workspace.archive([outside], logs=logs)
            self.assertEqual(moved, [])
            self.assertTrue(outside.is_file())


class PayloadTests(unittest.TestCase):
    TARGET = "aarch64-apple-darwin"

    def build_payload(self, base: Path, marker: str | None = None) -> Path:
        payload = base / "payload"
        (payload / "app" / "电力工具箱").mkdir(parents=True)
        (payload / "app" / "电力工具箱" / "main.py").write_bytes(b"c" * 30)
        (payload / "defaults").mkdir()
        (payload / "defaults" / "config.json").write_bytes(b"d" * 10)
        (payload / "bootstrap").mkdir()
        (payload / "manifest.json").write_text("{}", encoding="utf-8")
        (payload / "runtime" / "python").mkdir(parents=True)
        (payload / "runtime" / "python" / "python3").write_bytes(b"p" * 1000)
        if marker is not None:
            (payload / "runtime" / "target.txt").write_text(marker, encoding="utf-8")
        return payload

    def test_reusable_runtime_survives_and_staging_siblings_go(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            payload = self.build_payload(base, self.TARGET)
            names = {item.relative_to(payload).as_posix()
                     for item in clean_workspace.payload_plan(payload, target=self.TARGET)}
            self.assertEqual(names, {"app", "defaults", "bootstrap", "manifest.json"})
            self.assertTrue(clean_workspace.reusable_runtime(payload, self.TARGET))

    def test_marker_for_another_target_drops_the_whole_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = self.build_payload(Path(folder), "x86_64-apple-darwin")
            self.assertEqual(
                clean_workspace.payload_plan(payload, target=self.TARGET), [payload]
            )

    def test_missing_marker_or_drop_runtime_removes_everything(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            self.assertEqual(
                clean_workspace.payload_plan(self.build_payload(base / "a"), target=self.TARGET),
                [base / "a" / "payload"],
            )
            kept = self.build_payload(base / "b", self.TARGET)
            self.assertEqual(
                clean_workspace.payload_plan(kept, keep_runtime=False, target=self.TARGET),
                [kept],
            )

    def test_apply_keeps_the_runtime_tree_but_not_the_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            payload = self.build_payload(base, self.TARGET)
            removed = clean_workspace.apply(
                clean_workspace.payload_plan(payload, target=self.TARGET)
            )
            self.assertEqual(removed, 42)
            self.assertFalse((payload / "app").exists())
            self.assertTrue((payload / "runtime" / "python" / "python3").is_file())
            self.assertTrue((payload / "runtime" / "target.txt").is_file())


if __name__ == "__main__":
    unittest.main()
