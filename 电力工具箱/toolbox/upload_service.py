"""Shared macOS upload staging, independent of Tk."""
from __future__ import annotations
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

def copy_file_with_finder_fallback(source: Path, destination_dir: Path) -> Path:
    """Copy a selected file even when macOS TCC blocks Python from Desktop/Documents."""
    source = Path(source)
    destination_dir = Path(destination_dir)
    destination = destination_dir / source.name
    try:
        shutil.copy2(source, destination)
        return destination
    except PermissionError:
        if sys.platform != "darwin":
            raise

    script = (
        "on run argv\n"
        "set sourceFile to POSIX file (item 1 of argv) as alias\n"
        "set destinationFolder to POSIX file (item 2 of argv) as alias\n"
        'tell application "Finder" to duplicate sourceFile to destinationFolder with replacing\n'
        "end run"
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script, str(source), str(destination_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not destination.is_file():
        detail = (result.stderr or result.stdout or "Finder 未返回详细信息").strip()
        raise PermissionError(f"无法通过 Finder 复制文件：{source.name}。{detail[:300]}")
    return destination

def run_group_upload_with_staging(module, paths: list[Path], *, force: bool, on_log) -> None:
    target_date, original_uploads = module.prepare_upload_files(paths)
    original_province = next((item for item in original_uploads if item.kind == "省内"), None)
    original_energy = next(item for item in original_uploads if item.kind == "能销")

    with tempfile.TemporaryDirectory(prefix="electricity-group-upload-") as directory:
        staging_dir = Path(directory)
        on_log("正在通过 Finder 暂存所选 Excel，以获得 macOS 文件访问权限...")
        staged_paths = [
            item.path if item.builtin_template else copy_file_with_finder_fallback(item.path, staging_dir)
            for item in original_uploads
        ]
        _, staged_uploads = module.prepare_upload_files(staged_paths)
        staged_province = next((item for item in staged_uploads if item.kind == "省内"), None)
        staged_output = None
        if staged_province:
            output_options = {"output_dir": staging_dir} if staged_province.builtin_template else {}
            staged_output = module.province_output_path(staged_province.path, target_date, **output_options)

        module.run_api_upload(staged_paths, force=force)

        if original_province is None or staged_output is None:
            return

        copied_output = copy_file_with_finder_fallback(
            staged_output,
            original_energy.path.parent if original_province.builtin_template else original_province.path.parent,
        )
        on_log(f"已将更新后的省内数据复制回原目录：{copied_output}")
