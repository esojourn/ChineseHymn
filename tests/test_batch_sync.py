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
