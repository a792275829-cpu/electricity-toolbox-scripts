import unittest

from tools.build_500kv_position_candidates import candidate_score, name_variants


class Build500kVPositionCandidatesTest(unittest.TestCase):
    def test_station_suffix_and_diagram_bus_suffix_match(self):
        self.assertGreaterEqual(candidate_score("丹霞站", "丹霞Y", "primary", "node"), 0.9)
        self.assertGreaterEqual(candidate_score("宝安换流站", "宝安B", "primary", "node"), 0.9)

    def test_meaningful_a_b_station_variants_are_preserved(self):
        self.assertIn("鹅凰a", name_variants("鹅凰A站"))
        self.assertNotIn("鹅凰", name_variants("鹅凰A站"))
        self.assertLess(candidate_score("鹅凰A站", "鹅凰B", "primary", "node"), 0.9)

    def test_known_ocr_alias_matches(self):
        self.assertGreaterEqual(candidate_score("鹊垌站", "鹊洞", "primary", "node"), 0.9)


if __name__ == "__main__":
    unittest.main()
