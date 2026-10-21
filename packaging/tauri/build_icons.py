"""Build macOS icons from the original artwork without its oversized margin."""
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def build() -> None:
    source = ROOT / "desktop/assets/app-icon.png"
    output = ROOT / "desktop/src-tauri/icons"
    with tempfile.TemporaryDirectory(prefix="toolbox-icons-") as temporary:
        staging = Path(temporary)
        master = staging / "master.png"
        # Original: 1254 square; opaque artwork bounds (141, 164, 1113, 1114).
        # Keep the entire artwork, centered in a 1000 square with a small margin.
        subprocess.run([
            "sips", "--cropToHeightWidth", "1000", "1000", "--cropOffset", "139", "127",
            str(source), "--out", str(master),
        ], check=True, capture_output=True)
        iconset = staging / "icon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = size * scale
                suffix = "@2x" if scale == 2 else ""
                subprocess.run([
                    "sips", "-z", str(pixels), str(pixels), str(master), "--out",
                    str(iconset / f"icon_{size}x{size}{suffix}.png"),
                ], check=True, capture_output=True)
        for name, source_name in (
            ("32x32.png", "icon_32x32.png"),
            ("128x128.png", "icon_128x128.png"),
            ("128x128@2x.png", "icon_128x128@2x.png"),
            ("256x256.png", "icon_256x256.png"),
        ):
            (output / name).write_bytes((iconset / source_name).read_bytes())
        subprocess.run([
            "iconutil", "-c", "icns", str(iconset), "-o", str(output / "icon.icns"),
        ], check=True)


if __name__ == "__main__":
    build()
