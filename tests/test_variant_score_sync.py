import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('variant_score_cli', ROOT / 'tools/variant-score-sync.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class VariantScoreTests(unittest.TestCase):
    def test_match_rejects_weak_ambiguous_or_incomplete_audio(self):
        good = {'best': {'cost': .2, 'span': 34}, 'runnerUp': {'cost': .4}}
        self.assertEqual(cli.checked_match(good, 35), good['best'])
        for change in [({'cost': .36}, {}), ({'cost': float('nan')}, {}),
                       ({'span': 20}, {}), ({'span': 50}, {}), ({}, {'cost': .25})]:
            value = copy.deepcopy(good)
            value['best'].update(change[0])
            value['runnerUp'].update(change[1])
            with self.subTest(value=value), self.assertRaises(ValueError):
                cli.checked_match(value, 35)

    def test_changed_lyrics_cannot_produce_a_ready_candidate(self):
        song = {'songId': '02-234', 'source': 'changed.txt', 'sha256': 'changed',
                'reference': 'audio.mp3', 'tracks': [{'file': 'audio.mp3', 'sha256': 'old'}]}
        profile = {'targetSha256': 'reviewed', 'tracks': song['tracks']}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            _, status = cli.generate(song, profile, cache)
            self.assertTrue(status.startswith('needs-calibration:'))
            self.assertFalse((cache / 'ready' / 'changed.txt.json').exists())

    def test_changed_pdf_cannot_be_used_as_the_reviewed_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'score.pdf'
            path.write_bytes(b'changed score')
            profile = {'pdfiumVersion': '5.14.0', 'pdf': {'file': path.name, 'sha256': 'old'}}
            with self.assertRaisesRegex(ValueError, '哈希'):
                cli.printed_rows(Path(tmp), profile)


if __name__ == '__main__':
    unittest.main()
