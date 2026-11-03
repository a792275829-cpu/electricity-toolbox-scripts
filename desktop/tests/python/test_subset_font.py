"""The bundled CJK fallback font must stay small without losing metrics."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packaging/tauri"))
FONT = ROOT / "风力预测截图/fonts/NotoSerifSC.ttf"
MODULE = ROOT / "风力预测截图/wind_forecast_screenshot.py"

try:
    import fontTools  # noqa: F401
except ImportError:  # pragma: no cover - build machines without the optional tool
    fontTools = None


@unittest.skipIf(fontTools is None, "未安装 fontTools")
class FallbackFontSubsetTests(unittest.TestCase):
    def test_desired_characters_cover_gb2312_ascii_and_sources(self):
        from subset_font import desired_characters

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "labels.py"
            source.write_text('LABEL = "滘口站"\n', encoding="utf-8")
            characters = desired_characters([source])
        for expected in ("电", "日", "滘", "A", "7", "　", "、"):
            self.assertIn(expected, characters)
        self.assertNotIn("\n", characters)

    def test_shrink_keeps_metrics_weight_axis_and_drops_rare_glyphs(self):
        from fontTools.ttLib import TTFont
        from subset_font import shrink

        original = TTFont(FONT)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / FONT.name
            shutil.copy2(FONT, target)
            stats = shrink(target, [MODULE])
            font = TTFont(target)
            self.assertEqual(stats["before_bytes"], FONT.stat().st_size)
            self.assertLess(stats["after_bytes"], stats["before_bytes"] * 0.5)
            self.assertGreater(len(original.getBestCmap()), 3 * len(font.getBestCmap()))
            self.assertEqual([axis.axisTag for axis in font["fvar"].axes], ["wght"])
            before, after = original.getBestCmap(), font.getBestCmap()
            for character in "电日万千瓦时0123456789-（）：":
                self.assertEqual(font["hmtx"][after[ord(character)]],
                                 original["hmtx"][before[ord(character)]], character)
