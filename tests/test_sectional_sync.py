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
    from sync_batch_sectional import arranged_midi_bytes, optical_performance_rows, performance_times, four_path_time, stable_outro_times, reading_section_groups, source_verse_groups, verify_verse_music
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

    def test_stacked_pages_rebuild_original_pixels_in_order_and_verify_sources_before_cache_reuse(self):
        from PIL import Image, __version__ as pillow_version
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            sources = []
            for name, size, color in [('first.png', (3, 2), 'red'), ('second.png', (2, 3), 'blue')]:
                Image.new('RGB', size, color).save(cache / name)
                sources.append({'file': name, 'url': 'https://example.test/' + name,
                                'sha256': hashlib.sha256((cache / name).read_bytes()).hexdigest()})
            expected = Image.new('RGB', (3, 5), 'white')
            expected.paste(Image.new('RGB', (3, 2), 'red'), (0, 0))
            expected.paste(Image.new('RGB', (2, 3), 'blue'), (0, 2))
            stream = io.BytesIO()
            expected.save(stream, format='PNG')
            item = {'file': 'stack.png', 'sha256': hashlib.sha256(stream.getvalue()).hexdigest(),
                    'stackImages': {'version': 1, 'pillowVersion': pillow_version, 'sources': sources}}
            self.assertEqual(self.cli.pinned_file(cache, item, True), cache / 'stack.png')
            self.assertEqual((cache / 'stack.png').read_bytes(), stream.getvalue())
            with self.assertRaisesRegex(ValueError, '合并图片'):
                self.cli.pinned_file(cache, {**item, 'sha256': 'changed'}, True)
            self.assertEqual((cache / 'stack.png').read_bytes(), stream.getvalue())
            (cache / 'second.png').write_bytes(b'changed original page')
            for prepare in [False, True]:
                with self.assertRaises(ValueError):
                    self.cli.pinned_file(cache, item, prepare)
            self.assertEqual((cache / 'stack.png').read_bytes(), stream.getvalue())

    def test_stacked_pages_reject_unpinned_transforms_duplicate_pages_and_recursive_inputs(self):
        from PIL import __version__ as pillow_version
        source = {'file': 'first.png', 'url': 'https://example.test/first.png', 'sha256': 'first'}
        stack = {'version': 1, 'pillowVersion': pillow_version,
                 'sources': [source, {**source, 'file': 'second.png'}]}
        bad_stacks = [{**stack, 'version': True}, {**stack, 'pillowVersion': 'other'},
                      {**stack, 'resize': 2}, {**stack, 'sources': [source]},
                      {**stack, 'sources': [source, source]},
                      {**stack, 'sources': [source, {**source, 'file': 'stack.png'}]},
                      {**stack, 'sources': [source, {**source, 'file': 'second.png', 'renderPdf': {}}]}]
        with tempfile.TemporaryDirectory() as directory:
            for value in bad_stacks:
                with self.assertRaisesRegex(ValueError, '多页原谱'):
                    self.cli.pinned_file(Path(directory), {'file': 'stack.png', 'stackImages': value}, True)
            with self.assertRaisesRegex(ValueError, '多页原谱'):
                self.cli.pinned_file(Path(directory), {'file': 'stack.png', 'stackImages': stack, 'renderPdf': {}}, True)

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

    def test_complete_verse_policy_derives_all_groups_from_source_and_rejects_count_overrides(self):
        source = {'verses': [[{'region': 'r1'}, {'region': 'r2'}],
                             [{'region': 'r3'}, {'region': 'r4'}],
                             [{'region': 'r1'}, {'region': 'r2'}]]}
        review = {'version': 1, 'reason': 'complete repeated source verses'}
        self.assertIsNone(self.cli.source_verse_context(source, {}))
        self.assertEqual(self.cli.source_verse_context(source, {'sourceVerseContext': review}),
                         [[0, 1], [2, 3], [4, 5]])
        for value in [{}, {**review, 'version': True}, {**review, 'reason': ''},
                      {**review, 'verses': [[0, 1], [2, 3]]}, {**review, 'count': 2}]:
            with self.assertRaises(ValueError):
                self.cli.source_verse_context(source, {'sourceVerseContext': value})
        with self.assertRaises(ValueError):
            self.cli.source_verse_context(source, {'sourceVerseContext': review,
                                                  'previousReadingContext': {'version': 1}})
        for groups in [[[0, 1], [2, 3, 4, 5]], [[0, 1], [4, 5]], [[0, 1], [2, 3], [3, 4]],
                       [[0, True], [2, 3], [4, 5]], [[0, 1, 2, 3, 4, 5]], []]:
            with self.assertRaises(ValueError):
                source_verse_groups(6, groups)

    def test_complete_verse_music_checks_every_reading_phase_and_rejects_missing_repetitions(self):
        groups = [[0, 1], [2, 3], [4, 5]]
        music = {'period': 20, 'similarity': .8, 'shifts': [0, 20, 40]}
        proof = verify_verse_music([14, 20, 34.5, 40, 54, 60], groups, music)
        self.assertEqual(proof['phaseDifferences'], [[0, 0], [.5, 0], [0, 0]])
        for times in [[14, 20, 34.51, 40, 54, 60], [14, 20, 34, 41, 54, 60],
                      [14, 20, 34, 40, float('nan'), 60]]:
            with self.assertRaises(ValueError):
                verify_verse_music(times, groups, music)
        for change in [{'shifts': [0, 20]}, {'shifts': [0, 40, 20]}, {'shifts': [0, 20, float('nan')]},
                       {'shifts': [1, 20, 40]}, {'similarity': .59}]:
            with self.assertRaises(ValueError):
                verify_verse_music([14, 20, 34, 40, 54, 60], groups, {**music, **change})

    def test_complete_verse_context_keeps_current_boundary_separate_and_retains_complete_verse_edges(self):
        melody = [{'notes': [i + 1], 'pitchOffsets': [0], 'spatialWeights': [1]} for i in range(6)]
        optical = [{'notes': [i + 1], 'pitchOffsets': [0], 'rhythmWeights': [1]} for i in range(6)]
        verses = [[0, 1], [2, 3], [4, 5]]
        starts = [14, 20, 34, 40, 54, 60]
        queries = []
        def matcher(rows, chroma, times, duration, onset, weight):
            queries.append([row['notes'] for row in rows])
            found = starts if len(rows) == 6 else [starts[row['notes'][0] - 1] for row in rows]
            return {'best': {'cost': .2, 'span': duration,
                             'key': 1 if weight == 'spatialWeights' else 3, 'starts': found},
                    'runnerUp': {'cost': .4}}
        target = np.arange(77), np.zeros((12, 77))
        proof = {}
        self.assertEqual(performance_times(melody, optical, target, 60, 14, 2, matcher, proof,
                                          source_verses=verses), starts)
        self.assertEqual(proof['completeVerseSpans'], [20, 20, 20])
        self.assertEqual(proof['local'][3]['sourceVerseContext']['sections'], [0, 1, 2])
        self.assertEqual(proof['local'][4]['sourceVerseContext']['sections'], [2, 3])
        self.assertEqual(queries[8:10], [[[1, 2, 3], [4]], [[1, 2, 3], [4]]])
        with self.assertRaisesRegex(ValueError, '录音首尾'):
            performance_times(melody, optical, target, 60, 14, 2, matcher, {})
        with self.assertRaisesRegex(ValueError, '录音首尾'):
            performance_times(melody, optical, (np.arange(90), target[1]), 60, 14, 2, matcher, {},
                              source_verses=verses)
        starts[:] = [at + 2 for at in starts]
        with self.assertRaisesRegex(ValueError, '录音首尾'):
            performance_times(melody, optical, target, 60, 16, 2, matcher, {}, source_verses=verses)
        starts[:] = [at - 2 for at in starts]
        def expensive(*args):
            result = matcher(*args)
            if len(args[0]) == 2:
                result['best']['cost'] = .36
            return result
        with self.assertRaisesRegex(ValueError, '成本'):
            performance_times(melody, optical, target, 60, 14, 2, expensive, {}, source_verses=verses)
        def disputed(*args):
            result = matcher(*args)
            if len(args[0]) == 2 and args[-1] == 'rhythmWeights':
                result['best']['starts'][-1] += .6
            return result
        with self.assertRaisesRegex(ValueError, '四条路径'):
            performance_times(melody, optical, target, 60, 14, 2, disputed, {}, source_verses=verses)

    def test_independent_complete_verse_phase_failure_prevents_export(self):
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}, {'file': 'main.mp3', 'sha256': 'main'}]}
        definitions = [{'region': 'r' + str(i)} for i in range(4)]
        source = {'verses': [definitions[:2], definitions[2:]]}
        window = {'durationEstimate': 40, 'onsetEstimate': 2}
        profile = {'sampling': window, 'sourceVerseContext': {'version': 1, 'reason': 'reviewed repeated verses'},
                   'independentRecordings': [{'file': 'main.mp3', 'sha256': 'main', 'sampling': window}]}
        material = ([{}] * 4, [{}] * 4, {'symbolicRows': {'semitoneShift': 2}})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=(source, definitions)), \
                patch.object(self.cli, 'reference_material', return_value=material), \
                patch.object(self.cli, 'features', return_value=None), \
                patch.object(self.cli, 'performance_times', side_effect=[[2, 8, 22, 28], [2, 8, 22, 29]]), \
                patch.object(self.cli, 'repeated_verses', return_value={'similarity': .8, 'shifts': [0, 20]}), \
                patch.object(self.cli, 'export_result') as export:
            result = self.cli.generate(song, profile, Path(directory))
            self.assertIn('needs-calibration', result[1])
            export.assert_not_called()
            self.assertFalse((Path(directory) / 'ready/song.txt.json').exists())

    def test_complete_verse_music_also_rejects_an_outro_variant_with_disputed_repeat_phase(self):
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}]}
        definitions = [{'region': 'r' + str(i)} for i in range(4)]
        source = {'verses': [definitions[:2], definitions[2:]]}
        profile = {'sampling': {'durationEstimate': 40, 'onsetEstimate': 2},
                   'sourceVerseContext': {'version': 1, 'reason': 'reviewed repeated verses'}}
        material = ([{}] * 4, [{}] * 4, {'symbolicRows': {'semitoneShift': 2}})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=(source, definitions)), \
                patch.object(self.cli, 'reference_material', return_value=material), \
                patch.object(self.cli, 'features', return_value=None), \
                patch.object(self.cli, 'outro_profiles', return_value=[('alternate', {'midiReference': {}})]), \
                patch.object(self.cli, 'performance_times', side_effect=[[2, 8, 22, 28], [1.6, 7.6, 22.4, 28.4]]), \
                patch.object(self.cli, 'repeated_verses', return_value={'similarity': .8, 'shifts': [0, 20]}), \
                patch.object(self.cli, 'export_result') as export:
            result = self.cli.generate(song, profile, Path(directory))
            self.assertIn('相位', result[1])
            export.assert_not_called()

    def test_independent_recordings_require_current_hashes_unique_files_and_only_sampling_overrides(self):
        song = {'reference': 'lead.mp3', 'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'},
                                                  {'file': 'main.mp3', 'sha256': 'main'}]}
        item = {'file': 'main.mp3', 'sha256': 'main', 'sampling': {'durationEstimate': 30, 'onsetEstimate': 2}}
        self.assertEqual(self.cli.independent_recordings(song, {}), [])
        self.assertEqual(self.cli.independent_recordings(song, {'independentRecordings': [item]}), [item])
        for entries in [None, [item, item], [{**item, 'file': 'unknown.mp3'}],
                        [{**item, 'file': 'lead.mp3', 'sha256': 'lead'}], [{**item, 'sha256': 'stale'}],
                        [{**item, 'regions': ['r1']}], [{**item, 'sampling': {'durationEstimate': True, 'onsetEstimate': 2}}],
                        [{**item, 'sampling': {'durationEstimate': float('nan'), 'onsetEstimate': 2}}],
                        [{**item, 'sampling': {'durationEstimate': 30, 'onsetEstimate': -1}}]]:
            with self.assertRaises(ValueError):
                self.cli.independent_recordings(song, {'independentRecordings': entries})

    def test_failed_explicit_independent_recording_cannot_export_or_fall_back_to_another_track(self):
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}, {'file': 'main.mp3', 'sha256': 'main'}]}
        window = {'durationEstimate': 30, 'onsetEstimate': 2}
        profile = {'sampling': window, 'independentRecordings': [
            {'file': 'main.mp3', 'sha256': 'main', 'sampling': window}]}
        definitions = [{'region': 'r' + str(i)} for i in range(3)]
        material = ([{}] * 3, [{}] * 3, {'symbolicRows': {'semitoneShift': 2}})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=({}, definitions)), \
                patch.object(self.cli, 'reference_material', return_value=material), \
                patch.object(self.cli, 'features', return_value=None), \
                patch.object(self.cli, 'performance_times', side_effect=[[2, 12, 22], ValueError('bad independent track')]), \
                patch.object(self.cli, 'export_result') as export:
            result = self.cli.generate(song, profile, Path(directory))
            self.assertIn('needs-calibration', result[1])
            export.assert_not_called()
            self.assertFalse((Path(directory) / 'ready/song.txt.json').exists())

    def test_every_recording_measurement_rechecks_all_declared_outro_alternatives(self):
        material = ([{}] * 3, [{}] * 3, {'symbolicRows': {'semitoneShift': 2}})
        window = {'durationEstimate': 30, 'onsetEstimate': 2}
        with patch.object(self.cli, 'outro_profiles', return_value=[('alternate', {'midiReference': {}})]), \
                patch.object(self.cli, 'reference_material', return_value=material), \
                patch.object(self.cli, 'performance_times', side_effect=[[2, 12, 22], [2.1, 12, 22]]):
            detail = {}
            self.assertEqual(self.cli.measured_performance({}, window, material, None, Path('.'), False, detail),
                             [2, 12, 22])
            self.assertEqual(len(detail['instrumentalOutroChecks']), 1)
        with patch.object(self.cli, 'outro_profiles', return_value=[('alternate', {'midiReference': {}})]), \
                patch.object(self.cli, 'reference_material', return_value=material), \
                patch.object(self.cli, 'performance_times', side_effect=[[2, 12, 22], [2.6, 12, 22]]):
            with self.assertRaises(ValueError):
                self.cli.measured_performance({}, window, material, None, Path('.'), False, {})

    def ending_profile(self):
        return {'regions': ['r1', 'r2', 'r3'], 'readingBoundaries': [{'onsetIndex': 0}],
                'midiReference': {'file': 'body.mid', 'origin': {'file': 'original.mid'},
                                  'arrangement': self.recipe()},
                'opticalReference': {'order': ['A', 'B', 'A'], 'parts': {'A': [], 'B': []},
                                     'readingStarts': [[0, 0], [1, 0], [2, 0]]}}

    def ending_repeat(self):
        return {'version': 1, 'region': 'r3', 'opticalPart': 'A', 'quarterRange': [0, 4],
                'file': 'body-with-ending.mid', 'sha256': '0' * 64, 'reason': 'measured reprise of the final reading'}

    def test_recording_ending_can_only_append_the_final_part_without_changing_body_or_reading_boundaries(self):
        profile = self.ending_profile()
        original = copy.deepcopy(profile)
        actual = self.cli.ending_recording_profile(profile, {'endingReadingRepeat': self.ending_repeat()})
        self.assertEqual(profile, original)
        self.assertEqual(actual['midiReference']['arrangement']['sections'],
                         original['midiReference']['arrangement']['sections'] + [[0, 4]])
        self.assertEqual(actual['opticalReference']['order'], ['A', 'B', 'A', 'A'])
        self.assertEqual(actual['opticalReference']['readingStarts'], original['opticalReference']['readingStarts'])
        self.assertEqual(actual['readingBoundaries'], original['readingBoundaries'])
        self.assertIs(self.cli.ending_recording_profile(profile, {}), profile)

    def test_recording_ending_rejects_body_overrides_unreviewed_parts_and_reference_overwrites(self):
        profile = self.ending_profile()
        repeat = self.ending_repeat()
        for update in [{'version': True}, {'region': 'r2'}, {'opticalPart': 'B'}, {'reason': ''},
                       {'file': 'body.mid'}, {'file': 'original.mid'}, {'sha256': 'unreviewed'},
                       {'quarterRange': [0, 9]}, {'quarterRange': [False, 4]},
                       {'quarterRange': [0, float('nan')]}, {'sections': [[4, 8]]}]:
            with self.assertRaises(ValueError):
                self.cli.ending_recording_profile(profile, {'endingReadingRepeat': {**repeat, **update}})
        for update in [{'readingSectionReview': {}}, {'instrumentalOutroAlternatives': [{}]}]:
            with self.assertRaises(ValueError):
                self.cli.ending_recording_profile({**profile, **update}, {'endingReadingRepeat': repeat})
        with self.assertRaises(ValueError):
            self.cli.ending_recording_profile(profile, {'endingReadingRepeat': None})

    def test_failed_recording_ending_verification_cannot_export_or_reuse_the_body_timeline(self):
        profile = {**self.ending_profile(), 'sampling': {'durationEstimate': 30, 'onsetEstimate': 2}}
        profile['independentRecordings'] = [{'file': 'main.mp3', 'sha256': 'main',
            'sampling': profile['sampling'], 'endingReadingRepeat': self.ending_repeat()}]
        original = copy.deepcopy(profile)
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}, {'file': 'main.mp3', 'sha256': 'main'}]}
        definitions = [{'region': 'r' + str(i)} for i in range(3)]
        material = ([{}] * 3, [{}] * 3, {'symbolicRows': {'semitoneShift': 2}})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=({}, definitions)), \
                patch.object(self.cli, 'reference_material', return_value=material) as reference, \
                patch.object(self.cli, 'features', return_value=None), \
                patch.object(self.cli, 'performance_times', side_effect=[[2, 12, 22], ValueError('unverified ending')]), \
                patch.object(self.cli, 'export_result') as export:
            result = self.cli.generate(song, profile, Path(directory))
            self.assertIn('needs-calibration', result[1])
            export.assert_not_called()
            self.assertEqual(len(reference.call_args_list[1].args[0]['midiReference']['arrangement']['sections']), 4)
            self.assertEqual(profile, original)
            self.assertFalse((Path(directory) / 'ready/song.txt.json').exists())

    def reference_ending(self):
        profile = self.ending_profile()
        profile['opticalReference']['parts'] = {'A': [[0, 0, 8]], 'B': [[1, 0, 8]], 'E': [[0, 3, 8]],
            'otherVerse': [[1, 3, 8]], 'gap': [[0, 0, 2], [0, 5, 8]], 'tiny': [[0, 3, 6]]}
        profile['referenceEndingSequence'] = {'version': 1, 'region': 'r3',
            'sections': [{'opticalPart': 'A', 'quarterRange': [0, 4]},
                         {'opticalPart': 'E', 'quarterRange': [2, 4]}],
            'file': 'reference-with-ending.mid', 'sha256': '1' * 64,
            'reason': 'reference recording repeats the last region then its final phrase'}
        return profile

    def test_reference_ending_only_appends_printed_phrases_with_immutable_body_and_reading_boundaries(self):
        profile = self.reference_ending()
        original = copy.deepcopy(profile)
        result = self.cli.reference_ending_profile(profile)
        self.assertEqual(profile, original)
        self.assertEqual(result['midiReference']['arrangement']['sections'],
                         profile['midiReference']['arrangement']['sections'] + [[0, 4], [2, 4]])
        self.assertEqual(result['opticalReference']['order'], ['A', 'B', 'A', 'A', 'E'])
        self.assertEqual(result['opticalReference']['readingStarts'], profile['opticalReference']['readingStarts'])
        self.assertEqual(result['readingBoundaries'], profile['readingBoundaries'])
        for value in [None, {'version': True}, {'region': 'r2'}, {'file': 'body.mid'}, {'sha256': 'wrong'},
                      {'sections': []}, {'sections': [{'opticalPart': 'otherVerse', 'quarterRange': [2, 4]}]},
                      {'sections': [{'opticalPart': 'gap', 'quarterRange': [2, 4]}]},
                      {'sections': [{'opticalPart': 'tiny', 'quarterRange': [2, 4]}]},
                      {'sections': [{'opticalPart': 'E', 'quarterRange': [2, 20]}]},
                      {'sections': [{'opticalPart': 'E', 'quarterRange': [True, 4]}]},
                      {'sections': [{'opticalPart': 'E', 'quarterRange': [2, 4], 'skipCheck': True}]}]:
            bad = copy.deepcopy(profile)
            bad['referenceEndingSequence'] = value if value is None else {**profile['referenceEndingSequence'], **value}
            with self.assertRaises(ValueError):
                self.cli.reference_ending_profile(bad)

    def test_reference_ending_is_measured_without_leaking_the_reprise_into_another_recording(self):
        profile = self.reference_ending()
        profile.update(sampling={'durationEstimate': 30, 'onsetEstimate': 2}, independentRecordings=[
            {'file': 'main.mp3', 'sha256': 'main', 'sampling': {'durationEstimate': 30, 'onsetEstimate': 3}}])
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}, {'file': 'main.mp3', 'sha256': 'main'}]}
        definitions = [{'region': 'r' + str(i)} for i in range(3)]
        material = ([{}] * 3, [{}] * 3, {'symbolicRows': {'semitoneShift': 2}})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=({}, definitions)), \
                patch.object(self.cli, 'reference_material', return_value=material) as reference, \
                patch.object(self.cli, 'features', return_value=None), \
                patch.object(self.cli, 'performance_times', side_effect=[[2, 12, 22], [3, 13, 23]]), \
                patch.object(self.cli, 'export_result', return_value=('test', 'ready')) as export:
            self.assertEqual(self.cli.generate(song, profile, Path(directory)), ('test', 'ready'))
            self.assertEqual(len(reference.call_args_list), 2)
            self.assertEqual(reference.call_args_list[1].args[0]['midiReference']['file'], 'reference-with-ending.mid')
            evidence = export.call_args.args[4]
            self.assertEqual(export.call_args.args[8], [2, 12, 22])
            self.assertEqual(evidence['independentTimelines']['main.mp3']['times'], [3, 13, 23])
            independent = evidence['midiMatching']['independentRecordings'][0]['recordingReference']
            self.assertEqual(independent['midiReference']['file'], 'body.mid')

    def test_failed_reference_ending_material_stops_all_recordings_before_audio_or_export(self):
        profile = self.reference_ending()
        profile['sampling'] = {'durationEstimate': 30, 'onsetEstimate': 2}
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3', 'tracks': []}
        material = ([{}] * 3, [{}] * 3, {})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=({}, [])), \
                patch.object(self.cli, 'reference_material', side_effect=[material, ValueError('unverified ending hash')]), \
                patch.object(self.cli, 'features') as features, patch.object(self.cli, 'export_result') as export:
            self.assertIn('needs-calibration', self.cli.generate(song, profile, Path(directory))[1])
            features.assert_not_called()
            export.assert_not_called()

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
