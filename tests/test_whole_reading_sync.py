import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import sync_batch_whole_reading as whole


class WholeReadingTests(unittest.TestCase):
    def setUp(self):
        self.definition = {'region': 'r5', 'text': 'Be thou my wisdom and thou my true word'}
        self.anchor = {'at': 20., 'firstWord': 'be', 'firstWordEnd': 21.4}
        self.observations = []
        for i, (before, after) in enumerate([(6, 16), (4, 14), (8, 18), (10, 16)]):
            at, end, probability = [(21.4, 21.4, .0002), (21.7, 21.7, .0002),
                                    (20.4, 21.8, .5), (18.2, 21.8, .5)][i]
            self.observations.append({'index': 4, 'window': i, 'valid': True, 'at': at,
                'firstWord': 'be', 'firstWordStart': at, 'firstWordEnd': end,
                'firstWordProbability': probability, 'meanProbability': .8,
                'positiveDurationFraction': .9, 'bounds': [20. - before, 20. + after]})
        self.decoded = []
        for start, bounds in [(20.1, [14., 36.]), (20.14, [16., 34.])]:
            words = []
            at = start
            for i, token in enumerate(self.definition['text'].split()):
                duration = 1.4 if i == 0 else .4
                words.append({'word': token, 'start': at, 'end': at + duration, 'probability': .8})
                at += duration
            self.decoded.append({'bounds': bounds, 'segments': [{'words': words}]})

    def verify(self):
        return whole.verify(4, self.definition, self.anchor, self.observations, self.decoded)

    def test_entire_reading_and_independent_context_word_confirm_the_lost_word(self):
        before = copy.deepcopy((self.definition, self.anchor, self.observations, self.decoded))
        proof = self.verify()
        self.assertEqual(proof['at'], 20.12)
        self.assertEqual(len(proof['windows'][0]['words']), 9)
        self.assertEqual(len(proof['contextWitnesses']), 1)
        self.assertEqual(proof['excludedHeldWordCandidates'][0]['window'], 3)
        self.assertEqual((self.definition, self.anchor, self.observations, self.decoded), before)

    def test_a_prefix_or_inflection_is_not_a_complete_source_reading(self):
        changes = [lambda w: w.pop(), lambda w: w[3].update(word='wise'),
                   lambda w: w[0].update(word='To'), lambda w: w.extend(copy.deepcopy(w))]
        for change in changes:
            saved = copy.deepcopy(self.decoded)
            with self.subTest(change=change):
                change(self.decoded[1]['segments'][0]['words'])
                with self.assertRaises(ValueError):
                    self.verify()
            self.decoded = saved

    def test_every_source_word_requires_its_own_span_and_likelihood(self):
        for index, fields in [(0, {'probability': .11}), (4, {'probability': .11}),
                              (0, {'end': 20.14}), (5, {'end': 21.}),
                              (2, {'start': float('nan')}), (1, {'probability': 1.1})]:
            saved = copy.deepcopy(self.decoded)
            with self.subTest(index=index, fields=fields):
                self.decoded[1]['segments'][0]['words'][index].update(fields)
                with self.assertRaises(ValueError):
                    self.verify()
            self.decoded = saved

    def test_only_two_canonical_zero_spans_at_the_original_word_end_are_recoverable(self):
        for fields in [{'firstWordStart': 20., 'at': 20.}, {'firstWordEnd': 22.},
                       {'meanProbability': .11}, {'positiveDurationFraction': .6},
                       {'firstWord': 'bethou'}, {'at': 21.2}, {'bounds': [0, 40]}]:
            saved = copy.deepcopy(self.observations)
            with self.subTest(fields=fields):
                self.observations[0].update(fields)
                with self.assertRaises(ValueError):
                    self.verify()
            self.observations = saved

    def test_an_in_range_context_conflict_cannot_use_the_held_word_exception(self):
        self.observations[3].update(at=19.2, firstWordStart=19.2)
        with self.assertRaises(ValueError):
            self.verify()

    def test_an_out_of_range_word_must_end_at_the_same_original_word_end(self):
        self.observations[3]['firstWordEnd'] = 23.
        with self.assertRaises(ValueError):
            self.verify()

    def test_the_original_medium_witness_is_mandatory(self):
        self.observations[2]['firstWordProbability'] = .11
        with self.assertRaises(ValueError):
            self.verify()

    def test_free_word_edges_must_agree_with_each_other_and_the_full_transcript(self):
        for offsets in [(-.4, .3), (0, .6)]:
            saved = copy.deepcopy(self.decoded)
            with self.subTest(offsets=offsets):
                for clip, offset in zip(self.decoded, offsets):
                    for w in clip['segments'][0]['words']:
                        w['start'] += offset
                        w['end'] += offset
                with self.assertRaises(ValueError):
                    self.verify()
            self.decoded = saved

    def test_model_and_tokenizer_hashes_are_checked_before_using_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review = {'version': 1, 'indices': [4], 'reason': 'Complete English reading.'}
            for key, folder, repo in [('model', 'large-v3-turbo', 'dropbox-dash/faster-whisper-large-v3-turbo'),
                                      ('contextModel', 'medium', 'Systran/faster-whisper-medium')]:
                (root / folder).mkdir()
                model = {'repository': repo, 'revision': 'a' * 40}
                for name, field in [('model.bin', 'sha256'), ('tokenizer.json', 'tokenizerSha256')]:
                    data = (folder + name).encode()
                    (root / folder / name).write_bytes(data)
                    model[field] = hashlib.sha256(data).hexdigest()
                review[key] = model
            profile = {'freeWholeReading': review}
            self.assertEqual(whole.validate_review(profile, 'en', root), review)
            (root / 'medium/tokenizer.json').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                whole.validate_review(profile, 'en', root)
        self.assertIsNone(whole.validate_review({}, 'en', '/not-needed'))
        for fields in [{'version': True}, {'indices': [0]}, {'indices': [4, 4]}, {'reason': ''}]:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    whole.validate_review({'freeWholeReading': {**review, **fields}}, 'en', '/not-needed')


if __name__ == '__main__':
    unittest.main()
