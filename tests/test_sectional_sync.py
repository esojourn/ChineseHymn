import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
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

    def crossing_score(self):
        rows = [
            {'notes': [1, 2, 3, 4, 5, 5], 'pitchOffsets': [0] * 6,
             'rhythmWeights': [1, 1, 1, 1, 2, 1]},
            {'notes': [5, 6, 7, 1, 2, 3], 'pitchOffsets': [0] * 6,
             'rhythmWeights': [3, 1, 1, 1, 1, 1]}]
        review = {'ties': {'0': [5]}, 'crossSystemTies': {
            'version': 1, 'boundaries': [[0, 1]], 'reason': 'printed tie continues on next system'},
            'parts': {'verse': [[0, 0, 6], [1, 0, 6]]},
            'order': ['verse'], 'readingStarts': [[0, 0]]}
        return rows, review

    def test_cross_system_tie_preserves_one_sounding_note_and_complete_midi_duration(self):
        from sync_batch_printed_midi import printed_reference_bytes
        rows, review = self.crossing_score()
        unchanged = copy.deepcopy((rows, review))
        actual = optical_performance_rows(rows, review)
        expected_notes = [1, 2, 3, 4, 5, 6, 7, 1, 2, 3]
        expected_beats = [1, 1, 1, 1, 6, 1, 1, 1, 1, 1]
        self.assertEqual(actual, [{'notes': expected_notes, 'pitchOffsets': [0] * 10,
                                   'rhythmWeights': expected_beats}])
        self.assertEqual((rows, review), unchanged)
        reference = {'mode': 'symbolic-melody-and-optical', 'targetSha256': 'printed',
                     'printedRhythmReference': {'sourceSha256': 'printed', 'tonicMidi': 60,
                        'numberedRowsAndQuarterBeats': [[(1, 1), (2, 1), (3, 1), (4, 1),
                            (5, 6), (6, 1), (7, 1), (1, 1), (2, 1), (3, 1)]]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'independent.mid'
            path.write_bytes(printed_reference_bytes(reference, 'printed'))
            duration, _, melody = self.cli.midi.midi_features(path)
        self.assertEqual(duration, 7.5)
        self.assertEqual(melody['onsetTimes'], [0, .5, 1, 1.5, 2, 5, 5.5, 6, 6.5, 7])
        self.assertEqual(melody['onsetPitches'], [0, 2, 4, 5, 7, 9, 11, 0, 2, 4])
        # Without an explicit cross-system declaration, the same heads retain
        # their former two onsets and weights.
        review.pop('crossSystemTies')
        legacy = optical_performance_rows(rows, review)[0]
        self.assertEqual(legacy['notes'][4:6], [5, 5])
        self.assertEqual(legacy['rhythmWeights'][4:6], [3, 3])

    def test_cross_system_ties_reject_changed_pitches_rests_and_invalid_source_boundaries(self):
        for key, index, value in [('notes', 0, 4), ('notes', 0, 0), ('pitchOffsets', 0, 1)]:
            rows, review = self.crossing_score()
            rows[1][key][index] = value
            with self.assertRaises(ValueError):
                optical_performance_rows(rows, review)
        for changes in [{'version': True}, {'version': 2}, {'reason': ''}, {'boundaries': []},
                        {'boundaries': [[0, 0]]}, {'boundaries': [[0, 2]]},
                        {'boundaries': [[True, 1]]}, {'boundaries': [[0, 1], [0, 1]]},
                        {'boundaries': [[1, 0]]}, {'boundaries': [[-1, 0]]}, {'seconds': 2}]:
            rows, review = self.crossing_score()
            review['crossSystemTies'].update(changes)
            with self.assertRaises(ValueError):
                optical_performance_rows(rows, review)

    def test_cross_system_tie_cannot_be_split_omitted_or_joined_across_an_unrelated_range(self):
        for ranges in [[[1, 0, 6]], [[0, 0, 6]], [[0, 0, 5], [1, 0, 6]],
                       [[0, 0, 6], [1, 1, 6]], [[0, 0, 6], [0, 0, 1], [1, 0, 6]]]:
            rows, review = self.crossing_score()
            review['parts']['verse'] = ranges
            with self.assertRaises(ValueError):
                optical_performance_rows(rows, review)
        rows, review = self.crossing_score()
        review['ties']['1'] = [0]
        with self.assertRaises(ValueError):
            optical_performance_rows(rows, review)

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

    def svg_reference(self, cache):
        source = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text>1 2 3</text></svg>'
        (cache / 'original score.svg').write_bytes(source)
        return {'file': 'score.png', 'sha256': hashlib.sha256(b'reviewed raster').hexdigest(),
                'svgSource': {'file': 'original score.svg', 'url': 'https://example.test/score.svg',
                              'sha256': hashlib.sha256(source).hexdigest()},
                'renderSvg': {'version': 1, 'scale': 3, 'playwrightVersion': '1.64.1',
                              'chromiumVersion': '156.0.8078.4'}}

    def test_svg_rebuild_verifies_source_and_pixels_before_replacing_the_reviewed_raster(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            item = self.svg_reference(cache)
            def render(command, **kwargs):
                request = json.loads(kwargs['input'])
                self.assertEqual(command[0], 'node')
                self.assertEqual(Path(request['source']).read_bytes(), (cache / 'original score.svg').read_bytes())
                self.assertEqual(request['scale'], 3)
                self.assertTrue(kwargs['check'])
                Path(request['destination']).write_bytes(b'reviewed raster')
            with patch.object(self.cli.subprocess, 'run', side_effect=render) as renderer:
                self.assertEqual(self.cli.pinned_file(cache, item, True), cache / 'score.png')
                self.assertEqual((cache / 'score.png').read_bytes(), b'reviewed raster')
                self.assertEqual(self.cli.pinned_file(cache, item, False), cache / 'score.png')
                self.assertEqual(renderer.call_count, 1)
                with self.assertRaisesRegex(ValueError, '渲染图片'):
                    self.cli.pinned_file(cache, {**item, 'sha256': 'changed pixels'}, True)
                self.assertEqual((cache / 'score.png').read_bytes(), b'reviewed raster')
                (cache / 'original score.svg').write_bytes(b'changed original')
                for prepare in [False, True]:
                    with self.assertRaises(ValueError):
                        self.cli.pinned_file(cache, item, prepare)
                self.assertEqual(renderer.call_count, 2)
                self.assertEqual((cache / 'score.png').read_bytes(), b'reviewed raster')

    def test_svg_renderer_failure_and_non_svg_source_cannot_replace_the_reviewed_raster(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            item = self.svg_reference(cache)
            (cache / 'score.png').write_bytes(b'reviewed raster')
            for failure in [subprocess.CalledProcessError(1, ['node']), subprocess.TimeoutExpired(['node'], 60)]:
                with patch.object(self.cli.subprocess, 'run', side_effect=failure):
                    with self.assertRaisesRegex(ValueError, '渲染器'):
                        self.cli.pinned_file(cache, item, True)
                self.assertEqual((cache / 'score.png').read_bytes(), b'reviewed raster')
            for source in [b'<html/>', b'<svg']:
                (cache / 'original score.svg').write_bytes(source)
                changed = {**item, 'svgSource': {**item['svgSource'], 'sha256': hashlib.sha256(source).hexdigest()}}
                with patch.object(self.cli.subprocess, 'run') as renderer:
                    with self.assertRaisesRegex(ValueError, 'SVG 谱面'):
                        self.cli.pinned_file(cache, changed, True)
                    renderer.assert_not_called()
                self.assertEqual((cache / 'score.png').read_bytes(), b'reviewed raster')

    def test_reference_download_carries_the_reviewed_public_page_and_still_checks_the_original_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            data = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
            item = {'file': 'original.svg', 'url': 'https://example.test/score.svg',
                    'referer': 'https://example.test/hymn/525', 'sha256': hashlib.sha256(data).hexdigest()}
            with patch.object(self.cli.urllib.request, 'urlopen', return_value=io.BytesIO(data)) as download:
                self.assertEqual(self.cli.pinned_file(cache, item, True).read_bytes(), data)
                self.assertEqual(download.call_args.args[0].get_header('Referer'), item['referer'])
            (cache / item['file']).unlink()
            with patch.object(self.cli.urllib.request, 'urlopen', return_value=io.BytesIO(b'changed original')):
                with self.assertRaisesRegex(ValueError, '下载的原始'):
                    self.cli.pinned_file(cache, item, True)
                self.assertFalse((cache / item['file']).exists())
            for referer in [False, '', 'file:///private', 'https://example.test/\nInjected: value']:
                with patch.object(self.cli.urllib.request, 'urlopen') as download:
                    with self.assertRaisesRegex(ValueError, '来源页'):
                        self.cli.pinned_file(cache, {**item, 'referer': referer}, True)
                    download.assert_not_called()

    def test_svg_rebuild_rejects_unpinned_or_recursive_transforms(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            item = self.svg_reference(cache)
            updates = [{'renderPdf': {}}, {'stackImages': {}}]
            for change in [{'version': True}, {'version': 2}, {'scale': True}, {'scale': 1.5},
                           {'scale': 0}, {'scale': 5}, {'crop': [0, 10]}, {'playwrightVersion': ''},
                           {'chromiumVersion': None}]:
                updates.append({'renderSvg': {**item['renderSvg'], **change}})
            for change in [{'renderSvg': {}}, {'file': 'score.png'}, {'sha256': ''}]:
                updates.append({'svgSource': {**item['svgSource'], **change}})
            for update in updates:
                with self.subTest(update=update), patch.object(self.cli.subprocess, 'run') as renderer:
                    with self.assertRaisesRegex(ValueError, 'SVG 原谱'):
                        self.cli.pinned_file(cache, {**item, **update}, True)
                    renderer.assert_not_called()

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
                self.assertEqual(self.cli.validate_source(song, profile),
                                 self.cli.validate_source(song, {**profile, 'language': 'zh'}))
                for change in [{'tracks': song['tracks'][1:]}, {'sourceHash': 'changed'},
                               {'regions': ['r1', 'r2']}, {'audioFile': 'main.mp3'},
                               {'regionOpenings': ['第一段歌词', '另外的歌词', '第三段歌词']}]:
                    with self.assertRaises(ValueError):
                        self.cli.validate_source(song, {**profile, **change})

    def test_english_source_requires_explicit_language_and_keeps_all_original_region_and_recording_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            text = 'English hymn\n\nWhen peace like a river\n\nIt is well with my soul\n\nMy sin not in part but the whole\n\n--\nTest artist - Album credit'
            (root / 'data/song.txt').write_text(text)
            song = {'type': 'text', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                    'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}]}
            profile = {'version': 1, 'language': 'en', 'sourceHash': 'source', 'tracks': song['tracks'],
                       'audioFile': 'lead.mp3', 'regions': ['r1', 'r2', 'r3'],
                       'regionOpenings': ['WHEN PEACE', 'It is well', 'My sin not in part'],
                       'sampling': {'durationEstimate': 30, 'onsetEstimate': 0}}
            with patch.object(self.cli.batch, 'ROOT', root):
                source, definitions = self.cli.validate_source(song, profile)
                self.assertEqual(source['language'], 'en')
                self.assertEqual([item['region'] for item in definitions], ['r1', 'r2', 'r3'])
                self.assertEqual(source['regions']['r3'], {'kind': 'text', 'startLine': 6, 'endLine': 6})
                for update in [{'sourceHash': 'stale'}, {'tracks': []}, {'audioFile': 'other.mp3'},
                               {'regions': ['r1', 'r3']}, {'regions': ['r1', 'r3', 'r2']},
                               {'regionOpenings': ['When peace', 'Other lyric', 'My sin']},
                               {'regionOpenings': ['When peace', 'It', 'My sin']}]:
                    with self.assertRaises(ValueError):
                        self.cli.validate_source(song, {**profile, **update})
                missing = {key: value for key, value in profile.items() if key != 'language'}
                with self.assertRaises(ValueError):
                    self.cli.validate_source(song, missing)
                for language in [None, True, 'fr', {}, '']:
                    with self.assertRaisesRegex(ValueError, '中文或英文'):
                        self.cli.validate_source(song, {**profile, 'language': language})
                self.assertEqual((root / 'data/song.txt').read_text(), text)

    def notation(self):
        row = {'notes': [0, 1, 1, 2, 3, 4], 'pitchOffsets': [0] * 6, 'rhythmWeights': [1] * 6}
        review = {'ties': {'0': [2]},
                  'tuplets': [{'system': 0, 'noteIndices': [3, 4, 5], 'actualNotes': 3, 'normalNotes': 2}],
                  'parts': {'A': [[0, 0, 6]]}, 'order': ['A', 'A', 'A'],
                  'readingStarts': [[0, 1], [1, 1], [2, 1]]}
        return [row], review

    def musical_edge_fixture(self):
        parts = [[1, 2, 3, 4, 5, 6, 7], [7, 6, 5, 4, 3, 2, 1], [1, 3, 5, 1, 3, 5, 1]]
        definitions = [{'region': 'r' + str(index)} for index in range(10)]
        source = {'language': 'en', 'verses': [[item] for item in definitions]}
        review = {'version': 1, 'paragraphsPerVerse': [3, 3, 4], 'lastVerseRepeatsFinalReading': True,
                  'reason': 'Three complete musical stanzas, printed as separate original paragraphs.'}
        profile = {'language': 'en', 'musicalVerseEdges': review, 'midiReference': {'origin': {
            'kind': 'printed-numbered-score', 'printedRhythmReference': {
                'numberedRowsAndQuarterBeats': [[(note, 1) for note in part] for part in parts]}}}}
        performed = parts * 3 + [parts[-1]]
        optical = [{'notes': notes[:], 'pitchOffsets': [0] * len(notes), 'rhythmWeights': [1] * len(notes)}
                   for notes in performed]
        scale = [0, 2, 4, 5, 7, 9, 11]
        melody = [{'notes': [1] * len(notes), 'pitchOffsets': [scale[note - 1] for note in notes],
                   'spatialWeights': [1] * len(notes)} for notes in performed]
        return source, profile, (melody, optical, {'symbolicRows': {'semitoneShift': 0}}), definitions

    def initial_context_fixture(self):
        source, profile, material, definitions = self.musical_edge_fixture()
        source['verses'] = [definitions[:3], definitions[3:6], definitions[6:]]
        profile.pop('musicalVerseEdges')
        profile['initialVerseContext'] = {'version': 1, 'reason': 'complete first printed stanza'}
        profile['midiReference']['arrangement'] = {'quarterLength': 21, 'sections': [[0, 21]] * 3}
        material[2]['symbolicRows']['editCost'] = 0
        return source, profile, material, definitions

    def test_initial_context_requires_the_complete_original_printed_stanza_without_mutation(self):
        source, profile, material, _ = self.initial_context_fixture()
        original = copy.deepcopy((source, profile, material))
        sections, proof = self.cli.initial_verse_context(source, profile, material)
        self.assertEqual(sections, [0, 1, 2])
        self.assertEqual(proof['completePrintedSoundedNotes'], 21)
        self.assertEqual((source, profile, material), original)
        self.assertIsNone(self.cli.initial_verse_context(source, {}, material))
        for key in ['notes', 'pitchOffsets', 'rhythmWeights']:
            changed = copy.deepcopy(material)
            changed[1][1][key][0] += 1
            with self.assertRaises(ValueError):
                self.cli.initial_verse_context(source, profile, changed)
        for change in [{'version': True}, {'reason': ''}, {'at': 15}, {'readings': [0, 1]}]:
            bad = copy.deepcopy(profile)
            bad['initialVerseContext'].update(change)
            with self.assertRaises(ValueError):
                self.cli.initial_verse_context(source, bad, material)
        for key in ['sourceVerseContext', 'readingSectionReview', 'musicalVerseEdges', 'vocalInterludes']:
            with self.assertRaises(ValueError):
                self.cli.initial_verse_context(source, {**profile, key: {}}, material)
        for update in [{'quarterLength': 22}, {'sections': [[1, 21]]}, {'sections': []}]:
            bad = copy.deepcopy(profile)
            bad['midiReference']['arrangement'].update(update)
            with self.assertRaises(ValueError):
                self.cli.initial_verse_context(source, bad, material)
        bad = copy.deepcopy(material)
        bad[2]['symbolicRows']['editCost'] = 1
        with self.assertRaises(ValueError):
            self.cli.initial_verse_context(source, profile, bad)
        with self.assertRaises(ValueError):
            self.cli.initial_verse_context({**source, 'verses': [source['verses'][0][:2], *source['verses'][1:]]},
                                           profile, material)

    def test_initial_audio_context_uses_first_onset_and_retains_all_cost_and_time_gates(self):
        rows = [{'notes': [1], 'pitchOffsets': [0], 'spatialWeights': [1], 'rhythmWeights': [1]}] * 6
        target = (np.array([0., 70.]), None)
        calls = []
        failure = {}
        def match(query, chroma, times, duration, onset, weight):
            calls.append((len(query), duration, onset, weight))
            starts = [5. + 10 * i for i in range(6)] if len(query) == 6 else [onset + 10 * i for i in range(len(query))]
            best = {'cost': .1, 'key': 0, 'starts': starts, 'span': duration}
            if len(query) == 3:
                best.update(failure)
            return {'best': best, 'runnerUp': {'cost': .4}}
        evidence = {}
        actual = performance_times(rows, rows, target, 60, 5, 0, match, evidence,
                                   previous_context=True, initial_verse_sections=[0, 1, 2])
        self.assertEqual(actual, [5, 15, 25, 35, 45, 55])
        self.assertEqual(calls[2][:3], (3, 30, 5))
        self.assertEqual(evidence['local'][0]['initialVerseContext']['targetRow'], 0)
        self.assertEqual(evidence['local'][1]['previousReadingContext']['sections'], [0])
        for update in [{'cost': .36}, {'key': 1}, {'span': 20}, {'starts': [5.6, 15, 25]}]:
            failure.clear()
            failure.update(update)
            with self.assertRaises(ValueError):
                performance_times(rows, rows, target, 60, 5, 0, match, {},
                                  previous_context=True, initial_verse_sections=[0, 1, 2])
        for indices in [[0, 1], [0, 2, 3], [1, 2, 3], [False, 1, 2], list(range(6))]:
            with self.assertRaises(ValueError):
                performance_times(rows, rows, target, 60, 5, 0, match, {}, initial_verse_sections=indices)

    def test_generation_passes_initial_stanza_context_to_every_recording_and_rejects_disputed_audio(self):
        source, profile, material, definitions = self.initial_context_fixture()
        profile['sampling'] = {'durationEstimate': 60, 'onsetEstimate': 5}
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}]}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=(source, definitions)), \
                patch.object(self.cli, 'reference_material', return_value=material), \
                patch.object(self.cli, 'features', return_value=None), \
                patch.object(self.cli, 'performance_times', side_effect=ValueError('disputed initial audio')) as measure, \
                patch.object(self.cli, 'export_result') as export:
            self.assertIn('disputed initial audio', self.cli.generate(song, profile, Path(directory))[1])
            self.assertEqual(measure.call_args.kwargs['initial_verse_sections'], [0, 1, 2])
            export.assert_not_called()
            bad = copy.deepcopy(profile)
            bad['midiReference']['arrangement']['sections'][0] = [1, 21]
            measure.reset_mock()
            self.cli.generate(song, bad, Path(directory))
            measure.assert_not_called()
            export.assert_not_called()

    def test_musical_edges_require_every_printed_note_and_cover_all_paragraphs_without_changing_queries(self):
        source, profile, material, _ = self.musical_edge_fixture()
        profile['vocalInterludes'] = {'version': 1, 'afterVerses': [
            {'verse': 3, 'quarterBeats': 4}, {'verse': 6, 'quarterBeats': 4}], 'reason': 'between complete stanzas'}
        # Interlude silence contributes to the real query but cannot replace a printed melody note.
        for template, weight in [(material[0], 'spatialWeights'), (material[1], 'rhythmWeights')]:
            for index in [2, 5]:
                template[index]['notes'].append(0)
                template[index]['pitchOffsets'].append(0)
                template[index][weight].append(4)
        original = copy.deepcopy((source, profile, material))
        groups, proof = self.cli.musical_verse_edges(source, profile, material)
        self.assertEqual(groups, [[0, 1, 2], [3, 4, 5], [6, 7, 8, 9]])
        self.assertEqual(proof['completePrintedSoundedNotes'], 21)
        self.assertTrue(proof['lastVerseRepeatsFinalReading'])
        self.assertEqual((source, profile, material), original)
        self.assertIsNone(self.cli.musical_verse_edges(source, {}, material))

    def test_musical_edges_reject_partial_printed_tunes_changed_notes_rhythm_or_extra_final_repeats(self):
        source, profile, material, _ = self.musical_edge_fixture()
        for index, key in [(0, 'notes'), (0, 'pitchOffsets'), (0, 'spatialWeights'),
                           (1, 'notes'), (1, 'pitchOffsets'), (1, 'rhythmWeights')]:
            for row in [0, 4, 9]:
                bad = copy.deepcopy(material)
                bad[index][row][key][0] += 1
                with self.assertRaises(ValueError):
                    self.cli.musical_verse_edges(source, profile, bad)
        bad = copy.deepcopy(profile)
        bad['midiReference']['origin']['printedRhythmReference']['numberedRowsAndQuarterBeats'].pop()
        with self.assertRaisesRegex(ValueError, '全部音高与时值'):
            self.cli.musical_verse_edges(source, bad, material)
        empty = copy.deepcopy(material)
        for row in empty[0]:
            for key in row:
                row[key] = []
        with self.assertRaisesRegex(ValueError, '发声旋律'):
            self.cli.musical_verse_edges(source, profile, empty)
        changed = copy.deepcopy(profile)
        changed['musicalVerseEdges'].pop('lastVerseRepeatsFinalReading')
        with self.assertRaises(ValueError):
            self.cli.musical_verse_edges(source, changed, material)
        short_source = {**source, 'verses': source['verses'][:-1]}
        short_profile = copy.deepcopy(changed)
        short_profile['musicalVerseEdges']['paragraphsPerVerse'] = [3, 3, 3]
        self.assertEqual(self.cli.musical_verse_edges(short_source, short_profile,
            (material[0][:-1], material[1][:-1], {}))[0], [[0, 1, 2], [3, 4, 5], [6, 7, 8]])

    def test_musical_edges_reject_manual_times_missing_paragraphs_and_conflicting_contexts(self):
        source, profile, material, _ = self.musical_edge_fixture()
        for change in [{'version': True}, {'paragraphsPerVerse': [3, 3, 3]},
                       {'paragraphsPerVerse': [True, 3, 6]}, {'paragraphsPerVerse': [2, 4, 4]},
                       {'paragraphsPerVerse': [3.0, 3, 4]}, {'lastVerseRepeatsFinalReading': False},
                       {'at': 10}, {'groups': [[0, 2]]}, {'reason': ''}]:
            with self.assertRaises(ValueError):
                self.cli.musical_verse_edges(source,
                    {**profile, 'musicalVerseEdges': {**profile['musicalVerseEdges'], **change}}, material)
        for key in ['sourceVerseEdgeReview', 'sourceVerseContext', 'readingSectionReview',
                    'sourceVerseMusicRange', 'referenceEndingSequence', 'instrumentalOutroReview', 'instrumentalOutroAlternatives']:
            with self.assertRaises(ValueError):
                self.cli.musical_verse_edges(source, {**profile, key: {}}, material)
        for change in [{'language': 'zh'}, {'verses': source['verses'][1:]},
                       {'verses': [source['verses'][0] * 2] + source['verses'][1:]}]:
            with self.assertRaises(ValueError):
                self.cli.musical_verse_edges({**source, **change}, profile, material)
        with self.assertRaisesRegex(ValueError, '完整音乐诗节边界'):
            self.cli.musical_verse_edges(source, {**profile, 'vocalInterludes': {
                'afterVerses': [{'verse': 2, 'quarterBeats': 4}]}}, material)

    def test_generation_applies_verified_musical_edges_to_audio_checks_and_never_exports_disputed_audio(self):
        source, profile, material, definitions = self.musical_edge_fixture()
        profile['sampling'] = {'durationEstimate': 30, 'onsetEstimate': 1}
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}]}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=(source, definitions)), \
                patch.object(self.cli, 'reference_material', return_value=material), \
                patch.object(self.cli, 'features', return_value=None), \
                patch.object(self.cli, 'performance_times', side_effect=ValueError('disputed audio path')) as measure, \
                patch.object(self.cli, 'export_result') as export:
            self.assertIn('disputed audio path', self.cli.generate(song, profile, Path(directory))[1])
            self.assertEqual(measure.call_args.kwargs['edge_verses'], [[0, 1, 2], [3, 4, 5], [6, 7, 8, 9]])
            export.assert_not_called()
            bad = copy.deepcopy(profile)
            bad['musicalVerseEdges']['paragraphsPerVerse'] = [3, 3, 3]
            measure.reset_mock()
            self.cli.generate(song, bad, Path(directory))
            measure.assert_not_called()
            export.assert_not_called()

    def test_optical_form_preserves_rests_ties_and_tuplets_without_mutating_raw_recognition(self):
        rows, review = self.notation()
        original = copy.deepcopy(rows)
        actual = optical_performance_rows(rows, review)
        self.assertEqual(rows, original)
        self.assertEqual(actual[0]['notes'], [1, 2, 3, 4, 0])
        self.assertAlmostEqual(sum(sum(row['rhythmWeights']) for row in actual), 14)
        self.assertEqual(actual[0]['rhythmWeights'][0], 2)
        self.assertAlmostEqual(actual[0]['rhythmWeights'][1], 2 / 3)

    def test_mixed_quarter_eighth_triplets_preserve_the_printed_unit_and_reject_partial_groups(self):
        rows = [{'notes': [3, 2, 4, 5], 'pitchOffsets': [0, 1, 0, 0],
                 'rhythmWeights': [1, .5, .5, 1]}]
        groups = [{'system': 0, 'noteIndices': pair, 'actualNotes': 3, 'normalNotes': 2}
                  for pair in [[0, 1], [2, 3]]]
        review = {'tuplets': groups, 'parts': {'A': [[0, 0, 4]]}, 'order': ['A'], 'readingStarts': [[0, 0]]}
        original = copy.deepcopy((rows, review))
        actual = optical_performance_rows(rows, review)[0]
        self.assertEqual(actual['notes'], [3, 2, 4, 5])
        np.testing.assert_allclose(actual['rhythmWeights'], [2 / 3, 1 / 3, 1 / 3, 2 / 3])
        self.assertEqual(sum(actual['rhythmWeights']), 2)
        self.assertEqual((rows, review), original)
        for weights in [[1, 1, .5, 1], [.5, .5, .5, 1], [0, .5, .5, 1]]:
            with self.assertRaises(ValueError):
                optical_performance_rows([{**rows[0], 'rhythmWeights': weights}], review)
        for change in [{'parts': {'A': [[0, 0, 1]]}}, {'tuplets': groups + groups[:1]}]:
            with self.assertRaises(ValueError):
                optical_performance_rows(rows, {**review, **change})

    def short_form_fixture(self):
        source, profile, material, definitions = self.initial_context_fixture()
        definitions = definitions[:2]
        source['verses'] = [definitions]
        profile.pop('initialVerseContext')
        profile['shortFormReview'] = {'version': 1, 'reason': 'One complete short stanza, two original reading regions.'}
        profile['midiReference']['origin']['printedRhythmReference']['numberedRowsAndQuarterBeats'] = [
            [(note, 1) for note in row['notes']] for row in material[1][:2]]
        profile['midiReference']['arrangement'] = {'quarterLength': 14, 'sections': [[0, 14]]}
        return source, profile, (material[0][:2], material[1][:2], material[2]), definitions

    def test_short_form_requires_one_complete_zero_edit_print_and_two_original_regions(self):
        source, profile, material, _ = self.short_form_fixture()
        original = copy.deepcopy((source, profile, material))
        proof = self.cli.validate_short_form(source, profile, material)
        self.assertEqual(proof['readings'], [0, 1])
        self.assertEqual(proof['completePrintedSoundedNotes'], 14)
        self.assertEqual((source, profile, material), original)
        self.assertIsNone(self.cli.validate_short_form(source, {}, material))
        for review in [None, {'version': True, 'reason': 'x'}, {'version': 1, 'reason': ''},
                       {'version': 1, 'reason': 'x', 'at': 10}]:
            with self.assertRaises(ValueError):
                self.cli.validate_short_form(source, {**profile, 'shortFormReview': review}, material)
        for key in ['sourceVerseContext', 'readingSectionReview', 'initialVerseContext', 'vocalInterludes',
                    'sourceVerseEdgeReview', 'instrumentalOutroReview']:
            with self.assertRaises(ValueError):
                self.cli.validate_short_form(source, {**profile, key: {}}, material)
        for verses in [[source['verses'][0][:1]], [[source['verses'][0][0]], [source['verses'][0][1]]]]:
            with self.assertRaises(ValueError):
                self.cli.validate_short_form({**source, 'verses': verses}, profile, material)
        for change in [{'sections': [[0, 7]]}, {'sections': [[0, 14], [0, 14]]}, {'quarterLength': 15}]:
            bad = copy.deepcopy(profile)
            bad['midiReference']['arrangement'].update(change)
            with self.assertRaises(ValueError):
                self.cli.validate_short_form(source, bad, material)
        bad = copy.deepcopy(material)
        bad[2]['symbolicRows']['editCost'] = 1
        with self.assertRaises(ValueError):
            self.cli.validate_short_form(source, profile, bad)
        bad = copy.deepcopy(material)
        bad[1][1]['pitchOffsets'][0] = 1
        with self.assertRaises(ValueError):
            self.cli.validate_short_form(source, profile, bad)

    def test_two_original_paragraphs_require_explicit_short_form_binding_to_the_same_complete_print(self):
        source, profile, material, definitions = self.short_form_fixture()
        source['verses'] = [[item] for item in definitions]
        original = copy.deepcopy((source, profile, material))
        with self.assertRaises(ValueError):
            self.cli.validate_short_form(source, profile, material)
        explicit = copy.deepcopy(profile)
        explicit['shortFormReview']['sourceParagraphs'] = 2
        self.assertEqual(self.cli.validate_short_form(source, explicit, material)['completePrintedSoundedNotes'], 14)
        self.assertEqual((source, profile, material), original)
        for count in [True, 1, 3, 2., '2']:
            bad = copy.deepcopy(explicit)
            bad['shortFormReview']['sourceParagraphs'] = count
            with self.assertRaises(ValueError):
                self.cli.validate_short_form(source, bad, material)
        with self.assertRaises(ValueError):
            self.cli.validate_short_form({**source, 'verses': [definitions]}, explicit, material)

    def test_short_form_final_reprise_is_verified_separately_without_changing_original_regions(self):
        source, profile, material, definitions = self.short_form_fixture()
        profile['regions'] = [item['region'] for item in definitions]
        profile['readingBoundaries'] = [{'onsetIndex': 0}, {'onsetIndex': 7}]
        profile['opticalReference'] = {'parts': {'A': [[0, 0, 7]], 'B': [[1, 0, 7]]},
                                     'order': ['A', 'B'], 'readingStarts': [[0, 0], [1, 0]]}
        profile['midiReference'].update(file='body.mid', sha256='a' * 64)
        profile['midiReference']['origin']['file'] = 'original.mid'
        profile['referenceEndingSequence'] = {'version': 1, 'region': profile['regions'][-1],
            'sections': [{'opticalPart': 'B', 'quarterRange': [7, 14]}],
            'file': 'ending.mid', 'sha256': 'b' * 64, 'reason': 'The recording reprises the complete final reading.'}
        original = copy.deepcopy((source, profile, material))
        self.assertIsNotNone(self.cli.validate_short_form(source, profile, material))
        ending = self.cli.reference_ending_profile(profile)
        self.assertEqual(ending['midiReference']['arrangement']['sections'], [[0, 14], [7, 14]])
        self.assertEqual(ending['opticalReference']['order'], ['A', 'B', 'B'])
        self.assertEqual(ending['opticalReference']['readingStarts'], profile['opticalReference']['readingStarts'])
        self.assertEqual(ending['regions'], profile['regions'])
        self.assertEqual((source, profile, material), original)
        for change in [{'region': profile['regions'][0]},
                       {'sections': [{'opticalPart': 'A', 'quarterRange': [0, 7]}]}]:
            bad = copy.deepcopy(profile)
            bad['referenceEndingSequence'].update(change)
            with self.assertRaises(ValueError):
                self.cli.reference_ending_profile(bad)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.cli, 'validate_source', return_value=(source, definitions)), \
                patch.object(self.cli, 'reference_material') as read, \
                patch.object(self.cli, 'export_result') as export:
            self.assertIn('末个阅读区域', self.cli.generate(
                {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'main.mp3',
                 'tracks': [{'file': 'main.mp3', 'sha256': 'main'}]}, bad, Path(directory))[1])
            read.assert_not_called()
            export.assert_not_called()

    def test_short_form_keeps_all_audio_gates_and_audits_intro_against_the_complete_stanza(self):
        rows = [{'notes': [1], 'pitchOffsets': [0], 'spatialWeights': [1], 'rhythmWeights': [1]}] * 2
        target = (np.array([0., 40.]), None)
        failure = {}
        def match(query, chroma, times, duration, onset, weight):
            starts = [12., 23.] if len(query) == 2 else [onset]
            best = {'cost': .1, 'key': 0, 'starts': starts, 'span': duration}
            if len(query) == 1:
                best.update(failure)
            return {'best': best, 'runnerUp': {'cost': .4}}
        evidence = {}
        self.assertEqual(performance_times(rows, rows, target, 22, 12, 0, match, evidence,
                         previous_context=True, short_form=True), [12, 23])
        self.assertEqual(evidence['completeVerseSpans'], [22])
        self.assertEqual(len(evidence['local']), 2)
        with self.assertRaises(ValueError):
            performance_times(rows, rows, target, 22, 12, 0, match, {})
        for update in [{'cost': .36}, {'cost': .35}, {'key': 1}, {'span': 7}, {'starts': [12.6]}]:
            failure.clear()
            failure.update(update)
            with self.assertRaises(ValueError):
                performance_times(rows, rows, target, 22, 12, 0, match, {}, short_form=True)
        failure.clear()
        with self.assertRaisesRegex(ValueError, '录音首尾'):
            performance_times(rows, rows, (np.array([0., 60.]), None), 22, 12, 0, match, {}, short_form=True)
        for flag in [None, 1, 'true']:
            with self.assertRaises(ValueError):
                performance_times(rows, rows, target, 22, 12, 0, match, {}, short_form=flag)

    def test_short_form_source_binding_and_generation_cannot_export_disputed_audio(self):
        source, profile, material, definitions = self.short_form_fixture()
        profile['sampling'] = {'durationEstimate': 22, 'onsetEstimate': 12}
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'main.mp3',
                'tracks': [{'file': 'main.mp3', 'sha256': 'main'}], 'type': 'text'}
        profile.update(version=1, sourceHash='source', tracks=song['tracks'], audioFile='main.mp3',
                       language='zh', regions=['r1', 'r2'], regionOpenings=['第一部分歌词', '第二部分歌词'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'data/song.txt').write_text('标题\n\n第一部分歌词\n第二行歌词\n第三行歌词\n第四行歌词\n第二部分歌词\n第六行歌词')
            with patch.object(self.cli.batch, 'ROOT', root):
                self.assertEqual(len(self.cli.validate_source(song, profile)[1]), 2)
                with self.assertRaises(ValueError):
                    self.cli.validate_source(song, {key: value for key, value in profile.items() if key != 'shortFormReview'})
            with patch.object(self.cli, 'validate_source', return_value=(source, definitions)), \
                    patch.object(self.cli, 'reference_material', return_value=material), \
                    patch.object(self.cli, 'features', return_value=None), \
                    patch.object(self.cli, 'performance_times', side_effect=ValueError('disputed short audio')) as measure, \
                    patch.object(self.cli, 'export_result') as export:
                self.assertIn('needs-calibration', self.cli.generate(song, profile, root)[1])
                self.assertTrue(measure.call_args.kwargs['short_form'])
                export.assert_not_called()
                self.assertFalse((root / 'ready/song.txt.json').exists())

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

    def test_edge_verse_review_uses_source_groups_even_with_a_longer_final_verse(self):
        source = {'verses': [[{}, {}], [{}, {}, {}]]}
        review = {'version': 1, 'reason': 'last source verse repeats more lines'}
        self.assertIsNone(self.cli.source_verse_edges(source, {}))
        self.assertEqual(self.cli.source_verse_edges(source, {'sourceVerseEdgeReview': review}),
                         [[0, 1], [2, 3, 4]])
        for value in [{}, {**review, 'version': True}, {**review, 'reason': ''},
                      {**review, 'groups': [[0], [1, 2, 3, 4]]}]:
            with self.assertRaises(ValueError):
                self.cli.source_verse_edges(source, {'sourceVerseEdgeReview': value})
        with self.assertRaises(ValueError):
            self.cli.source_verse_edges(source, {'sourceVerseEdgeReview': review, 'sourceVerseContext': review})

    def test_complete_verse_edge_check_preserves_four_paths_and_rejects_a_missing_whole_verse(self):
        starts = [14, 24, 34, 44, 54]
        def matcher(rows, chroma, times, duration, onset, weight):
            return {'best': {'cost': .2, 'key': 1 if weight == 'spatialWeights' else 3,
                             'starts': starts if len(rows) == 5 else [onset], 'span': duration},
                    'runnerUp': {'cost': .4}}
        target = np.arange(70), np.zeros((12, 70))
        groups = [[0, 1], [2, 3, 4]]
        proof = {}
        self.assertEqual(performance_times([{}] * 5, [{}] * 5, target, 50, 14, 2,
                         matcher, proof, edge_verses=groups), starts)
        self.assertEqual(proof['completeVerseSpans'], [20, 30])
        self.assertEqual(len(proof['local']), 5)
        with self.assertRaises(ValueError):
            performance_times([{}] * 5, [{}] * 5, target, 50, 14, 2, matcher, {})
        with self.assertRaises(ValueError):
            performance_times([{}] * 5, [{}] * 5, (np.arange(90), target[1]), 50, 14, 2,
                              matcher, {}, edge_verses=groups)
        for groups in [[[0], [2, 3, 4]], [[0, 1], [1, 2, 3, 4]], [[0, 1], [2, True, 4]]]:
            with self.assertRaises(ValueError):
                performance_times([{}] * 5, [{}] * 5, target, 50, 14, 2,
                                  matcher, {}, edge_verses=groups)
        def disputed(*args):
            result = matcher(*args)
            if len(args[0]) == 1 and args[-1] == 'rhythmWeights':
                result['best']['starts'] = [args[-2] + .6]
            return result
        with self.assertRaises(ValueError):
            performance_times([{}] * 5, [{}] * 5, target, 50, 14, 2,
                              disputed, {}, edge_verses=[[0, 1], [2, 3, 4]])

    def test_previous_reading_policy_is_explicit_and_cannot_change_context_length(self):
        self.assertFalse(self.cli.previous_reading_context({}))
        self.assertTrue(self.cli.previous_reading_context({'previousReadingContext': {'version': 1}}))
        for value in [True, {}, {'version': True}, {'version': 2}, {'version': 1, 'count': 2}]:
            with self.assertRaises(ValueError):
                self.cli.previous_reading_context({'previousReadingContext': value})

    def interlude_fixture(self):
        definitions = [{'region': 'r' + str(i)} for i in range(3)]
        source = {'verses': [definitions[:1], definitions[1:]]}
        melody = [{'notes': [1] * 10, 'pitchOffsets': [0] * 10,
                   'spatialWeights': [1] * 10} for _ in definitions]
        optical = [{'notes': [1, 2, 3, 4, 5], 'pitchOffsets': [0] * 5,
                    'rhythmWeights': [1] * 5} for _ in definitions]
        material = (melody, optical, {'symbolicRows': {'semitoneShift': 2}})
        profile = {'midiReference': {'arrangement': {'sections': [[0, 15]]}},
                   'vocalInterludes': {'version': 1, 'reason': 'reviewed instrumental passage',
                                      'afterVerses': [{'verse': 1, 'quarterBeats': 4}]}}
        return source, profile, material

    def test_interludes_preserve_every_printed_note_and_add_only_unpitched_context_between_source_verses(self):
        source, profile, material = self.interlude_fixture()
        original = copy.deepcopy((source, profile, material))
        self.assertIs(self.cli.vocal_interlude_material(None, {}, material), material)
        actual = self.cli.vocal_interlude_material(source, profile, material)
        self.assertEqual((source, profile, material), original)
        self.assertEqual(actual[0][0]['notes'], material[0][0]['notes'] + [0])
        self.assertEqual(actual[0][0]['pitchOffsets'], material[0][0]['pitchOffsets'] + [0])
        self.assertEqual(actual[0][0]['spatialWeights'], [1] * 10 + [8])
        self.assertEqual(actual[1][0]['notes'], [1, 2, 3, 4, 5, 0])
        self.assertEqual(actual[1][0]['rhythmWeights'], [1] * 5 + [4])
        self.assertEqual(actual[0][1:], material[0][1:])
        self.assertEqual(actual[1][1:], material[1][1:])
        self.assertEqual(actual[2]['symbolicRows'], material[2]['symbolicRows'])
        self.assertEqual(actual[2]['vocalInterludes']['sourceVerseGroups'], [[0], [1, 2]])
        self.assertEqual(actual[2]['vocalInterludes']['gaps'][0]['afterReadingIndex'], 0)

    def test_interludes_reject_manual_times_incomplete_verses_invalid_beats_and_conflicting_forms(self):
        source, profile, material = self.interlude_fixture()
        review = profile['vocalInterludes']
        for change in [{'version': True}, {'version': 2}, {'reason': ''}, {'afterVerses': []},
                       {'seconds': 10}, {'groups': [[0], [1, 2]]},
                       *[{'afterVerses': [{'verse': verse, 'quarterBeats': beats}]}
                         for verse, beats in [(0, 4), (2, 4), (True, 4), (1, 0), (1, -1),
                                              (1, True), (1, 1.5), (1, 65), (1, float('nan'))]],
                       {'afterVerses': [{'verse': 1, 'quarterBeats': 4, 'at': 12}]},
                       {'afterVerses': [{'verse': 1, 'quarterBeats': 4}] * 2}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.cli.vocal_interlude_material(source, {**profile, 'vocalInterludes': {**review, **change}}, material)
        for verses in [[], [source['verses'][0]], [[], source['verses'][1]],
                       [source['verses'][0], source['verses'][1][:1]]]:
            with self.assertRaises(ValueError):
                self.cli.vocal_interlude_material({'verses': verses}, profile, material)
        for key in ['readingSectionReview', 'sourceVerseContext', 'sourceVerseMusicRange',
                    'instrumentalOutroReview', 'instrumentalOutroAlternatives', 'referenceEndingSequence']:
            with self.assertRaises(ValueError):
                self.cli.vocal_interlude_material(source, {**profile, key: {}}, material)
        with self.assertRaises(ValueError):
            self.cli.vocal_interlude_material(source, {**profile,
                'independentRecordings': [{'endingReadingRepeat': {}}]}, material)

    def test_interlude_pipeline_rechecks_every_recording_and_all_original_alignment_gates(self):
        source, profile, material = self.interlude_fixture()
        definitions = [item for verse in source['verses'] for item in verse]
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}, {'file': 'main.mp3', 'sha256': 'main'}]}
        window = {'durationEstimate': 30, 'onsetEstimate': 2}
        profile.update(sampling=window, independentRecordings=[
            {'file': 'main.mp3', 'sha256': 'main', 'sampling': window}])
        for bad in [None, 'cost', 'margin', 'span', 'key', 'local']:
            seen = []
            def matcher(rows, chroma, times, duration, onset, weight):
                main = bool(chroma[0, 0])
                if len(rows) == 3:
                    seen.append((main, weight, rows[0]['notes'][-1], rows[1]['notes'][-1]))
                return self.matcher(bad if main else None)(rows, chroma, times, duration, onset, weight)
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as directory, \
                    patch.object(self.cli, 'validate_source', return_value=(source, definitions)), \
                    patch.object(self.cli, 'reference_material', return_value=material), \
                    patch.object(self.cli, 'features', side_effect=lambda path, cache:
                        (np.arange(36), np.full((12, 36), int(path.name == 'main.mp3')))), \
                    patch.object(self.cli.midi, 'match_template', side_effect=matcher), \
                    patch.object(self.cli, 'export_result', return_value=('test', 'ready')) as export:
                result = self.cli.generate(song, profile, Path(directory))
                self.assertEqual(len(seen), 4)
                self.assertTrue(all(rest == 0 and next_note != 0 for _, _, rest, next_note in seen))
                if bad is None:
                    self.assertEqual(result, ('test', 'ready'))
                    evidence = export.call_args.args[4]
                    self.assertEqual(evidence['independentTimelines']['main.mp3']['times'], [2, 12, 22])
                    self.assertEqual(len(evidence['midiMatching']['performance']['local']), 3)
                else:
                    self.assertIn('needs-calibration', result[1])
                    export.assert_not_called()

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

    def test_music_range_can_exclude_only_one_identical_final_chorus_after_all_source_verses(self):
        verses = [[0, 1], [2, 3], [4, 5]]
        profile = {'sourceVerseMusicRange': {'version': 1, 'endBeforeSection': 6,
                    'reason': 'source verses followed by one repeated final chorus'},
                   'readingSectionReview': {'version': 1, 'groups': [[0], [1], [2], [3], [4], [5, 6]],
                    'instrumentalSections': [], 'reason': 'same final chorus repeated once'}}
        row = {'notes': [1, 2, 3, 4, 5], 'pitchOffsets': [0] * 5, 'rhythmWeights': [1] * 5}
        optical = [copy.deepcopy(row) for _ in range(7)]
        optical[-1] = {'notes': row['notes'] + [0], 'pitchOffsets': [0] * 6,
                       'rhythmWeights': [1] * 5 + [4]}
        self.assertIsNone(self.cli.verse_music_section({}, None, optical))
        self.assertEqual(self.cli.verse_music_section(profile, verses, optical), 6)
        for change in [{'version': True}, {'version': 2}, {'endBeforeSection': 5},
                       {'reason': ''}, {'count': 2}]:
            invalid = copy.deepcopy(profile)
            invalid['sourceVerseMusicRange'].update(change)
            with self.assertRaises(ValueError):
                self.cli.verse_music_section(invalid, verses, optical)
        for invalid in [{**profile, 'referenceEndingSequence': {'version': 1}},
                        {**profile, 'instrumentalOutroAlternatives': [{}]}]:
            with self.assertRaises(ValueError):
                self.cli.verse_music_section(invalid, verses, optical)
        with self.assertRaises(ValueError):
            self.cli.verse_music_section(profile, None, optical)
        changed = copy.deepcopy(optical)
        changed[-1]['notes'][0] = 6
        with self.assertRaises(ValueError):
            self.cli.verse_music_section(profile, verses, changed)
        changed = copy.deepcopy(optical)
        changed[-1]['rhythmWeights'][0] = 2
        with self.assertRaises(ValueError):
            self.cli.verse_music_section(profile, verses, changed)
        proof = {'fullMelody': {'best': {'starts': [2, 8, 22, 28, 42, 48, 58]}},
                 'fullOptical': {'best': {'starts': [2.2, 8, 22, 28, 42, 48, 58.4]}}}
        self.assertEqual(self.cli.verse_music_bounds(6, proof), [2.1, 58.2])
        proof['fullOptical']['best']['starts'][6] = 58.51
        with self.assertRaises(ValueError):
            self.cli.verse_music_bounds(6, proof)

    def test_reviewed_music_range_is_measured_from_both_full_paths_and_phase_failure_blocks_export(self):
        definitions = [{'region': 'r' + str(i)} for i in range(6)]
        source = {'verses': [definitions[:2], definitions[2:4], definitions[4:]]}
        profile = {'sampling': {'durationEstimate': 70, 'onsetEstimate': 2},
                   'sourceVerseContext': {'version': 1, 'reason': 'three source verses'},
                   'sourceVerseMusicRange': {'version': 1, 'endBeforeSection': 6,
                    'reason': 'same final chorus repeated once'},
                   'readingSectionReview': {'version': 1, 'groups': [[0], [1], [2], [3], [4], [5, 6]],
                    'instrumentalSections': [], 'reason': 'repeated final chorus'}}
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'lyrics', 'tracks': [], 'reference': 'lead.mp3'}
        row = {'notes': [1] * 5, 'pitchOffsets': [0] * 5, 'rhythmWeights': [1] * 5}
        material = ([{}] * 7, [row] * 7, {'symbolicRows': {'semitoneShift': 2}})
        def measured(profile, window, material, target, cache, prepare, detail, **kwargs):
            detail['performance'] = {name: {'best': {'starts': [2, 8, 22, 28, 42, 48, 58]}}
                                     for name in ['fullMelody', 'fullOptical']}
            return [2, 8, 22, 28, 42, 48]
        for shifts in [[0, 20, 40], [0, 20, 41]]:
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(self.cli, 'validate_source', return_value=(source, definitions)), \
                    patch.object(self.cli, 'reference_material', return_value=material), \
                    patch.object(self.cli, 'features', return_value=None), \
                    patch.object(self.cli, 'measured_performance', side_effect=measured), \
                    patch.object(self.cli, 'repeated_verses', return_value={'similarity': .8, 'shifts': shifts}) as music, \
                    patch.object(self.cli, 'export_result', return_value=('test', 'ready')) as export:
                result = self.cli.generate(song, profile, Path(directory))
                self.assertEqual(music.call_args.kwargs, {'audio_range': [2, 58]})
                if shifts[-1] == 40:
                    self.assertEqual(result, ('test', 'ready'))
                    self.assertEqual(export.call_args.args[8], [2, 8, 22, 28, 42, 48])
                else:
                    self.assertIn('相位', result[1])
                    export.assert_not_called()

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
