import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import sync_batch_cyclic_onset as cyclic
from sync_batch_align import confirm_boundaries, context_cluster_time


class CyclicOnsetTests(unittest.TestCase):
    def setUp(self):
        self.source = {'language': 'zh', 'verses': [
            [{'region': 'r1', 'text': '在十架下站甲'}, {'region': 'r2', 'text': '第一节副歌'}],
            [{'region': 'r3', 'text': '在十架下站乙'}, {'region': 'r4', 'text': '第二节副歌'}]]}
        self.estimates = [10., 50., 110., 150.]
        self.music = {'similarity': .95, 'period': 100., 'shifts': [0., 100.]}
        self.words = [self.word(c, t, t + .4) for c, t in zip('在世家下占', [10., 11.4, 11.8, 12.2, 12.6])]
        self.words[0]['end'] = 11.4
        self.words.append(self.word('在', 110.2, 111.6))
        self.transcript = {'duration': 180., 'segments': [{'words': self.words}]}
        self.observations = []
        for index, at in [(1, 50.), (3, 150.)]:
            for window in [0, 1]:
                self.observations.append({'index': index, 'window': window, 'at': at, 'valid': True,
                                          'firstWordStart': at, 'firstWordEnd': at + 1})
        self.decoded = []
        for i, (before, after) in enumerate([(6, 16), (4, 14), (8, 18), (10, 16)]):
            bounds = [110. - before, 110. + after]
            self.observations.append({'index': 2, 'window': i, 'at': 111.6, 'valid': True,
                'firstWord': '在', 'firstWordStart': 111.6, 'firstWordEnd': 111.6,
                'positiveDurationFraction': .8, 'meanProbability': .4, 'bounds': bounds})
            if i < 2:
                words = [self.word(c, t, t + .4) for c, t in zip('在世家下占', [110.2, 111.6, 112., 112.4, 112.8])]
                words[0]['end'] = 111.6
                self.decoded.append({'bounds': bounds, 'segments': [{'words': words}]})
        self.review = {'version': 1, 'indices': [2], 'transcriptHash': 'a' * 64, 'pinyinVersion': '0.55.0',
                       'reason': 'Source-free first-word recovery with independent word edges and music.',
                       'model': {'repository': 'Systran/faster-whisper-medium', 'revision': 'b' * 40,
                                 'sha256': 'c' * 64, 'tokenizerSha256': 'd' * 64}}

    @staticmethod
    def word(text, start, end):
        return {'word': text, 'start': start, 'end': end, 'probability': .7}

    def verify(self):
        return cyclic.verify(2, self.source, self.estimates, self.music, self.observations,
                             self.transcript, self.decoded, context_cluster_time)

    def test_zero_duration_recovery_requires_all_independent_witnesses(self):
        original = copy.deepcopy((self.source, self.observations, self.transcript, self.decoded))
        proof = self.verify()
        self.assertEqual(proof['at'], 110.2)
        self.assertEqual(proof['sourcePrefix'], '在十架下站')
        self.assertEqual(proof['syllables'], ['zai', 'shi', 'jia', 'xia', 'zhan'])
        self.assertEqual(proof['peers'][0]['otherReadingAnchors'], [{'slot': 1, 'peer': 50., 'current': 150.}])
        self.assertEqual((self.source, self.observations, self.transcript, self.decoded), original)
        with self.assertRaises(ValueError):
            confirm_boundaries(self.source['verses'], self.music, self.observations, True)
        times, starts, decisions = confirm_boundaries(self.source['verses'], self.music, self.observations, True, [proof])
        self.assertEqual(times, [10.2, 50., 110.2, 150.])
        self.assertEqual(starts, [0, 2])
        self.assertIn(proof, decisions)

    def test_full_prefix_and_literal_first_word_are_required_in_both_windows(self):
        changes = [('wrong syllable', lambda w: w[2].update(word='树')),
                   ('nonliteral first', lambda w: w[0].update(word='再')),
                   ('short prefix', lambda w: w.pop()),
                   ('zero first span', lambda w: w[0].update(end=w[0]['start'])),
                   ('weak first', lambda w: w[0].update(probability=.11)),
                   ('nonfinite', lambda w: w[0].update(start=float('nan')))]
        for name, change in changes:
            with self.subTest(name=name):
                saved = copy.deepcopy(self.decoded)
                change(self.decoded[1]['segments'][0]['words'])
                with self.assertRaises(ValueError):
                    self.verify()
                self.decoded = saved

    def test_wrong_context_word_end_or_a_nonzero_context_word_cannot_be_replaced(self):
        for update in [{'firstWordStart': 110.2}, {'firstWordEnd': 112.2, 'firstWordStart': 112.2},
                       {'meanProbability': .11}, {'positiveDurationFraction': .6}, {'bounds': [0, 180]}]:
            with self.subTest(update=update):
                saved = copy.deepcopy(self.observations)
                self.observations[-1].update(update)
                with self.assertRaises(ValueError):
                    self.verify()
                self.observations = saved

    def test_music_peer_prefix_and_normal_other_reading_are_all_required(self):
        self.music['similarity'] = .89
        with self.assertRaises(ValueError):
            self.verify()
        self.music['similarity'] = .95
        self.music['similarity'] = float('nan')
        with self.assertRaises(ValueError):
            self.verify()
        self.music['similarity'] = .95
        self.source['verses'][0][0]['text'] = '另一个不同开头'
        with self.assertRaises(ValueError):
            self.verify()
        self.source['verses'][0][0]['text'] = '在十架下站甲'
        self.words[1]['word'] = '树'
        with self.assertRaises(ValueError):
            self.verify()
        self.words[1]['word'] = '世'
        for w in self.observations:
            if w['index'] == 3:
                w['at'] += .6
        with self.assertRaises(ValueError):
            self.verify()

    def test_held_word_in_silence_cannot_match_just_by_word_end(self):
        for w in self.decoded[1]['segments'][0]['words'][:1]:
            w['start'] -= 2
        with self.assertRaises(ValueError):
            self.verify()

    def test_duplicate_prefix_or_wrong_audio_window_is_rejected(self):
        self.decoded[0]['bounds'][0] -= 1
        with self.assertRaises(ValueError):
            self.verify()
        self.decoded[0]['bounds'][0] += 1
        duplicate = [self.word(c, t, t + .4) for c, t in zip('在世家下占', [116., 116.4, 116.8, 117.2, 117.6])]
        self.decoded[0]['segments'][0]['words'].extend(duplicate)
        with self.assertRaises(ValueError):
            self.verify()

    def test_opt_in_profile_cannot_supply_seconds_or_override_other_structure(self):
        self.assertIsNone(cyclic.validate_review({}, self.source))
        profile = {'extendedContext': {'version': 1}, 'cyclicFreeOnsets': self.review}
        self.assertEqual(cyclic.validate_review(profile, self.source), self.review)
        for update in [{'at': 110.2}, {'indices': [1, 9]}, {'version': True}, {'pinyinVersion': '0.0'},
                       {'transcriptHash': None}, {'model': None}]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                cyclic.validate_review({**profile, 'cyclicFreeOnsets': {**self.review, **update}}, self.source)
        with self.assertRaises(ValueError):
            cyclic.validate_review({**profile, 'sourceCycle': {}}, self.source)
        with self.assertRaises(ValueError):
            cyclic.validate_review({**profile, 'extendedContext': {'version': True}}, self.source)

    def test_model_files_are_pinned(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'medium'
            model.mkdir()
            for name in ['model.bin', 'tokenizer.json']:
                (model / name).write_bytes(b'wrong-model')
            with self.assertRaises(ValueError):
                cyclic.verify_model(folder, self.review)


if __name__ == '__main__':
    unittest.main()
