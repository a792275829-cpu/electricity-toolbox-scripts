"""Move a large installer to a draft release in parts.

A 700+ MB DMG occasionally fails to upload in one `gh release upload` from a
home connection. This tool splits it into parts of identical bytes, uploads
them as draft assets, and removes those parts only after the canonical
installer is confirmed on the release with matching size and digest.

Every file it writes lives in ``--work`` (default ``logs/release-<tag>/transfer``);
it refuses to write inside ``dist``, which is reserved for products.

    split  dist/ElectricityToolbox-0.1.27-apple-silicon.dmg
    upload --tag v0.1.27
    join   --output /tmp/ElectricityToolbox-0.1.27-apple-silicon.dmg
    clean  --tag v0.1.27
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
INSTALLER_RE = re.compile(r"^ElectricityToolbox-(\d+\.\d+\.\d+)-apple-silicon\.dmg$")
CHUNK_READ = 8 * 1024 * 1024


class TransferError(RuntimeError):
    pass


def run(args, **kwargs):
    """Subprocess seam so tests never reach the network."""

    return subprocess.run(args, capture_output=True, text=True, check=True, **kwargs)


def gh_json(*args):
    return json.loads(run(["gh", *args]).stdout)


def gh_repo() -> str:
    return gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_READ), b""):
            sha.update(block)
    return sha.hexdigest()


def tag_for(installer: Path) -> str:
    match = INSTALLER_RE.match(installer.name)
    if not match:
        raise TransferError(f"不是版本化安装包：{installer.name}")
    return f"v{match.group(1)}"


def default_work(tag: str) -> Path:
    return ROOT / "logs" / f"release-{tag}" / "transfer"


def ensure_outside_dist(work: Path) -> None:
    resolved = work.resolve()
    if resolved == DIST or DIST in resolved.parents:
        raise TransferError("分片不能写进 dist：dist 只放产物，发布过程放 logs/release-<tag>/")


def split(installer: Path, work: Path, parts: int = 16) -> dict:
    if not 2 <= parts <= 64:
        raise TransferError("分片数须在 2 到 64 之间")
    if not installer.is_file():
        raise TransferError(f"安装包不存在：{installer}")
    ensure_outside_dist(work)
    work.mkdir(parents=True, exist_ok=True)
    for stale in work.glob("*.part"):
        stale.unlink()

    tag = tag_for(installer)
    size = installer.stat().st_size
    total_sha = digest_file(installer)
    chunk = (size + parts - 1) // parts
    prefix = f"ToolboxTransfer-{tag}-{total_sha[:12]}-"

    entries, written = [], 0
    with installer.open("rb") as source:
        for index in range(parts):
            data = source.read(chunk)
            if not data:
                break
            name = f"{prefix}{index:02d}.part"
            (work / name).write_bytes(data)
            entries.append({"name": name, "size": len(data), "sha256": digest_bytes(data)})
            written += len(data)
    if written != size:
        raise TransferError(f"分片总长 {written} 与文件大小 {size} 不一致")

    manifest = {"tag": tag, "canonical": installer.name, "size": size,
                "sha256": total_sha, "prefix": prefix, "parts": entries}
    path = work / f"{prefix}manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(path)
    return manifest


def load_manifest(work: Path) -> dict:
    found = sorted(work.glob("*manifest.json"))
    if len(found) != 1:
        raise TransferError(f"{work} 中应恰有一个 manifest，实际 {len(found)} 个")
    manifest = json.loads(found[0].read_text(encoding="utf-8"))
    manifest["manifest_path"] = str(found[0])
    return manifest


def release_state(tag: str, repo: str) -> tuple[bool, dict]:
    payload = gh_json("release", "view", tag, "--repo", repo, "--json", "isDraft,assets")
    return bool(payload["isDraft"]), {asset["name"]: asset for asset in payload["assets"]}


def expected_assets(work: Path, manifest: dict) -> list[dict]:
    manifest_path = Path(manifest["manifest_path"])
    return manifest["parts"] + [{
        "name": manifest_path.name,
        "size": manifest_path.stat().st_size,
        "sha256": digest_file(manifest_path),
    }]


def upload(work: Path, tag: str, repo: str) -> None:
    manifest = load_manifest(work)
    if manifest["tag"] != tag:
        raise TransferError(f"manifest 属于 {manifest['tag']}，不是 {tag}")
    draft, assets = release_state(tag, repo)
    if not draft:
        raise TransferError(f"{tag} 已发布；分片只能补传进草稿发布")

    for part in expected_assets(work, manifest):
        existing = assets.get(part["name"]) or {}
        if existing.get("size") == part["size"] and existing.get("digest") == f"sha256:{part['sha256']}":
            continue
        run(["gh", "release", "upload", tag, str(work / part["name"]),
             "--repo", repo, "--clobber"])
        print(f"uploaded {part['name']} ({part['size']} bytes)", flush=True)

    _, after = release_state(tag, repo)
    missing = [part["name"] for part in manifest["parts"]
               if (after.get(part["name"]) or {}).get("digest") != f"sha256:{part['sha256']}"]
    if missing:
        raise TransferError(f"上传后校验失败：{', '.join(missing[:3])}")


def join(work: Path, output: Path) -> Path:
    manifest = load_manifest(work)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = hashlib.sha256()
    size = 0
    try:
        with output.open("wb") as sink:
            for part in manifest["parts"]:
                data = (work / part["name"]).read_bytes()
                if len(data) != part["size"] or digest_bytes(data) != part["sha256"]:
                    raise TransferError(f"分片校验失败：{part['name']}")
                sink.write(data)
                total.update(data)
                size += len(data)
        if size != manifest["size"] or total.hexdigest() != manifest["sha256"]:
            raise TransferError("合并结果与 manifest 摘要不一致")
    except TransferError:
        # Never leave a half-written installer behind to be mistaken for a good one.
        output.unlink(missing_ok=True)
        raise
    return output


def clean(work: Path, tag: str, repo: str, *, keep_local: bool = False) -> int:
    """Delete parts only once the canonical installer is verified on the release."""

    manifest = load_manifest(work)
    _, assets = release_state(tag, repo)
    canonical = assets.get(manifest["canonical"])
    if not canonical:
        raise TransferError(f"{tag} 上还没有 {manifest['canonical']}，保留分片")
    if (canonical.get("size") != manifest["size"]
            or canonical.get("digest") != f"sha256:{manifest['sha256']}"):
        raise TransferError("发布上的完整包与 manifest 不一致，保留分片")

    removed = 0
    for part in expected_assets(work, manifest):
        asset = assets.get(part["name"])
        if asset:
            run(["gh", "api", "--method", "DELETE",
                 f"repos/{repo}/releases/assets/{asset['id']}"])
        if not keep_local:
            (work / part["name"]).unlink(missing_ok=True)
        removed += 1
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="大安装包分片上传到草稿发布；完整包确认存在后才清理分片。",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["split", "upload", "join", "clean"])
    parser.add_argument("installer", nargs="?", type=Path, help="split 的安装包路径")
    parser.add_argument("--parts", type=int, default=16)
    parser.add_argument("--work", type=Path, help="默认 logs/release-<tag>/transfer")
    parser.add_argument("--tag")
    parser.add_argument("--repo", help="默认取 gh repo view")
    parser.add_argument("--output", type=Path, help="join 的输出路径")
    parser.add_argument("--keep-local", action="store_true", help="clean 时保留本地分片")
    args = parser.parse_args(argv)

    try:
        if args.action == "split":
            if not args.installer:
                parser.error("split 需要安装包路径")
            work = args.work or default_work(tag_for(args.installer))
            manifest = split(args.installer, work, args.parts)
            print(f"{len(manifest['parts'])} 个分片 -> {work}")
            print(f"完整包 {manifest['size']} 字节，sha256 {manifest['sha256']}")
            print(f"下一步：upload --tag {manifest['tag']} --work {work}")
            return 0

        work = args.work
        tag = args.tag
        if not tag and work:
            tag = load_manifest(work)["tag"]
        if not work:
            if not tag:
                parser.error("需要 --tag 或 --work")
            work = default_work(tag)
        if not tag:
            parser.error("需要 --tag 或 --work")

        if args.action == "join":
            if not args.output:
                parser.error("join 需要 --output")
            print(join(work, args.output))
            return 0

        repo = args.repo or gh_repo()
        if args.action == "upload":
            upload(work, tag, repo)
        else:
            print(f"已清理 {clean(work, tag, repo, keep_local=args.keep_local)} 项分片资产")
        return 0
    except (TransferError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(f"失败：{detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
