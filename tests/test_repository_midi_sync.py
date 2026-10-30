import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import mido

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
spec = importlib.util.spec_from_file_location('repository_sync', Path(__file__).parents[1] / 'tools/repository-midi-sync.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class RepositoryMidiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        data = self.root / 'data'
        data.mkdir()
        source = data / '03-999测试.txt'
        source.write_text('测试诗歌\n\n1.第一段完整原文歌词足够长并且保留每一个字供核对\n\n'
                          '2.第二段完整原文歌词也足够长并且仍然保留原始内容\n')
        self.source = native.text_source(source)
        self.definitions = [d for v in self.source['verses'] for d in v]
        audio = data / '03-999测试.mp3'
        audio.write_bytes(b'bound recording fixture')
        self.song = {'type': 'text', 'source': source.name, 'sha256': native.digest(source),
                     'reference': audio.name, 'tracks': [{'file': audio.name, 'sha256': native.digest(audio)}]}
        self.path = data / '03-999测试.mid'
        self.write_midi()
        period, harmony, melody = native.cli.midi_features(self.path)
        self.profile = {'version': 1, 'targetSha256': self.song['sha256'], 'tracks': self.song['tracks'],
            'sourceVerseShape': [1, 1], 'file': self.path.name, 'sha256': native.digest(self.path),
            'review': 'Original paired MIDI with both complete source paragraphs.', 'referenceAudioFile': audio.name,
            'trackMethods': {audio.name: 'four-paths'}, 'sections': [
                {'quarter': q, 'onsetIndex': i, 'pitches': melody['onsetPitches'][i:i + 5],
                 'region': d['region'], 'opening': native.clean(d['text'])[:10]}
                for q, i, d in zip([0, 4], [0, 8], self.definitions)]}

    def write_midi(self, polyphonic=True, close=True):
        score = mido.MidiFile(type=1, ticks_per_beat=480)
        meta = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000),
                              mido.MetaMessage('set_tempo', tempo=1000000, time=1920)])
        lead = mido.MidiTrack()
        for i in range(16):
            lead.append(mido.Message('note_on', note=72 + i % 7, velocity=80))
            if close or i < 15:
                lead.append(mido.Message('note_off', note=72 + i % 7, time=240))
        score.tracks.extend([meta, lead])
        if polyphonic:
            harmony = mido.MidiTrack()
            for pitch in [48, 52, 55]:
                harmony.append(mido.Message('note_on', channel=1, note=pitch, velocity=70))
            for i, pitch in enumerate([48, 52, 55]):
                harmony.append(mido.Message('note_off', channel=1, note=pitch, time=3840 if i == 0 else 0))
            score.tracks.append(harmony)
        score.save(self.path)

    def reference(self):
        return native.reference_sections(self.path, self.profile, self.definitions)

    def test_original_tempo_and_all_source_regions_are_preserved(self):
        before = self.path.read_bytes()
        native.validate_source(self.song, self.profile, self.source, self.root)
        period, harmony, rows, offsets, proof = self.reference()
        self.assertEqual(offsets, [0, 2])
        self.assertEqual(len(rows), 2)
        self.assertEqual(proof['analysisSha256'], proof['sourceSha256'])
        self.assertGreaterEqual(proof['maximumDistinctSoundingPitches'], 4)
        self.assertEqual(self.path.read_bytes(), before)

    def test_source_audio_and_midi_bindings_cannot_be_changed(self):
        for field, value in [('targetSha256', '0' * 64), ('tracks', []), ('sourceVerseShape', [2]),
                             ('file', '../different.mid'), ('referenceAudioFile', 'different.mp3'),
                             ('trackMethods', {self.song['reference']: 'chroma-dtw'})]:
            with self.subTest(field=field):
                profile = {**self.profile, field: value}
                with self.assertRaises(ValueError):
                    native.validate_source(self.song, profile, self.source, self.root)
        self.profile['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.reference()

    def test_omitted_reordered_or_unbound_readings_are_rejected(self):
        cases = [self.profile['sections'][:1], list(reversed(self.profile['sections']))]
        changed = copy.deepcopy(self.profile['sections'])
        changed[1]['opening'] = '另外一首诗歌原文'
        cases.append(changed)
        changed = copy.deepcopy(self.profile['sections'])
        changed[1]['region'] = None
        cases.append(changed)
        for sections in cases:
            with self.subTest(sections=sections):
                profile = {**self.profile, 'sections': sections}
                with self.assertRaises(ValueError):
                    native.reference_sections(self.path, profile, self.definitions)

    def test_quarter_positions_require_exact_original_melody_fingerprints(self):
        for changes in [{'quarter': 4.1}, {'onsetIndex': 7}, {'pitches': [0] * 5}, {'quarter': float('nan')}]:
            with self.subTest(changes=changes):
                profile = copy.deepcopy(self.profile)
                profile['sections'][1].update(changes)
                with self.assertRaises(ValueError):
                    native.reference_sections(self.path, profile, self.definitions)

    def test_single_melody_or_unclosed_notes_cannot_pass_as_original_polyphony(self):
        for options in [{'polyphonic': False}, {'close': False}]:
            with self.subTest(options=options):
                self.write_midi(**options)
                self.profile['sha256'] = native.digest(self.path)
                with self.assertRaises(ValueError):
                    self.reference()

    def test_failed_four_path_checks_propagate_without_threshold_changes(self):
        self.song['songId'] = '03-999'
        with patch.object(native.cli.batch, 'ROOT', self.root), \
                patch.object(native, 'recording_features', return_value=None), \
                patch.object(native, 'reference_times', side_effect=ValueError('four paths disagree')), \
                patch.object(native.cli, 'export_result') as export:
            result = native.generate(self.song, self.profile, self.root / 'cache')
        self.assertIn('needs-calibration', result[1])
        export.assert_not_called()
        evidence = json.loads((self.root / 'cache/midi-alignment' / (self.song['source'] + '.json')).read_text())
        self.assertEqual(evidence['status'], 'needs-calibration')


if __name__ == '__main__':
    unittest.main()
