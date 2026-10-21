"""Build and install the optional helper beside the 500kV Python modules."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def build(destination: Path | None = None, target: str | None = None) -> Path:
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo")
    crate = Path(__file__).resolve().parent
    args = [cargo, "build", "--release", "--locked", "--manifest-path", str(crate / "Cargo.toml")]
    if target:
        args.extend(["--target", target])
    subprocess.run(args, check=True)
    artifacts = Path(os.environ.get("CARGO_TARGET_DIR", crate / "target"))
    if target:
        artifacts /= target
    binary = artifacts / "release/toolbox-price-xlsx"
    destination = destination or ROOT / "500kV断面分析/bin/toolbox-price-xlsx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, destination)
    destination.chmod(0o755)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(build(destination=args.output, target=args.target))
