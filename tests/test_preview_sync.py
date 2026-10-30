import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sync_batch_preview import bilingual_lines, verify


class PreviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.previous = 'He must hold me fast'
        self.current = 'Those he saves are his delight'
        self.anchors = [{'previous': {'end': end, 'matchedFraction': .95},
                         'current': {'at': 20., 'firstWord': 'those', 'firstWordEnd': 21., 'matchedFraction': .95}}
                        for end in (15., 15.2)]
        self.decoded = []
        for before in (18, 20):
            entries = []
            for i, word in enumerate(self.previous.split()):
                entries.append({'word': word, 'start': 13 + i * .4, 'end': 13.3 + i * .4, 'probability': .9})
            for i, word in enumerate(self.current.split()):
                entries.append({'word': word, 'start': 20.5 + i * .4, 'end': 20.8 + i * .4, 'probability': .9})
            self.decoded.append({'bounds': [20. - before, 50. - before], 'segments': [{'words': entries}]})

    def check(self):
        return verify(1, self.previous, self.current, self.anchors, self.decoded)

    def test_conservative_preview_is_not_a_sung_onset(self):
        result = self.check()
        self.assertEqual(result['at'], 15.7)
        self.assertIn('not a measured', result['method'])

    def test_missing_or_added_word_rejected(self):
        for index in (0, 1):
            original = copy.deepcopy(self.decoded)
            self.decoded[index]['segments'][0]['words'][1]['word'] = 'other'
            with self.assertRaises(ValueError): self.check()
            self.decoded = original
        self.decoded[0]['segments'][0]['words'].insert(5, {'word': 'extra', 'start': 15.5, 'end': 15.7, 'probability': .9})
        with self.assertRaises(ValueError): self.check()

    def test_uncertain_or_zero_length_words_rejected(self):
        for key, value in [('probability', .01), ('end', 13.0)]:
            original = copy.deepcopy(self.decoded)
            self.decoded[0]['segments'][0]['words'][0][key] = value
            with self.assertRaises(ValueError): self.check()
            self.decoded = original

    def test_duplicate_matching_line_is_ambiguous(self):
        entries = self.decoded[0]['segments'][0]['words']
        entries[5:5] = copy.deepcopy(entries[:5])
        with self.assertRaises(ValueError): self.check()

    def test_window_and_full_transcript_conflicts_rejected(self):
        self.decoded[0]['bounds'][0] += .2
        with self.assertRaises(ValueError): self.check()
        self.decoded[0]['bounds'][0] -= .2
        self.anchors[1]['previous']['end'] = 17
        with self.assertRaises(ValueError): self.check()

    def test_requires_gap_after_the_previous_line(self):
        for anchor in self.anchors:
            anchor['current']['at'] = 15.8
        with self.assertRaises(ValueError): self.check()

    def test_original_transcript_first_word_and_coverage_required(self):
        self.anchors[0]['current']['firstWord'] = 'another'
        with self.assertRaises(ValueError): self.check()
        self.anchors[0]['current']['firstWord'] = 'those'
        self.anchors[1]['previous']['matchedFraction'] = .79
        with self.assertRaises(ValueError): self.check()
        self.anchors[1]['previous']['matchedFraction'] = float('nan')
        with self.assertRaises(ValueError): self.check()

    def test_bilingual_position_preserves_strict_version_one(self):
        self.anchors[1]['previous']['end'] = 16.1
        self.anchors[1]['current']['at'] = 16.2
        self.decoded[0]['bounds'] = [4., 34.]
        self.decoded[1]['bounds'] = [2., 32.]
        result = verify(1, self.previous, self.current, self.anchors, self.decoded, bilingual=True)
        self.assertEqual(result['at'], 16.6)
        self.assertIn('must already be visible', result['method'])
        with self.assertRaises(ValueError): self.check()

    def test_bilingual_source_requires_adjacent_unchanged_translation_lines(self):
        lines = ['He must hold me fast', '他必保守我', 'Those he saves are his delight', '他所救的都是喜乐']
        self.assertEqual(bilingual_lines(lines, {'startLine': 0, 'endLine': 1}, {'startLine': 2, 'endLine': 3}),
                         (lines[0], lines[2]))
        lines[1] = 'no translation'
        with self.assertRaises(ValueError):
            bilingual_lines(lines, {'startLine': 0, 'endLine': 1}, {'startLine': 2, 'endLine': 3})


if __name__ == '__main__':
    unittest.main()
