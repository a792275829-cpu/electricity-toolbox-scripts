"""Source export and runtime download helpers for the macOS desktop build."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE_VERSION = "22.23.3"
PLAYWRIGHT_VERSION = "1.55.0"


def normalize_permissions(root: Path) -> None:
    """Make bundled files readable after PKG installation changes their owner."""
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        path.chmod(0o755 if path.is_dir() or mode & 0o111 else 0o644)


EXCLUDED_PARTS = {".git", ".venv", "node_modules", "dist", "__pycache__", ".codegraph",
                  ".toolbox", ".state", ".qingneng-session", ".browser-profile",
                  ".browser-profile-uploader", "wps-browser-profile", "输出", "logs", "local_data", "result", "测试样例"}
EXCLUDED_NAMES = {"config.json", "auth_state.json", "auth_state.json.lock", "upload.config.json",
                  "wps_excel_to_kdocs_config.json", "file_dialog_settings.json", "wps-chrome.pid",
                  "wps-chrome-state.json", "500kv_node_position_candidates.png",
                  "500kv_node_position_candidates_contact_sheet.png"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exportable(relative: Path) -> bool:
    if relative in {Path("packaging/tauri/requirements-macos.lock"), Path("native/price-xlsx/Cargo.lock")}:
        return True
    return (not EXCLUDED_PARTS.intersection(relative.parts)
            and relative.name not in EXCLUDED_NAMES
            and not relative.name.endswith((".lock", ".log", ".pyc", ".bak", ".tmp"))
            and relative.name not in {".DS_Store", "Thumbs.db"}
            and not relative.name.startswith("~$"))


def export_source(destination: Path, *, predicate=exportable) -> int:
    raw = subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT)
    count = 0
    for name in sorted(set(raw.decode("utf-8").split("\0"))):
        if not name:
            continue
        relative = Path(name)
        source = ROOT / relative
        if not predicate(relative) or not source.is_file() or source.is_symlink():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    return count


def download(url: str, target: Path, expected: str | None = None) -> Path:
    if target.is_file() and (expected is None or sha256(target) == expected):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {target.name}", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as source, temporary.open("wb") as stream:
            shutil.copyfileobj(source, stream)
        if expected and sha256(temporary) != expected:
            raise RuntimeError(f"SHA256 mismatch: {target.name}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as package:
            for name in package.namelist():
                if not (destination / name).resolve().is_relative_to(destination.resolve()):
                    raise ValueError(f"Unsafe archive entry: {name}")
            package.extractall(destination)
    else:
        with tarfile.open(archive) as package:
            package.extractall(destination, filter="data")

