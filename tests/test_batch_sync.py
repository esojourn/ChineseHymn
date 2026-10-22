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
    from sync_batch_align import confirm_boundaries, export_result
    from sync_batch_audio import alignment_band
except ImportError:
    confirm_boundaries = None

spec = importlib.util.spec_from_file_location('batch_sync', TOOLS / 'batch-sync.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class BatchTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
