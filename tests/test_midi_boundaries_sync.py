import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
import mido

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from sync_batch_midi_boundaries import check_match, quarter_second, region_groups, validate_degenerate_windows, verse_reference
from sync_batch_repeat_homophones import validate_review, verify


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


midi = load('test_boundary_midi', TOOLS / 'midi-sync.py')
image = load('test_boundary_image', TOOLS / 'align-image-transcript-sync.py')


class MidiBoundariesTests(unittest.TestCase):
    def test_midi_cannot_replace_a_real_or_conflicting_first_word_or_unverified_windows(self):
        rows = [{'window': window, 'at': 8, 'firstWordStart': 8, 'firstWordEnd': 8,
                 'firstWordProbability': .001, 'positiveDurationFraction': .9, 'meanProbability': .5, 'valid': True}
                for window in (0, 1)]
        validate_degenerate_windows(rows, 10)
        for change in ['missing', 'duplicate', 'real', 'weak', 'conflict']:
            modified = copy.deepcopy(rows)
            if change == 'missing':
                modified.pop()
            elif change == 'duplicate':
                modified[1]['window'] = 0
            elif change == 'real':
                modified[0].update(firstWordEnd=8.8, firstWordProbability=.5)
            elif change == 'weak':
                modified[0]['meanProbability'] = .1
            else:
                modified.append({**modified[0], 'window': 2, 'firstWordEnd': 8.8, 'firstWordProbability': .5})
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_degenerate_windows(modified, 10)

    def make_midi(self, path, crossing=False):
        score = mido.MidiFile(type=1, ticks_per_beat=100)
        score.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000),
                                          mido.MetaMessage('set_tempo', tempo=600000, time=800)]))
        for index, name in enumerate(['Soprano', 'Alto', 'Tenor', 'Bass']):
            events = [(0, mido.MetaMessage('track_name', name=name))]
            for step in range(32):
                at, pitch = step * 200, 72 - index * 8 + step % 7
                events += [(at, mido.Message('note_on', channel=index, note=pitch, velocity=90)),
                           (at + 200, mido.Message('note_off', channel=index, note=pitch, velocity=0))]
            if index == 0:
                events += [(0, mido.MetaMessage('lyrics', text='1. Je')),
                           (1600, mido.MetaMessage('lyrics', text='No ')),
                           (3200, mido.MetaMessage('lyrics', text='2. Je'))]
            if index == (0 if crossing else 2):
                events += [(1400, mido.Message('note_on', channel=index, note=40, velocity=70)),
                           (2000, mido.Message('note_off', channel=index, note=40))]
            track, previous = mido.MidiTrack(), 0
            for at, event in sorted(events, key=lambda item: (item[0], item[1].type == 'note_on')):
                track.append(event.copy(time=at - previous))
                previous = at
            score.tracks.append(track)
        score.save(path)
        return score

    def test_numbered_verse_retains_original_tempo_and_lower_voice_sustains_without_file_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'original.mid'
            score = self.make_midi(path)
            printed = root / 'preview.pdf'
            printed.write_bytes(b'%PDF-test')
            review = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'scoreFile': printed.name, 'scoreSha256': hashlib.sha256(printed.read_bytes()).hexdigest(),
                      'melodyTrack': 1, 'melodyTrackName': 'Soprano', 'openingLyric': 'No '}
            before = path.read_bytes()
            templates, proof = verse_reference(review, root, midi.midi_features)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(proof['verseQuarterLength'], 32)
            self.assertEqual(proof['openingQuarterLength'], 16)
            self.assertEqual(proof['openingAccompanimentSustains'][0]['track'], 3)
            self.assertGreaterEqual(proof['maximumDistinctSoundingPitches'], 4)
            self.assertAlmostEqual(quarter_second(score, 16), 8.8)
            self.assertAlmostEqual(templates[0][1], 18.4)
            for key, value in [('sha256', 'stale'), ('scoreSha256', 'stale'),
                               ('melodyTrackName', 'wrong'), ('melodyTrack', 2), ('openingLyric', 'wrong')]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    verse_reference({**review, key: value}, root, midi.midi_features)
            self.make_midi(path, crossing=True)
            review['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, '旋律'):
                verse_reference(review, root, midi.midi_features)

    def test_asr_onset_bounds_exclude_earlier_repeated_harmony_and_invalid_ranges(self):
        times = np.arange(40, dtype=np.float32) / 10
        chroma = np.random.default_rng(21).normal(size=(12, 40)).astype(np.float32)
        chroma /= np.linalg.norm(chroma, axis=0)
        target_times = np.arange(80, dtype=np.float32) / 10
        target = (target_times, np.tile(chroma, 2))
        default = midi.same_tune.match_cycle((times, chroma), target, 0, 4, 4, 3, cue_offsets=[0])
        bounded = midi.same_tune.match_cycle((times, chroma), target, 0, 4, 4, 3,
                                           cue_offsets=[0], onset_bounds=[4, 4.5])
        self.assertEqual(default['best']['starts'], [0])
        self.assertEqual(bounded['best']['starts'], [4])
        self.assertAlmostEqual(default['best']['cost'], bounded['best']['cost'])
        for bounds in [[3, 2], [True, 4], [100, 101], [float('nan'), 4]]:
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                midi.same_tune.match_cycle((times, chroma), target, 0, 4, 4, 3, onset_bounds=bounds)

    def test_midi_cost_key_margin_span_and_source_coverage_gates_are_strict(self):
        valid = {'best': {'cost': .2, 'span': 20, 'key': 3, 'starts': [12]}, 'runnerUp': {'cost': .4}}
        self.assertEqual(check_match(valid, 20, .08)['starts'], [12])
        for field, value in [('cost', .351), ('span', 14.9), ('key', float('nan')), ('starts', [12, 13])]:
            changed = copy.deepcopy(valid)
            changed['best'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_match(changed, 20, .08)
        with self.assertRaises(ValueError):
            check_match({**valid, 'runnerUp': {'cost': .27}}, 20, .08)
        definitions = [{'region': region, 'text': text} for region, text in [
            ('a', '耶稣说当我口渴'), ('b', '耶稣说当我软弱'), ('c', '耶稣说当我惧怕'), ('d', '下句')]]
        source = {'regions': {item['region']: {} for item in definitions},
                  'blocks': {item['region']: item for item in definitions}}
        self.assertEqual(region_groups({'regions': ['a', 'b', 'c']}, source, definitions), [[0], [1], [2]])
        for regions in [['a', 'b'], ['a', 'a', 'c'], ['a', 'c', 'b'], ['a', 'b', 'd']]:
            with self.subTest(regions=regions), self.assertRaises(ValueError):
                region_groups({'regions': regions}, source, definitions)
        partial = copy.deepcopy(definitions)
        partial[1]['text'] = '耶稣说当我'
        with self.assertRaises(ValueError):
            region_groups({'regions': ['a', 'b', 'c']}, source, partial)


class RepeatedHomophoneTests(unittest.TestCase):
    def setUp(self):
        self.text = '因我主良善又信实保守我黑夜白日就让我们奔向耶稣他坚强仁慈'
        heard = '分我主两相有心事保守我黑夜白日就让我们奔向夜宿踏见小人子'
        self.words = [{'word': char, 'start': i, 'end': i + .8}
                      for i, char in enumerate(self.text + self.text + heard)]
        n = len(self.text)
        self.profile = {'language': 'zh', 'blocks': [
            {'region': region, 'wordRange': [i * n, (i + 1) * n]} for i, region in enumerate(['a', 'b', 'c'])],
            'repeatHomophones': {'version': 1, 'blocks': [2], 'pinyinVersion': '0.55.0', 'reason': 'repeated refrain'}}
        self.profile['blocks'][-1]['wordRange'][1] = len(self.words)
        self.source = {'layout': 'lyrics-only-text', 'blocks': {region: {'text': self.text} for region in ['a', 'b', 'c']}}

    def test_homophones_need_two_other_literal_confirmed_complete_source_repeats(self):
        validate_review(self.profile, 'zh', self.source)
        proof = verify(2, self.profile, self.source, self.words, image.checks.match_order, image.checks.lexical_positions)
        self.assertEqual(proof['corroboratingBlocks'], [0, 1])
        self.assertGreaterEqual(proof['literalMatchedFraction'], .5)
        self.assertLess(proof['literalMatchedFraction'], .6)
        self.assertGreaterEqual(proof['syllableMatchedFraction'], .8)
        overlap = copy.deepcopy(self.profile)
        overlap['blocks'][1]['wordRange'] = overlap['blocks'][0]['wordRange']
        with self.assertRaises(ValueError):
            verify(2, overlap, self.source, self.words, image.checks.match_order, image.checks.lexical_positions)
        changed = copy.deepcopy(self.words)
        for word in changed[:len(self.text)]:
            word['word'] = '甲'
        with self.assertRaises(ValueError):
            verify(2, self.profile, self.source, changed, image.checks.match_order, image.checks.lexical_positions)
        with self.assertRaises(ValueError):
            verify(1, self.profile, self.source, self.words, image.checks.match_order, image.checks.lexical_positions)
        for word in self.words[2 * len(self.text):]:
            word['word'] = '甲'
        with self.assertRaises(ValueError):
            verify(2, self.profile, self.source, self.words, image.checks.match_order, image.checks.lexical_positions)

    def test_complete_reviewed_blocks_keep_all_original_words_and_still_fail_without_opt_in(self):
        source = copy.deepcopy(self.source)
        source['regions'] = {region: {'kind': 'text', 'startLine': index * 2, 'endLine': index * 2 + 1}
                             for index, region in enumerate(['a', 'b', 'c'])}
        for region, item in source['blocks'].items():
            item['label'] = region
        original = copy.deepcopy(self.words)
        proofs = []
        diagnostics = []
        definitions, estimates, ranges, words = image.transcript_blocks(self.profile, {'segments': [{'words': self.words}]}, source, proofs, diagnostics)
        self.assertEqual(len(definitions), 3)
        self.assertEqual([item['text'] for item in definitions], [self.text] * 3)
        self.assertEqual(proofs[0]['corroboratingBlocks'], [0, 1])
        self.assertEqual(self.words, original)
        self.assertEqual(len(diagnostics), 3)
        self.assertEqual(diagnostics[-1]['end'], self.words[-1]['end'])
        self.assertEqual(diagnostics[0]['at'], estimates[0])
        strict = {key: value for key, value in self.profile.items() if key != 'repeatHomophones'}
        with self.assertRaises(ValueError):
            image.transcript_blocks(strict, {'segments': [{'words': self.words}]}, source)

    def test_homophone_review_and_initial_position_reject_stale_or_partial_source_bindings(self):
        for field, value in [('version', True), ('blocks', [2, 2]), ('blocks', [10]), ('pinyinVersion', 'old')]:
            profile = copy.deepcopy(self.profile)
            profile['repeatHomophones'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_review(profile, 'zh', self.source)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'asr.json'
            path.write_text('{}')
            song = {'type': 'text', 'sha256': 'lyrics', 'tracks': [{'file': 'a.mp3', 'sha256': 'audio'}], 'reference': 'a.mp3'}
            definitions = [{'region': region, 'text': self.text} for region in ['a', 'b', 'c']]
            source = {**self.source, 'regions': {region: {} for region in ['a', 'b', 'c']}}
            view = {'version': 1, 'sourceHash': 'lyrics', 'tracks': song['tracks'], 'audioFile': song['reference'],
                    'transcriptHash': image.batch.checksum(path), 'regions': ['a', 'b', 'c']}
            profile = {**self.profile, 'initialReadingView': view}
            self.assertFalse(image.reviewed_initial_view(song, profile, path, source, definitions)['lyricOnsetMeasured'])
            repeated = definitions + [copy.deepcopy(definitions[1])]
            repeated_profile = copy.deepcopy(profile)
            repeated_profile['initialReadingView']['regions'].append('b')
            self.assertFalse(image.reviewed_initial_view(song, repeated_profile, path, source, repeated)['lyricOnsetMeasured'])
            repeated[-1]['text'] = self.text[:8]
            with self.assertRaises(ValueError):
                image.reviewed_initial_view(song, repeated_profile, path, source, repeated)
            partial = copy.deepcopy(definitions)
            partial[1]['text'] = self.text[:8]
            with self.assertRaises(ValueError):
                image.reviewed_initial_view(song, profile, path, source, partial)
            profile['initialReadingView']['sourceHash'] = 'stale'
            with self.assertRaises(ValueError):
                image.reviewed_initial_view(song, profile, path, source, definitions)


if __name__ == '__main__':
    unittest.main()
