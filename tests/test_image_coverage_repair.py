import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repair = load('image_coverage_test', 'image-coverage-repair-sync.py')
batch = repair.batch


class ImageCoverageRepair(unittest.TestCase):
    def test_reviewed_bands_preserve_four_verses_when_ocr_split_each_line(self):
        systems = [
            {
                'notes': {'recognitionBand': [10, 20]},
                'lyrics': [{'recognitionBand': [30 + i * 10, 38 + i * 10], 'text': '原图完整歌词'} for i in range(4)],
            }
        ]
        profile = {
            'printedVerses': 4,
            'regions': {f's1_v{i + 1}': {} for i in range(4)},
            'systemsReview': {'version': 1, 'reason': '原图有四段，识别将歌词拆行', 'systems': systems},
        }
        source = repair.source_readings({'height': 100, 'rows': []}, profile)
        self.assertEqual([len(verse) for verse in source['verses']], [1, 1, 1, 1])
        self.assertEqual(source['verses'][3][0]['region'], 's1_v4')
        systems[0]['lyrics'].pop()
        with self.assertRaisesRegex(ValueError, '全部印刷段落'):
            repair.source_readings({'height': 100, 'rows': []}, profile)

    def test_optical_recovery_cannot_hide_other_row_disagreements(self):
        diagnostic = {
            'rejectedCounts': [
                {
                    'reason': '整曲两种节奏路径的完整段落不一致',
                    'fullTemplates': [
                        {'best': {'key': 1, 'starts': [4.3, 16]}},
                        {'best': {'key': 1, 'starts': [5.5, 17]}},
                    ],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, '同音开头'):
            repair.optical_cycles(Path('unused'), [{'notes': [1, 2, 3, 4]}], 8, Path('.'), diagnostic)
        with self.assertRaisesRegex(ValueError, '首行'):
            repair.optical_cycles(
                Path('unused'), [{'notes': [3] * 4}, {'notes': [1, 1, 2, 2]}], 8, Path('.'), diagnostic
            )

    def test_old_recognition_that_omitted_last_system_is_refreshed(self):
        song = {'songId': '05-103', 'source': 'missing.jpg', 'sha256': 'a' * 64}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ocr/missing.jpg.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'sha256': song['sha256'], 'recognizerVersion': 5}))
            self.assertEqual(batch.recognize_image((song, directory, 1)), ('05-103', 'cached'))
            path.write_text(json.dumps({'sha256': song['sha256'], 'recognizerVersion': 4}))
            with patch.object(batch, '_ocr', object()), patch(
                'PIL.Image.open', side_effect=ValueError('fresh recognition')
            ):
                with self.assertRaisesRegex(ValueError, 'fresh recognition'):
                    batch.recognize_image((song, directory, 1))

    def test_review_can_exclude_only_explicit_final_amen(self):
        row = {
            'notes': [5, 6, 1, 6, 1],
            'pitchOffsets': [0] * 5,
            'x': list(range(5)),
            'spatialWeights': [1] * 5,
            'ocrRhythmWeights': [1] * 5,
            'opticalDiagnostics': [{}] * 5,
        }
        profile = {
            'rawNotes': [row['notes']],
            'bodyRhythms': [[1, 2, 3]],
            'review': {
                'trailingAmen': '最后两音是末尾阿们，不属于每段正文；只在重复模板中排除，原图和全部歌词区域保留。'
            },
        }
        actual = repair.reviewed_body([row], profile)
        self.assertEqual(actual[0]['notes'], [5, 6, 1])
        self.assertEqual(actual[0]['rhythmWeights'], [1, 2, 3])
        self.assertEqual(len(row['notes']), 5)
        profile['bodyRhythms'] = [[1, 2]]
        with self.assertRaises(ValueError):
            repair.reviewed_body([row], profile)

    def test_failed_calibration_clears_old_ready_file(self):
        song = {'songId': '05-103', 'source': 'sample.jpg', 'sha256': 'a' * 64, 'tracks': []}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            ready = cache / 'ready/sample.jpg.json'
            ready.parent.mkdir()
            ready.write_text('{}')
            self.assertIn('needs-calibration', repair.generate(song, {}, cache)[1])
            self.assertFalse(ready.exists())


if __name__ == '__main__':
    unittest.main()
