import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
from sync_batch_chorus_groups import source_chorus_groups
from sync_batch_sources import text_source


class ChorusGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('chorus_group_midi',
            Path(__file__).parents[1] / 'tools/midi-sync.py')
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def original(self, root):
        lines = ['感恩歌曲标题', '']
        for number in range(1, 4):
            lines += [str(number), f'第{number}节原文主歌第一行', '原文主歌第二行歌词', '',
                      '感谢救主赐我奇妙恩典，', '原文副歌完整第二行歌词。', '']
        path = root / 'song.txt'
        path.write_bytes('\r\n'.join(lines).encode('gb18030'))
        return path

    def profile(self):
        return {'sourceChorusGroups': {'version': 1, 'reason': '按原始编号及逐行相同副歌恢复完整诗节。'},
                'fullPerformanceCheck': {'version': 1}}

    def test_only_explicit_numbered_groups_are_recovered_without_changing_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.original(Path(directory))
            raw = path.read_bytes()
            source = text_source(path)
            before = copy.deepcopy(source)
            self.assertEqual([len(v) for v in source['verses']], [1] * 6)
            self.assertIs(source_chorus_groups(source, Path('missing'), {}), source)
            actual = source_chorus_groups(source, path, self.profile())
            self.assertEqual([len(v) for v in actual['verses']], [2] * 3)
            self.assertEqual(actual['regions'], before['regions'])
            self.assertEqual([d for v in actual['verses'] for d in v],
                             [d for v in before['verses'] for d in v])
            self.assertEqual(actual['chorusGroups']['originalGroups'], [[0, 1], [2, 3], [4, 5]])
            self.assertEqual(actual['chorusGroups']['numberLines'], [2, 9, 16])
            actual['verses'][0][0]['text'] = 'changed copy'
            self.assertEqual(source, before)
            self.assertEqual(path.read_bytes(), raw)

    def test_changed_chorus_numbering_missing_regions_or_extra_text_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mutation in ['chorus', 'punctuation', 'number', 'missing-number', 'same-body', 'footer']:
                path = self.original(root)
                raw = path.read_bytes().decode('gb18030')
                if mutation == 'chorus':
                    raw = raw.replace('感谢救主赐我奇妙恩典，', '第二节这是不同的副歌，', 1)
                elif mutation == 'punctuation':
                    raw = raw.replace('原文副歌完整第二行歌词。', '原文副歌完整第二行歌词！', 1)
                elif mutation == 'number':
                    raw = raw.replace('\r\n2\r\n', '\r\n4\r\n')
                elif mutation == 'missing-number':
                    raw = raw.replace('\r\n2\r\n', '\r\n')
                elif mutation == 'same-body':
                    raw = raw.replace('第1节原文主歌第一行\r\n原文主歌第二行歌词',
                                      '感谢救主赐我奇妙恩典，\r\n原文副歌完整第二行歌词。')
                else:
                    raw += '\r\n还有未覆盖的原文歌词'
                path.write_bytes(raw.encode('gb18030'))
                with self.assertRaises(ValueError, msg=mutation):
                    source_chorus_groups(text_source(path), path, self.profile())
            path = self.original(root)
            source = text_source(path)
            for bad in [None, {}, {'version': True, 'reason': 'x'}, {'version': 1, 'reason': ''},
                        {'version': 1, 'reason': 'x', 'groups': [[0, 1]]},
                        {'version': 1, 'reason': 'x', 'at': 20}]:
                with self.assertRaises(ValueError):
                    source_chorus_groups(source, path, {**self.profile(), 'sourceChorusGroups': bad})
            for full in [None, {'version': True}, {'version': 1, 'cost': .5}]:
                with self.assertRaises(ValueError):
                    source_chorus_groups(source, path, {**self.profile(), 'fullPerformanceCheck': full})
            for key in ['referenceDiscovery', 'opticalReference']:
                with self.assertRaises(ValueError):
                    source_chorus_groups(source, path, {**self.profile(), key: {}})
            broken = copy.deepcopy(source)
            broken['regions']['r2']['startLine'] = 0
            with self.assertRaises(ValueError):
                source_chorus_groups(broken, path, self.profile())

    def fixture(self, root):
        data = root / 'data'
        data.mkdir()
        path = self.original(data)
        for name in ['lead.mp3', 'main.mp3']:
            (data / name).write_bytes(name.encode())
        cache = root / 'cache'
        (cache / 'midi').mkdir(parents=True)
        (cache / 'midi/original.mid').write_bytes(b'original midi')
        (cache / 'page.html').write_bytes(b'original lyrics page')
        tracks = [{'file': name, 'sha256': self.cli.batch.checksum(data / name)}
                  for name in ['lead.mp3', 'main.mp3']]
        song = {'songId': 'test', 'type': 'text', 'source': path.name,
                'sha256': self.cli.batch.checksum(path), 'reference': 'lead.mp3', 'tracks': tracks}
        profile = {**self.profile(), 'targetSha256': song['sha256'], 'tracks': tracks,
            'audioFile': song['reference'], 'verseCount': 3, 'file': 'original.mid',
            'sha256': self.cli.batch.checksum(cache / 'midi/original.mid'),
            'textReferenceFile': 'page.html', 'textReferenceSha256': self.cli.batch.checksum(cache / 'page.html')}
        return song, profile, cache

    def test_all_recordings_pass_original_four_paths_and_any_failed_track_stops_export(self):
        for failure in [False, True]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                song, profile, cache = self.fixture(root)
                def feature(path, cache):
                    return np.arange(351) * .1, np.full((12, 351), 2 if path.name == 'main.mp3' else 1)
                def music(path, count, cache):
                    self.assertEqual(count, 3)
                    return {'period': 10, 'initialOnsetEstimate': 2.2 if path.name == 'main.mp3' else 2,
                            'shifts': [0, 10, 20]}
                def harmonic(reference, target, zero, reference_period, duration, onset, cue_offsets):
                    cost = .36 if failure and target[1][0, 0] == 2 else .1
                    return {'best': {'cost': cost, 'key': 2, 'span': duration,
                                     'starts': [onset + t for t in cue_offsets]},
                            'runnerUp': {'cost': .6}}
                def melodic(rows, chroma, times, duration, onset, weight):
                    return {'best': {'cost': .1, 'key': 2, 'span': duration,
                                     'starts': [onset + i * 5 for i in range(len(rows))]},
                            'runnerUp': {'cost': .6}}
                with patch.object(self.cli.batch, 'ROOT', root), \
                        patch.object(self.cli, 'midi_features', return_value=(10, (np.arange(100) * .1, np.zeros((12, 100))), {})), \
                        patch.object(self.cli, 'reading_rows', return_value=([0, 5], [{}, {}])), \
                        patch.object(self.cli, 'features', side_effect=feature), \
                        patch.object(self.cli, 'repeated_verses', side_effect=music), \
                        patch.object(self.cli.same_tune, 'match_cycle', side_effect=harmonic) as harmony, \
                        patch.object(self.cli, 'match_template', side_effect=melodic) as melody, \
                        patch.object(self.cli, 'export_result') as export:
                    result = self.cli.generate(song, profile, cache)
                    if failure:
                        self.assertIn('needs-calibration', result[1])
                        export.assert_not_called()
                    else:
                        self.assertEqual(harmony.call_count, 8)
                        self.assertEqual(melody.call_count, 8)
                        evidence = export.call_args.args[4]
                        self.assertEqual(set(evidence['midiMatching']['recordings']), {'lead.mp3', 'main.mp3'})
                        self.assertEqual(evidence['independentTimelines']['lead.mp3']['times'][0], 2)
                        self.assertEqual(evidence['independentTimelines']['main.mp3']['times'][0], 2.2)

    def test_current_source_reference_page_and_audio_hashes_are_required(self):
        for changed in ['source', 'page', 'audio']:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                song, profile, cache = self.fixture(root)
                if changed == 'source':
                    path = root / 'data/song.txt'
                    path.write_bytes(path.read_bytes().replace('标题'.encode('gb18030'), '题名'.encode('gb18030')))
                elif changed == 'page':
                    (cache / 'page.html').write_bytes(b'changed')
                else:
                    (root / 'data/lead.mp3').write_bytes(b'changed')
                with patch.object(self.cli.batch, 'ROOT', root), \
                        patch.object(self.cli, 'midi_features', return_value=(10, None, {})), \
                        patch.object(self.cli, 'reading_rows', return_value=([0, 5], [{}, {}])), \
                        patch.object(self.cli, 'features') as feature, \
                        patch.object(self.cli, 'export_result') as export:
                    self.assertIn('needs-calibration', self.cli.generate(song, profile, cache)[1])
                    feature.assert_not_called()
                    export.assert_not_called()


if __name__ == '__main__':
    unittest.main()
