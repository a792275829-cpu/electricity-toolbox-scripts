"""Install a complete, portable sample pack using locally staged business copies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]
from toolbox.catalog import default_catalog
from toolbox_credentials import write_private_json

ONLINE_ONLY = {"online-energy", "wind-forecast"}
ALLOWED_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".docx", ".pdf", ".md", ".json", ".csv", ".txt"}
PRIVATE_NAMES = {"config.json", "auth_state.json", "upload.config.json", "wps_excel_to_kdocs_config.json"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(
        p in (".", "..") or "\\" in p or ":" in p for p in relative.parts
    ):
        raise ValueError("样例路径必须是工作目录内的相对路径。")
    target = root.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("样例路径超出工作目录。")
    return target


def read_plan(source: Path, manifest: Path) -> dict:
    plan = json.loads(manifest.read_text(encoding="utf-8"))
    if plan.get("version") != 1 or not isinstance(plan.get("tools"), list):
        raise ValueError("无法识别样例清单。")
    catalog = {item.tool_id: item.name for item in default_catalog()}
    tools = plan["tools"]
    if len(tools) != len(catalog) or {t["id"] for t in tools} != set(catalog):
        raise ValueError("样例清单必须覆盖工具箱的全部模块。")
    outputs = set()
    for tool in tools:
        if tool["name"] != catalog[tool["id"]]:
            raise ValueError("样例模块名称与工具箱不一致。")
        files = tool.get("files", [])
        if tool["id"] not in ONLINE_ONLY and not files:
            raise ValueError(f"模块缺少本地样例：{tool['name']}")
        for item in files:
            original = safe(source, item["source"])
            relative = PurePosixPath(tool["name"]) / item["path"]
            safe(Path.cwd(), relative.as_posix())
            if original.is_symlink() or not original.is_file():
                raise FileNotFoundError(f"样例源文件不存在：{item['source']}")
            if original.suffix.lower() not in ALLOWED_SUFFIXES or original.name in PRIVATE_NAMES:
                raise ValueError("账号配置和登录态不能作为测试样例。")
            if relative.as_posix().casefold() in outputs:
                raise ValueError("样例文件路径重复。")
            outputs.add(relative.as_posix().casefold())
            if digest(original) != item["sha256"]:
                raise ValueError(f"样例源文件已变化：{item['source']}")
    return plan


def migrate(source: Path, destination: Path, manifest: Path | None = None,
            *, samples_root: Path | None = None) -> dict:
    source, destination = source.resolve(), destination.resolve()
    manifest = manifest or source / ".toolbox/migration-samples.json"
    plan = read_plan(source, manifest)
    samples = samples_root or destination / "测试样例"
    samples.mkdir(parents=True, exist_ok=True)
    pending, preserved = [], []
    for tool in plan["tools"]:
        for item in tool.get("files", []):
            original = safe(source, item["source"])
            target = safe(samples, (PurePosixPath(tool["name"]) / item["path"]).as_posix())
            if target.exists():
                if not target.is_file() or digest(target) != item["sha256"]:
                    preserved.append(str(target.relative_to(samples)))
                continue
            pending.append((original, target, item["sha256"]))
    if preserved:
        raise FileExistsError("已保留用户修改过的样例，请先核对：" + "、".join(preserved))
    created = []
    try:
        for original, target, expected in pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix=".sample-", dir=target.parent)
            os.close(handle)
            try:
                shutil.copyfile(original, name)
                if digest(Path(name)) != expected:
                    raise RuntimeError("样例复制校验失败。")
                Path(name).replace(target)
                created.append(target)
            finally:
                Path(name).unlink(missing_ok=True)
        # The distributable index contains relative paths, never source machine paths.
        public = {
            "version": 1,
            "tools": [{k: v for k, v in tool.items() if k != "files"} | {
                "files": [{k: v for k, v in item.items() if k != "source"}
                          for item in tool.get("files", [])]
            } for tool in plan["tools"]],
        }
        write_private_json(samples / "样例清单.json", public)
        lines = ["# 电力工具箱本地测试样例", "", "每个模块的文件均为独立副本。沿用工具箱现有的选择文件／选择目录入口，按下面说明选取。", "",
                 "预测模块正式总表仍保存在模块输出目录；这里另放一份测试副本。网站采集、登录及上传仍需要网络与有效账号。", "",
                 "上传样例用于本地识别、解析和预览；本次验证没有执行网站写入或上传。", ""]
        for tool in plan["tools"]:
            folder = samples / tool["name"]
            folder.mkdir(exist_ok=True)
            details = [f"# {tool['name']}", "", tool["instructions"], ""]
            if tool.get("parameters"):
                details.extend(f"- {key}：{value}" for key, value in tool["parameters"].items())
                details.append("")
            details.extend(f"- `{item['path']}`：{item['role']}" for item in tool.get("files", []))
            details.append("")
            (folder / "使用说明.md").write_text("\n".join(details), encoding="utf-8")
            lines.extend([f"## {tool['name']}", "", tool["instructions"], "",
                          f"本模块目录：`{tool['name']}/`，详见目录内使用说明。", ""])
        (samples / "使用说明.md").write_text("\n".join(lines), encoding="utf-8")
    except BaseException:
        for target in created:
            target.unlink(missing_ok=True)
        raise
    return {"modules": len(plan["tools"]), "local_file_modules": len(plan["tools"]) - len(ONLINE_ONLY),
            "sample_files": sum(len(t.get("files", [])) for t in plan["tools"]),
            "copied_files": len(created), "byte_verification_passed": True, "samples": str(samples)}


def export_pack(samples: Path, output: Path) -> int:
    plan = json.loads((samples / "样例清单.json").read_text(encoding="utf-8"))
    names = {"使用说明.md", "样例清单.json"}
    for tool in plan["tools"]:
        names.add(f"{tool['name']}/使用说明.md")
        for item in tool.get("files", []):
            name = f"{tool['name']}/{item['path']}"
            if digest(safe(samples, name)) != item["sha256"]:
                raise ValueError("样例已修改，请从迁移源重新生成分发包。")
            names.add(name)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            archive.write(safe(samples, name), "测试样例/" + name)
    return len(names)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="迁移所有模块的本地测试样例")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--app-home", action="store_true", help="destination 为新版用户目录，样例放入 samples/")
    parser.add_argument("--zip", type=Path)
    args = parser.parse_args()
    result = migrate(args.source, args.destination, args.manifest,
                     samples_root=args.destination / "samples" if args.app_home else None)
    if args.zip:
        result["archive_entries"] = export_pack(Path(result["samples"]), args.zip)
        result["archive"] = str(args.zip.resolve())
    print(json.dumps(result, ensure_ascii=False))
