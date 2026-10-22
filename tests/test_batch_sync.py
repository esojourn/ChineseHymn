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
    from sync_batch_align import align_windows, confirm_boundaries, export_result
    from sync_batch_audio import alignment_band
    transfer_spec = importlib.util.spec_from_file_location('transfer_sync', TOOLS / 'transfer-sync.py')
    transfer = importlib.util.module_from_spec(transfer_spec)
    transfer_spec.loader.exec_module(transfer)
    transcript_spec = importlib.util.spec_from_file_location('transcript_sync', TOOLS / 'align-transcript-sync.py')
    transcript = importlib.util.module_from_spec(transcript_spec)
    transcript_spec.loader.exec_module(transcript)
    midi_spec = importlib.util.spec_from_file_location('midi_sync', TOOLS / 'midi-sync.py')
    midi_sync = importlib.util.module_from_spec(midi_spec)
    midi_spec.loader.exec_module(midi_sync)
except ImportError:
    confirm_boundaries = None

spec = importlib.util.spec_from_file_location('batch_sync', TOOLS / 'batch-sync.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class BatchTests(unittest.TestCase):
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
            melody = mido.MidiTrack([mido.Message('note_on', note=60, velocity=90),
                mido.Message('note_off', note=60, time=960), mido.Message('note_on', note=64, velocity=90),
                mido.Message('note_off', note=64, time=480)])
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
