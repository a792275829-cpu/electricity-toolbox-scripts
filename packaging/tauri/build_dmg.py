"""Build the same user-install image used by full and incremental updates."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desktop/python"))
from installer_image import build as build_image


def build(app: Path, output: Path) -> Path:
    return build_image(app, output, Path(__file__).with_name("install_user_app.py"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.app, args.output))
