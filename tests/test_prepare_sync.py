import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("prepare_sync", Path(__file__).parents[1] / "tools/prepare-sync.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparationTests(unittest.TestCase):
    def test_line_numbers_preserve_blank_lines_and_bound_long_stanzas(self):
        regions = prepare.text_regions("标题\r\n\r\n一\n二\n三\n四\n五\n\n副歌")
        self.assertEqual(regions["r2"]["startLine"], 2)
        self.assertEqual(regions["r2"]["endLine"], 5)
        self.assertEqual(regions["r3"]["startLine"], 6)
        self.assertEqual(regions["r4"]["startLine"], 8)

    def test_known_lyrics_and_long_gap_generate_only_candidate_interlude(self):
        order = [{"region": "top", "text": "第一段"}, {"region": "bottom", "text": "副歌"}, {"region": "top", "text": "第二段"}]
        words = [{"word": "第一段", "start": 10, "end": 20}, {"word": "副歌", "start": 21, "end": 30}, {"word": "第二段", "start": 50, "end": 60}]
        cues, diagnostics = prepare.candidate_cues(order, [{"words": words}])
        self.assertEqual(cues[2]["moveStart"], 30)
        self.assertEqual(cues[2]["region"], "top")
        self.assertNotIn("moveStart", cues[1])
        self.assertTrue(all(row["matchedFraction"] == 1 for row in diagnostics))

    def test_unrecognized_singing_does_not_turn_into_a_long_interlude(self):
        order = [{"region": "a", "text": "天地"}, {"region": "b", "text": "恩典"}, {"region": "c", "text": "荣耀"}]
        words = [{"word": "天地", "start": 5, "end": 10}, {"word": "荣耀", "start": 60, "end": 65}]
        cues, _ = prepare.candidate_cues(order, [{"words": words}])
        self.assertEqual(len(cues), 2)
        self.assertNotIn("moveStart", cues[1])

    def test_empty_or_unaligned_words_do_not_invent_times(self):
        cues, _ = prepare.candidate_cues([{"region": "r1", "text": "赞美"}], [{"words": [{"word": "赞美"}]}])
        self.assertEqual(cues, [])


if __name__ == "__main__":
    unittest.main()
