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


if __name__ == '__main__':
    unittest.main()
