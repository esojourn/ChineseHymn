import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
try:
    import mido
    import numpy as np
    from sync_batch_sectional import arranged_midi_bytes, optical_performance_rows, performance_times, four_path_time
except ImportError:
    mido = None


@unittest.skipIf(mido is None, 'requires optional audio dependencies')
class SectionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parents[1] / 'tools/sectional-midi-sync.py'
        spec = importlib.util.spec_from_file_location('tested_sectional_cli', path)
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def score(self):
        score = mido.MidiFile(ticks_per_beat=480)
        meta = mido.MidiTrack()
        meta.append(mido.MetaMessage('set_tempo', tempo=600000))
        meta.append(mido.MetaMessage('end_of_track', time=8 * 480))
        score.tracks.append(meta)
        for name, pitch in [('Accompaniment', 48), ('Melody', 60)]:
            track = mido.MidiTrack()
            track.append(mido.MetaMessage('track_name', name=name))
            track.append(mido.Message('note_on', note=pitch, velocity=80, time=480))
            track.append(mido.Message('note_off', note=pitch, time=480))
            track.append(mido.Message('note_on', note=pitch + 2, velocity=90, time=480))
            track.append(mido.Message('note_off', note=pitch + 2, time=960))
            track.append(mido.MetaMessage('end_of_track', time=3 * 480))
            score.tracks.append(track)
        data = io.BytesIO()
        score.save(file=data)
        return data.getvalue()

    def recipe(self):
        return {'melodyTrack': 2, 'melodyTrackName': 'Melody', 'quarterLength': 8,
                'sections': [[0, 4], [4, 8], [0, 4]]}

    def test_arrangement_preserves_source_pitches_and_clips_notes_at_explicit_section_edges(self):
        source = self.score()
        actual = arranged_midi_bytes(io.BytesIO(source), self.recipe())
        self.assertEqual(actual, arranged_midi_bytes(io.BytesIO(source), self.recipe()))
        score = mido.MidiFile(file=io.BytesIO(actual))
        self.assertEqual(score.tracks[1].name, 'Melody')
        self.assertAlmostEqual(score.length, 12 * .6)
        tick, events = 0, []
        for event in score.tracks[1]:
            tick += event.time
            if event.type in ['note_on', 'note_off']:
                events.append((tick / 480, event.type, event.note))
        self.assertIn((4, 'note_off', 62), events)
        self.assertIn((4, 'note_on', 62), events)
        self.assertIn((5, 'note_off', 62), events)
        self.assertEqual({note for tick, kind, note in events}, {60, 62})

    def test_arrangement_rejects_wrong_voice_and_unavailable_or_unbounded_sections(self):
        for update in [{'melodyTrackName': 'Bass'}, {'melodyTrack': True},
                       {'sections': [[0, 9]]}, {'sections': [[-1, 3]]},
                       {'sections': [[0, float('nan')]]}, {'quarterLength': 20}, {'sections': []}]:
            with self.assertRaises(ValueError):
                arranged_midi_bytes(io.BytesIO(self.score()), {**self.recipe(), **update})

    def test_failed_reference_hash_cannot_replace_an_existing_derived_midi(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            original = self.score()
            (cache / 'original.mid').write_bytes(original)
            destination = cache / 'derived.mid'
            destination.write_bytes(b'keep existing reference')
            profile = {'midiReference': {
                'origin': {'file': 'original.mid', 'sha256': hashlib.sha256(original).hexdigest()},
                'file': 'derived.mid', 'sha256': 'changed', 'arrangement': self.recipe()}}
            with self.assertRaisesRegex(ValueError, '重建'):
                self.cli.reference_material(profile, cache)
            self.assertEqual(destination.read_bytes(), b'keep existing reference')

    def test_source_profile_binds_every_recording_and_every_original_reading_region(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'data/song.txt').write_text('标题\n\n第一段歌词\n\n第二段歌词\n\n第三段歌词')
            song = {'type': 'text', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                    'tracks': [{'file': 'main.mp3', 'sha256': 'main'}, {'file': 'lead.mp3', 'sha256': 'lead'}]}
            profile = {'version': 1, 'sourceHash': 'source', 'tracks': song['tracks'], 'audioFile': 'lead.mp3',
                       'regions': ['r1', 'r2', 'r3'], 'regionOpenings': ['第一段歌词', '第二段歌词', '第三段歌词'],
                       'sampling': {'durationEstimate': 30, 'onsetEstimate': 2}}
            with patch.object(self.cli.batch, 'ROOT', root):
                self.assertEqual(len(self.cli.validate_source(song, profile)[1]), 3)
                for change in [{'tracks': song['tracks'][1:]}, {'sourceHash': 'changed'},
                               {'regions': ['r1', 'r2']}, {'audioFile': 'main.mp3'},
                               {'regionOpenings': ['第一段歌词', '另外的歌词', '第三段歌词']}]:
                    with self.assertRaises(ValueError):
                        self.cli.validate_source(song, {**profile, **change})

    def notation(self):
        row = {'notes': [0, 1, 1, 2, 3, 4], 'pitchOffsets': [0] * 6, 'rhythmWeights': [1] * 6}
        review = {'ties': {'0': [2]},
                  'tuplets': [{'system': 0, 'noteIndices': [3, 4, 5], 'actualNotes': 3, 'normalNotes': 2}],
                  'parts': {'A': [[0, 0, 6]]}, 'order': ['A', 'A', 'A'],
                  'readingStarts': [[0, 1], [1, 1], [2, 1]]}
        return [row], review

    def test_optical_form_preserves_rests_ties_and_tuplets_without_mutating_raw_recognition(self):
        rows, review = self.notation()
        original = copy.deepcopy(rows)
        actual = optical_performance_rows(rows, review)
        self.assertEqual(rows, original)
        self.assertEqual(actual[0]['notes'], [1, 2, 3, 4, 0])
        self.assertAlmostEqual(sum(sum(row['rhythmWeights']) for row in actual), 14)
        self.assertEqual(actual[0]['rhythmWeights'][0], 2)
        self.assertAlmostEqual(actual[0]['rhythmWeights'][1], 2 / 3)

    def test_optical_annotations_cannot_merge_other_pitches_or_cut_tuplets_or_tied_heads(self):
        for changes in [{'ties': {'0': [3]}}, {'parts': {'A': [[0, 2, 6]]}},
                        {'parts': {'A': [[0, 0, 5]]}}, {'order': ['missing']},
                        {'readingStarts': [[0, 1], [0, 1]]},
                        {'tuplets': [{'system': 0, 'noteIndices': [], 'actualNotes': 3, 'normalNotes': 2}]}]:
            rows, review = self.notation()
            with self.assertRaises(ValueError):
                optical_performance_rows(rows, {**review, **changes})

    def matcher(self, bad=None):
        def match(rows, chroma, times, duration, onset, weight):
            key = 1 if weight == 'spatialWeights' else 3
            starts = [2, 12, 22] if len(rows) == 3 else [onset]
            best = {'cost': .2, 'key': key, 'span': duration, 'starts': starts}
            result = {'best': best, 'runnerUp': {'cost': .4, 'key': (key + 1) % 12}}
            if bad == 'local' and len(rows) == 1 and weight == 'rhythmWeights' and onset == 12:
                best['starts'] = [12.6]
            if bad == 'cost':
                best['cost'] = .36
            if bad == 'margin':
                result['runnerUp']['cost'] = .25
            if bad == 'nan-margin':
                result['runnerUp']['cost'] = float('nan')
            if bad == 'span':
                best['span'] = duration * .6
            if bad == 'key' and weight == 'rhythmWeights':
                best['key'] = 4
            return result
        return match

    def test_complete_and_local_paths_must_all_agree_and_cover_the_whole_performance(self):
        target = np.arange(36), np.zeros((12, 36))
        evidence = {}
        self.assertEqual(performance_times([{}] * 3, [{}] * 3, target, 30, 2, 2,
                                            self.matcher(), evidence), [2, 12, 22])
        self.assertEqual(len(evidence['local']), 3)
        for bad in ['local', 'cost', 'margin', 'nan-margin', 'span', 'key']:
            with self.assertRaises(ValueError, msg=bad):
                performance_times([{}] * 3, [{}] * 3, target, 30, 2, 2, self.matcher(bad), {})
        with self.assertRaises(ValueError):
            performance_times([{}] * 3, [{}] * 3, (np.arange(80), target[1]), 30, 2, 2, self.matcher(), {})

    def test_four_path_check_uses_the_extreme_difference_and_includes_half_second_boundary(self):
        self.assertEqual(four_path_time([1, 1.5, 1.1, 1.4]), 1.25)
        for values in [[1, 1.51, 1.2, 1.3], [1, 1, 1, float('nan')], [1, 1, 1]]:
            with self.assertRaises(ValueError):
                four_path_time(values)


if __name__ == '__main__':
    unittest.main()
