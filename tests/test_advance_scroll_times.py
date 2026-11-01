import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('advance_scroll', Path(__file__).resolve().parents[1] / 'tools/advance-scroll-times.py')
advance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advance)


class AdvanceScrollTimes(unittest.TestCase):
    def fixture(self):
        return {'songId': '05-100', 'cues': [{'at': 0, 'region': 'a'}, {'at': 10, 'moveStart': 8, 'region': 'b'}],
                'tracks': [{'file': 'lead.mp3', 'offset': -.7},
                           {'file': 'backing.mp3', 'offset': 0, 'cues': [{'at': 1.4, 'moveStart': 0, 'region': 'a'},
                                                                    {'at': 15.05, 'moveStart': 12, 'region': 'b'}]}]}

    def test_shared_independent_and_interlude_times_shift_together(self):
        original = self.fixture()
        result = advance.advance(original, 'a' * 64)
        self.assertEqual([c['at'] for c in result['cues']], [0, 8.5])
        self.assertEqual(result['cues'][1]['moveStart'], 6.5)
        self.assertEqual(result['tracks'][1]['cues'], [{'at': 0, 'moveStart': 0, 'region': 'a'},
                                                      {'at': 13.55, 'moveStart': 10.5, 'region': 'b'}])
        self.assertEqual(result['tracks'][0]['offset'], -.7)
        self.assertEqual(original['cues'][1]['at'], 10)

    def test_excluded_song_and_already_adjusted_files_are_never_shifted_again(self):
        data = self.fixture()
        data['songId'] = '05-099'
        self.assertIsNone(advance.advance(data, 'a' * 64))
        data['songId'] = '05-100'
        result = advance.advance(data, 'a' * 64)
        self.assertIsNone(advance.advance(result, 'b' * 64))

    def test_conflicting_adjustments_and_collapsed_cues_require_review(self):
        data = self.fixture()
        data['provenance'] = {'timingAdjustment': {'requestId': 'other'}}
        with self.assertRaises(ValueError):
            advance.advance(data, 'a' * 64)
        data = self.fixture()
        data['cues'][1]['at'] = 1
        with self.assertRaises(ValueError):
            advance.advance(data, 'a' * 64)


if __name__ == '__main__':
    unittest.main()
