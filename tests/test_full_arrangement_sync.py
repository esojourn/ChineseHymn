import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mido
import numpy as np
sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import sync_batch_full_arrangement as full
from sync_batch_sources import text_source


class FullArrangementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('complete_arrangement_midi',
            Path(__file__).parents[1] / 'tools/midi-sync.py')
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def fixture(self, root):
        (root / 'data').mkdir()
        (root / 'midi').mkdir()
        path = root / 'data/song.txt'
        path.write_text('原始歌曲\n\n' + '\n\n'.join(f'第{i}原始歌词第一行\n第{i}原始歌词第二行' for i in range(4)))
        source = text_source(path)
        score = mido.MidiFile(type=1, ticks_per_beat=100)
        score.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000)]))
        for i, name in enumerate(['Soprano', 'Tenor', 'Piano']):
            track = mido.MidiTrack([mido.MetaMessage('track_name', name=name)])
            for n in range(20):
                track.extend([mido.Message('note_on', channel=i, note=60 - i * 12 + n % 5, velocity=70),
                              mido.Message('note_off', channel=i, note=60 - i * 12 + n % 5, time=100)])
            score.tracks.append(track)
        score.save(root / 'midi/original.mid')
        (root / 'score.pdf').write_bytes(b'%PDF-1.7\noriginal source score')
        tracks = []
        for name in ['main.mp3', 'lead.mp3']:
            (root / 'data' / name).write_bytes(name.encode())
            tracks.append({'file': name, 'sha256': full.digest(root / 'data' / name)})
        song = {'songId': 'test', 'type': 'text', 'source': path.name, 'reference': 'lead.mp3',
                'sha256': full.digest(path), 'tracks': tracks}
        definitions = [d for verse in source['verses'] for d in verse]
        profile = {'fullArrangementReview': {'version': 1, 'reason': '原文完整编排及全部声部核对。'},
            'targetSha256': song['sha256'], 'tracks': tracks, 'verseCount': 4,
            'sourceVerseShape': [1] * 4, 'sourceRegions': [d['region'] for d in definitions],
            'regionOpenings': [[d['text'][:8]] for d in definitions], 'referenceAudioFile': 'main.mp3',
            'scoreReferenceFile': 'score.pdf', 'scoreReferenceSha256': full.digest(root / 'score.pdf'),
            'midiOrigin': {'file': 'original.mid', 'sha256': full.digest(root / 'midi/original.mid')},
            'file': 'derived.mid', 'sha256': 'pending', 'midiArrangement': {'version': 1,
                'voiceTracks': [1, 2], 'voiceTrackNames': ['Soprano', 'Tenor'],
                'reason': '原谱两条声乐声部逐音保留，钢琴不作为主旋律。',
                'recipe': {'melodyTrack': 1, 'melodyTrackName': full.VOICE_NAME,
                           'quarterLength': 20, 'sections': [[0, 20]]}}}
        # Compute the independently expected derivation using the original arrangement utility.
        import io
        combined = mido.MidiFile(type=1, ticks_per_beat=100)
        combined.tracks.append(score.tracks[0].copy())
        merged = mido.merge_tracks(score.tracks[1:3])
        merged.name = full.VOICE_NAME
        combined.tracks += [merged, score.tracks[3].copy()]
        stream = io.BytesIO()
        combined.save(file=stream)
        stream.seek(0)
        expected = full.arranged_midi_bytes(stream, profile['midiArrangement']['recipe'])
        import hashlib
        profile['sha256'] = hashlib.sha256(expected).hexdigest()
        return song, profile, source, expected

    def notes(self, path):
        result = []
        score = mido.MidiFile(path)
        for track in score.tracks:
            tick = 0
            for event in track:
                tick += event.time
                if event.type in ['note_on', 'note_off']:
                    result.append((tick, event.type, event.channel, event.note,
                                   event.velocity if event.type == 'note_on' else 0))
        return sorted(result)

    def test_merges_only_bound_voices_preserves_every_original_note_and_rebuilds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            song, profile, source, expected = self.fixture(root)
            original = (root / 'midi/original.mid').read_bytes()
            before = copy.deepcopy(source)
            definitions, reference = full.validate_source(song, profile, source, root, root)
            self.assertEqual(reference, 'main.mp3')
            self.assertEqual(len(definitions), 4)
            self.assertEqual(source, before)
            derived, proof = full.reference_midi(profile, root)
            self.assertEqual(derived.read_bytes(), expected)
            self.assertEqual(self.notes(derived), self.notes(root / 'midi/original.mid'))
            derived.write_bytes(b'corrupt cache')
            self.assertEqual(full.reference_midi(profile, root), (derived, proof))
            self.assertEqual(derived.read_bytes(), expected)
            self.assertEqual((root / 'midi/original.mid').read_bytes(), original)

    def test_rejects_stale_sources_omitted_recordings_and_unreviewed_parameters(self):
        mutations = ['source', 'audio', 'score', 'midi', 'tracks', 'regions', 'shape', 'opening',
                     'reference', 'bool', 'free-time', 'voice', 'name', 'transpose', 'derived-hash']
        for mutation in mutations:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                song, p, source, _ = self.fixture(root)
                if mutation in ['source', 'audio', 'score', 'midi']:
                    path = root / {'source': 'data/song.txt', 'audio': 'data/lead.mp3',
                                   'score': 'score.pdf', 'midi': 'midi/original.mid'}[mutation]
                    path.write_bytes(path.read_bytes() + b'changed')
                elif mutation == 'tracks': p['tracks'] = p['tracks'][:1]
                elif mutation == 'regions': p['sourceRegions'].pop()
                elif mutation == 'shape': p['sourceVerseShape'] = [2, 2]
                elif mutation == 'opening': p['regionOpenings'][0] = ['不同原文起句']
                elif mutation == 'reference': p['referenceAudioFile'] = 'other.mp3'
                elif mutation == 'bool': p['fullArrangementReview']['version'] = True
                elif mutation == 'free-time': p['fullArrangementReview']['at'] = 12
                elif mutation == 'voice': p['midiArrangement']['voiceTracks'] = [1, 1]
                elif mutation == 'name': p['midiArrangement']['voiceTrackNames'][0] = 'other'
                elif mutation == 'transpose': p['midiArrangement']['recipe']['transposeSections'] = [1]
                elif mutation == 'derived-hash': p['sha256'] = 'wrong'
                with self.assertRaises(ValueError, msg=mutation):
                    full.validate_source(song, p, source, root, root)
                    full.reference_midi(p, root)

    def match(self, starts, span, key=2, cost=.1, runner=.6):
        return {'best': {'starts': starts, 'span': span, 'key': key, 'cost': cost},
                'runnerUp': {'cost': runner}}

    def helpers(self, failure=None):
        def harmonic(reference, target, zero, period, duration, at, cue_offsets):
            starts = [2, 12, 22, 32] if len(cue_offsets) == 4 else [at]
            cost = .36 if failure == 'local-cost' and len(cue_offsets) == 1 else .1
            runner = .15 if failure == 'global-margin' and len(cue_offsets) == 4 else .6
            return self.match(starts, duration if len(cue_offsets) == 1 else 40, cost=cost, runner=runner)
        def melodic(rows, chroma, times, duration, at, weight):
            starts = [2, 12, 22, 32] if len(rows) == 4 else [at]
            if failure == 'onset' and len(rows) == 1: starts = [at + .6]
            key = 3 if failure == 'key' and len(rows) == 1 else 2
            return self.match(starts, duration if len(rows) == 1 else 40, key=key)
        return SimpleNamespace(same_tune=SimpleNamespace(match_cycle=harmonic), match_template=melodic)

    def test_reference_requires_all_four_paths_and_original_thresholds(self):
        times = np.arange(451) * .1
        target = times, np.zeros((12, len(times)))
        for failure in [None, 'local-cost', 'global-margin', 'onset', 'key']:
            detail = {}
            if failure:
                with self.assertRaises(ValueError, msg=failure):
                    full.reference_times(self.helpers(failure), 40, target, [{}, {}, {}, {}],
                                         [0, 10, 20, 30], target, detail)
            else:
                actual, key = full.reference_times(self.helpers(), 40, target, [{}, {}, {}, {}],
                                                   [0, 10, 20, 30], target, detail)
                self.assertEqual(actual, [2, 12, 22, 32])
                self.assertEqual(key, 2)
                self.assertEqual(len(detail['fourPathOnsets']), 4)

    def test_mapped_recordings_must_also_pass_their_own_melody(self):
        times = [2, 12, 22, 32]
        target = np.arange(451) * .1, np.zeros((12, 451))
        for failure in [None, 'onset', 'key', 'map']:
            cli = self.helpers(failure)
            with patch.object(full, 'features', return_value=target), \
                    patch.object(full, 'map_recordings', side_effect=ValueError('map failed') if failure == 'map'
                                 else None, return_value=(times, {'semitoneShift': 0})):
                if failure:
                    with self.assertRaises(ValueError):
                        full.recording_times(cli, Path('main'), Path('lead'), times, [{}, {}, {}, {}], 2, Path('.'), {})
                else:
                    self.assertEqual(full.recording_times(cli, Path('main'), Path('lead'), times,
                        [{}, {}, {}, {}], 2, Path('.'), {}), times)

    def test_fresh_and_cached_raw_audio_have_identical_features_in_this_mode(self):
        from scipy.io.wavfile import write
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'original.wav'
            t = np.arange(16000 * 9) / 16000
            audio = (.4 * np.sin(2 * np.pi * 220 * t) + .15 * np.sin(2 * np.pi * 330 * t))
            write(path, 16000, (audio * 32767).astype(np.int16))
            first = full.recording_features(path, root)
            cached = full.recording_features(path, root)
            for a, b in zip(first, cached):
                self.assertEqual(a.dtype, np.float32)
                self.assertTrue(np.array_equal(a, b))

    def short_fixture(self, root):
        song, profile, source, _ = self.fixture(root)
        path = root / 'data/song.txt'
        path.write_text('原始完整诗歌\n\n' + '\n'.join(f'完整原文第{i}行歌词全部保留' for i in range(6)))
        source = text_source(path)
        song['sha256'] = full.digest(path)
        score = mido.MidiFile(root / 'midi/original.mid')
        bass = copy.deepcopy(score.tracks[-1])
        for event in bass:
            if event.type in ['note_on', 'note_off']:
                event.channel, event.note = 3, event.note - 12
        score.tracks.append(bass)
        score.save(root / 'midi/original.mid')
        profile.pop('midiOrigin')
        profile.pop('midiArrangement')
        definitions = source['verses'][0]
        profile.update(targetSha256=song['sha256'], sourceVerseShape=[2], verseCount=1,
            sourceRegions=[d['region'] for d in definitions],
            regionOpenings=[[d['text'][:8] for d in definitions]],
            file='original.mid', sha256=full.digest(root / 'midi/original.mid'),
            shortOriginalMidiReview={'version': 1, 'reason': '完整原始四声部及全文两区，禁止截取或重排。'})
        return song, profile, source

    def test_two_regions_require_complete_unmodified_polyphonic_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            song, profile, source = self.short_fixture(root)
            raw = (root / 'midi/original.mid').read_bytes()
            full.validate_source(song, profile, source, root, root)
            path, proof = full.reference_midi(profile, root)
            self.assertEqual(path, root / 'midi/original.mid')
            self.assertEqual(path.read_bytes(), raw)
            self.assertTrue(proof['unmodifiedOriginal'])
            self.assertGreaterEqual(proof['maximumDistinctSoundingPitches'], 4)
            without = {k: v for k, v in profile.items() if k != 'shortOriginalMidiReview'}
            with self.assertRaises(ValueError):
                full.validate_source(song, without, source, root, root)
            for extra in ['midiArrangement', 'midiOrigin']:
                with self.assertRaises(ValueError):
                    full.reference_midi({**profile, extra: {}}, root)
            for review in [{'version': True, 'reason': 'x'}, {'version': 1, 'reason': ''},
                           {'version': 1, 'reason': 'x', 'seconds': [5, 20]}]:
                with self.assertRaises(ValueError):
                    full.reference_midi({**profile, 'shortOriginalMidiReview': review}, root)
            score = mido.MidiFile(path)
            score.tracks = score.tracks[:2]
            score.save(path)
            profile['sha256'] = full.digest(path)
            with self.assertRaises(ValueError):
                full.reference_midi(profile, root)

    def test_failed_additional_recording_prevents_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            song, profile, source, _ = self.fixture(root)
            path, _ = full.reference_midi(profile, root)
            _, _, melody = self.cli.midi_features(path)
            profile['readingBoundaries'] = [{'onsetIndex': i, 'pitches': melody['onsetPitches'][i:i + 5]}
                                            for i in [0, 5, 10, 15]]
            helpers = self.helpers()
            def feature(path, cache):
                return np.arange(451) * .1, np.full((12, 451), 2 if path.name == 'lead.mp3' else 1)
            def melodic(rows, chroma, times, duration, at, weight):
                match = helpers.match_template(rows, chroma, times, duration, at, weight)
                if chroma[0, 0] == 2:
                    match['best']['cost'] = .36
                return match
            with patch.object(self.cli.batch, 'ROOT', root), \
                    patch.object(full, 'features', side_effect=feature), \
                    patch.object(full, 'map_recordings', return_value=([2, 12, 22, 32], {'semitoneShift': 0})), \
                    patch.object(self.cli.same_tune, 'match_cycle', side_effect=helpers.same_tune.match_cycle), \
                    patch.object(self.cli, 'match_template', side_effect=melodic), \
                    patch.object(self.cli, 'export_result') as export:
                self.assertIn('needs-calibration', self.cli.generate(song, profile, root)[1])
                export.assert_not_called()


if __name__ == '__main__':
    unittest.main()
