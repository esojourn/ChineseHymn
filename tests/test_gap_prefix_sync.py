"""Guard free decoding against false word starts, edits, and unsupported gaps."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sync_batch_gap_prefix import gap_edges, prefix_candidate, reviewed_prefix, recover

spec = importlib.util.spec_from_file_location('gap_transcript', Path(__file__).resolve().parents[1] / 'tools/align-transcript-sync.py')
transcript = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transcript)


class GapPrefixTests(unittest.TestCase):
    def setUp(self):
        self.definition = {'text': 'You prepare a table before me in the presence of my enemies'}
        self.review = {'inflections': [{'source': 'prepare', 'decoded': 'prepared'}]}
        self.anchor = {'at': 40, 'firstWordEnd': 41, 'firstWord': 'you'}
        self.decoded = {'bounds': [34, 56], 'segments': [{'words': [
            {'word': word, 'start': 40 + index, 'end': 41 + index, 'probability': .9}
            for index, word in enumerate('You prepared a table before me in the presence of'.split())]}]}

    def test_complete_ten_words_allow_only_one_explicit_same_stem_inflection(self):
        candidate = prefix_candidate(self.decoded, self.definition, self.review, self.anchor)
        self.assertEqual(candidate['at'], 40)
        self.assertEqual(candidate['wordEnd'], 41)
        self.assertEqual(candidate['sourcePrefix'][1], 'prepare')
        self.assertEqual(candidate['prefix'][1], 'prepared')
        with self.assertRaises(ValueError):
            prefix_candidate(self.decoded, self.definition, {'inflections': []}, self.anchor)
        for variant in [{'source': 'prepare', 'decoded': 'set'}, {'source': 'you', 'decoded': 'youed'},
                        {'source': 'missing', 'decoded': 'missinged'}]:
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                reviewed_prefix(self.definition['text'], {'inflections': [variant]})
        with self.assertRaises(ValueError):
            reviewed_prefix('You prepare a table', self.review)

    def test_wrong_missing_repeated_and_low_quality_openings_are_rejected(self):
        for change in ['wrong', 'missing', 'repeat', 'zero', 'low', 'late', 'end', 'outside']:
            decoded = copy.deepcopy(self.decoded)
            rows = decoded['segments'][0]['words']
            if change == 'wrong':
                rows[6]['word'] = 'at'
            elif change == 'missing':
                rows.pop()
            elif change == 'repeat':
                rows.extend(copy.deepcopy(rows))
            elif change == 'zero':
                rows[0]['end'] = rows[0]['start']
            elif change == 'low':
                rows[0]['probability'] = .01
            elif change == 'late':
                rows[0].update(start=42, end=43)
            elif change == 'end':
                rows[0]['end'] = 42
            elif change == 'outside':
                rows[-1]['end'] = 60
            with self.subTest(change=change), self.assertRaises(ValueError):
                prefix_candidate(decoded, self.definition, self.review, self.anchor)

    def test_whole_transcript_must_prove_prior_lyrics_outside_both_original_windows(self):
        definitions = [{'text': 'previous words'}, self.definition]
        diagnostics = [{'end': 20}, self.anchor]
        observations = [{'index': 1, 'window': window, 'bounds': bounds, 'firstWord': 'you',
                         'firstWordStart': 41, 'firstWordEnd': 41, 'positiveDurationFraction': .8,
                         'meanProbability': .5, 'valid': True}
                        for window, bounds in enumerate([[34, 56], [36, 54]])]
        segments = [{'words': [{'word': 'previous', 'start': 18, 'end': 20},
                               {'word': 'you', 'start': 40, 'end': 41}]}]
        edges, previous = gap_edges(1, definitions, diagnostics, observations, segments)
        self.assertEqual(len(edges), 2)
        self.assertEqual(previous, 20)
        with self.assertRaises(ValueError):
            gap_edges(1, definitions, [{'end': 35}, self.anchor], observations, segments)
        with self.assertRaises(ValueError):
            gap_edges(1, definitions, diagnostics, observations,
                      segments + [{'words': [{'word': 'extra', 'start': 25, 'end': 27}]}])
        for change in ['missing', 'same', 'span', 'quality']:
            rows = copy.deepcopy(observations)
            if change == 'missing':
                rows.pop()
            elif change == 'same':
                rows[1]['bounds'] = rows[0]['bounds']
            elif change == 'span':
                rows[1]['firstWordEnd'] = 42
            elif change == 'quality':
                rows[1]['positiveDurationFraction'] = .5
            with self.subTest(change=change), self.assertRaises(ValueError):
                gap_edges(1, definitions, diagnostics, rows, segments)

    def test_recovery_preserves_other_verified_onsets_rejects_stale_model_and_caches_only_prompt_free_audio(self):
        definitions = [{'text': 'first reading'}, self.definition, {'text': 'last reading'}]
        diagnostics = [{'at': 5, 'end': 20}, {**self.anchor, 'end': 50},
                       {'at': 70, 'firstWordEnd': 71, 'firstWord': 'last', 'end': 80}]
        observations = [{'index': 1, 'window': window, 'bounds': bounds, 'firstWord': 'you', 'at': 41,
                         'firstWordStart': 41, 'firstWordEnd': 41, 'positiveDurationFraction': .8,
                         'meanProbability': .5, 'valid': True}
                        for window, bounds in enumerate([[34, 56], [36, 54]])]
        observations += [{'index': 2, 'window': window, 'at': 70, 'firstWordStart': 70,
                          'firstWordEnd': 71, 'valid': True} for window in (0, 1)]
        asr = {'language': 'en', 'sha256': 'audio', 'segments': [{'words': [
            {'word': 'previous', 'start': 18, 'end': 20}, {'word': 'you', 'start': 40, 'end': 41}]}]}
        song = {'type': 'text', 'reference': 'song.mp3', 'tracks': [{'file': 'song.mp3', 'sha256': 'audio'}]}
        calls = []
        def transcribe(samples, **kwargs):
            self.assertEqual(kwargs, {'language': 'en', 'beam_size': 5, 'temperature': 0,
                                      'word_timestamps': True, 'condition_on_previous_text': False, 'vad_filter': False})
            bound = [34, 36][len(calls)]
            calls.append(len(samples))
            decoded = [SimpleNamespace(word=word['word'], start=word['start'] - bound,
                                       end=word['end'] - bound, probability=word['probability'])
                       for word in self.decoded['segments'][0]['words']]
            return iter([SimpleNamespace(start=40 - bound, end=50 - bound, text='free decoding', words=decoded)]), None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'medium').mkdir()
            for name in ['model.bin', 'tokenizer.json']:
                (root / 'medium' / name).write_bytes(b'pinned')
            digest = hashlib.sha256(b'pinned').hexdigest()
            review = {**self.review, 'version': 1, 'reason': 'long gap', 'model': {
                'repository': 'test', 'revision': 'test', 'sha256': digest, 'tokenizerSha256': digest}}
            def save(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
            args = (SimpleNamespace(transcribe=transcribe), np.zeros(90 * 16000), song, definitions, diagnostics,
                    observations, asr, review, root, root / 'cache', transcript.confirm_transcript_windows, save)
            times, decisions = recover(*args)
            self.assertEqual(times, [0, 40, 70])
            self.assertEqual(len(calls), 2)
            self.assertEqual(decisions[-1]['index'], 1)
            self.assertEqual(decisions[-1]['previousLyricEnd'], 20)
            self.assertEqual(recover(*args), (times, decisions))
            self.assertEqual(len(calls), 2)
            # A different, non-gap ambiguous later cue cannot inherit this recovery.
            bad_rows = copy.deepcopy(observations)
            bad_rows[-1]['valid'] = False
            bad_args = (*args[:5], bad_rows, *args[6:])
            with self.assertRaises(ValueError):
                recover(*bad_args)
            (root / 'medium/model.bin').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                recover(*args)


if __name__ == '__main__':
    unittest.main()
