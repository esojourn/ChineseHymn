import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import sync_batch_sources as sources
try:
    from sync_batch_align import align_windows, confirm_boundaries, export_result, review_recordings
    from sync_batch_audio import alignment_band
    from sync_batch_symbolic import score_row_offsets, documented_row_offsets
    from sync_batch_score import is_barline
    import sync_batch_ctc as ctc
    transfer_spec = importlib.util.spec_from_file_location('transfer_sync', TOOLS / 'transfer-sync.py')
    transfer = importlib.util.module_from_spec(transfer_spec)
    transfer_spec.loader.exec_module(transfer)
    transcript_spec = importlib.util.spec_from_file_location('transcript_sync', TOOLS / 'align-transcript-sync.py')
    transcript = importlib.util.module_from_spec(transcript_spec)
    transcript_spec.loader.exec_module(transcript)
    image_transcript_spec = importlib.util.spec_from_file_location('image_transcript_sync', TOOLS / 'align-image-transcript-sync.py')
    image_transcript = importlib.util.module_from_spec(image_transcript_spec)
    image_transcript_spec.loader.exec_module(image_transcript)
    midi_spec = importlib.util.spec_from_file_location('midi_sync', TOOLS / 'midi-sync.py')
    midi_sync = importlib.util.module_from_spec(midi_spec)
    midi_spec.loader.exec_module(midi_sync)
    midi_image_spec = importlib.util.spec_from_file_location('midi_image_sync', TOOLS / 'midi-image-sync.py')
    midi_image = importlib.util.module_from_spec(midi_image_spec)
    midi_image_spec.loader.exec_module(midi_image)
    retrieval_spec = importlib.util.spec_from_file_location('midi_retrieval', TOOLS / 'find-midi-references.py')
    retrieval = importlib.util.module_from_spec(retrieval_spec)
    retrieval_spec.loader.exec_module(retrieval)
except ImportError:
    confirm_boundaries = None

spec = importlib.util.spec_from_file_location('batch_sync', TOOLS / 'batch-sync.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class BatchTests(unittest.TestCase):
    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_initial_view_requires_current_assets_and_coverage_of_every_reading_region(self):
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as directory:
            asr = Path(directory) / 'asr.json'
            asr.write_text('{"segments": []}')
            song = {'type': 'text', 'sha256': 'lyrics', 'reference': 'voice.mp3',
                    'tracks': [{'file': 'voice.mp3', 'sha256': 'audio'}]}
            definitions = [{'region': 'r1'}, {'region': 'r2'}]
            diagnostics = [{'matchedFraction': .8}, {'matchedFraction': .9}]
            profile = {'version': 1, 'sourceHash': 'lyrics', 'tracks': song['tracks'],
                       'audioFile': 'voice.mp3', 'transcriptHash': batch.checksum(asr), 'regions': ['r1', 'r2']}
            view = transcript.validate_initial_view(song, profile, asr, definitions, diagnostics)
            self.assertEqual(view['at'], 0)
            self.assertFalse(view['lyricOnsetMeasured'])
            for key, value in [('version', True), ('sourceHash', 'old'), ('tracks', []),
                               ('audioFile', 'other.mp3'), ('transcriptHash', 'old'), ('regions', ['r2', 'r1'])]:
                invalid = deepcopy(profile)
                invalid[key] = value
                with self.assertRaises(ValueError):
                    transcript.validate_initial_view(song, invalid, asr, definitions, diagnostics)
            with self.assertRaises(ValueError):
                transcript.validate_initial_view(song, profile, asr, definitions, [{'matchedFraction': .59}, diagnostics[1]])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_initial_view_preserves_all_later_acoustic_checks_and_their_preceding_lyrics(self):
        definitions = [{'region': 'r1', 'text': '首屏歌词'}, {'region': 'r2', 'text': '第二屏'},
                       {'region': 'r3', 'text': '第三屏'}]
        later = [{'index': 0, 'window': 0}, {'index': 1, 'window': 0}]
        saved = []
        def verify(*args):
            self.assertEqual(args[3], definitions[1:])
            self.assertEqual(args[6], ['首屏歌词', '第二屏'])
            args[-1](later)
            return later, [30, 50], [{'index': 0, 'at': 30}]
        with patch.object(transcript, 'verified_window_times', side_effect=verify):
            rows, times, decisions = transcript.initial_view_window_times(None, None, [], definitions, [9, 30, 50],
                'zh', ['', '首屏歌词', '第二屏'], [(9, 10), (30, 31), (50, 51)], ['首', '第', '第'], [], saved.append)
        self.assertEqual(times, [0, 30, 50])
        self.assertEqual([row['index'] for row in rows], [1, 2])
        self.assertEqual(saved[-1], rows)
        self.assertEqual(decisions[1]['index'], 1)
        with patch.object(transcript, 'verified_window_times', side_effect=ValueError('later onset ambiguous')):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                transcript.initial_view_window_times(None, None, [], definitions, [9, 30, 50], 'zh',
                    ['', '首屏歌词', '第二屏'], [(9, 10), (30, 31), (50, 51)], ['首', '第', '第'], [], saved.append)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_initial_reading_position_is_zero_in_each_recording_and_never_used_as_a_sung_anchor(self):
        from time import monotonic
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            song = {'songId': '01-001', 'source': '01-001.txt', 'sha256': 'lyrics', 'type': 'text',
                    'reference': 'voice.mp3', 'tracks': [{'file': 'music.mp3', 'sha256': 'music'},
                                                       {'file': 'voice.mp3', 'sha256': 'voice'}]}
            definitions = [{'region': 'r1', 'text': '首屏', 'label': '第1段'},
                           {'region': 'r2', 'text': '后续', 'label': '第2段'}]
            source = {'regions': {'r1': {'kind': 'text', 'startLine': 2, 'endLine': 3},
                                  'r2': {'kind': 'text', 'startLine': 5, 'endLine': 6}}}
            evidence = {'initialReadingView': {'version': 1, 'region': 'r1', 'at': 0, 'lyricOnsetMeasured': False},
                        'transcriptMatching': {}}
            with patch('mutagen.mp3.MP3', return_value=SimpleNamespace(info=SimpleNamespace(length=90))), \
                    patch('sync_batch_align.map_recordings', return_value=([21], {'method': 'test'})) as mapping:
                export_result(song, root, root, root / 'evidence.json', evidence, monotonic(),
                              source, definitions, [0, 20], [0])
            self.assertEqual(mapping.call_args.args[2], [20])
            result = json.loads((root / 'ready/01-001.txt.json').read_text())
            self.assertEqual(result['cues'][0]['at'], 0)
            self.assertEqual([cue['at'] for cue in result['tracks'][0]['cues']], [0, 21])
            self.assertFalse(result['provenance']['initialReadingView']['lyricOnsetMeasured'])
            for key, value in [('version', True), ('at', 4), ('lyricOnsetMeasured', True)]:
                invalid = {**evidence, 'initialReadingView': {**evidence['initialReadingView'], key: value}}
                with self.assertRaises(ValueError):
                    export_result(song, root, root, root / 'invalid.json', invalid, monotonic(),
                                  source, definitions, [0, 20], [0])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_context_family_rejects_distinct_clusters_and_zero_duration_words(self):
        from sync_batch_align import context_cluster_time, extended_context_enabled
        def rows(times):
            return [{'window': i, 'valid': True, 'at': at, 'firstWordStart': at, 'firstWordEnd': at + .3}
                    for i, at in enumerate(times)]
        at, detail = context_cluster_time(rows([20, 20.2, 20.22, 20.7]))
        self.assertAlmostEqual(at, 20.21)
        self.assertEqual(detail['chosenWindows'], [1, 2])
        for values in [[20, 20.1, 21.3, 21.4], [20, 20.1, 20.8, 20.9], [20, 20.7]]:
            self.assertIsNone(context_cluster_time(rows(values))[0])
        zero = rows([20, 20.1])
        zero[0]['firstWordEnd'] = zero[0]['firstWordStart']
        self.assertIsNone(context_cluster_time(zero)[0])
        duplicate = rows([20, 20.1])
        duplicate[1]['window'] = 0
        self.assertIsNone(context_cluster_time(duplicate)[0])
        self.assertFalse(extended_context_enabled(None))
        self.assertFalse(extended_context_enabled({}))
        self.assertTrue(extended_context_enabled({'extendedContext': {'version': 1}}))
        for invalid in [True, {'version': True}, {'version': '1'}, {'version': 2}]:
            with self.assertRaises(ValueError):
                extended_context_enabled({'extendedContext': invalid})

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_additional_known_lyric_windows_preserve_context_and_completed_boundaries(self):
        from sync_batch_align import supplement_context_windows
        definitions = [{'region': 'r1', 'text': '第一句'}, {'region': 'r2', 'text': '第二句'},
                       {'region': 'r3', 'text': '第三句'}]
        rows = [{'index': i, 'window': w, 'valid': True, 'at': 10 * i + w * (.8 if i == 1 else .1),
                 'firstWordStart': 10 * i, 'firstWordEnd': 10 * i + 1}
                for i in range(3) for w in range(2)]
        extra = [{'index': 0, 'window': w, 'valid': True, 'at': 10.3 + w * .02,
                  'firstWordStart': 10.3, 'firstWordEnd': 10.7} for w in range(2)]
        saved = []
        with patch('sync_batch_align.align_windows', return_value=extra) as align:
            result = supplement_context_windows(None, None, [], definitions, [0, 10, 20], 'zh', rows, saved.append)
        self.assertEqual(align.call_count, 1)
        self.assertEqual(align.call_args.args[3:6], ([definitions[1]], [10], 'zh'))
        self.assertEqual(align.call_args.kwargs['preceding_texts'], ['第一句'])
        self.assertEqual(align.call_args.kwargs['window_bounds'], [(8, 18), (10, 16)])
        self.assertEqual([row for row in result if row['window'] < 2], rows)
        self.assertEqual([row['window'] for row in result if row['index'] == 1], [0, 1, 2, 3])
        self.assertEqual(saved[-1], result)
        # A completed family is read without a new model call.
        with patch('sync_batch_align.align_windows') as align:
            self.assertEqual(supplement_context_windows(None, None, [], definitions, [0, 10, 20], 'zh', result,
                                                       saved.append), result)
            align.assert_not_called()

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_known_context_family_requires_opt_in_and_keeps_measured_verse_phases(self):
        verses = [[{'region': 'r1'}], [{'region': 'r2'}], [{'region': 'r3'}]]
        music = {'shifts': [0, 20, 40], 'similarity': .85}
        rows = [{'index': i, 'window': w, 'valid': True, 'at': at,
                 'firstWordStart': at, 'firstWordEnd': at + .3}
                for i, times in enumerate([[0, .1], [20, 20.2, 20.22, 20.7], [40, 40.1]])
                for w, at in enumerate(times)]
        with self.assertRaises(ValueError):
            confirm_boundaries(verses, music, rows)
        times, starts, decisions = confirm_boundaries(verses, music, rows, extended_context=True)
        self.assertAlmostEqual(times[1], 20.21)
        self.assertEqual(starts, [0, 1, 2])
        self.assertAlmostEqual(times[0], .13)
        self.assertEqual(decisions[0]['chosenWindows'], [1, 2])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_repeated_music_does_not_confirm_a_zero_duration_first_word(self):
        verses = [[{'region': 'r1'}], [{'region': 'r2'}], [{'region': 'r3'}]]
        music = {'shifts': [0, 20, 40], 'similarity': 1}
        rows = [{'index': i, 'window': w, 'valid': True, 'at': 1 + 20 * i,
                 'firstWordStart': 1 + 20 * i, 'firstWordEnd': 1.5 + 20 * i}
                for i in range(3) for w in range(2)]
        self.assertEqual(confirm_boundaries(verses, music, rows)[0], [1, 21, 41])
        for row in rows:
            if row['index'] == 1:
                row['firstWordEnd'] = row['firstWordStart']
        with self.assertRaises(ValueError):
            confirm_boundaries(verses, music, rows)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_reviewed_window_estimates_bind_assets_and_order_without_confirming_onsets(self):
        from copy import deepcopy
        from sync_batch_align import reviewed_estimates
        song = {'sha256': 'lyrics', 'reference': 'voice.mp3', 'tracks': [{'file': 'voice.mp3', 'sha256': 'audio'}]}
        definitions = [{'region': 'r1'}, {'region': 'r2'}]
        profile = {'version': 1, 'sourceHash': 'lyrics', 'tracks': song['tracks'],
                   'audioFile': 'voice.mp3', 'regions': ['r1', 'r2'], 'estimates': [8, 30]}
        self.assertEqual(reviewed_estimates(song, definitions, [2, 20], None), [2, 20])
        self.assertEqual(reviewed_estimates(song, definitions, [2, 20], profile), [8, 30])
        for key, value in [('sourceHash', 'stale'), ('tracks', []), ('audioFile', 'other.mp3'),
                           ('regions', ['r2', 'r1']), ('estimates', [8]), ('estimates', [8, 8.5]),
                           ('estimates', [-1, 30]), ('estimates', [False, 30]),
                           ('estimates', [8, float('nan')])]:
            bad = deepcopy(profile)
            bad[key] = value
            with self.assertRaises(ValueError):
                reviewed_estimates(song, definitions, [2, 20], bad)
        # A reviewed estimate supplies no observations, so later cue confirmation fails.
        with self.assertRaises(ValueError):
            confirm_boundaries([[definitions[0]], [definitions[1]]],
                               {'shifts': [0, 22], 'similarity': 1}, [])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_complete_score_corroborates_each_region_despite_imprecise_repeat_shifts(self):
        import numpy as np
        pitches = np.repeat(np.random.default_rng(17).integers(0, 12, 24), 10)
        harmony = np.eye(12, dtype=np.float32)[:, pitches]
        harmony -= harmony.mean(axis=0)
        harmony /= np.linalg.norm(harmony, axis=0)
        rows = [{'notes': [1] * 120, 'pitchOffsets': pitches[a:a + 120].tolist(),
                 'spatialWeights': [1] * 120} for a in [0, 120]]
        lengths = [245, 240, 239, 241]
        target = np.zeros((12, 1040), dtype=np.float32)
        cursor, expected = 30, []
        for length in lengths:
            indices = np.minimum(np.arange(length) * 240 // length, 239)
            target[:, cursor:cursor + length] = np.roll(harmony[:, indices], 5, axis=0)
            expected.extend([cursor * .1, (cursor + length / 2) * .1])
            cursor += length
        reference = (np.arange(240) * .1, harmony)
        audio = (np.arange(1040) * .1, target)
        music = {'period': 24.125, 'initialOnsetEstimate': 3,
                 'shifts': [0, 24.8, 47.8, 72.8], 'similarity': .9}
        matches = []
        for shift in music['shifts']:
            harmonic = midi_sync.same_tune.match_cycle(reference, audio, 0, 24, music['period'],
                3 + shift, cue_offsets=[0, 12])
            melodic = midi_sync.match_template(rows, target, audio[0], music['period'], 3 + shift, 'spatialWeights')
            matches.append({'harmony': harmonic, 'melody': melodic})
        local = [at for row in matches for at in row['harmony']['best']['starts']]
        with self.assertRaises(ValueError):
            transfer.consistent_phases(local, music, 4, 2)
        evidence = {}
        times = midi_sync.full_performance_times({'fullPerformanceCheck': {'version': 1}},
            reference, rows, [0, 12], 24, music, audio, matches, (.35, .35), evidence)
        self.assertEqual(len(times), 8)
        for actual, wanted in zip(times, expected):
            self.assertAlmostEqual(actual, wanted, delta=.3)
        self.assertEqual(evidence['harmony']['best']['key'], 5)
        self.assertEqual(len(evidence['onsetEstimates']), 4)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_complete_score_requires_pairwise_agreement_not_just_agreement_with_one_anchor(self):
        import numpy as np
        def match(starts, span):
            return {'best': {'starts': starts, 'span': span, 'cost': .1, 'key': 3},
                    'runnerUp': {'cost': .4}}
        local = [{'harmony': match([4 + i * 20, 14 + i * 20], 20),
                  'melody': match([4 + i * 20, 14 + i * 20], 20)} for i in range(3)]
        starts = [4, 14, 24, 34, 44, 54]
        # Both complete paths are near the local path, but differ by .8 seconds from each other.
        with patch.object(midi_sync.same_tune, 'match_cycle', return_value=match([x + .4 for x in starts], 60)), \
                patch.object(midi_sync, 'match_template', return_value=match([x - .4 for x in starts], 60)):
            with self.assertRaisesRegex(ValueError, '0.5秒'):
                midi_sync.full_performance_times({'fullPerformanceCheck': {'version': 1}},
                    (np.arange(200) * .1, np.zeros((12, 200))), [{}, {}], [0, 10], 20,
                    {'shifts': [0, 20, 40], 'period': 20, 'initialOnsetEstimate': 4},
                    (None, None), local, (.35, .35), {})

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_complete_score_rejects_retrieval_bypass_and_incomplete_or_weak_paths(self):
        import copy
        import numpy as np
        def match(starts, span):
            return {'best': {'starts': starts, 'span': span, 'cost': .1, 'key': 3},
                    'runnerUp': {'cost': .4}}
        profile = {'fullPerformanceCheck': {'version': 1}}
        reference = (np.arange(200) * .1, np.zeros((12, 200)))
        music = {'shifts': [0, 20, 40], 'period': 20, 'initialOnsetEstimate': 4}
        local = [{'harmony': match([4 + i * 20], 20), 'melody': match([4 + i * 20], 20)} for i in range(3)]
        for invalid in [{}, {'fullPerformanceCheck': {'version': 2}},
                        {**profile, 'referenceDiscovery': {'file': 'candidate.mid'}}]:
            with self.assertRaisesRegex(ValueError, '原谱配置'):
                midi_sync.full_performance_times(invalid, None, [{}], [0], 20, music, None, local, (.35, .35), {})
        for field, value in [('cost', .36), ('span', 40), ('key', 4), ('starts', [4, 24])]:
            full = match([4, 24, 44], 60)
            wrong = copy.deepcopy(full)
            wrong['best'][field] = value
            with patch.object(midi_sync.same_tune, 'match_cycle', return_value=wrong), \
                    patch.object(midi_sync, 'match_template', return_value=full), self.assertRaises(ValueError):
                midi_sync.full_performance_times(profile, reference, [{}], [0], 20,
                    music, reference, local, (.35, .35), {})
        full = match([4, 24, 44], 60)
        weak_key = copy.deepcopy(full)
        weak_key['runnerUp']['cost'] = .179
        with patch.object(midi_sync.same_tune, 'match_cycle', return_value=weak_key), \
                patch.object(midi_sync, 'match_template', return_value=full), self.assertRaises(ValueError):
            midi_sync.full_performance_times(profile, reference, [{}], [0], 20,
                music, reference, local, (.35, .35), {})

    def test_known_source_conflict_blocks_publication_and_clears_after_material_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / 'source-reviews.json'
            song = {'songId': '02-395', 'source': '02-395主前省察.txt', 'sha256': 'a' * 64,
                    'type': 'text', 'lyricsAvailable': True, 'tracks': []}
            batch.save(registry, {song['source']: {'sha256': song['sha256'], 'reason': '正文误复制了另一曲'}})
            with patch.object(batch, 'ROOT', root), patch.object(batch, 'SOURCE_REVIEW_FILE', registry):
                self.assertEqual(batch.source_review(song)['reason'], '正文误复制了另一曲')
                self.assertEqual(batch.progress([song], root / 'cache'), {'needs-source-review': 1})
                # No ready candidate exists: the source gate must reject before reading it.
                with self.assertRaisesRegex(ValueError, '原素材待核对'):
                    batch.publish(song, root / 'cache')
                self.assertFalse((root / 'data/sync').exists())
                song['sha256'] = 'b' * 64
                self.assertIsNone(batch.source_review(song))
                self.assertEqual(batch.progress([song], root / 'cache'), {'missing-audio': 1})

    @unittest.skipIf(confirm_boundaries is None, 'requires optional optical dependencies')
    def test_reference_score_crops_meter_and_preserves_rests_at_an_interior_pickup(self):
        from sync_batch_reference_score import group_reference_rows
        rows = [{'notes': [4, 0, 1, 2, 3, 4], 'pitchOffsets': [0] * 6,
                 'rhythmWeights': [1, 4, .5, .5, 1, 2], 'x': [.05, .15, .3, .4, .5, .6]},
                {'notes': [5, 0, 6, 7, 1], 'pitchOffsets': [0, 0, 1, 0, 0],
                 'rhythmWeights': [2, 1, .5, .5, 3], 'x': [.1, .2, .3, .4, .5]}]
        grouped = group_reference_rows(rows, {'0': [.1, 1]}, [[0, 0], [1, 2]])
        self.assertEqual(grouped[0]['notes'], [1, 2, 3, 4, 5, 0])
        self.assertEqual(grouped[0]['rhythmWeights'], [.5, .5, 1, 2, 2, 1])
        self.assertEqual(grouped[1], {'notes': [6, 7, 1], 'pitchOffsets': [1, 0, 0],
                                     'rhythmWeights': [.5, .5, 3]})
        for starts in [[[0, 0], [1, 1]], [[0, 0], [0, 0]], [[0, 0], [2, 0]], [[1, 0]]]:
            with self.assertRaises(ValueError):
                group_reference_rows(rows, {'0': [.1, 1]}, starts)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional optical dependencies')
    def test_reviewed_reference_bands_reject_missing_overlapping_and_out_of_image_ranges(self):
        from sync_batch_score import reviewed_score_systems
        valid = [{'notes': {'recognitionBand': [10, 24]},
                  'lyrics': [{'recognitionBand': [27, 40]}]}]
        self.assertEqual(reviewed_score_systems(valid, 100), valid)
        for invalid in [[], [{}], [{'notes': {'recognitionBand': [-1, 24]}, 'lyrics': valid[0]['lyrics']}],
                        [{'notes': {'recognitionBand': [10, 101]}, 'lyrics': valid[0]['lyrics']}],
                        [{'notes': {'recognitionBand': [10, 24]}, 'lyrics': [{'recognitionBand': [23, 40]}]}],
                        [{'notes': {'recognitionBand': [10, 24]}, 'lyrics': [
                            {'recognitionBand': [27, 40]}, {'recognitionBand': [35, 45]}]}],
                        [valid[0], valid[0]],
                        [{'notes': {'recognitionBand': [10.5, 24]}, 'lyrics': valid[0]['lyrics']}]]:
            with self.assertRaises(ValueError):
                reviewed_score_systems(invalid, 100)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional optical dependencies')
    def test_reviewed_reference_layout_is_bound_to_note_cache_without_changing_default_detection(self):
        from PIL import Image
        import sync_batch_score as score
        class Reader:
            calls = 0
            def text_rec(self, crop, return_word_box):
                self.calls += 1
                return [('1234', 1., (100, ['1234'], [[10, 30, 50, 70]], None, None))], None
        reader = Reader()
        systems = [{'notes': {'recognitionBand': [10, 24]},
                    'lyrics': [{'recognitionBand': [27, 40]}]}]
        changed = [{'notes': {'recognitionBand': [11, 25]}, 'lyrics': systems[0]['lyrics']}]
        with tempfile.TemporaryDirectory() as directory, patch.object(score, '_ocr', reader):
            cache = Path(directory)
            raster = cache / 'score.png'
            Image.new('RGB', (100, 100), 'white').save(raster)
            recognition = {'source': 'score.png', 'sha256': 'pinned-raster', 'recognizerVersion': 5,
                           'recognitionAngle': 1}
            with patch.object(score, 'image_source', side_effect=AssertionError('not a Chinese score')):
                first = score.note_rows(raster, recognition, cache, reviewed_systems=systems)
                self.assertEqual(first[0]['notes'], [1, 2, 3, 4])
                self.assertEqual(score.note_rows(raster, recognition, cache, reviewed_systems=systems), first)
                self.assertEqual(reader.calls, 1)
                score.note_rows(raster, recognition, cache, reviewed_systems=changed)
                self.assertEqual(reader.calls, 2)
            with patch.object(score, 'image_source', return_value={'systems': systems}) as detected:
                score.note_rows(raster, recognition, cache)
                detected.assert_called_once_with(recognition)
                self.assertEqual(reader.calls, 3)
                saved = json.loads((cache / 'notes/score.png.json').read_text())
                self.assertEqual(saved['version'], 3)
                self.assertNotIn('reviewedSystems', saved)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional optical dependencies')
    def test_reference_score_rejects_changed_source_render_and_cached_notes(self):
        import hashlib
        import sync_batch_reference_score as reference_score
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / 'score.svg').write_bytes(b'<svg/>')
            (cache / 'score.png').write_bytes(b'rendered image')
            digest = hashlib.sha256(b'rendered image').hexdigest()
            (cache / 'ocr.json').write_text(json.dumps({'source': 'score.png', 'sha256': digest,
                                                      'recognizerVersion': 5}))
            profile = {'scoreReferenceSha256': hashlib.sha256(b'<svg/>').hexdigest(),
                       'opticalReference': {'svgFile': 'score.svg', 'imageFile': 'score.png',
                           'recognitionFile': 'ocr.json', 'imageSha256': digest, 'recognizerVersion': 5,
                           'rowsSha256': reference_score.score_rows_hash([]), 'readingStarts': [[0, 0]]}}
            with patch.object(reference_score, 'note_rows', return_value=[{'changed': True}]):
                with self.assertRaisesRegex(ValueError, '音符或节奏已改变'):
                    reference_score.reference_rows(profile, cache, [0], {})
            (cache / 'score.png').write_bytes(b'changed image')
            with patch.object(reference_score, 'note_rows') as read:
                with self.assertRaisesRegex(ValueError, '渲染图片已改变'):
                    reference_score.reference_rows(profile, cache, [0], {})
                read.assert_not_called()
            (cache / 'score.svg').write_bytes(b'changed source')
            with self.assertRaisesRegex(ValueError, '独立原谱'):
                reference_score.reference_rows(profile, cache, [0], {})

    @unittest.skipIf(confirm_boundaries is None, 'requires optional optical dependencies')
    def test_retrieved_score_cannot_bypass_stronger_audio_limits_through_optical_mode(self):
        from sync_batch_reference_score import reference_rows
        with self.assertRaisesRegex(ValueError, '严格录音检查'):
            reference_rows({'referenceDiscovery': {'file': 'proposed.mid'}}, Path('/missing'), [], {})

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_onset_tolerance_includes_float32_half_second_and_rejects_larger_errors(self):
        import numpy as np
        from sync_batch_audio import onsets_agree
        self.assertTrue(onsets_agree([23.1, 351.1], [np.float32(23.6), np.float32(351.6)]))
        self.assertFalse(onsets_agree([23.1, 351.1], [np.float32(23.6), np.float32(351.61)]))
        self.assertFalse(onsets_agree([23.1, 351.1], [23.1]))
        self.assertFalse(onsets_agree([], []))
        self.assertFalse(onsets_agree([float('nan')], [1]))

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_two_reference_cycles_locate_all_reading_regions_in_a_longer_performance(self):
        import numpy as np
        generator = np.random.default_rng(75)
        phrase = np.eye(12, dtype=np.float32)[:, np.repeat(generator.integers(0, 12, 48), 5)]
        reference = np.zeros((12, 550), dtype=np.float32)
        reference[:, 30:270] = phrase
        reference[:, 270:510] = phrase
        stretched = phrase[:, np.minimum(np.arange(320) * 3 // 4, 239)]
        target = np.zeros((12, 1050), dtype=np.float32)
        for start in [50, 370, 690]:
            target[:, start:start + 320] = np.roll(stretched, 5, axis=0)
        cues = [{'at': at} for at in [3.4, 11.4, 19.4, 27.4, 35.4, 43.4]]
        with patch.object(transfer, 'features', side_effect=[(np.arange(550) * .1, reference),
                                                           (np.arange(1050) * .1, target)]):
            times, evidence = transfer.cycle_times('reference', 'target', cues, 3,
                {'period': 24, 'shifts': [0, 24]},
                {'period': 32, 'initialOnsetEstimate': 5, 'shifts': [0, 32, 64]}, None, [0, 2])
        self.assertEqual(len(times), 6)
        for actual, expected in zip(times, [5.5, 26.9, 37.5, 58.9, 69.5, 90.9]):
            self.assertAlmostEqual(actual, expected, delta=.2)
        self.assertEqual(evidence['referenceSlots'], [0, 2])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_two_reference_cycles_reject_a_conflicting_later_region_and_invalid_slots(self):
        first = {'best': {'cost': .1, 'key': 3, 'span': 20, 'starts': [4, 14]},
                 'runnerUp': {'cost': .4}}
        second = {'best': {'cost': .1, 'key': 3, 'span': 20, 'starts': [4, 15]},
                  'runnerUp': {'cost': .4}}
        cues = [{'at': at} for at in [2, 12, 22, 32]]
        with patch.object(transfer, 'features', return_value=None), \
                patch.object(transfer, 'match_cycle', side_effect=[first, second]):
            with self.assertRaisesRegex(ValueError, '阅读区域起点不一致'):
                transfer.cycle_times('a', 'b', cues, 2, {'period': 20, 'shifts': [0, 20]},
                                     {'period': 20, 'initialOnsetEstimate': 4, 'shifts': [0]}, None, [0, 1])
        with patch.object(transfer, 'features', return_value=None):
            for slots in [[], [1], [0, 0], [0, 2], [0, True]]:
                with self.assertRaises(ValueError):
                    transfer.cycle_times('a', 'b', cues, 2, {'shifts': [0, 20]}, {}, None, slots)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional MIDI dependencies')
    def test_printed_reference_preserves_rests_and_fractional_note_times(self):
        import io
        import mido
        from sync_batch_printed_midi import printed_reference_bytes
        profile = {'targetSha256': 'image', 'mode': 'symbolic-melody-and-optical',
                   'printedRhythmReference': {'sourceSha256': 'image', 'tonicMidi': 63,
                       'numberedRowsAndQuarterBeats': [[[1, .75], [2, .25], [0, 1], [3, 1], [4, 1], [5, 1]]]}}
        data = printed_reference_bytes(profile, 'image')
        midi = mido.MidiFile(file=io.BytesIO(data))
        seconds, starts = 0, []
        for message in midi:
            seconds += message.time
            if message.type == 'note_on' and message.velocity:
                starts.append((message.note, seconds))
        self.assertEqual(starts, [(63, 0), (65, .375), (67, 1), (68, 1.5), (70, 2)])
        self.assertEqual(printed_reference_bytes(profile, 'image'), data)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional MIDI dependencies')
    def test_printed_reference_requires_current_image_optical_review_and_valid_rhythm(self):
        import copy
        from sync_batch_printed_midi import printed_reference_bytes
        profile = {'targetSha256': 'image', 'mode': 'symbolic-melody-and-optical',
                   'printedRhythmReference': {'sourceSha256': 'image', 'tonicMidi': 63,
                       'numberedRowsAndQuarterBeats': [[[1, 1], [2, 1], [3, 1], [4, 1], [5, 1]]]}}
        for field, value in [('mode', 'harmony-only'), ('targetSha256', 'old-image')]:
            bad = {**profile, field: value}
            with self.assertRaises(ValueError):
                printed_reference_bytes(bad, 'image')
        for note in [[8, 1], [True, 1], [1, 0], [1, float('nan')], [1, .3333]]:
            bad = copy.deepcopy(profile)
            bad['printedRhythmReference']['numberedRowsAndQuarterBeats'][0][0] = note
            with self.assertRaises(ValueError):
                printed_reference_bytes(bad, 'image')
        with self.assertRaises(ValueError):
            printed_reference_bytes(profile, 'changed-image')

    @unittest.skipIf(confirm_boundaries is None, 'requires optional MIDI dependencies')
    def test_printed_reference_hash_failure_cannot_replace_a_cached_reference(self):
        from sync_batch_printed_midi import materialize_printed_reference
        profile = {'targetSha256': 'image', 'sha256': 'wrong', 'mode': 'symbolic-melody-and-optical',
                   'printedRhythmReference': {'sourceSha256': 'image', 'tonicMidi': 63,
                       'numberedRowsAndQuarterBeats': [[[1, 1], [2, 1], [3, 1], [4, 1], [5, 1]]]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reference.mid'
            path.write_bytes(b'original')
            with self.assertRaisesRegex(ValueError, '哈希'):
                materialize_printed_reference(profile, 'image', path)
            self.assertEqual(path.read_bytes(), b'original')

    @unittest.skipIf(confirm_boundaries is None, 'requires optional retrieval dependencies')
    def test_cyclic_retrieval_identifies_a_transposed_and_shifted_reference(self):
        import numpy as np
        generator = np.random.default_rng(31)
        known = np.eye(12, dtype=np.float32)[:, np.repeat(generator.integers(0, 12, 64), 8)]
        other = np.eye(12, dtype=np.float32)[:, np.repeat(generator.integers(0, 12, 64), 8)]
        query = np.roll(np.roll(known, 5, axis=0), 37, axis=1)
        spectra = np.stack([np.fft.rfft(value, axis=1) for value in [other, known]])
        result = retrieval.rank_candidates(spectra, ['other.mid', 'known.mid'], query)
        self.assertEqual(result[0]['file'], 'known.mid')
        self.assertEqual(result[0]['key'], 5)
        self.assertEqual(result[0]['cyclicPhase'], 37)
        self.assertGreater(result[0]['similarity'] - result[1]['similarity'], .5)

    @staticmethod
    def ctc_fixture(first=50, probability=.9):
        import numpy as np
        labels = ['-', 'a', 'i', 'e', 'n', 'o', 'u', 't', 's', 'r', 'm', 'k', 'l', 'd', 'g', 'h',
                  'y', 'b', 'p', 'w', 'c', 'v', 'j', 'z', 'f', "'", 'q', 'x', '*']
        vocabulary = {letter: index for index, letter in enumerate(labels)}
        logp = np.full((200, 29), -12., dtype=np.float32)
        logp[:, 0] = logp[:, 28] = 0
        for offset, letter in enumerate('vong'):
            frame = first + offset * 4
            logp[frame:frame + 2, 0] = -12
            logp[frame:frame + 2, vocabulary[letter]] = np.log(probability)
        return logp, vocabulary

    @unittest.skipIf(confirm_boundaries is None, 'requires optional CTC dependencies')
    def test_ctc_freely_decoded_prefix_ignores_constant_wildcard_and_rejects_weak_letters(self):
        logp, vocabulary = self.ctc_fixture()
        candidates = ctc.decode_prefix(logp, vocabulary, 'Von', 'guten', [0, 20])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['at'], 5)
        self.assertEqual(candidates[0]['followingStart'], 6.2)
        weak, _ = self.ctc_fixture(probability=.19)
        self.assertEqual(ctc.decode_prefix(weak, vocabulary, 'Von', 'guten', [0, 20]), [])
        self.assertEqual(ctc.decode_prefix(logp, vocabulary, 'Von', 'heute', [0, 20]), [])
        # A recognizer that hears only blank/wildcard cannot be forced to emit lyrics.
        logp[:, :28] = -12
        logp[:, 0] = 0
        self.assertEqual(ctc.decode_prefix(logp, vocabulary, 'Von', 'guten', [0, 20]), [])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional CTC dependencies')
    def test_ctc_recovery_preserves_later_windows_and_checks_word_end_and_asr_range(self):
        import numpy as np
        definitions = [{'text': 'Von guten Machten'}, {'text': 'And next'}]
        observations = [{'index': 0, 'window': window, 'valid': False, 'at': start,
            'firstWord': 'von', 'firstWordProbability': .8, 'firstWordEnd': 6.1,
            'positiveDurationFraction': 1, 'meanProbability': .8, 'bounds': [start, start + 20]}
            for window, start in enumerate([0, 2])]
        observations += [{'index': 1, 'window': window, 'valid': True, 'at': 11 + window * .1}
                         for window in [0, 1]]
        fake = lambda audio, bounds, *args: self.ctc_fixture(first=50 if bounds[0] == 0 else 30)
        with patch.object(ctc, 'emissions', side_effect=fake):
            times, decisions = ctc.recover_initial(np.zeros(16000), 'hash', definitions, [4.5, 11],
                [(4.5, 6.1), (11, 11.5)], ['von', 'and'], observations, Path('model'), Path('cache'),
                transcript.confirm_transcript_windows)
            self.assertEqual(times, [5, 11.05])
            self.assertEqual(decisions[-1]['index'], 0)
            with self.assertRaisesRegex(ValueError, '存在冲突'):
                ctc.recover_initial(np.zeros(16000), 'hash', definitions, [4.5, 11],
                    [(4.5, 6.1), (11, 11.5)], ['von', 'and'],
                    observations + [{'index': 0, 'window': 2, 'valid': True, 'at': 3}],
                    Path('model'), Path('cache'), transcript.confirm_transcript_windows)
            with self.assertRaises(ValueError):
                ctc.recover_initial(np.zeros(16000), 'hash', definitions, [6, 11],
                    [(6, 6.1), (11, 11.5)], ['von', 'and'], observations, Path('model'), Path('cache'),
                    transcript.confirm_transcript_windows)
        wrong = [dict(row) for row in observations]
        wrong[-1]['at'] = 13
        with patch.object(ctc, 'emissions') as infer:
            with self.assertRaises(ValueError):
                ctc.recover_initial(np.zeros(16000), 'hash', definitions, [4.5, 11],
                    [(4.5, 6.1), (11, 11.5)], ['von', 'and'], wrong, Path('model'), Path('cache'),
                    transcript.confirm_transcript_windows)
            infer.assert_not_called()

    def test_lyrics_only_image_keeps_number_gaps_and_the_alternate_chorus(self):
        texts = ['歌词图片标题', '1.第一段第一行', '第一段第二行', '**共同副歌第一行', '共同副歌第二行',
                 'Von guten Machten wunderbar', 'An jedem neuen Tag', '2.第二段第一行', '第二段第二行',
                 '6.烛光段落第一行', '烛光段落第二行']
        ocr = {'rows': [{'text': text, 'top': .02 + index * .07, 'bottom': .05 + index * .07,
                         'confidence': .99} for index, text in enumerate(texts)]}
        source = sources.image_source(ocr)
        self.assertEqual(source['layout'], 'lyrics-only')
        self.assertEqual(list(source['regions']), ['v1', 'chorus', 'chorus-alt-1', 'v2', 'v6'])
        self.assertEqual([verse[0]['region'] for verse in source['verses']], ['v1', 'v2', 'v6'])
        self.assertTrue(all(verse[1]['region'] == 'chorus' for verse in source['verses']))
        self.assertGreater(source['regions']['chorus-alt-1']['top'], source['regions']['chorus']['bottom'])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional transcript dependencies')
    def test_original_language_image_blocks_account_for_every_transcribed_segment(self):
        segments = [{'text': text, 'words': [{'word': text.split()[0], 'start': at, 'end': at + .5}]}
                    for text, at in [('Von guten Machten', 10), ('Gott ist bei uns', 30), ('Ende.', 50)]]
        source = {'regions': {'v1': {}, 'chorus': {}, 'v2': {}},
                  'blocks': {'v1': {'label': '原图第1段'}, 'chorus': {'label': '副歌'}}}
        profile = {'language': 'de', 'blocks': [{'region': 'v1', 'segmentIndices': [0]},
                   {'region': 'chorus', 'segmentIndices': [1]}], 'nonLyricSegments': [{'index': 2, 'text': 'Ende.'}]}
        definitions, estimates, ranges, words = image_transcript.transcript_blocks(profile, {'segments': segments}, source)
        self.assertEqual([row['region'] for row in definitions], ['v1', 'chorus'])
        self.assertEqual(estimates, [10, 30])
        self.assertEqual(words, ['von', 'gott'])
        with self.assertRaisesRegex(ValueError, '未说明'):
            image_transcript.transcript_blocks({**profile, 'nonLyricSegments': []}, {'segments': segments}, source)
        with self.assertRaisesRegex(ValueError, '尾语不一致'):
            image_transcript.transcript_blocks({**profile, 'nonLyricSegments': [{'index': 2, 'text': 'different'}]},
                                                {'segments': segments}, source)
        self.assertEqual(sources.clean('Mächten großzügig Straße', 'de'), 'machtengrosszugigstrasse')

    @unittest.skipIf(confirm_boundaries is None, 'requires optional transcript dependencies')
    def test_reviewed_text_order_covers_source_and_actual_repeated_fragments(self):
        import copy
        source = {'layout': 'lyrics-only-text', 'regions': {'r1': {}, 'r2': {}},
                  'blocks': {'r1': {'text': '你是荣耀的君王你是和平之主', 'label': '主歌'},
                             'r2': {'text': '和撒那归于大卫的子孙愿荣耀归于至高神', 'label': '副歌'}}}
        texts = ['你是荣耀的君王你是和平之主', '和撒那归于大卫的子孙愿荣耀归于至高神', '愿荣耀归于至高神']
        segments = [{'text': text, 'words': [{'word': text, 'start': at, 'end': at + 5}]}
                    for text, at in zip(texts, [5, 30, 55])]
        profile = {'language': 'zh', 'blocks': [{'region': 'r1', 'segmentIndices': [0]},
                   {'region': 'r2', 'segmentIndices': [1]},
                   {'region': 'r2', 'segmentIndices': [2], 'sourceText': texts[2]}]}
        definitions, estimates, ranges, words = image_transcript.transcript_blocks(profile, {'segments': segments}, source)
        self.assertEqual([item['region'] for item in definitions], ['r1', 'r2', 'r2'])
        self.assertEqual(definitions[-1]['text'], texts[2])
        for change in ['invented', 'overlap', 'uncovered', 'unrecognized']:
            wrong = copy.deepcopy(profile)
            actual = copy.deepcopy(segments)
            if change == 'invented':
                wrong['blocks'][-1]['sourceText'] = '这不是原文歌词'
            elif change == 'overlap':
                wrong['blocks'][-1]['segmentIndices'] = [1, 2]
            elif change == 'uncovered':
                wrong['blocks'][0]['region'] = 'r2'
                actual[0] = copy.deepcopy(actual[1])
            else:
                actual[2]['words'][0]['word'] = '毫不相关的演唱内容'
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    image_transcript.transcript_blocks(wrong, {'segments': actual}, source)

        # A recording may omit a printed stanza only with its exact source and an explicit review.
        source['regions']['r3'] = {}
        source['blocks']['r3'] = {'text': '这是录音未唱的原文', 'label': '未演唱段落'}
        omission = {'region': 'r3', 'sourceText': '这是录音未唱的原文', 'reason': '已核对整曲实际演唱顺序'}
        definitions, *_ = image_transcript.transcript_blocks({**profile, 'unsungRegions': [omission]},
                                                              {'segments': segments}, source)
        self.assertEqual([item['region'] for item in definitions], ['r1', 'r2', 'r2'])
        for omissions in [[], None, [None], [omission, omission], [{**omission, 'reason': ''}],
                          [{**omission, 'sourceText': '改动的正文'}], [{**omission, 'region': 'r2'}],
                          [{**omission, 'region': 'missing'}]]:
            with self.subTest(omissions=omissions):
                with self.assertRaises(ValueError):
                    image_transcript.transcript_blocks({**profile, 'unsungRegions': omissions}, {'segments': segments}, source)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional transcript dependencies')
    def test_word_ranges_cross_asr_segments_without_losing_or_repeating_words(self):
        import copy
        words = [{'word': word, 'start': 5 + i, 'end': 5.5 + i}
                 for i, word in enumerate('God will make a way He will make a way for me Thank you'.split())]
        segments = [{'text': 'God will make a way He will', 'words': words[:7]},
                    {'text': 'make a way for me', 'words': words[7:12]},
                    {'text': 'Thank you', 'words': words[12:]}, {'text': '♪', 'words': []}]
        source = {'layout': 'lyrics-only-text', 'regions': {'r1': {}, 'r2': {}},
                  'blocks': {'r1': {'text': 'God will make a way', 'label': 'first'},
                             'r2': {'text': 'He will make a way for me', 'label': 'second'}}}
        profile = {'language': 'en', 'blocks': [{'region': 'r1', 'wordRange': [0, 5]},
                   {'region': 'r2', 'wordRange': [5, 12]}],
                   'nonLyricSegments': [{'index': 2, 'text': 'Thank you'}, {'index': 3, 'text': '♪'}]}
        definitions, estimates, *_ = image_transcript.transcript_blocks(profile, {'segments': segments}, source)
        self.assertEqual(estimates, [5, 10])
        self.assertEqual([row['text'] for row in definitions], ['God will make a way', 'He will make a way for me'])
        for interval in [[4, 12], [6, 12], [-1, 12], [5, 15], [True, 12], [5, 5], '5:12']:
            wrong = copy.deepcopy(profile)
            wrong['blocks'][1]['wordRange'] = interval
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    image_transcript.transcript_blocks(wrong, {'segments': segments}, source)
        for change in ['mixed', 'missing-word-outro', 'missing-empty-outro', 'excluded-lyric']:
            wrong = copy.deepcopy(profile)
            if change == 'mixed':
                wrong['blocks'][0]['segmentIndices'] = [0]
            elif change == 'missing-word-outro':
                wrong['nonLyricSegments'].pop(0)
            elif change == 'missing-empty-outro':
                wrong['nonLyricSegments'].pop()
            else:
                wrong['nonLyricSegments'].append({'index': 0, 'text': segments[0]['text']})
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    image_transcript.transcript_blocks(wrong, {'segments': segments}, source)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional transcript dependencies')
    def test_free_initial_prefix_requires_exact_unique_opening_and_measured_first_word(self):
        import copy
        import sync_batch_free_prefix as free
        definition = {'text': '你是荣耀的君王你是和平之主'}
        words = [{'word': '你是', 'start': 5.74, 'end': 7.14, 'probability': .7}]
        words.extend({'word': character, 'start': 7.14 + index * .5, 'end': 7.64 + index * .5, 'probability': .8}
                     for index, character in enumerate('荣耀的君王你是和平之主'))
        decoded = {'bounds': [0, 21.66], 'segments': [{'words': words}]}
        edge = {'firstWord': '你是', 'firstWordEnd': 7.14}
        result = free.prefix_candidate(decoded, definition, 'zh', edge, 5.66)
        self.assertEqual(result['at'], 5.74)
        self.assertEqual(result['prefix'], '你是荣耀的君王你是和')
        for change in ['text', 'duplicate', 'probability', 'zero', 'edge', 'end', 'range', 'nan']:
            wrong = copy.deepcopy(decoded)
            first = wrong['segments'][0]['words'][0]
            if change == 'text':
                wrong['segments'][0]['words'][1]['word'] = '错'
            elif change == 'duplicate':
                wrong['segments'][0]['words'] *= 2
            elif change == 'probability':
                first['probability'] = .1
            elif change == 'zero':
                first['end'] = first['start']
            elif change == 'edge':
                first['start'] = 0
            elif change == 'end':
                first['end'] = 8
            elif change == 'range':
                first['start'] = -1
            else:
                first['probability'] = float('nan')
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    free.prefix_candidate(wrong, definition, 'zh', edge, 5.66)
        self.assertEqual(free.initial_prefix('Oh Lord my God when I wonder', 'en'), 'ohlordmygod')
        with self.assertRaises(ValueError):
            free.initial_prefix('很短', 'zh')

    @unittest.skipIf(confirm_boundaries is None, 'requires optional transcript dependencies')
    def test_free_initial_recovery_requires_clamped_windows_and_pinned_model(self):
        import copy
        import hashlib
        import sync_batch_free_prefix as free
        from types import SimpleNamespace
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'medium').mkdir()
            for name in ['model.bin', 'tokenizer.json']:
                (root / 'medium' / name).write_bytes(b'fixed-test-model')
            digest = hashlib.sha256(b'fixed-test-model').hexdigest()
            review = {'version': 1, 'model': {'sha256': digest, 'tokenizerSha256': digest}}
            edges = [{'index': 0, 'window': index, 'valid': False, 'at': start, 'bounds': [start, end],
                      'firstWord': '你是', 'firstWordEnd': 7.14, 'firstWordProbability': .7,
                      'positiveDurationFraction': 1, 'meanProbability': .8}
                     for index, (start, end) in enumerate([(0, 21.66), (1.66, 19.66)])]
            class Model:
                calls = []

                def transcribe(self, audio, **options):
                    self.calls.append(options)
                    shift = 0 if len(self.calls) == 1 else 1.66
                    at = 5.74 if len(self.calls) == 1 else 5.78
                    words = [SimpleNamespace(word='你是', start=at - shift, end=7.14 - shift, probability=.7)]
                    words += [SimpleNamespace(word=character, start=7.14 + i * .5 - shift,
                              end=7.64 + i * .5 - shift, probability=.8)
                              for i, character in enumerate('荣耀的君王你是和平之主')]
                    return [SimpleNamespace(start=at - shift, end=14 - shift, text='你是荣耀的君王你是和平之主', words=words)], None

            model = Model()
            args = (model, np.zeros(25 * 16000), 'audio-hash', [{'text': '你是荣耀的君王你是和平之主'}],
                    [5.66], [(5.66, 7.14)], ['你是'], edges, 'zh', review, root, root / 'cache', lambda *a: ([], []), batch.save)
            times, decisions = free.recover_initial(*args)
            self.assertEqual(times, [5.76])
            self.assertEqual(len(decisions[0]['windows']), 2)
            self.assertTrue(all(not call['vad_filter'] and not call['condition_on_previous_text'] and
                                'initial_prompt' not in call for call in model.calls))
            wrong = copy.deepcopy(edges)
            wrong[0]['valid'] = True
            with self.assertRaisesRegex(ValueError, '边缘'):
                free.recover_initial(*args[:7], wrong, *args[8:])
            for path in (root / 'cache/free-initial-windows').glob('*.json'):
                decoded = json.loads(path.read_text())
                if decoded['bounds'][0] == 1.66:
                    decoded['segments'][0]['words'][0]['start'] += 1
                    path.write_text(json.dumps(decoded))
            with self.assertRaisesRegex(ValueError, '两组自由转写'):
                free.recover_initial(*args)
            (root / 'medium/model.bin').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, '模型或词表'):
                free.recover_initial(*args)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional transcript dependencies')
    def test_free_initial_profile_rejects_changed_text_or_recording_before_alignment(self):
        song = {'songId': '06-009', 'source': 'lyrics.txt', 'sha256': 'source-hash', 'reference': 'song.mp3',
                'tracks': [{'file': 'song.mp3', 'sha256': 'audio-hash'}]}
        original = {'sourceHash': 'source-hash', 'audioFile': 'song.mp3', 'audioHash': 'audio-hash',
                    'freeInitialPrefix': {'version': 1}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field in ['sourceHash', 'audioFile', 'audioHash']:
                result = transcript.generate(song, root, root, 2, free_initial_profile={**original, field: 'changed'})
                self.assertIn('当前歌词或录音不一致', result[1])
                evidence = json.loads((root / 'transcript-alignment/lyrics.txt.json').read_text())
                self.assertEqual(evidence['status'], 'needs-calibration')

    def test_interrupted_transcription_replays_only_the_incomplete_chunk(self):
        from types import SimpleNamespace
        class Model:
            feature_extractor = SimpleNamespace(time_per_frame=.01)
            calls = []

            def transcribe(self, audio, **options):
                self.calls.append(options)

                def segment(seek, text):
                    return SimpleNamespace(seek=seek, text=text, start=seek / 100, end=seek / 100 + 1,
                                           avg_logprob=-.1, no_speech_prob=0, words=[])

                def first():
                    yield segment(0, 'a')
                    yield segment(0, 'b')
                    yield segment(3000, 'c')
                    raise InterruptedError('interrupted during the second chunk')

                rows = first() if len(self.calls) == 1 else iter([segment(3000, 'c'), segment(3000, 'd'), segment(6000, 'e')])
                return rows, SimpleNamespace(language='en', duration=90)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'data/audio.mp3').write_bytes(b'audio')
            song = {'songId': '06-001', 'reference': 'audio.mp3'}
            model = Model()
            with patch.object(batch, 'ROOT', root), patch.object(batch, '_model', model):
                with self.assertRaises(InterruptedError):
                    batch.transcribe((song, str(root), 'models', 'medium', 1))
                self.assertFalse((root / 'asr/audio.mp3.json').exists())
                saved = json.loads((root / 'asr-windows/audio.mp3.json').read_text())
                self.assertEqual(saved['seek'], 3000)
                self.assertEqual([row['text'] for row in saved['segments']], ['a', 'b'])
                batch.transcribe((song, str(root), 'models', 'medium', 1))
            self.assertEqual(model.calls[1]['clip_timestamps'], [30])
            complete = json.loads((root / 'asr/audio.mp3.json').read_text())
            self.assertEqual([row['text'] for row in complete['segments']], list('abcde'))

    @unittest.skipIf(confirm_boundaries is None, 'requires optional score dependencies')
    def test_shifted_ocr_barline_is_removed_but_adjacent_digit_one_is_preserved(self):
        components = [[30, 0, 3, 40, 110], [70, 4, 11, 32, 230], [85, 0, 3, 40, 100]]
        self.assertTrue(is_barline(components, 22, 40))
        self.assertTrue(is_barline(components, 32, 40))
        self.assertFalse(is_barline(components, 74, 40))
        self.assertFalse(is_barline([[30, 4, 5, 32, 150]], 32, 40))

    def test_cache_writers_with_the_same_pid_do_not_share_a_temporary_file(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'inventory.json'
            barrier = Barrier(4)
            replace = Path.replace

            def simultaneous_replace(path, target):
                barrier.wait(timeout=10)
                return replace(path, target)

            with patch.object(Path, 'replace', simultaneous_replace), ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(batch.save, destination, {'writer': index}) for index in range(4)]
                for future in futures:
                    future.result(timeout=15)
            self.assertIn(json.loads(destination.read_text())['writer'], range(4))
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_chorus_headings_with_punctuation_are_not_sung_lyrics(self):
        for heading in ['副歌.', '副歌：', '副：', '（副歌）', '[Chorus]', 'Chorus:']:
            self.assertTrue(sources.CHORUS.fullmatch(heading), heading)
        self.assertFalse(sources.CHORUS.fullmatch('我们同唱副歌'))

    def test_inline_chorus_labels_preserve_display_lines_but_do_not_add_sung_words(self):
        original = '示例\n\n1.主歌真实起句\n主歌下一句\n\n副歌：哈利路亚\n哈利路亚\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'example.txt'
            path.write_text(original)
            source = sources.text_source(path)
            self.assertEqual(source['verses'][0][1]['text'], '哈利路亚 哈利路亚')
            self.assertEqual(source['regions']['r2'], {'kind': 'text', 'startLine': 5, 'endLine': 6})
            self.assertEqual(path.read_text(), original)
        for text in ['副：哈利路亚', '（副歌）哈利路亚', '[Chorus] Hallelujah', 'Chorus: Hallelujah']:
            self.assertIn(sources.strip_verse(text), ['哈利路亚', 'Hallelujah'])
        for text in ['我们同唱副歌', '副歌颂扬救恩', 'Chorus of angels']:
            self.assertEqual(sources.strip_verse(text), text)

    def test_inventory_keeps_missing_media_and_never_treats_existing_data_as_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            (data / 'sync').mkdir(parents=True)
            (data / '01-001示例.txt').write_text('示例\n\n歌词')
            (data / '01-001示例.mp3').write_bytes(b'instrumental')
            (data / '01-001示例-领唱.mp3').write_bytes(b'lead')
            (data / 'sync/01-001示例.txt.json').write_text('{}')
            (data / '05-002无音频.jpg').write_bytes(b'image')
            items = batch.inventory(root)
            self.assertEqual(len(items), 2)
            self.assertTrue(items[0]['existing'])
            self.assertEqual(items[0]['reference'], '01-001示例-领唱.mp3')
            self.assertEqual(len(items[0]['tracks']), 2)
            self.assertIsNone(items[1]['reference'])

    def test_four_line_regions_preserve_original_source_lines_and_stanzas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '01-001示例.txt'
            path.write_text('标题\n\n1.甲\n乙\n丙\n丁\n戊\n己\n\n2.庚\n辛\n壬\n癸', encoding='utf-8')
            with patch.object(sources, 'clean', lambda text, language='zh': ''.join(re.findall('[\u3400-\u9fff]', text))):
                definition = sources.text_source(path)
            self.assertEqual([len(verse) for verse in definition['verses']], [2, 1])
            self.assertEqual(definition['regions']['r1'], {'kind': 'text', 'startLine': 2, 'endLine': 5})
            self.assertEqual(definition['regions']['r2'], {'kind': 'text', 'startLine': 6, 'endLine': 7})
            self.assertEqual(definition['verses'][1][0]['text'], '庚 辛 壬 癸')

    def test_english_and_chinese_verse_headings_do_not_create_fictitious_stanzas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '06-001示例.txt'
            path.write_text('标题\n\n1 Lord I come\n主我来到\n\n2 Give me strength\n赐我力量', encoding='utf-8')
            with patch.object(sources, 'clean', lambda text, language='zh': ''.join(re.findall('[A-Za-z]' if language == 'en' else '[\u3400-\u9fff]', text))):
                definition = sources.text_source(path, 'en')
                self.assertEqual([len(verse) for verse in definition['verses']], [1, 1])
                self.assertEqual(definition['verses'][0][0]['text'], 'Lord I come')
                path.write_text('标题\n\n（一）\n第一段歌词\n\n第二组歌词\n\n（二）\n另一段歌词', encoding='utf-8')
                definition = sources.text_source(path)
                self.assertEqual([len(verse) for verse in definition['verses']], [2, 1])

    def test_inventory_identifies_title_only_and_placeholder_lyrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'data/06-024Amazing Grace.txt').write_text('Alan Jackson - Amazing Grace')
            (root / 'data/06-015示例.txt').write_text('标题\n\n歌词整理中。。。\nhttps://example.com/song')
            self.assertTrue(all(not item['lyricsAvailable'] for item in batch.inventory(root)))

    def test_final_repeat_instruction_reuses_original_regions_in_performed_order(self):
        original = '标题\n\n1.第一段歌词\n下一行\n\n2.第二段歌词\n末句\n\n（重复一次）\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'example.txt'
            path.write_text(original)
            source = sources.text_source(path)
            self.assertEqual(source['regions'], {
                'r1': {'kind': 'text', 'startLine': 2, 'endLine': 3},
                'r2': {'kind': 'text', 'startLine': 5, 'endLine': 6}})
            self.assertEqual([verse[0]['region'] for verse in source['verses']], ['r1', 'r2', 'r1', 'r2'])
            self.assertEqual(path.read_text(), original)
            path.write_text(original.replace('（重复一次）', '我们重复一次爱的诗歌'))
            ordinary = sources.text_source(path)
            self.assertEqual(len(ordinary['verses']), 2)
            self.assertIn('我们重复一次爱的诗歌', ordinary['verses'][-1][-1]['text'])
            path.write_text(original + '最后还有歌词\n')
            self.assertEqual(len(sources.text_source(path)['verses']), 2)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional audio dependencies')
    def test_text_repeats_mark_only_backward_reading_moves_as_returns(self):
        from sync_batch_align import recording_cues
        regions = {'r1': {'kind': 'text', 'startLine': 2, 'endLine': 5},
                   'r2': {'kind': 'text', 'startLine': 8, 'endLine': 11}}
        definitions = [{'region': region, 'label': region} for region in ['r1', 'r2', 'r1', 'r2']]
        cues = recording_cues(definitions, [1, 11, 21, 31], regions, 40, set())
        self.assertEqual([cue['transition'] for cue in cues], ['intro', 'reading', 'return', 'reading'])

    def test_artist_and_songwriting_credits_are_not_english_lyric_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '06-001示例.txt'
            path.write_text('Title\n\n1 He will hold me fast\nFor my Savior loves me so\n\n歌手：Keith & Kristyn Getty\n作词 : Ada Habershon/Matt Merker')
            definition = sources.text_source(path, 'en')
            self.assertEqual(len(definition['regions']), 1)
            self.assertEqual(definition['verses'][0][0]['text'], 'He will hold me fast For my Savior loves me so')
            path.write_text('Title\n\n1 He will hold me fast\nFor my Savior loves me so\n\n--\nArtist Name - Album Title')
            definition = sources.text_source(path, 'en')
            self.assertEqual(len(definition['regions']), 1)
            path.write_text('Title\n\n1 He will hold me fast\n--\n2 Jesus - my Savior forever')
            self.assertEqual(len(sources.text_source(path, 'en')['verses']), 2)

    def test_album_credit_after_bilingual_lyrics_preserves_original_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '06-011示例.txt'
            for credit in ['所属专辑：Precious Memories', '所屬專輯：Precious Memories']:
                path.write_text('Title\n\nHow great Thou art\n你真伟大\n\n---\n歌手：Alan Jackson\n' + credit)
                definition = sources.text_source(path, 'en')
                self.assertEqual(definition['regions'], {'r1': {'kind': 'text', 'startLine': 2, 'endLine': 3}})
                self.assertEqual(definition['verses'][0][0]['text'], 'How great Thou art')

    def test_image_order_crosses_printed_rows_and_repeats_the_shared_chorus(self):
        def row(top, text):
            return {'top': top, 'bottom': top + .025, 'text': text, 'confidence': .99}
        recognition = {'rows': [row(.1, '11335566'), row(.15, '1.第一段的歌词'), row(.20, '2.第二段的歌词'),
                                row(.3, '33221155'), row(.35, '1.第一段的后半'), row(.40, '2.第二段的后半'),
                                row(.5, '55566677'), row(.55, '共同演唱的副歌')]}
        definition = sources.image_source(recognition)
        self.assertEqual(len(definition['verses']), 2)
        self.assertEqual([item['region'] for item in definition['verses'][0]], ['s1_v1', 's2_v1', 's3_chorus'])
        self.assertEqual([item['region'] for item in definition['verses'][1]], ['s1_v2', 's2_v2', 's3_chorus'])
        self.assertGreater(definition['regions']['s1_v2']['top'], definition['regions']['s1_v1']['top'])

    def test_atomic_output_and_source_hash_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache' / 'result.json'
            batch.save(path, {'text': '中文', 'at': 12})
            old = batch.checksum(path)
            self.assertEqual(json.loads(path.read_text())['text'], '中文')
            batch.save(path, {'text': '中文', 'at': 13})
            self.assertNotEqual(batch.checksum(path), old)
            self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_publishing_rejects_stale_sources_and_preserves_existing_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'cache'
            song = {'source': '01-001示例.txt', 'sha256': 'a' * 64,
                    'tracks': [{'file': '01-001示例.mp3', 'sha256': 'b' * 64}], 'type': 'text'}
            candidate = {'source': {'sha256': 'c' * 64}, 'tracks': [{**song['tracks'][0], 'reviewed': True}],
                         'provenance': {'evidence': 'docs/evidence/01-001示例.txt.json'}}
            path = cache / 'ready' / (song['source'] + '.json')
            batch.save(path, candidate)
            with patch.object(batch, 'ROOT', root), self.assertRaises(ValueError):
                batch.publish(song, cache)
            candidate['source']['sha256'] = song['sha256']
            batch.save(path, candidate)
            batch.save(cache / 'alignment' / path.name, {'sourceHash': song['sha256'], 'status': 'ready'})
            existing = root / 'data/sync' / path.name
            batch.save(existing, {'preserved': True})
            with patch.object(batch, 'ROOT', root), patch.object(batch.subprocess, 'run'), self.assertRaises(ValueError):
                batch.publish(song, cache)
            self.assertEqual(json.loads(existing.read_text()), {'preserved': True})

    def test_progress_does_not_report_old_ready_candidate_after_failed_retry_or_asset_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'cache'
            song = {'songId': '01-001', 'source': '01-001示例.txt', 'sha256': 'a' * 64,
                    'tracks': [{'file': '01-001示例.mp3', 'sha256': 'b' * 64}],
                    'type': 'text', 'lyricsAvailable': True}
            candidate = {'source': {'sha256': song['sha256']},
                         'tracks': [{**song['tracks'][0], 'reviewed': True}],
                         'provenance': {'evidence': 'docs/sync-bulk-evidence/01-001示例.txt.json'}}
            ready = cache / 'ready' / (song['source'] + '.json')
            evidence = cache / 'alignment' / ready.name
            batch.save(ready, candidate)
            batch.save(evidence, {'sourceHash': song['sha256'], 'status': 'ready'})
            with patch.object(batch, 'ROOT', root):
                self.assertEqual(batch.progress([song], cache), {'ready-to-verify': 1})
                batch.save(evidence, {'sourceHash': song['sha256'], 'status': 'needs-calibration', 'reason': '重试未通过'})
                self.assertEqual(batch.progress([song], cache), {'needs-calibration': 1})
                batch.save(evidence, {'sourceHash': song['sha256'], 'status': 'ready'})
                song['tracks'][0]['sha256'] = 'c' * 64
                self.assertEqual(batch.progress([song], cache), {'needs-calibration': 1})
                song['tracks'][0]['sha256'] = 'b' * 64
                song['sha256'] = 'd' * 64
                self.assertEqual(batch.progress([song], cache), {'needs-calibration': 1})

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_same_tune_reference_recovers_only_first_pickup_from_multiple_later_anchors(self):
        mapped = [12.8, 28.2, 47.2, 65.8, 85.0, 103.5, 122.6, 141.1]
        music = {'shifts': [0, 37.6, 75.3, 112.9], 'similarity': .97, 'initialOnsetEstimate': 8.6}
        times, decisions = transfer.consistent_phases(mapped, music, 4, 2)
        self.assertAlmostEqual(times[0], 9.7)
        self.assertEqual(times[1:], mapped[1:])
        self.assertEqual(decisions[0]['originalAt'], 12.8)
        with self.assertRaises(ValueError):
            transfer.consistent_phases(mapped, {**music, 'similarity': .94}, 4, 2)
        with self.assertRaises(ValueError):
            transfer.consistent_phases(mapped[:4], {**music, 'shifts': [0, 37.6]}, 2, 2)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_same_tune_reference_rejects_disagreement_at_later_passages_and_missing_verses(self):
        music = {'shifts': [0, 40, 80], 'similarity': .99, 'initialOnsetEstimate': 10}
        with self.assertRaises(ValueError):
            transfer.consistent_phases([10, 30, 50, 70, 90, 112], music, 3, 2)
        with self.assertRaises(ValueError):
            transfer.consistent_phases([10, 30, 50, 70], music, 3, 2)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_complete_cycle_locates_actual_transposed_music_start(self):
        import numpy as np
        generator = np.random.default_rng(17)
        melody = np.repeat(generator.integers(0, 12, 24), 10)
        query = np.eye(12, dtype=np.float32)[:, melody]
        query -= query.mean(axis=0)
        query /= np.linalg.norm(query, axis=0)
        target = np.zeros((12, 400), dtype=np.float32)
        target[:, 30:270] = np.roll(query, 5, axis=0)
        match = transfer.match_cycle((np.arange(240) * .1, query),
                                     (np.arange(400) * .1, target), 0, 24, 24, 4)
        self.assertEqual(match['best']['key'], 5)
        self.assertAlmostEqual(match['best']['at'], 3.4, delta=.1)
        self.assertLess(match['best']['cost'], .01)
        self.assertGreater(match['runnerUp']['cost'] - match['best']['cost'], .08)
        passages = transfer.match_cycle((np.arange(240) * .1, query),
                                        (np.arange(400) * .1, target), 0, 24, 24, 4,
                                        cue_offsets=[0, 16])
        self.assertEqual(passages['best']['key'], 5)
        self.assertAlmostEqual(passages['best']['starts'][0], 3, delta=.1)
        self.assertAlmostEqual(passages['best']['starts'][1], 19, delta=.1)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional MIDI dependencies')
    def test_text_midi_passages_require_verified_region_openings_and_melody(self):
        import copy
        melody = {'notes': [1] * 100, 'pitchOffsets': [0] * 100, 'spatialWeights': [1] * 100,
                  'onsetTimes': list(range(10)), 'onsetPitches': [0, 2, 4, 5, 7, 9, 7, 5, 4, 2]}
        source = {'verses': [[{'text': '第一段主歌起句'}, {'text': '共同副歌起句'}],
                             [{'text': '第二段主歌起句'}, {'text': '共同副歌起句'}]]}
        profile = {'regionOpenings': [['第一段主歌', '共同副歌'], ['第二段主歌', '共同副歌']],
                   'readingBoundaries': [{'onsetIndex': 0, 'pitches': [0, 2, 4, 5, 7]},
                                         {'onsetIndex': 5, 'pitches': [9, 7, 5, 4, 2]}]}
        offsets, rows = midi_sync.reading_rows(source, profile, melody)
        self.assertEqual(offsets, [0, 5])
        self.assertEqual([len(row['notes']) for row in rows], [50, 50])
        wrong = copy.deepcopy(profile)
        wrong['regionOpenings'][1][1] = '尚未核对'
        with self.assertRaises(ValueError):
            midi_sync.reading_rows(source, wrong, melody)
        wrong = copy.deepcopy(profile)
        wrong['readingBoundaries'][1]['pitches'][0] = 11
        with self.assertRaises(ValueError):
            midi_sync.reading_rows(source, wrong, melody)
        with self.assertRaises(ValueError):
            midi_sync.reading_rows(source, {}, melody)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional MIDI dependencies')
    def test_retrieved_midi_references_pin_audio_and_apply_stronger_actual_match_limits(self):
        import copy
        song = {'sha256': 'source', 'reference': 'voice.mp3', 'tracks': [{'file': 'voice.mp3', 'sha256': 'audio'}]}
        profile = {'file': 'tune.mid', 'referenceDiscovery': {'sourceHash': 'source', 'audio': 'voice.mp3',
            'audioHash': 'audio', 'matches': [{'file': 'tune.mid', 'similarity': .8},
                                           {'file': 'other.mid', 'similarity': .6}]}}
        self.assertEqual(midi_sync.reference_limits(song, profile), (.25, .20))
        self.assertEqual(midi_sync.reference_limits(song, {}), (.35, .35))
        for field, value in [('audioHash', 'old-audio'), ('sourceHash', 'old-source'), ('audio', 'other.mp3')]:
            wrong = copy.deepcopy(profile)
            wrong['referenceDiscovery'][field] = value
            with self.assertRaises(ValueError):
                midi_sync.reference_limits(song, wrong)
        wrong = copy.deepcopy(profile)
        wrong['referenceDiscovery']['matches'][1]['similarity'] = .75
        with self.assertRaises(ValueError):
            midi_sync.reference_limits(song, wrong)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional MIDI dependencies')
    def test_retrieved_reference_rejects_an_unexplained_extra_complete_cycle(self):
        import numpy as np
        harmonic = {'best': {'cost': .1, 'key': 2, 'starts': [140], 'span': 40}, 'runnerUp': {'cost': .5}}
        melodic = {'best': {'cost': .1, 'key': 2, 'starts': [140.1], 'span': 40}, 'runnerUp': {'cost': .5}}
        target = (np.arange(250), np.zeros((12, 250)))
        music = {'period': 40}
        with patch.object(midi_sync.same_tune, 'match_cycle', return_value=harmonic), \
                patch.object(midi_sync, 'match_template', return_value=melodic):
            with self.assertRaisesRegex(ValueError, '额外完整'):
                midi_sync.check_extra_cycle(None, None, target, 40, music, 100, (.25, .20))
            harmonic['best']['starts'] = [140.1]
            melodic['best']['starts'] = [np.float32(140.6)]
            with self.assertRaisesRegex(ValueError, '额外完整'):
                midi_sync.check_extra_cycle(None, None, target, 40, music, 100, (.25, .20))
            melodic['best']['starts'] = [100.1]
            harmonic['best']['starts'] = [100]
            result = midi_sync.check_extra_cycle(None, None, target, 40, music, 100, (.25, .20))
            self.assertTrue(result['checked'])
        with patch.object(midi_sync.same_tune, 'match_cycle') as match:
            result = midi_sync.check_extra_cycle(None, None, (np.arange(150), None), 40, music, 100, (.25, .20))
            self.assertFalse(result['checked'])
            match.assert_not_called()

    @unittest.skipIf(confirm_boundaries is None, 'requires optional symbolic dependencies')
    def test_symbolic_and_optical_pair_requires_all_boundaries_key_and_full_span(self):
        import copy
        symbolic = {'best': {'cost': .24, 'key': 11, 'starts': [6, 17, 28, 39], 'span': 44},
                    'runnerUp': {'cost': .44}}
        optical = {'best': {'cost': .17, 'key': 9, 'starts': [6.1, 16.9, 28.2, 39], 'span': 44},
                   'runnerUp': {'cost': .42}}
        self.assertEqual(midi_image.paired_melody_times(symbolic, optical, 44, 10), [6.05, 16.95, 28.1, 39])
        for field, value in [('key', 8), ('starts', [6, 17, 28.6, 39]), ('starts', [6, 17, 28]),
                             ('span', 20), ('cost', .36)]:
            wrong = copy.deepcopy(optical)
            wrong['best'][field] = value
            with self.assertRaises(ValueError):
                midi_image.paired_melody_times(symbolic, wrong, 44, 10)
        wrong = copy.deepcopy(optical)
        wrong['runnerUp']['cost'] = .2
        with self.assertRaises(ValueError):
            midi_image.paired_melody_times(symbolic, wrong, 44, 10)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional symbolic dependencies')
    def test_complete_optical_review_is_bound_to_selected_recording_hashes(self):
        tracks = [{'file': 'main.mp3', 'sha256': 'main-hash'}, {'file': 'lead.mp3', 'sha256': 'lead-hash'}]
        review = {'version': 1, 'recordings': [tracks[0]]}
        self.assertEqual(midi_image.complete_optical_recordings({}, tracks), set())
        self.assertEqual(midi_image.complete_optical_recordings({'completeOpticalCheck': review}, tracks), {'main.mp3'})
        for invalid in [None, {'version': True, 'recordings': [tracks[0]]}, {'version': 2, 'recordings': [tracks[0]]},
                        {'version': 1, 'recordings': []}, {'version': 1, 'recordings': [tracks[0], tracks[0]]},
                        {'version': 1, 'recordings': [{'file': 'other.mp3', 'sha256': 'main-hash'}]},
                        {'version': 1, 'recordings': [{'file': 'main.mp3', 'sha256': 'old-hash'}]}]:
            profile = {'completeOpticalCheck': invalid}
            if invalid is None:
                self.assertEqual(midi_image.complete_optical_recordings(profile, tracks), set())
            else:
                with self.assertRaises(ValueError):
                    midi_image.complete_optical_recordings(profile, tracks)
        with self.assertRaises(ValueError):
            midi_image.complete_optical_recordings({'completeOpticalCheck': review, 'referenceDiscovery': {'matches': []}}, tracks)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional symbolic dependencies')
    def test_complete_optical_paths_measure_variable_stanza_tempos_and_reject_disagreements(self):
        import copy
        import numpy as np

        def match(starts, span, key):
            return {'best': {'cost': .2, 'key': key, 'starts': starts, 'span': span}, 'runnerUp': {'cost': .5}}

        results = [match([5, 15, 27, 37, 47, 57], 61, 9), match([5.1, 15.1, 27.1, 37.1, 47.1, 57.1], 60.9, 7)]
        for start, span in [(5, 22), (27, 20), (47, 19)]:
            results.extend([match([start + .1, start + 10], span, 9), match([start + .2, start + 10.1], span, 7)])
        source, target = {'verses': [[{}, {}]] * 3}, (np.array([0., 70.]), None)

        def run(values, actual_target=target):
            evidence = {}
            with patch.object(midi_image, 'features', return_value=actual_target), \
                    patch.object(midi_image, 'match_template', side_effect=values):
                times, _ = midi_image.complete_optical_recording_times(Path('main.mp3'), source,
                    [{}, {}], [{}, {}], [0, 10], Path('/cache'), 10, evidence)
            return times, evidence

        times, evidence = run(copy.deepcopy(results))
        self.assertEqual(times, [5.1, 15.05, 27.1, 37.05, 47.1, 57.05])
        self.assertEqual(len(evidence['onsetEstimates']), 4)
        self.assertGreater(evidence['measuredVerseSpans'][0], evidence['measuredVerseSpans'][-1])
        for index, field, value in [(0, 'cost', .36), (1, 'span', 30), (3, 'key', 6),
                                    (2, 'key', 8), (3, 'starts', [5.7, 15.1]), (7, 'starts', [47.2]),
                                    (0, 'starts', [5, 15, 27, 37]), (0, 'span', 100)]:
            wrong = copy.deepcopy(results)
            wrong[index]['best'][field] = value
            with self.subTest(index=index, field=field):
                with self.assertRaises(ValueError):
                    run(wrong)
        # Each local pair agrees, but two valid pairs disagree with each other.
        wrong = copy.deepcopy(results)
        wrong[2]['best']['starts'] = [5.7, 15]
        wrong[3]['best']['starts'] = [5.8, 15.1]
        with self.assertRaisesRegex(ValueError, '0.5秒'):
            run(wrong)
        wrong = copy.deepcopy(results)
        wrong[2]['best']['key'], wrong[3]['best']['key'] = 8, 6
        with self.assertRaisesRegex(ValueError, '调性'):
            run(wrong)
        # A spare whole verse at either end must remain unresolved.
        wrong = copy.deepcopy(results)
        for result in wrong[:2]:
            result['best']['starts'] = [start - 5 for start in result['best']['starts']]
        with self.assertRaisesRegex(ValueError, '首尾'):
            run(wrong, (np.array([0., 80.]), None))
        wrong = copy.deepcopy(results)
        for result in wrong[:2]:
            result['best']['starts'] = [start + 13 for start in result['best']['starts']]
        with self.assertRaisesRegex(ValueError, '首尾'):
            run(wrong, (np.array([0., 80.]), None))
        with self.assertRaisesRegex(ValueError, '至少三个'):
            midi_image.complete_optical_recording_times(None, {'verses': [[{}]] * 2}, [{}], [{}], [0], None)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_score_accidentals_are_kept_separate_from_scale_degrees(self):
        from sync_batch_score import accidental_offsets
        text = '3.*2 3 | b6 ♮6 #4'
        characters = [(char, index) for index, char in enumerate(text)]
        indices = [index for index, char in enumerate(text) if char.isdigit()]
        self.assertEqual(accidental_offsets(characters, indices), [0, 1, 0, -1, 0, 1])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_optical_rhythm_distinguishes_beams_dots_holds_and_bar_lines(self):
        import numpy as np
        from sync_batch_score import optical_rhythm
        ink = np.zeros((60, 160), dtype=np.uint8)
        for x in (20, 70, 130):
            ink[10:34, x:x + 10] = 1
        ink[38:40, 15:36] = 1
        ink[44:46, 15:36] = 1
        ink[20:23, 35:38] = 1
        ink[38:41, 74:77] = 1  # Octave dot is below the glyph, not a duration dot.
        ink[22:24, 85:94] = 1
        ink[22:24, 105:114] = 1
        ink[5:53, 120:122] = 1
        ink[38:40, 125:146] = 1
        weights, diagnostics = optical_rhythm(ink, [25 / 160, 75 / 160, 135 / 160], 50, [1, 4, 2])
        self.assertEqual(weights, [.375, 3, .5])
        self.assertEqual(diagnostics[0]['beams'], 2)
        self.assertFalse(diagnostics[1]['dot'])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_transcript_order_includes_an_actually_recognized_final_reprise(self):
        definitions = [{'region': 'r1', 'text': 'Morning voices sing the beauty of creation'},
                       {'region': 'r2', 'text': 'Evening praises rise in thanks unto our King'},
                       {'region': 'r3', 'text': 'Forever mercy shines with everlasting love'}]
        words = []
        for definition in definitions + definitions[:2]:
            for word in definition['text'].split():
                at = len(words) * .5
                words.append({'word': word, 'start': at, 'end': at + .4})
        order, estimates, diagnostics = transcript.performed_order(definitions, [{'words': words}], 'en')
        self.assertEqual([item['region'] for item in order], ['r1', 'r2', 'r3', 'r1', 'r2'])
        self.assertEqual(len(estimates), 5)
        with self.assertRaisesRegex(ValueError, '全部正文'):
            transcript.performed_order(definitions, [{'words': words[:8]}], 'en')
        with self.assertRaisesRegex(ValueError, '尾部'):
            extra = [{'word': 'unrelated material absent from all documented verses completely',
                      'start': words[-1]['end'] + 1, 'end': words[-1]['end'] + 2}]
            transcript.performed_order(definitions, [{'words': words[:24] + extra}], 'en')

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_transcript_confirmation_requires_two_windows_and_a_nearby_lexical_anchor(self):
        rows = [{'index': index, 'window': window, 'at': estimate + window * .1,
                 'valid': index > 0, 'positiveDurationFraction': .9, 'meanProbability': .6}
                for index, estimate in enumerate([0, 10, 20]) for window in range(2)]
        times, decisions = transcript.confirm_transcript_windows(rows, [0, 10, 20])
        self.assertEqual(times, [.05, 10.05, 20.05])
        self.assertEqual(len(decisions), 2)
        with self.assertRaises(ValueError):
            transcript.confirm_transcript_windows(rows[:-1], [0, 10, 20])
        with self.assertRaises(ValueError):
            transcript.confirm_transcript_windows(rows, [0, 5, 20])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_edit_alignment_keeps_two_imperfect_choruses_as_separate_performances(self):
        chorus = 'He will hold me fast He will hold me fast For my Savior loves me so He will hold me fast'
        definitions = [{'region': 'r1', 'text': chorus}, {'region': 'r2', 'text': chorus}]
        first = 'He will hold me back He will hold me back Oh must you hold me so He will hold me back'.split()
        second = 'He will hold me back He will hold me back For my Savior loves me so He will hold me fair'.split()
        words = [{'word': word, 'start': index * .5, 'end': index * .5 + .4}
                 for index, word in enumerate(first + second)]
        anchors, count = transcript.match_order(definitions, words, 'en')
        self.assertEqual([anchor['index'] for anchor in anchors], [0, 1])
        self.assertAlmostEqual(anchors[1]['at'], len(first) * .5, delta=1)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_additional_window_resolves_one_disagreement_but_rejects_two_distinct_clusters(self):
        rows = [{'index': 0, 'window': window, 'at': at, 'valid': True}
                for window, at in enumerate([10, 10.7, 10.75])]
        times, decisions = transcript.confirm_transcript_windows(rows, [10.5])
        self.assertAlmostEqual(times[0], 10.72)
        rows = [{'index': 0, 'window': window, 'at': at, 'valid': True}
                for window, at in enumerate([10, 10.1, 13, 13.1])]
        with self.assertRaises(ValueError):
            transcript.confirm_transcript_windows(rows, [11.4], [(11.4, 11.7)])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_lexical_word_interval_accounts_for_silence_in_its_initial_timestamp(self):
        rows = [{'index': 0, 'window': window, 'at': 22 + window * .1, 'valid': True}
                for window in range(2)]
        with self.assertRaises(ValueError):
            transcript.confirm_transcript_windows(rows, [20])
        times, decisions = transcript.confirm_transcript_windows(rows, [20], [(20, 22.4)])
        self.assertAlmostEqual(times[0], 22.05)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_extra_cached_windows_do_not_recompute_already_saved_original_windows(self):
        import numpy as np
        class Model:
            def feature_extractor(self, samples):
                raise AssertionError('completed windows must be reused')
        rows = [{'index': 0, 'window': window, 'at': 5, 'valid': True} for window in range(3)]
        result = align_windows(Model(), None, np.zeros(16000 * 20), [{'text': '天地'}], [5], 'zh', rows)
        self.assertEqual(result, rows)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_held_first_word_requires_two_matching_word_ends_and_identity(self):
        rows = [{'index': 0, 'window': window, 'at': 159.76, 'valid': True, 'firstWord': 'i',
                 'firstWordStart': 159.76, 'firstWordEnd': 163.06, 'firstWordProbability': probability}
                for window, probability in enumerate([.27, .51])]
        times, decisions = transcript.confirm_transcript_windows(rows, [161.48], [(161.48, 162.88)], ['i'])
        self.assertEqual(times, [159.76])
        self.assertEqual(decisions[0]['word'], 'i')
        rows[1]['firstWord'] = 'you'
        with self.assertRaises(ValueError):
            transcript.confirm_transcript_windows(rows, [161.48], [(161.48, 162.88)], ['i'])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_zero_duration_first_word_cannot_determine_a_boundary(self):
        rows = [{'index': 0, 'window': window, 'at': 10, 'valid': True,
                 'firstWordStart': 10, 'firstWordEnd': 10.4, 'firstWordProbability': probability}
                for window, probability in enumerate([.8, .01])]
        self.assertEqual(transcript.confirm_transcript_windows(rows, [10])[0], [10])
        rows[1].update(firstWordProbability=.8, firstWordEnd=10)
        with self.assertRaises(ValueError):
            transcript.confirm_transcript_windows(rows, [10])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_overlapping_held_vowel_onsets_form_one_compact_cluster(self):
        rows = [{'index': 0, 'window': window, 'at': at, 'valid': True, 'firstWord': 'all',
                 'firstWordStart': at, 'firstWordEnd': 202.42, 'firstWordProbability': probability}
                for window, (at, probability) in enumerate([(200.32, .148), (200.94, .046), (200.02, .121), (200.48, .089)])]
        times, decisions = transcript.confirm_transcript_windows(rows, [201.84], [(201.84, 202.32)], ['all'])
        self.assertEqual(times, [200.4])

    @unittest.skipIf(confirm_boundaries is None or importlib.util.find_spec('mido') is None, 'requires optional MIDI dependencies')
    def test_midi_templates_follow_tempo_changes_and_exclude_percussion(self):
        import mido
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reference.mid'
            score = mido.MidiFile(ticks_per_beat=480)
            tempo = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000),
                                   mido.MetaMessage('set_tempo', tempo=1000000, time=480)])
            melody = mido.MidiTrack([mido.Message('note_on', note=55, velocity=30),
                mido.Message('note_on', note=60, velocity=90),
                mido.Message('note_off', note=60, time=960), mido.Message('note_on', note=64, velocity=90),
                mido.Message('note_off', note=64, time=480), mido.Message('note_off', note=55)])
            drums = mido.MidiTrack([mido.Message('note_on', channel=9, note=90, velocity=120),
                                    mido.Message('note_off', channel=9, note=90, time=1440)])
            score.tracks.extend([tempo, melody, drums])
            score.save(path)
            duration, (times, harmony), row = midi_sync.midi_features(path)
            self.assertAlmostEqual(duration, 2.5)
            self.assertEqual(row['pitchOffsets'][14], 0)
            self.assertEqual(row['pitchOffsets'][16], 4)
            self.assertEqual(int(np.argmax(harmony[:, 1])), 0)
            self.assertEqual(int(np.argmax(harmony[:, 16])), 4)
            self.assertEqual(row['onsetPitches'], [0, 4])
            self.assertEqual(row['onsetTimes'], [0, 1.5])

    @unittest.skipIf(confirm_boundaries is None or importlib.util.find_spec('mido') is None, 'requires optional MIDI dependencies')
    def test_overlapping_unison_does_not_erase_the_held_soprano(self):
        import mido
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unison.mid'
            score = mido.MidiFile(ticks_per_beat=480)
            score.tracks.append(mido.MidiTrack([
                mido.Message('note_on', note=76, velocity=90),
                mido.Message('note_on', note=72, velocity=30),
                mido.Message('note_off', note=72, time=480),
                mido.Message('note_on', note=76, velocity=30),
                mido.Message('note_off', note=76, time=480),
                mido.Message('note_on', note=74, velocity=30, time=480),
                mido.Message('note_off', note=74, time=240),
                mido.Message('note_off', note=76, time=240),
                mido.Message('note_on', note=77, velocity=90),
                mido.Message('note_off', note=77, time=480)]))
            score.save(path)
            duration, (times, harmony), row = midi_sync.midi_features(path)
            self.assertEqual(row['onsetPitches'], [4, 5])
            self.assertEqual(row['onsetTimes'], [0, 2])
            self.assertEqual(row['pitchOffsets'][17], 4)

    @unittest.skipIf(confirm_boundaries is None or importlib.util.find_spec('mido') is None, 'requires optional MIDI dependencies')
    def test_documented_first_melody_track_cycle_preserves_its_full_notes(self):
        import mido
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'two-instruments.mid'
            score = mido.MidiFile(ticks_per_beat=480)
            score.tracks.append(mido.MidiTrack([
                mido.Message('note_on', note=72, velocity=90, time=480),
                mido.Message('note_off', note=72, time=1920),
                mido.Message('note_on', note=74, velocity=90),
                mido.Message('note_off', note=74, time=1920)]))
            score.tracks.append(mido.MidiTrack([
                mido.Message('note_on', note=72, velocity=90, time=8160),
                mido.Message('note_off', note=72, time=1920)]))
            score.save(path)
            duration, (times, harmony), row = midi_sync.midi_features(path, 'first-melody-track')
            self.assertAlmostEqual(duration, 4)
            self.assertEqual(row['onsetTimes'], [0, 2])
            self.assertEqual(row['onsetPitches'], [0, 2])
            self.assertAlmostEqual(times[-1], 3.9)
            self.assertEqual(len(row['notes']), len(times))
            self.assertGreater(midi_sync.midi_features(path)[0], duration)
            with self.assertRaises(ValueError):
                midi_sync.midi_features(path, 'guessed-duration')

    @unittest.skipIf(confirm_boundaries is None, 'requires optional symbolic dependencies')
    def test_symbolic_score_mapping_rejects_an_ambiguous_repeated_opening_note(self):
        rows = [{'notes': [1, 2, 3, 4]}, {'notes': [5, 6, 7, 1, 2]}]
        times, proof = score_row_offsets(rows, list(range(9)), [0, 2, 4, 5, 7, 9, 11, 0, 2])
        self.assertEqual(times, [0, 4])
        self.assertEqual(proof['editCost'], 0)
        # One inserted nonmatching note does not obscure the exact row start.
        times, proof = score_row_offsets(rows, list(range(10)), [0, 2, 4, 5, 3, 7, 9, 11, 0, 2])
        self.assertEqual(times, [0, 5])
        # Either G could begin the second row in an equally good full alignment.
        with self.assertRaisesRegex(ValueError, '多个'):
            score_row_offsets(rows, list(range(10)), [0, 2, 4, 5, 7, 7, 9, 11, 0, 2])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional symbolic dependencies')
    def test_documented_row_witnesses_require_five_exact_pitches_and_only_skip_ties(self):
        rows = [{'notes': [1, 1, 2, 3, 4, 5]}, {'notes': [6, 7, 1, 2, 3]}]
        times = [0, .5, 1, 1.5, 2, 3, 3.5, 4, 4.5, 5]
        pitches = [2, 4, 6, 7, 9, 11, 1, 2, 4, 6]
        anchors = [{'sourceNoteIndices': [0, 2, 3, 4, 5], 'referenceNoteIndices': [0, 1, 2, 3, 4],
                    'tiedSourceNoteIndices': [1]},
                   {'sourceNoteIndices': [0, 1, 2, 3, 4], 'referenceNoteIndices': [5, 6, 7, 8, 9],
                    'tiedSourceNoteIndices': []}]
        offsets, proof = documented_row_offsets(rows, times, pitches, anchors, 2)
        self.assertEqual(offsets, [0, 3])
        self.assertEqual(proof['rowStarts'][0]['sourcePitches'], [0, 2, 4, 5, 7])
        wrong = pitches.copy()
        wrong[3] = 8
        with self.assertRaisesRegex(ValueError, '不符'):
            documented_row_offsets(rows, times, wrong, anchors, 2)
        rows[0]['notes'][1] = 7
        with self.assertRaisesRegex(ValueError, '连结'):
            documented_row_offsets(rows, times, pitches, anchors, 2)

    @unittest.skipIf(confirm_boundaries is None or importlib.util.find_spec('mido') is None, 'requires optional MIDI dependencies')
    def test_percussion_only_midi_cannot_be_a_lyric_melody_reference(self):
        import mido
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drums.mid'
            score = mido.MidiFile(ticks_per_beat=480)
            score.tracks.append(mido.MidiTrack([mido.Message('note_on', channel=9, note=60),
                mido.Message('note_off', channel=9, note=60, time=2400)]))
            score.save(path)
            with self.assertRaisesRegex(ValueError, '打击乐'):
                midi_sync.midi_features(path)

    def test_shared_chorus_continuation_stays_visible_for_every_printed_verse(self):
        def row(top, text):
            return {'top': top, 'bottom': top + .025, 'text': text, 'confidence': .99}
        definition = sources.image_source({'rows': [row(.1, '11335566'), row(.15, '1.第一段的歌词'),
            row(.2, '2.第二段的歌词'), row(.3, '（副歌）'), row(.4, '33355566'),
            row(.45, '1.哈利路亚共同歌词只印一次'), row(.5, '2.哈利路亚')]})
        self.assertEqual(definition['regions']['s2_v1'], definition['regions']['s2_v2'])
        self.assertLess(definition['regions']['s2_v2']['top'], .45)

    def test_tune_reference_footer_is_not_a_fifth_lyric_row(self):
        def row(top, text):
            return {'top': top, 'bottom': top + .025, 'text': text, 'confidence': .99}
        definition = sources.image_source({'rows': [row(.1, '11335566'), row(.15, '第一段实际歌词'),
            row(.2, '2.第二段实际歌词'), row(.3, '通用调399三一颂')]})
        self.assertEqual(len(definition['verses']), 2)
        self.assertEqual(definition['verses'][0][0]['text'], '第一段实际歌词')

    def test_one_misrecognized_bar_symbol_does_not_hide_the_last_music_system(self):
        def row(top, text):
            return {'top': top, 'bottom': top + .025, 'text': text, 'confidence': .95}
        definition = sources.image_source({'rows': [row(.1, '11335566'), row(.15, '第一谱行的歌词'),
            row(.3, '冏3211---1-1-'), row(.35, '最后一行的歌词')]})
        self.assertEqual(len(definition['systems']), 2)

    def test_short_lyric_rows_and_missing_number_punctuation_remain_in_all_stanzas(self):
        def row(top, text):
            return {'top': top, 'bottom': top + .02, 'text': text, 'confidence': .99}
        recognition = {'rows': [row(.1, '12345566'), row(.15, '1.第一段歌词'),
            row(.2, '2.第二段歌词'), row(.3, '53344333'), row(.35, '我来。'), row(.4, "'2我来。") ]}
        definition = sources.image_source(recognition)
        self.assertEqual(len(definition['systems']), 2)
        self.assertEqual(len(definition['verses']), 2)
        self.assertEqual(definition['verses'][0][-1]['text'], '我来。')
        self.assertEqual(definition['verses'][1][-1]['text'], '我来。')

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_music_resolves_a_conflict_only_after_two_independent_acoustic_anchors(self):
        verses = [[{'region': 'r' + str(i)}] for i in range(5)]
        music = {'shifts': [0, 24.7, 49.4, 74.1, 98.8], 'similarity': .97}
        rows = []
        for index, pair in enumerate([(1, 3), (31.66, 33.2), (57.98, 58.02), (82.7, 82.72), (107.26, 106.48)]):
            for window, at in enumerate(pair):
                rows.append({'index': index, 'window': window, 'at': at, 'valid': index > 0})
        times, first_indices, decisions = confirm_boundaries(verses, music, rows)
        self.assertAlmostEqual(times[0], 8.55, delta=.1)
        self.assertEqual(times[1], 33.2)
        self.assertEqual(times[4], 107.26)
        self.assertEqual([row['index'] for row in decisions], [1, 4])
        for row in rows:
            if row['index'] == 3:
                row['valid'] = False
        with self.assertRaises(ValueError):
            confirm_boundaries(verses, music, rows)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_alignment_search_includes_both_ends_for_longer_and_shorter_arrangements(self):
        for n, m, lag in [(1000, 500, 200), (500, 1000, -200), (1000, 900, -200)]:
            band = alignment_band(n, m, lag)
            self.assertLess(abs(lag), band)
            self.assertLess(abs(m - n - lag), band)

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_changed_system_count_cannot_silently_truncate_the_performance_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, '谱行数量'):
                export_result({'reference': 'missing.mp3'}, root, root, root / 'evidence.json', {},
                              0, {}, [{'region': 'a'}, {'region': 'b'}], [10], [0])
            self.assertFalse((root / 'evidence.json').exists())

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_shorter_image_recording_requires_its_own_complete_score_match(self):
        from types import SimpleNamespace
        from time import monotonic
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            song = {'songId': '05-001', 'source': '05-001示例.jpg', 'sha256': 'a' * 64,
                    'type': 'image', 'reference': 'lead.mp3',
                    'tracks': [{'file': 'lead.mp3'}, {'file': 'short.mp3'}]}
            first = [{'region': 'a', 'label': '一'}, {'region': 'b', 'label': '二'}]
            second = [{'region': 'c', 'label': '三'}, {'region': 'd', 'label': '四'}]
            regions = {key: {'kind': 'image', 'top': i * .1, 'bottom': (i + 1) * .1}
                       for i, key in enumerate('abcd')}
            source = {'regions': regions, 'verses': [first, second]}
            (root / 'ocr').mkdir()
            (root / 'ocr' / (song['source'] + '.json')).write_text('{}')
            for independent_times, success in [([5, 20], True), ([5], False), ([5, 101], False)]:
                evidence = {'scoreMatching': {'algorithmVersion': 3}}
                with patch('mutagen.mp3.MP3', return_value=SimpleNamespace(info=SimpleNamespace(length=100))), \
                     patch('sync_batch_align.map_recordings', side_effect=ValueError('反复段数不同')), \
                     patch('sync_batch_score.score_times', return_value=(
                         {'regions': regions, 'verses': [first]}, independent_times, {'dualTemplates': 'checked'})):
                    export_result(song, root, root, root / 'evidence.json', evidence, monotonic(),
                                  source, first + second, [5, 20, 45, 60], [0, 2])
                candidate = json.loads((root / 'ready' / (song['source'] + '.json')).read_text())
                self.assertEqual(len(candidate['cues']), 4)
                self.assertEqual(candidate['cues'][2]['transition'], 'return')
                short = candidate['tracks'][1]
                self.assertEqual(short['reviewed'], success)
                if success:
                    self.assertEqual([cue['at'] for cue in short['cues']], [5, 20])
                    self.assertEqual([cue['region'] for cue in short['cues']], ['a', 'b'])
                    self.assertEqual(short['cues'][0]['moveStart'], 0)
                    self.assertIn('short.mp3', evidence['recordingScores'])
                    self.assertNotIn('trackExceptions', evidence)
                else:
                    self.assertEqual(short['cues'], [])
                    self.assertIn('short.mp3', evidence['trackExceptions'])

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_pending_text_track_uses_independent_checks_and_rejects_stale_audio(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            song = {'songId': '03-001', 'source': '03-001示例.txt', 'sha256': 'a' * 64,
                    'type': 'text', 'reference': 'lead.mp3', 'existing': False,
                    'tracks': [{'file': 'lead.mp3', 'sha256': 'b' * 64},
                               {'file': 'base.mp3', 'sha256': 'c' * 64}]}
            source = {'regions': {'r1': {'kind': 'text', 'startLine': 2, 'endLine': 3},
                                  'r2': {'kind': 'text', 'startLine': 5, 'endLine': 6}}}
            order = [{'region': 'r1', 'label': '一'}, {'region': 'r2', 'label': '二'}]
            candidate = {'source': {'sha256': song['sha256']}, 'regions': source['regions'],
                         'tracks': [{**track, 'reviewed': index == 0} for index, track in enumerate(song['tracks'])],
                         'cues': [{'at': 3, 'region': 'r1'}, {'at': 21, 'region': 'r2'}],
                         'provenance': {'method': 'original', 'evidence': 'docs/source.txt.json'}}
            evidence = {'sourceHash': song['sha256'], 'trackHashes': song['tracks'], 'status': 'ready',
                        'trackExceptions': {'base.mp3': 'mapping failed'}}
            candidate_path = cache / 'ready' / (song['source'] + '.json')
            evidence_path = cache / 'alignment' / candidate_path.name
            for stale, times, expected in [(True, [7, 27], False), (False, [7], False), (False, [7, 27], True)]:
                candidate['tracks'][1]['sha256'] = 'd' * 64 if stale else 'c' * 64
                batch.save(candidate_path, candidate)
                batch.save(evidence_path, evidence)
                with patch('mutagen.mp3.MP3', return_value=SimpleNamespace(info=SimpleNamespace(length=80))), \
                     patch('sync_batch_align.vocal_times', return_value=(source, order, times, [0, 1])) as acoustic:
                    review_recordings((song, str(cache), 'models', 2, 'align-tracks', True))
                    if stale:
                        acoustic.assert_not_called()
                    else:
                        self.assertEqual(acoustic.call_args.args[0]['reference'], 'base.mp3')
                result = json.loads(candidate_path.read_text())
                self.assertEqual(result['tracks'][1]['reviewed'], expected)
                self.assertEqual(result['cues'], candidate['cues'])
                if expected:
                    self.assertEqual([cue['at'] for cue in result['tracks'][1]['cues']], [7, 27])
                    saved = json.loads(evidence_path.read_text())
                    self.assertEqual(saved['recordingAcoustics']['base.mp3']['referenceTrack'], 'base.mp3')
                    self.assertEqual(saved['trackExceptions'], {})

    @unittest.skipIf(confirm_boundaries is None, 'requires optional batch audio dependencies')
    def test_window_checkpoint_resumes_completed_chunks_without_duplicate_alignment(self):
        import numpy as np
        class Model:
            calls = 0
            fail_second = True

            def feature_extractor(self, samples):
                return np.zeros((80, 100))

            def encode(self, features):
                self.calls += 1
                if self.fail_second and self.calls == 2:
                    raise InterruptedError('simulated interruption')
                return features

            def find_alignment(self, tokenizer, tokens, encoded, frames):
                return [[{'word': chr(token), 'tokens': [token], 'start': .2 + i * .1,
                          'end': .3 + i * .1, 'probability': .9} for i, token in enumerate(row)] for row in tokens]

        class Tokenizer:
            def encode(self, text):
                return [ord(char) for char in text]

        definitions = [{'text': '天地'}] * 5
        saved = []
        model = Model()
        with self.assertRaises(InterruptedError):
            align_windows(model, Tokenizer(), np.zeros(16000 * 40), definitions, [5, 10, 15, 20, 25],
                          'zh', on_chunk=lambda rows: saved.append(list(rows)))
        self.assertEqual(len(saved[-1]), 8)
        resumed = Model()
        resumed.fail_second = False
        rows = align_windows(resumed, Tokenizer(), np.zeros(16000 * 40), definitions, [5, 10, 15, 20, 25], 'zh', saved[-1])
        self.assertEqual(resumed.calls, 1)
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({(row['index'], row['window']) for row in rows}), 10)


if __name__ == '__main__':
    unittest.main()
