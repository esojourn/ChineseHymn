import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
try:
    import mido
    from sync_batch_sources import text_source
    from sync_batch_reused_chorus import source_chorus_reuse, validate_reused_chorus
except ImportError:
    mido = None


@unittest.skipIf(mido is None, 'requires optional sync dependencies')
class ReusedChorusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('reused_chorus_cli',
            Path(__file__).parents[1] / 'tools/sectional-midi-sync.py')
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def original(self, root):
        lines = ['诗歌标题', '']
        for verse in range(1, 5):
            lines += [str(verse), f'第{verse}节主歌第一行歌词', f'第{verse}节主歌第二行歌词']
            if verse < 4:
                lines += ['立在基督坚固磐石，其余根基全是沙土，', '耶稣基督就是磐石。']
            lines.append('')
        path = root / 'song.txt'
        path.write_bytes('\r\n'.join(lines).encode('gb18030'))
        return path

    def review(self):
        return {'sourceChorusReuse': {'version': 1, 'reason': 'The final stanza performs the same original chorus.'}}

    def material(self):
        notes = [1, 3, 5, 6, 4, 2, 7, 1]
        performed = [notes[:], notes[:], notes[:], notes[:5], notes[5:], notes[:]]
        optical = [{'notes': row, 'pitchOffsets': [0] * len(row), 'rhythmWeights': [1] * len(row)}
                   for row in performed]
        profile = {**self.review(), 'midiReference': {'origin': {'kind': 'printed-numbered-score',
            'printedRhythmReference': {'numberedRowsAndQuarterBeats': [[(note, 1) for note in notes]]}},
            'arrangement': {'quarterLength': 8, 'sections': [[0, 8]] * 5}},
            'readingSectionReview': {'version': 1, 'groups': [[0], [1], [2], [3], [4]],
                'instrumentalSections': [5], 'reason': 'An instrumental final cycle has no additional lyric cue.'}}
        return profile, ([{} for row in optical], optical, {'symbolicRows': {'editCost': 0, 'semitoneShift': 0}})

    def test_literal_original_suffix_is_reused_without_changing_bytes_or_existing_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.original(Path(directory))
            before = path.read_bytes()
            source = text_source(path)
            original = copy.deepcopy(source)
            self.assertIs(source_chorus_reuse(source, path, {}), source)
            reused = source_chorus_reuse(source, path, self.review())
            self.assertEqual(source, original)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual([len(verse) for verse in reused['verses']], [1, 1, 1, 2])
            self.assertEqual(reused['regions']['r5'], {'kind': 'text', 'startLine': 5, 'endLine': 6})
            self.assertEqual(reused['verses'][-1][1]['text'],
                             '立在基督坚固磐石，其余根基全是沙土， 耶稣基督就是磐石。')
            for key, region in source['regions'].items():
                self.assertEqual(reused['regions'][key], region)
            self.assertEqual(reused['reusedChorus']['matchingOriginalVerses'], [0, 1, 2])

    def test_reuse_rejects_unshared_partial_already_present_and_whole_verse_suffixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for replacement in ['different-chorus', 'already-present', 'whole-verse']:
                path = self.original(root)
                text = path.read_bytes().decode('gb18030')
                if replacement == 'different-chorus':
                    text = text.replace('立在基督坚固磐石，其余根基全是沙土，', '此处另一段副歌歌词，', 1)
                elif replacement == 'already-present':
                    text += '\r\n立在基督坚固磐石，其余根基全是沙土，\r\n耶稣基督就是磐石。'
                else:
                    for verse in range(1, 4):
                        text = text.replace(f'第{verse}节主歌第一行歌词\r\n第{verse}节主歌第二行歌词\r\n', '')
                path.write_bytes(text.encode('gb18030'))
                with self.assertRaises(ValueError, msg=replacement):
                    source_chorus_reuse(text_source(path), path, self.review())
            path = self.original(root)
            source = text_source(path)
            for review in [None, {'version': True, 'reason': 'x'}, {'version': 1, 'reason': ''},
                           {'version': 1, 'reason': 'x', 'at': 120}, {'version': 1, 'reason': 'x', 'lines': [5, 6]}]:
                with self.assertRaises(ValueError):
                    source_chorus_reuse(source, path, {'sourceChorusReuse': review})
            with self.assertRaises(ValueError):
                source_chorus_reuse({**source, 'language': 'en'}, path, self.review())

    def test_every_whole_stanza_and_the_final_split_must_match_the_entire_independent_print(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.original(Path(directory))
            source = source_chorus_reuse(text_source(path), path, self.review())
            profile, material = self.material()
            original = copy.deepcopy((source, profile, material))
            proof = validate_reused_chorus(source, profile, material)
            self.assertEqual(proof['completePrintedSoundedNotes'], 8)
            self.assertEqual(proof['performedWholeVerses'], 4)
            self.assertEqual((source, profile, material), original)
            self.assertIsNone(validate_reused_chorus(source, {}, material))
            for index, key in [(0, 'notes'), (3, 'pitchOffsets'), (4, 'rhythmWeights')]:
                bad = copy.deepcopy(material)
                bad[1][index][key][0] += 1
                with self.assertRaises(ValueError):
                    validate_reused_chorus(source, profile, bad)
            bad = copy.deepcopy(material)
            bad[2]['symbolicRows']['editCost'] = 1
            with self.assertRaises(ValueError):
                validate_reused_chorus(source, profile, bad)
            bad = copy.deepcopy(profile)
            bad['midiReference']['arrangement']['sections'][3] = [0, 4]
            with self.assertRaises(ValueError):
                validate_reused_chorus(source, bad, material)

    def test_generation_requires_current_source_binding_and_disputed_audio_never_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            path = self.original(root / 'data')
            profile, material = self.material()
            reused = source_chorus_reuse(text_source(path), path, profile)
            definitions = [item for verse in reused['verses'] for item in verse]
            song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'type': 'text',
                    'reference': 'main.mp3', 'tracks': [{'file': 'main.mp3', 'sha256': 'main'}]}
            profile.update(version=1, sourceHash='source', tracks=song['tracks'], audioFile='main.mp3',
                regions=[item['region'] for item in definitions], regionOpenings=[item['text'][:12] for item in definitions],
                sampling={'durationEstimate': 50, 'onsetEstimate': 2})
            with patch.object(self.cli.batch, 'ROOT', root):
                self.assertEqual(len(self.cli.validate_source(song, profile)[1]), 5)
                with self.assertRaises(ValueError):
                    self.cli.validate_source(song, {**profile, 'sourceHash': 'stale'})
                with patch.object(self.cli, 'reference_material', return_value=material), \
                        patch.object(self.cli, 'features', return_value=None), \
                        patch.object(self.cli, 'performance_times', side_effect=ValueError('disputed chorus audio')), \
                        patch.object(self.cli, 'export_result') as export:
                    self.assertIn('disputed chorus audio', self.cli.generate(song, profile, root)[1])
                    export.assert_not_called()
                    self.assertFalse((root / 'ready/song.txt.json').exists())
                with patch.object(self.cli, 'reference_material', return_value=material), \
                        patch.object(self.cli, 'features', return_value=None), \
                        patch.object(self.cli, 'performance_times', return_value=[2, 12, 22, 32, 40]), \
                        patch.object(self.cli, 'export_result', return_value=('test', 'ready')) as export:
                    self.assertEqual(self.cli.generate(song, profile, root), ('test', 'ready'))
                    evidence = export.call_args.args[4]
                    self.assertEqual(evidence['returnIndices'], [4])
                    self.assertEqual(evidence['midiMatching']['sourceChorusReuse']['addedRegion'], 'r5')


if __name__ == '__main__':
    unittest.main()
