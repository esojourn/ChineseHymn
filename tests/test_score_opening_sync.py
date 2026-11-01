from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
from sync_batch_score import score_times


class ScoreOpening(unittest.TestCase):
    def test_repeated_melody_cannot_select_second_verse_as_the_opening(self):
        source = {'verses': [[{'region': f'v{i}'}] for i in range(3)]}
        calls = []
        def repeated(audio, count, cache):
            return {'period': 10, 'initialOnsetEstimate': 2, 'similarity': .95,
                    'shifts': [i * 10 for i in range(count)]}
        def match(rows, chroma, times, period, onset, weight, onset_bounds=None):
            calls.append(onset_bounds)
            # Without an opening constraint the later, identical verse wins.
            first = 1 if onset_bounds is not None and onset_bounds[1] < 11 else 11
            return {'best': {'cost': .2, 'key': 0, 'starts': [first, first + 3, first + 6]},
                    'runnerUp': {'cost': .4}}
        with patch('sync_batch_score.image_source', return_value=source), \
                patch('sync_batch_score.note_rows', return_value=[{}] * 3), \
                patch('sync_batch_score.features', return_value=(np.arange(32), np.zeros((12, 32)))), \
                patch('sync_batch_score.repeated_verses', side_effect=repeated), \
                patch('sync_batch_score.match_template', side_effect=match):
            definition, times, evidence = score_times(Path('score.jpg'), Path('audio.mp3'), {}, Path('.'))
        self.assertEqual(len(definition['verses']), 3)
        self.assertEqual(times, [1, 4, 7, 11, 14, 17, 21, 24, 27])
        self.assertEqual(evidence['music']['shifts'], [0, 10, 20])
        self.assertTrue(all(bounds is not None and bounds[0] == 0 for bounds in calls))


if __name__ == '__main__':
    unittest.main()
