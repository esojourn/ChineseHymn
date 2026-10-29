import copy
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
from sync_batch_sectional import repeated_source_readings


class RepeatedReadingTests(unittest.TestCase):
    def setUp(self):
        self.source = {'language': 'zh', 'regions': {
            'r1': {'kind': 'text', 'startLine': 2, 'endLine': 5},
            'r2': {'kind': 'text', 'startLine': 6, 'endLine': 9},
            'r3': {'kind': 'text', 'startLine': 10, 'endLine': 13}},
            'verses': [[{'region': name, 'text': name + '完整原文', 'label': '原段'}
                        for name in ['r1', 'r2', 'r3']]]}
        self.review = {'version': 1, 'verses': [['r1', 'r2'], ['r2'], ['r3']],
                       'reason': 'Actual recording repeats the complete second reading after the interlude.'}

    def test_repeat_retains_original_text_coordinates_and_first_occurrences(self):
        original = copy.deepcopy(self.source)
        result = repeated_source_readings(self.source, {'repeatedSourceReadings': self.review})
        self.assertEqual(self.source, original)
        self.assertEqual(result['regions'], original['regions'])
        self.assertEqual(result['verses'][1][0], original['verses'][0][1])
        self.assertEqual(result['repeatedSourceReadings']['returnIndices'], [2])
        result['verses'][1][0]['text'] = 'modified result'
        self.assertEqual(self.source, original)
        self.assertNotEqual(result['verses'][0][1]['text'], 'modified result')

    def test_default_is_identity(self):
        self.assertIs(repeated_source_readings(self.source, {}), self.source)

    def test_missing_unknown_reordered_and_unreviewed_readings_are_rejected(self):
        for verses in [[['r1'], ['r1'], ['r3']], [['r1', 'r2'], ['unknown'], ['r3']],
                       [['r2', 'r1'], ['r2'], ['r3']], [['r1', 'r2'], ['r3']],
                       [['r1'], [], ['r2', 'r3']], [['r1'], [2], ['r2', 'r3']]]:
            with self.subTest(verses=verses), self.assertRaises(ValueError):
                repeated_source_readings(self.source, {'repeatedSourceReadings': {**self.review, 'verses': verses}})
        for update in [{'version': True}, {'reason': ''}, {'at': 42}]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                repeated_source_readings(self.source, {'repeatedSourceReadings': {**self.review, **update}})
        with self.assertRaises(ValueError):
            repeated_source_readings(self.source, {'repeatedSourceReadings': self.review, 'sourceChorusReuse': {}})


class BrokenBarlineTests(unittest.TestCase):
    def test_only_opted_in_bar_detection_joins_two_pixel_gap(self):
        try:
            from PIL import Image, ImageDraw
            import cv2
            from sync_batch_score import note_rows
        except ImportError:
            self.skipTest('requires optional image alignment dependencies')
        raster = Image.new('RGB', (100, 60), 'white')
        draw = ImageDraw.Draw(raster)
        draw.rectangle((25, 0, 26, 19), fill='black')
        draw.rectangle((25, 22, 26, 39), fill='black')
        # A real digit 1 has margins above and below; it must survive.
        draw.rectangle((49, 6, 51, 32), fill='black')
        draw.rectangle((45, 9, 49, 12), fill='black')
        layout = [{'notes': {'recognitionBand': [0, 40]},
                   'lyrics': [{'recognitionBand': [48, 58]}]}]
        # The OCR box is four pixels left of the scanned bar, as in the real score.
        detail = [100, [list('211345')], [[10, 21, 50, 65, 80, 90]], None, None]

        class OCR:
            def text_rec(self, *args, **kwargs):
                return [('211345', .99, detail)], None

        with tempfile.TemporaryDirectory() as folder, patch('sync_batch_score._ocr', OCR()):
            cache = Path(folder)
            image = cache / 'source.png'
            raster.save(image)
            recognition = {'source': image.name, 'sha256': 'test-image', 'recognizerVersion': 3}
            ordinary = note_rows(image, recognition, cache, reviewed_systems=layout, normalize_digits=True)
            corrected = note_rows(image, recognition, cache, reviewed_systems=layout, normalize_digits=True,
                                  join_broken_barlines=True)
            self.assertEqual(ordinary[0]['notes'], [2, 1, 1, 3, 4, 5])
            self.assertEqual(corrected[0]['notes'], [2, 1, 3, 4, 5])
            import json
            self.assertEqual(json.loads((cache / 'notes/source.png.json').read_text())['version'], 7)
            self.assertEqual(raster.tobytes(), Image.open(image).tobytes())
            # Switching back must rebuild the old result, not reuse version 7.
            again = note_rows(image, recognition, cache, reviewed_systems=layout, normalize_digits=True)
            self.assertEqual(again, ordinary)
            with self.assertRaises(ValueError):
                note_rows(image, recognition, cache, reviewed_systems=layout, join_broken_barlines=True)
            with self.assertRaises(ValueError):
                note_rows(image, recognition, cache, reviewed_systems=layout, normalize_digits=True,
                          separate_octave_dots=True, join_broken_barlines=True)


class ChromaCachePrecisionTests(unittest.TestCase):
    def test_first_calculation_and_cache_return_identical_feature_arrays(self):
        import numpy as np
        from sync_batch_audio import features
        time = np.arange(32000) / 16000
        signal = np.sin(2 * np.pi * 440 * time).astype(np.float32)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'audio.mp3'
            source.write_bytes(b'hash-bound audio fixture')
            with patch('faster_whisper.audio.decode_audio', return_value=signal) as decode:
                cold = features(source, root)
                warm = features(source, root)
                decode.assert_called_once()
            for first, cached in zip(cold, warm):
                self.assertEqual(first.dtype, np.float32)
                np.testing.assert_array_equal(first, cached)


if __name__ == '__main__':
    unittest.main()
