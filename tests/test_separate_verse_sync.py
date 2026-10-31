import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('separate_verse_cli', ROOT / 'tools/separate-verse-sync.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def match(rows, chroma, times, duration, onset, weight, onset_bounds=None):
    starts = [onset, onset + 40] if len(rows) == 2 else [onset]
    return {'best': {'cost': .2, 'key': 2, 'starts': starts, 'span': duration},
            'runnerUp': {'cost': .4}}


class SeparateVerseTests(unittest.TestCase):
    def test_independent_complete_verses_allow_a_short_interlude(self):
        rows = [{}, {}]
        target = (np.array([0., 199.]), None)
        with patch.object(cli.sectional.midi, 'match_template', side_effect=match):
            times, detail = cli.measure(target, rows, rows, {'editCost': 0, 'semitoneShift': 0},
                                        {'periodEstimate': 50, 'verseEstimates': [10, 60, 140]})
        self.assertEqual(times, [10, 50, 60, 100, 140, 180])
        self.assertEqual(len(detail['verses']), 3)

    def test_possible_extra_verse_in_interlude_is_rejected(self):
        target = (np.array([0., 209.]), None)
        with patch.object(cli.sectional.midi, 'match_template', side_effect=match):
            with self.assertRaisesRegex(ValueError, '另一个完整诗节'):
                cli.measure(target, [{}, {}], [{}, {}], {'editCost': 0, 'semitoneShift': 0},
                            {'periodEstimate': 50, 'verseEstimates': [10, 60, 150]})

    def test_inexact_pitch_sequence_cannot_use_segmented_verses(self):
        with self.assertRaisesRegex(ValueError, '逐音一致'):
            cli.measure((np.array([0., 199.]), None), [{}, {}], [{}, {}], {'editCost': 1},
                        {'periodEstimate': 50, 'verseEstimates': [10, 60, 140]})


if __name__ == '__main__':
    unittest.main()
