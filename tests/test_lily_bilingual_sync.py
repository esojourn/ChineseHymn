import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
from sync_batch_lily_bilingual import chinese_preview, final_line_tail, korean_final_verse, korean_opening


def word(text, start, end, probability=0.9):
    return {'word': text, 'start': start, 'end': end, 'probability': probability}


def transcript(text, words, duration=75):
    return {'duration': duration, 'segments': [{'text': text, 'start': words[0]['start'], 'words': words}]}


class BilingualPreviews(unittest.TestCase):
    def test_chinese_preview_is_early_and_requires_two_agreeing_source_prefixes(self):
        definition = {'text': '悲伤时祂来解忧'}
        a = transcript('悲伤是他来解忧', [word('悲', 10, 10.5), word('伤', 10.5, 11)])
        b = transcript('悲伤是他来解忧', [word('悲', 10.3, 10.8), word('伤', 10.8, 11.3)])
        self.assertEqual(chinese_preview(definition, '悲伤', 11, [a, b])['at'], 9.2)
        b['segments'][0]['words'][0]['start'] = 11
        with self.assertRaises(ValueError):
            chinese_preview(definition, '悲伤', 11, [a, b])
        with self.assertRaises(ValueError):
            chinese_preview(definition, '陌生词', 11, [a, a])

    def test_korean_refrain_requires_both_original_meaningful_words(self):
        a = transcript('산 밑에 백합 빛나는 새벽별', [word('산', 20, 21), word('백합', 21, 22), word('새벽별', 22, 23)])
        b = transcript(
            '산 밑에 백합 빛나는 새벽별', [word('산', 20.4, 21.4), word('백합', 21.4, 22.4), word('새벽별', 22.4, 23.4)]
        )
        self.assertEqual(korean_opening(21, [a, b])['at'], 20)
        b['segments'][0]['text'] = '산 밑에 백합'
        with self.assertRaises(ValueError):
            korean_opening(21, [a, b])

    def test_korean_final_verse_requires_independent_common_word_ends(self):
        a = transcript('내 맘을 주님을 따르며', [word('내', 30, 30), word('맘을', 30, 32)])
        b = transcript('내 맘을 주님을 따르며', [word('내', 29.5, 30.5), word('맘을', 30.5, 32.2)])
        self.assertEqual(korean_final_verse(30, [a, b])['at'], 28.7)
        b['segments'][0]['words'][1]['end'] = 33
        with self.assertRaises(ValueError):
            korean_final_verse(30, [a, b])

    def test_final_line_uses_two_recognized_word_ends_and_checks_remaining_audio(self):
        definition = {'text': '万人中救主最美好，我爱祂。'}
        chars = list('最美好我爱她')
        a = transcript('最美好我爱她', [word(c, 56 + i, 56.7 + i) for i, c in enumerate(chars)])
        b = transcript('最美好我爱她', [word(c, 56.1 + i, 56.8 + i) for i, c in enumerate(chars)])
        result = final_line_tail(definition, [a, b], 20, 59)
        self.assertEqual(result['homophone'], '祂/她')
        self.assertAlmostEqual(result['conservativeEnd'], 61.7)
        b['segments'][0]['words'][-1]['end'] += 1
        with self.assertRaises(ValueError):
            final_line_tail(definition, [a, b], 20, 59)
        with self.assertRaises(ValueError):
            final_line_tail(definition, [a, a], 10, 59)

    def test_failed_recheck_cannot_leave_an_old_candidate(self):
        path = Path(__file__).resolve().parent.parent / 'tools/lily-bilingual-sync.py'
        spec = importlib.util.spec_from_file_location('lily_generator', path)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        song = {'songId': '06-018', 'source': 'sample.txt', 'sha256': 'a' * 64, 'tracks': []}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            candidate = cache / 'ready/sample.txt.json'
            candidate.parent.mkdir()
            candidate.write_text('{}')
            self.assertIn('needs-calibration', generator.generate(song, {}, cache, cache)[1])
            self.assertFalse(candidate.exists())
            evidence = json.loads((cache / 'transcript-alignment/sample.txt.json').read_text())
            self.assertEqual(evidence['status'], 'needs-calibration')


if __name__ == '__main__':
    unittest.main()
