import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_wait_score import agreement, source_phrase


def word(text, start, end, probability=0.9):
    return {'word': text, 'start': start, 'end': end, 'probability': probability}


class WaitScore(unittest.TestCase):
    def test_literal_fragment_is_bound_to_original_words_and_valid_audio(self):
        definition = {'text': '你若究察 我的罪孽，我怎能在你前站立'}
        words = [
            word('我', 10, 10.4),
            word('怎', 10.4, 10.8),
            word('能', 10.8, 11.2),
            word('在', 11.2, 11.6),
            word('你', 11.6, 12),
        ]
        result = source_phrase(definition, '我怎能在你', [{'words': words}], [9, 13])
        self.assertEqual((result['start'], result['end']), (10, 12))
        with self.assertRaises(ValueError):
            source_phrase(definition, '不存在', [{'words': words}], [9, 13])
        words[1]['probability'] = 0.01
        with self.assertRaises(ValueError):
            source_phrase(definition, '我怎能在你', [{'words': words}], [9, 13])

    def test_independent_audio_paths_must_agree(self):
        self.assertEqual(agreement([10, 10.5]), 10)
        with self.assertRaises(ValueError):
            agreement([10, 10.51])
        with self.assertRaises(ValueError):
            agreement([10, float('nan')])

    def test_failed_recheck_removes_previous_candidate(self):
        spec = importlib.util.spec_from_file_location('wait_generator', ROOT / 'tools/wait-score-sync.py')
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        song = {'songId': '02-007', 'source': 'sample.txt', 'sha256': 'a' * 64, 'tracks': []}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            candidate = cache / 'ready/sample.txt.json'
            candidate.parent.mkdir()
            candidate.write_text('{}')
            self.assertIn('needs-calibration', generator.generate(song, {}, cache, cache)[1])
            self.assertFalse(candidate.exists())
            evidence = json.loads((cache / 'transcript-alignment/sample.txt.json').read_text())
            self.assertEqual(evidence['status'], 'needs-calibration')


if __name__ == '__main__':
    unittest.main()
