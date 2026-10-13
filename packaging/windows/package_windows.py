"""Assemble a complete, relocatable Windows x64 distribution on any host."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_URL = ("https://releases.astral.sh/github/python-build-standalone/releases/download/20260610/"
              "cpython-3.11.15%2B20260610-x86_64-pc-windows-msvc-install_only_stripped.tar.gz")
NODE_VERSION = "22.23.3"
PLAYWRIGHT_VERSION = "1.55.0"
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
    if relative == Path("packaging/windows/requirements.lock"):
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
        if relative.suffix.lower() == ".bat":
            target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
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


def launcher(destination: Path, *, diagnostics: bool) -> None:
    from pip._vendor.distlib import resources

    resource = "t64.exe" if diagnostics else "w64.exe"
    stub = resources.finder("pip._vendor.distlib").find(resource).bytes
    if "<launcher_dir>".encode("utf-16le") not in stub:
        raise RuntimeError("pip's distlib launcher lacks relocatable-path support")
    script = (
        "import sys\nfrom pathlib import Path\n"
        "root = Path(sys.argv[0]).resolve().parent\n"
        "sys.path.insert(0, str(root / 'packaging' / 'windows'))\n"
        "from portable_bootstrap import main\n"
        f"result = main(root, diagnostics={diagnostics!r})\n"
    )
    if diagnostics:
        script += "try:\n    input('Press Enter to close...')\nexcept EOFError:\n    pass\n"
    script += "raise SystemExit(result)\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("__main__.py", script)
    interpreter = "python.exe" if diagnostics else "pythonw.exe"
    shebang = f"#!<launcher_dir>\\runtime\\python311\\{interpreter} -I\n".encode("utf-8")
    destination.write_bytes(stub + shebang + buffer.getvalue())


def install_windows_browsers(uploader: Path, cache: Path, destination: Path) -> None:
    """Use Playwright's registry, with Python extraction across host Node versions."""
    registry_cache = cache / f"ms-playwright-{PLAYWRIGHT_VERSION}-win64"
    environment = {**os.environ, "PLAYWRIGHT_HOST_PLATFORM_OVERRIDE": "win64",
                   "PLAYWRIGHT_BROWSERS_PATH": str(registry_cache)}
    query = (
        "const {registry}=require('./node_modules/playwright-core/lib/server/registry/index.js');"
        "console.log(JSON.stringify(['chromium','chromium-headless-shell','ffmpeg','winldd'].map(name=>{"
        "const e=registry.findExecutable(name);return {name,directory:e.directory,urls:e.downloadURLs};})));"
    )
    raw = subprocess.check_output([shutil.which("node") or "node", "-e", query],
                                  cwd=uploader, env=environment, text=True)
    for browser in json.loads(raw):
        directory = Path(browser["directory"])
        if not directory.resolve().is_relative_to(registry_cache.resolve()):
            raise ValueError("Unexpected browser directory")
        marker = directory / "INSTALLATION_COMPLETE"
        if marker.is_file():
            continue
        archive = cache / f"{directory.name}-win64.zip"
        failures = []
        for url in browser["urls"]:
            try:
                download(url, archive)
                with zipfile.ZipFile(archive) as package:
                    if package.testzip() is not None:
                        raise RuntimeError("Browser ZIP CRC check failed")
                break
            except Exception as exc:
                failures.append(str(exc))
                archive.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"Cannot download {browser['name']}: {'; '.join(failures)}")
        extract(archive, directory)
        marker.touch()
    shutil.copytree(registry_cache, destination)
    shutil.rmtree(destination / ".links", ignore_errors=True)


def build(output: Path, *, python_runtime: Path | None = None, skip_zip: bool = False) -> Path:
    cache = ROOT / "dist" / "windows-build" / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y%m%d")
    name = f"电力工具箱_Windows_x64_便携版_{date}"
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=cache.parent)) / name
    stage.mkdir()
    print(f"Exported {export_source(stage)} source files", flush=True)
    runtime = stage / "runtime"
    python = runtime / "python311"
    if python_runtime is None:
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("The build machine needs uv, or pass --python-runtime with a clean Windows Python")
        python_downloads = cache / "python-download"
        subprocess.run([uv, "python", "install", "cpython-3.11.15-windows-x86_64-none",
                        "--install-dir", str(python_downloads), "--no-bin", "--no-registry"], check=True)
        python_runtime = python_downloads / "cpython-3.11.15-windows-x86_64-none"
    shutil.copytree(python_runtime, python, ignore_dangling_symlinks=True)
    for required in ("python.exe", "pythonw.exe", "python311.dll", "DLLs/_tkinter.pyd",
                     "tcl/tcl8.6/init.tcl", "vcruntime140.dll", "vcruntime140_1.dll"):
        if not (python / required).is_file():
            raise RuntimeError(f"Incomplete Python runtime: {required}")
    dependencies = python / "Lib" / "site-packages"
    lock = ROOT / "packaging" / "windows" / "requirements.lock"
    # pip resolves ONLY Windows wheels, never the build host's binaries.
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
        "--no-deps", "--require-hashes", "--only-binary=:all:", "--platform", "win_amd64",
        "--python-version", "3.11", "--implementation", "cp", "--abi", "cp311",
        "--target", str(dependencies), "--upgrade", "-r", str(lock),
    ], check=True, cwd=ROOT)
    node_name = f"node-v{NODE_VERSION}-win-x64"
    base = f"https://nodejs.org/dist/v{NODE_VERSION}/"
    checksums = download(base + "SHASUMS256.txt", cache / f"node-{NODE_VERSION}-SHASUMS256.txt")
    expected = next(line.split()[0] for line in checksums.read_text().splitlines()
                    if line.split()[-1] == node_name + ".zip")
    archive = download(base + node_name + ".zip", cache / (node_name + ".zip"), expected)
    with tempfile.TemporaryDirectory(dir=cache.parent) as directory:
        extract(archive, Path(directory))
        shutil.copytree(Path(directory) / node_name, runtime / "node")

    uploader = stage / "电力工具脚本" / "private-data-uploader-tool"
    # Build only this subtool's Node dependencies. No install scripts are run.
    package = json.loads((uploader / "package.json").read_text())
    package["dependencies"]["playwright"] = PLAYWRIGHT_VERSION
    (uploader / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    (uploader / "package-lock.json").unlink(missing_ok=True)
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("The build machine needs npm")
    subprocess.run([npm, "install", "--ignore-scripts", "--omit=optional", "--no-audit", "--no-fund"], cwd=uploader, check=True)
    registry = uploader / "node_modules" / "playwright-core" / "browsers.json"
    python_registry = dependencies / "playwright" / "driver" / "package" / "browsers.json"
    if json.loads(registry.read_text()) != json.loads(python_registry.read_text()):
        raise RuntimeError("Python and Node browser registries differ")
    install_windows_browsers(uploader, cache, runtime / "ms-playwright")

    for example in stage.rglob("config.example.json"):
        data = json.loads(example.read_text(encoding="utf-8"))
        for key in ("username", "password"):
            if key in data:
                data[key] = ""
        example.with_name("config.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (uploader / "upload.config.json").write_text(json.dumps({
        "baseUrl": "https://xhxt.chng.com.cn", "sourceDir": ".", "headless": True,
    }, indent=2), encoding="utf-8")
    launcher(stage / "电力工具箱.exe", diagnostics=False)
    launcher(stage / "环境自检.exe", diagnostics=True)
    shutil.copy2(ROOT / "packaging" / "windows" / "README.md", stage / "首次使用说明.md")
    # Keep the launcher copyright alongside its copied executable stub.
    import pip._vendor.distlib
    license_file = Path(pip._vendor.distlib.__file__).parent / "LICENSE.txt"
    if license_file.is_file():
        shutil.copy2(license_file, stage / "packaging" / "windows" / "distlib-LICENSE.txt")
    manifest = {
        "name": name, "target": ["Windows 10 22H2 x64", "Windows 11 x64"],
        "python": "3.11.15", "node": NODE_VERSION, "playwright": PLAYWRIGHT_VERSION,
        "windows_native_validation": "not_run_on_build_host",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "includes_uncommitted_source": True,
        "files": {},
    }
    for file in sorted(stage.rglob("*")):
        if file.is_file():
            manifest["files"][file.relative_to(stage).as_posix()] = {"size": file.stat().st_size, "sha256": sha256(file)}
    (stage / "便携包清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if skip_zip:
        return stage
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (name + ".zip")
    if archive.exists():
        archive = output / (name + "_" + datetime.now().strftime("%H%M%S") + ".zip")
    print("Creating ZIP...", flush=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                package.write(file, (Path(name) / file.relative_to(stage)).as_posix())
    (archive.with_suffix(".zip.sha256")).write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
    print(f"Package: {archive}\nStage: {stage}", flush=True)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--python-runtime", type=Path, help="Reuse an already downloaded clean Windows Python")
    parser.add_argument("--skip-zip", action="store_true")
    args = parser.parse_args()
    build(args.output_dir, python_runtime=args.python_runtime, skip_zip=args.skip_zip)
