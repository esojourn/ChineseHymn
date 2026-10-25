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
    from sync_batch_sectional import arranged_midi_bytes, optical_performance_rows, performance_times, four_path_time, stable_outro_times, reading_section_groups
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

    def test_modulation_transposes_all_voices_with_balanced_note_edges(self):
        recipe = {**self.recipe(), 'transposeSections': [0, 2, 0],
                  'transpositionReview': {'version': 1, 'reason': 'independently verified modulation'}}
        score = mido.MidiFile(file=io.BytesIO(arranged_midi_bytes(io.BytesIO(self.score()), recipe)))
        for track, pitches in zip(score.tracks[1:], [{60, 62, 64}, {48, 50, 52}]):
            tick, active, seen = 0, {}, set()
            for event in track:
                tick += event.time
                if event.type == 'note_on':
                    active[event.note] = active.get(event.note, 0) + 1
                    seen.add(event.note)
                    if tick == 4 * 480:
                        self.assertEqual(event.note, max(pitches))
                elif event.type == 'note_off':
                    self.assertGreater(active.get(event.note, 0), 0)
                    active[event.note] -= 1
            self.assertEqual(seen, pitches)
            self.assertFalse(any(active.values()))
        for change in [{'transposeSections': [0, 2]}, {'transposeSections': [0, True, 0]},
                       {'transposeSections': [0, 13, 0]}, {'transposeSections': [0, 0, 0]},
                       {'transpositionReview': {}}, {'transpositionReview': {'version': 1, 'reason': ''}}]:
            with self.assertRaises(ValueError):
                arranged_midi_bytes(io.BytesIO(self.score()), {**recipe, **change})

    def test_identical_duplicate_tempos_do_not_change_music_and_conflicting_rates_are_rejected(self):
        original = arranged_midi_bytes(io.BytesIO(self.score()), self.recipe())
        for tempo in [600000, 500000]:
            score = mido.MidiFile(file=io.BytesIO(self.score()))
            score.tracks[0].insert(1, mido.MetaMessage('set_tempo', tempo=tempo))
            stream = io.BytesIO()
            score.save(file=stream)
            if tempo == 600000:
                self.assertEqual(arranged_midi_bytes(io.BytesIO(stream.getvalue()), self.recipe()), original)
            else:
                with self.assertRaises(ValueError):
                    arranged_midi_bytes(io.BytesIO(stream.getvalue()), self.recipe())

    def test_pdf_and_renderer_hashes_are_checked_before_overwriting_a_reference_image(self):
        try:
            import pypdfium2 as pdfium
            from pypdfium2.version import PYPDFIUM_INFO, PDFIUM_INFO
        except ImportError:
            self.skipTest('requires optional PDF renderer')
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with pdfium.PdfDocument.new() as document:
                document.new_page(72, 72)
                document.save(cache / 'source.pdf')
            source = (cache / 'source.pdf').read_bytes()
            item = {'file': 'reference.png', 'sha256': 'wrong rendered hash',
                    'pdfSource': {'file': 'source.pdf', 'sha256': hashlib.sha256(source).hexdigest()},
                    'renderPdf': {'version': 1, 'page': 0, 'scale': 2,
                                  'pypdfium2Version': PYPDFIUM_INFO.version, 'pdfiumVersion': PDFIUM_INFO.version}}
            (cache / 'reference.png').write_bytes(b'keep reviewed image')
            with self.assertRaisesRegex(ValueError, '渲染图片'):
                self.cli.pinned_file(cache, item, True)
            self.assertEqual((cache / 'reference.png').read_bytes(), b'keep reviewed image')
            with self.assertRaisesRegex(ValueError, '渲染器版本'):
                self.cli.pinned_file(cache, {**item, 'renderPdf': {**item['renderPdf'], 'pdfiumVersion': 'changed'}}, True)
            (cache / 'source.pdf').write_bytes(b'changed PDF')
            with self.assertRaises(ValueError):
                self.cli.pinned_file(cache, item, True)
            self.assertEqual((cache / 'reference.png').read_bytes(), b'keep reviewed image')

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

    def test_printed_origin_is_bound_to_the_actual_optical_image_before_any_midi_write(self):
        from sync_batch_printed_midi import printed_reference_bytes
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            image = b'fixed printed reference'
            (cache / 'score.png').write_bytes(image)
            digest = hashlib.sha256(image).hexdigest()
            printed = {'sourceSha256': digest, 'tonicMidi': 60,
                       'numberedRowsAndQuarterBeats': [[[1, 1], [2, 1], [3, 1], [4, 1], [5, 4]]]}
            origin = printed_reference_bytes({'mode': 'symbolic-melody-and-optical', 'targetSha256': digest,
                                              'printedRhythmReference': printed}, digest)
            profile = {'midiReference': {'origin': {'kind': 'printed-numbered-score', 'file': 'origin.mid',
                       'sha256': hashlib.sha256(origin).hexdigest(), 'printedRhythmReference': printed},
                       'file': 'performance.mid', 'sha256': 'not accepted',
                       'arrangement': {'melodyTrack': 0, 'melodyTrackName': 'Manual transcription of a printed numbered melody',
                                       'quarterLength': 8, 'sections': [[0, 8]]}},
                       'opticalReference': {'image': {'file': 'score.png', 'sha256': digest}}}
            with self.assertRaisesRegex(ValueError, '重建'):
                self.cli.reference_material(profile, cache)
            self.assertEqual((cache / 'origin.mid').read_bytes(), origin)
            self.assertFalse((cache / 'performance.mid').exists())
            (cache / 'origin.mid').unlink()
            (cache / 'score.png').write_bytes(b'changed raster')
            with self.assertRaises(ValueError):
                self.cli.reference_material(profile, cache)
            self.assertFalse((cache / 'origin.mid').exists())

    def test_local_context_restricts_candidate_onsets_without_waiving_acoustic_cost(self):
        from sync_batch_score import match_template
        from sync_batch_sectional import check_match
        times = np.arange(0, 12, .1)
        chroma = np.zeros((12, len(times)), dtype=np.float32)
        row = {'notes': [1, 2, 3, 4], 'spatialWeights': [1] * 4}
        for start, strength in [(2, .4), (7, .6)]:
            for index, pitch in enumerate([0, 2, 4, 5]):
                lo = round((start + index * .5) / .1)
                chroma[pitch, lo:lo + 5] = strength
        free = match_template([row], chroma, times, 2, 2, 'spatialWeights')
        self.assertGreater(free['best']['starts'][0], 6)
        local = match_template([row], chroma, times, 2, 2, 'spatialWeights', onset_bounds=[1, 3])
        self.assertTrue(1 <= local['best']['starts'][0] <= 3)
        self.assertEqual(local['onsetBounds'], [1, 3])
        chroma[:, 10:40] = 0
        rejected = match_template([row], chroma, times, 2, 2, 'spatialWeights', onset_bounds=[1, 3])
        with self.assertRaises(ValueError):
            check_match(rejected, 2)
        for bounds in [[3, 1], [float('nan'), 3], [30, 31]]:
            with self.assertRaises(ValueError):
                match_template([row], chroma, times, 2, 2, 'spatialWeights', onset_bounds=bounds)

    def test_local_context_policy_is_explicit_and_fixed_and_outro_variants_are_pairwise_stable(self):
        self.assertIsNone(self.cli.local_matcher({}))
        self.assertIsNotNone(self.cli.local_matcher({'localOnsetWindow': {'version': 1, 'fraction': .4}}))
        for value in [{'version': True, 'fraction': .4}, {'version': 1, 'fraction': .5}, {}]:
            with self.assertRaises(ValueError):
                self.cli.local_matcher({'localOnsetWindow': value})
        self.assertEqual(stable_outro_times([[2, 31], [2.1, 31.2], [2.2, 31.1]]), [2, 31])
        for timelines in [[[1], [1.5], [.5]], [[1, 2], [1]], [[float('nan')]], []]:
            with self.assertRaises(ValueError):
                stable_outro_times(timelines)

    def test_outro_variants_cannot_replace_body_sections_or_mutate_the_base_recipe(self):
        profile = {'regions': ['r1', 'r2', 'r3'],
                   'midiReference': {'arrangement': {'sections': [[0, 8], [8, 16], [16, 24]]}},
                   'opticalReference': {'parts': {'A': []}, 'order': ['A', 'A', 'A', 'A'],
                                        'readingStarts': [[0, 0], [1, 0], [2, 0]]},
                   'instrumentalOutroReview': {'version': 1, 'nonLyric': True, 'region': 'r3', 'reason': 'instrumental tail'},
                   'instrumentalOutroAlternatives': [{'name': 'alternative', 'quarterRange': [0, 8],
                       'opticalPart': 'A', 'file': 'alternative.mid', 'sha256': 'hash'}]}
        original = copy.deepcopy(profile)
        variants = self.cli.outro_profiles(profile)
        self.assertEqual(profile, original)
        self.assertEqual(variants[0][1]['midiReference']['arrangement']['sections'], [[0, 8], [8, 16], [0, 8]])
        for change in [{'nonLyric': False}, {'region': 'r2'}, {'reason': ''}]:
            bad = {**profile, 'instrumentalOutroReview': {**profile['instrumentalOutroReview'], **change}}
            with self.assertRaises(ValueError):
                self.cli.outro_profiles(bad)
        bad = copy.deepcopy(profile)
        bad['opticalReference']['readingStarts'][-1] = [3, 0]
        with self.assertRaises(ValueError):
            self.cli.outro_profiles(bad)

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

    def test_optical_modulation_preserves_source_notes_and_rhythm_and_keeps_rests_neutral(self):
        rows, review = self.notation()
        original = copy.deepcopy(rows)
        unchanged = optical_performance_rows(rows, review)
        review.update(transposeParts=[0, 2, 0], transpositionReview={'version': 1, 'reason': 'reviewed modulation'})
        changed = optical_performance_rows(rows, review)
        self.assertEqual(rows, original)
        for index, (before, after) in enumerate(zip(unchanged, changed)):
            self.assertEqual(before['notes'], after['notes'])
            self.assertEqual(before['rhythmWeights'], after['rhythmWeights'])
            self.assertEqual(after['pitchOffsets'], [2 if note and index == 1 else 0 for note in after['notes']])
        with self.assertRaises(ValueError):
            optical_performance_rows(rows, {**review, 'transposeParts': [0, 2]})

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

    def test_previous_reading_policy_is_explicit_and_cannot_change_context_length(self):
        self.assertFalse(self.cli.previous_reading_context({}))
        self.assertTrue(self.cli.previous_reading_context({'previousReadingContext': {'version': 1}}))
        for value in [True, {}, {'version': True}, {'version': 2}, {'version': 1, 'count': 2}]:
            with self.assertRaises(ValueError):
                self.cli.previous_reading_context({'previousReadingContext': value})

    def test_previous_context_retains_current_row_label_and_rejects_disputed_target_onsets(self):
        melody = [{'notes': [i + 1], 'pitchOffsets': [0], 'spatialWeights': [1]} for i in range(3)]
        optical = [{'notes': [i + 1], 'pitchOffsets': [0], 'rhythmWeights': [1]} for i in range(3)]
        queries = []
        def matcher(rows, chroma, times, duration, onset, weight):
            queries.append([[*row['notes']] for row in rows])
            starts = [2, 12, 22] if len(rows) == 3 else [onset, onset + 10] if len(rows) == 2 else [onset]
            return {'best': {'cost': .2, 'span': duration, 'key': 1 if weight == 'spatialWeights' else 3,
                             'starts': starts}, 'runnerUp': {'cost': .4}}
        evidence = {}
        target = np.arange(36), np.zeros((12, 36))
        self.assertEqual(performance_times(melody, optical, target, 30, 2, 2, matcher, evidence,
                                          previous_context=True), [2, 12, 22])
        self.assertEqual(queries[4:6], [[[1], [2]], [[1], [2]]])
        self.assertEqual(evidence['local'][1]['previousReadingContext']['queryDuration'], 20)
        self.assertNotIn('previousReadingContext', evidence['local'][0])
        def disputed(*args):
            result = matcher(*args)
            if len(args[0]) == 2 and args[-1] == 'rhythmWeights':
                result['best']['starts'][-1] += .6
            return result
        with self.assertRaisesRegex(ValueError, '四条路径'):
            performance_times(melody, optical, target, 30, 2, 2, disputed, {}, previous_context=True)
        def expensive(*args):
            result = matcher(*args)
            if len(args[0]) == 2:
                result['best']['cost'] = .36
            return result
        with self.assertRaises(ValueError):
            performance_times(melody, optical, target, 30, 2, 2, expensive, {}, previous_context=True)

    def test_previous_context_includes_intervening_instrumental_sections_without_new_reading_cues(self):
        review = {'version': 1, 'groups': [[0], [2], [4]], 'instrumentalSections': [1, 3, 5],
                  'reason': 'reviewed instrumental interludes and outro'}
        melody = [{'notes': [i + 1], 'pitchOffsets': [0], 'spatialWeights': [1]} for i in range(6)]
        optical = [{'notes': [i + 1], 'pitchOffsets': [0], 'rhythmWeights': [1]} for i in range(6)]
        queries = []
        def matcher(rows, chroma, times, duration, onset, weight):
            queries.append([row['notes'] for row in rows])
            starts = [2, 8, 12, 18, 22, 28] if len(rows) == 6 else [onset, onset + 10] if len(rows) == 2 else [onset]
            return {'best': {'cost': .2, 'span': duration, 'key': 1 if weight == 'spatialWeights' else 3,
                             'starts': starts}, 'runnerUp': {'cost': .4}}
        actual = performance_times(melody, optical, (np.arange(36), np.zeros((12, 36))),
                                   30, 2, 2, matcher, {}, reading_sections=review, previous_context=True)
        self.assertEqual(actual, [2, 12, 22])
        self.assertEqual(queries[4:6], [[[1, 2], [3]], [[1, 2], [3]]])
        self.assertEqual(queries[6:8], [[[3, 4], [5]], [[3, 4], [5]]])

    def test_reading_groups_account_for_every_section_and_cannot_skip_undeclared_music(self):
        review = {'version': 1, 'groups': [[0, 1], [3], [4]],
                  'instrumentalSections': [2, 5], 'reason': 'reviewed instrumental interlude and outro'}
        self.assertEqual(reading_section_groups(6, review), [[0, 1], [3], [4]])
        self.assertEqual(reading_section_groups(3), [[0], [1], [2]])
        for change in [{'version': True}, {'reason': ''}, {'groups': [[1], [3], [4]]},
                       {'groups': [[0, 2], [3], [4]]}, {'groups': [[0, 1], [1], [4]]},
                       {'groups': [[0, 1], [4], [3]]}, {'groups': [[0, 1], [3], [6]]},
                       {'groups': [[False, 1], [3], [4]]}, {'instrumentalSections': [2]},
                       {'instrumentalSections': [2, 4, 5]}, {'instrumentalSections': [5, 2]}]:
            with self.assertRaises(ValueError, msg=str(change)):
                reading_section_groups(6, {**review, **change})

    def test_instrumental_sections_remain_in_complete_paths_and_are_excluded_from_sung_local_queries(self):
        review = {'version': 1, 'groups': [[0, 1], [3], [4]],
                  'instrumentalSections': [2, 5], 'reason': 'instrumental gaps reviewed against recording'}
        melody = [{'notes': [i + 1], 'pitchOffsets': [0], 'spatialWeights': [1]} for i in range(6)]
        optical = [{'notes': [i + 1], 'pitchOffsets': [0], 'rhythmWeights': [1]} for i in range(6)]
        queries = []
        def matcher(rows, chroma, times, duration, onset, weight):
            complete = len(rows) == 6
            queries.append((complete, [n for row in rows for n in row['notes']], duration))
            starts = [2, 6, 12, 17, 25, 34] if complete else [onset]
            key = 1 if weight == 'spatialWeights' else 3
            return {'best': {'cost': .2, 'span': duration, 'key': key, 'starts': starts},
                    'runnerUp': {'cost': .4}}
        evidence = {}
        target = np.arange(45), np.zeros((12, 45))
        self.assertEqual(performance_times(melody, optical, target, 40, 2, 2, matcher,
                         evidence, reading_sections=review), [2, 17, 25])
        self.assertEqual([notes for complete, notes, span in queries if complete], [[1, 2, 3, 4, 5, 6]] * 2)
        self.assertEqual([notes for complete, notes, span in queries if not complete], [[1, 2]] * 2 + [[4]] * 2 + [[5]] * 2)
        self.assertEqual([item['duration'] for item in evidence['local']], [10, 8, 9])
        self.assertEqual([item['sections'] for item in evidence['local']], review['groups'])
        def wrong_local(*args):
            answer = matcher(*args)
            if len(args[0]) == 1 and args[-1] == 'rhythmWeights':
                answer['best']['starts'][0] += .6
            return answer
        with self.assertRaisesRegex(ValueError, '四条路径'):
            performance_times(melody, optical, target, 40, 2, 2, wrong_local, {}, reading_sections=review)


if __name__ == '__main__':
    unittest.main()
