import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_holy_vector import rhythm_rows


class HolyVectorScore(unittest.TestCase):
    def test_printed_ties_keep_their_noteheads_and_rests_keep_their_place(self):
        rows = rhythm_rows(
            [2, 6, 6, 9],
            [
                {'weights': [0.5, 0.25, 0.5], 'rests': [{'after': 2, 'beats': 1}]},
                {'weights': [1.5], 'rests': [{'after': 1, 'beats': 0.5}]},
            ],
        )
        self.assertEqual(rows[0]['pitchOffsets'], [2, 6, 0, 6])
        self.assertEqual(rows[0]['notes'], [1, 1, 0, 1])
        self.assertEqual(rows[0]['opticalWeights'], [0.5, 0.25, 1, 0.5])
        self.assertEqual(rows[1]['notes'], [1, 0])

    def test_rhythm_cannot_drop_notes_or_add_notes(self):
        for pitches, weights in [([2, 6], [0.5]), ([2], [0.5, 0.5])]:
            with self.assertRaises(ValueError):
                rhythm_rows(pitches, [{'weights': weights, 'rests': []}])
        for bad in [0, -1, float('nan')]:
            with self.assertRaises(ValueError):
                rhythm_rows([2], [{'weights': [bad], 'rests': []}])
        with self.assertRaises(ValueError):
            rhythm_rows([2], [{'weights': [0.5], 'rests': [{'after': 2, 'beats': 0.5}]}])

    def test_failed_recheck_removes_stale_candidate(self):
        spec = importlib.util.spec_from_file_location('holy_vector_test', ROOT / 'tools/holy-vector-sync.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        song = {'songId': '02-026', 'source': 'sample.txt', 'sha256': 'a' * 64, 'tracks': []}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            ready = cache / 'ready/sample.txt.json'
            ready.parent.mkdir()
            ready.write_text('{}')
            self.assertIn('needs-calibration', module.generate(song, {}, cache)[1])
            self.assertFalse(ready.exists())
            evidence = json.loads((cache / 'score-alignment/sample.txt.json').read_text())
            self.assertEqual(evidence['status'], 'needs-calibration')


if __name__ == '__main__':
    unittest.main()
