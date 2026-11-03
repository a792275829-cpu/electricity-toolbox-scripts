"""Shrink the bundled CJK fallback font to the characters the renderer can emit.

The 24 MiB Noto Serif SC variable font only serves as a fallback when no system
CJK font loads, so shipping all 30k glyphs costs most of its size for nothing.
The subset keeps every GB2312 character plus every non-ASCII character that
appears in the harvested sources, so metric-compatible text still renders.
"""
from __future__ import annotations

from pathlib import Path

EXTRA_CHARS = "　、。，．·；：？！…—～《》〈〉“”‘’（）【】「」"
SOURCE_SUFFIXES = (".py",)


def gb2312_characters() -> set[str]:
    characters = set()
    for high in range(0xA1, 0xFF):
        for low in range(0xA1, 0xFF):
            try:
                characters.add(bytes([high, low]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return characters


def harvested_characters(paths: list[Path]) -> set[str]:
    characters = set()
    for path in paths:
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        characters.update(ch for ch in path.read_text(encoding="utf-8", errors="ignore")
                          if ord(ch) > 0x7F)
    return characters


def desired_characters(sources: list[Path]) -> set[str]:
    printable = {chr(code) for code in range(0x20, 0x7F)}
    return gb2312_characters() | harvested_characters(sources) | printable | set(EXTRA_CHARS)


def shrink(font_path: Path, sources: list[Path]) -> dict:
    """Subset ``font_path`` in place; return the measured before/after sizes."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    before = font_path.stat().st_size
    characters = desired_characters(sources)
    options = subset.Options()
    # Keep family naming, layout features and the variable weight axis so the
    # renderer can still call set_variation_by_axes([400 or 700]).
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.notdef_outline = True
    options.drop_tables = []
    options.recalc_bounds = False
    options.recalc_timestamp = False
    font = TTFont(font_path)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=[ord(ch) for ch in characters])
    subsetter.subset(font)
    font.save(font_path)
    return {"font": font_path.name, "characters": len(characters),
            "before_bytes": before, "after_bytes": font_path.stat().st_size}
