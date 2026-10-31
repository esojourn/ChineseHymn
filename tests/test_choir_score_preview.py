import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / 'tools'))
s = importlib.util.spec_from_file_location('p', R / 'tools/sync_batch_choir_prefix.py')
p = importlib.util.module_from_spec(s)
s.loader.exec_module(p)


def window(words, at):
    return {
        'bounds': [at - 3, at + 10],
        'segments': [
            {
                'words': [
                    {'word': x, 'start': at + i * 0.4, 'end': at + i * 0.4 + 0.3, 'probability': 0.8}
                    for i, x in enumerate(words)
                ]
            }
        ],
    }


class Preview(unittest.TestCase):

    def test_contractions_are_spoken_equivalents_and_source_is_unchanged(self):
        d = {'text': "For ev'ry sin on Him was laid"}
        w = window(['For', 'every', 'sin'], 100)
        c = p.candidate(1, d, 100, w, 3)
        self.assertEqual(c['words'][1]['word'], 'every')
        self.assertEqual(d['text'], "For ev'ry sin on Him was laid")
        with self.assertRaises(ValueError):
            p.candidate(1, d, 100, window(['For', 'other', 'sin'], 100), 3)

    def test_disagreement_is_a_bounded_preview_not_an_onset_claim(self):
        d = {'text': 'And as He stands in victory'}
        a = p.candidate(1, d, 100, window(['And', 'as', 'He'], 98), 3)
        b = p.candidate(1, d, 100, window(['And', 'as', 'He'], 100), 3)
        r = p.decision(1, [a, b], 95)
        self.assertEqual(r['at'], 98)
        self.assertIn('not claimed', r['method'])
        with self.assertRaises(ValueError):
            p.decision(1, [a, b], 97.5)
        with self.assertRaises(ValueError):
            p.decision(1, [a], 95)

    def test_unrecognized_word_remains_explicitly_unverified(self):
        definition = {'text': 'This gift of love and righteousness'}
        result = p.reviewed_gap(5, definition, 100, window(['This', 'year', 'of', 'love', 'and'], 100))
        self.assertEqual(result['unverifiedSourceWord'], 'gift')
        self.assertEqual(result['words'][1]['word'], 'year')
        with self.assertRaises(ValueError):
            p.reviewed_gap(
                5,
                {'text': 'This gift of peace and righteousness'},
                100,
                window(['This', 'year', 'of', 'love', 'and'], 100),
            )
        independent = window(['This', 'young', 'man'], 100.2)
        self.assertEqual(p.fresh_first_word(definition, 100, independent)['word'], 'This')
        with self.assertRaises(ValueError):
            p.fresh_first_word(definition, 100, window(['These', 'young', 'men'], 100))
        independent['segments'][0]['words'][0]['probability'] = .01
        with self.assertRaises(ValueError):
            p.fresh_first_word(definition, 100, independent)

    def test_clipped_first_word_requires_independent_word_end(self):
        definition = {'text': 'No guilt in life'}
        clipped = window(['No', 'guilt', 'in', 'life'], 100)
        clipped['bounds'][0] = 100
        first = p.reviewed_clipped(12, definition, 101, clipped, True)
        whole = p.reviewed_clipped(12, definition, 101, window(['No', 'guilt', 'in', 'life'], 100.1), False)
        result = p.clipped_decision(12, [first, whole], 98)
        self.assertEqual(result['at'], 100)
        self.assertIn('not claimed', result['method'])
        whole['words'][0]['end'] += 1
        with self.assertRaises(ValueError):
            p.clipped_decision(12, [first, whole], 98)

    def test_failed_recheck_removes_previous_ready_candidate(self):
        spec = importlib.util.spec_from_file_location('choir_generator', R / 'tools/choir-score-preview-sync.py')
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        song = {'songId': '06-010', 'source': 'sample.txt', 'sha256': 'a' * 64, 'tracks': []}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            candidate = cache / 'ready/sample.txt.json'
            candidate.parent.mkdir()
            candidate.write_text('{}')
            _, status = generator.generate(song, {}, cache, cache)
            self.assertTrue(status.startswith('needs-calibration'))
            self.assertFalse(candidate.exists())
            evidence = json.loads((cache / 'transcript-alignment/sample.txt.json').read_text())
            self.assertEqual(evidence['status'], 'needs-calibration')


if __name__ == '__main__':
    unittest.main()
