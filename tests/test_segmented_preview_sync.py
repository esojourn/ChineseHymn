import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import numpy as np
from sync_batch_segmented_preview import common_gap_plan, match_score, override_context_witness, prefix_candidate, preview_decision

spec = importlib.util.spec_from_file_location('segmented_preview_cli', Path(__file__).resolve().parents[1] / 'tools/segmented-score-preview-sync.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class SegmentedPreviewTests(unittest.TestCase):
    def setUp(self):
        self.definition = {'region': 'r2', 'text': 'When thy voice first came to my ear'}
        self.window = {'bounds': [4., 34.], 'segments': [{'words': [
            {'word': 'When', 'start': 19.4, 'end': 20., 'probability': .8},
            {'word': 'thy', 'start': 20., 'end': 20.4, 'probability': .9},
            {'word': 'voice', 'start': 20.4, 'end': 21., 'probability': .9}]}]}

    def candidate(self):
        return prefix_candidate(1, self.definition, 20., self.window, 3)

    def test_two_audio_windows_produce_an_early_reading_position(self):
        first = self.candidate()
        second = copy.deepcopy(first)
        second['words'][0]['start'] += .2
        result = preview_decision(1, [first, second], 18.)
        self.assertEqual(result['at'], 19.4)
        self.assertIn('sung onset not claimed', result['method'])

    def test_short_prefix_must_be_unique_near_the_score(self):
        self.definition['text'] = 'No more I who live'
        first = self.window['segments'][0]['words']
        first[:] = [{'word': 'No', 'start': 19.4, 'end': 19.8, 'probability': .9},
                    {'word': 'more', 'start': 19.8, 'end': 20.2, 'probability': .9},
                    {'word': 'No', 'start': 20.4, 'end': 20.8, 'probability': .9},
                    {'word': 'more', 'start': 20.8, 'end': 21.2, 'probability': .9}]
        with self.assertRaises(ValueError):
            prefix_candidate(1, self.definition, 20., self.window, 2)

    def test_source_mismatch_and_invalid_word_rejected(self):
        for key, value in [('word', 'wrong'), ('probability', .01), ('end', 19.4)]:
            original = copy.deepcopy(self.window)
            self.window['segments'][0]['words'][0][key] = value
            with self.assertRaises(ValueError): self.candidate()
            self.window = original

    def test_only_the_reviewed_two_word_recognition_difference_is_allowed(self):
        definition = {'region': 'r10', 'text': 'To Thee Lord my all I outpour'}
        tokens = ['through', 'the', 'lord', 'my', 'all', 'i']
        self.window['segments'][0]['words'] = [{'word': t, 'start': 19 + i * .4,
            'end': 19.3 + i * .4, 'probability': .9} for i, t in enumerate(tokens)]
        self.assertEqual(prefix_candidate(9, definition, 20., self.window, 6, tokens)['decodedPrefix'], tokens)
        with self.assertRaises(ValueError):
            prefix_candidate(9, definition, 20., self.window, 6, ['wrong'] + tokens[1:])

    def test_disagreement_and_preview_before_previous_final_line_rejected(self):
        first = self.candidate()
        second = copy.deepcopy(first)
        second['words'][0]['start'] += .6
        with self.assertRaises(ValueError): preview_decision(1, [first, second], 18.)
        with self.assertRaises(ValueError): preview_decision(1, [first, first], 18.8)

    def test_spelling_review_requires_two_contextual_to_thee_witnesses(self):
        candidates = [{'words': [{'start': 19.2, 'end': 20.1}]}, {'words': [{'start': 19.3, 'end': 20.1}]}]
        rows = []
        for index, lo, hi in [(0, 14., 36.), (1, 16., 34.)]:
            rows.append({'index': 0, 'window': index, 'bounds': [lo, hi], 'valid': True, 'firstWord': 'to',
                         'firstWordStart': 19.4, 'firstWordEnd': 20., 'firstWordProbability': .7,
                         'meanProbability': .7, 'positiveDurationFraction': 1.,
                         'words': [{'word': 'To', 'start': 19.4-lo, 'end': 20.-lo, 'probability': .7},
                                   {'word': 'Thee', 'start': 20.-lo, 'end': 20.4-lo, 'probability': .8}]})
        self.assertEqual(len(override_context_witness(rows, 20., candidates)), 2)
        rows[1]['words'][1]['probability'] = .01
        with self.assertRaises(ValueError): override_context_witness(rows, 20., candidates)
        rows[1]['words'][1]['probability'] = .8
        candidates[0]['words'][0]['end'] = 19.3
        with self.assertRaises(ValueError): override_context_witness(rows, 20., candidates)

    def test_two_common_long_gaps_and_complete_coverage_are_required(self):
        medium = {'segments': [{'start': 1., 'end': 5.}, {'start': 13., 'end': 20.}, {'start': 28., 'end': 37.}]}
        turbo = {'segments': [{'start': 1.2, 'end': 5.2}, {'start': 13.1, 'end': 20.2}, {'start': 28.1, 'end': 36.}]}
        plans, gaps = common_gap_plan(medium, turbo, 40.)
        self.assertEqual(len(plans), 3)
        self.assertEqual(len(gaps), 2)
        turbo['segments'][1]['start'] = 8.
        with self.assertRaises(ValueError): common_gap_plan(medium, turbo, 40.)

    def test_pinned_input_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text(json.dumps({'value': 1}))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(cli.load_bound(path, digest), {'value': 1})
            path.write_text(json.dumps({'value': 2}))
            with self.assertRaises(ValueError): cli.load_bound(path, digest)

    def test_score_checks_include_the_repeated_final_phrase(self):
        practice = {'notes': [[0, 129, 60, i] for i in range(146)], 'chords': [[0, [4, 7], -1, 0, 129]]}
        plans = [{'index': i, 'span': 10., 'estimate': 1., 'onsetBounds': [0., 2.]} for i in range(3)]
        target = (np.arange(200, dtype=np.float32) / 10, np.zeros((12, 200), dtype=np.float32))
        def result(count, shifted=False):
            starts = [float(i) for i in range(count)]
            if shifted and count == 20: starts[17] += .6
            return {'best': {'cost': .2, 'key': 0, 'span': 10., 'starts': starts},
                    'runnerUp': {'cost': .4, 'key': 1}}
        def midi(shifted):
            return SimpleNamespace(match_template=lambda rows, *args, **kwargs: result(len(rows)),
                same_tune=SimpleNamespace(match_cycle=lambda *args, **kwargs: result(len(kwargs['cue_offsets']), shifted)))
        self.assertEqual(len(match_score(practice, plans, target, midi(False))), 3)
        with self.assertRaises(ValueError): match_score(practice, plans, target, midi(True))


if __name__ == '__main__':
    unittest.main()
