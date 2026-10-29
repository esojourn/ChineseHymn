import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import mido
sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
from sync_batch_fermata import checksum, score_time_reference


class FermataTests(unittest.TestCase):
    def fixture(self, root):
        xml = root / 'score.xml'
        xml.write_text('''<score-partwise><part id="P1"><measure number="1">
          <attributes><divisions>4</divisions></attributes>
          <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
          <note><pitch><step>D</step><octave>4</octave></pitch><duration>8</duration><voice>1</voice><notations><fermata/></notations></note>
          <backup><duration>12</duration></backup>
          <note><pitch><step>G</step><octave>3</octave></pitch><duration>12</duration><voice>2</voice></note>
        </measure></part></score-partwise>''')
        score = mido.MidiFile(type=1, ticks_per_beat=100)
        score.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000)]))
        for pitch in [60, 48]:
            score.tracks.append(mido.MidiTrack([
                mido.Message('note_on', note=pitch, time=50),
                mido.Message('note_off', note=pitch, time=100),
                mido.Message('note_on', note=pitch + 2),
                mido.MetaMessage('set_tempo', tempo=3000000, time=150),
                mido.Message('note_off', note=pitch + 2, time=50),
                mido.MetaMessage('set_tempo', tempo=500000)]))
        path = root / 'original.mid'
        score.save(path)
        profile = {'terminalFermataTempo': {'version': 1, 'reason': '原谱最后延长音。'},
            'fullPerformanceCheck': {'version': 1}, 'sha256': checksum(path),
            'scoreReferenceFile': 'score.xml', 'scoreReferenceSha256': checksum(xml)}
        return path, profile

    def events(self, path):
        return [[dict(m.dict()) for m in track] for track in mido.MidiFile(path).tracks]

    def test_only_terminal_tempo_changes_original_notes_ticks_and_files_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, profile = self.fixture(root)
            original = path.read_bytes()
            xml = (root / 'score.xml').read_bytes()
            self.assertEqual(score_time_reference(path, {}, root), (path, None))
            derived, proof = score_time_reference(path, profile, root)
            self.assertEqual(proof['replacedTempoEvents'], 2)
            self.assertEqual(proof['fermata'], {'measure': '1', 'startQuarter': '1', 'endQuarter': '3'})
            self.assertEqual(proof['midiLeadInQuarters'], '1/2')
            self.assertEqual(proof['terminalTempoQuarter'], '5/2')
            expected = self.events(path)
            for track in expected:
                for message in track:
                    if message['type'] == 'set_tempo':
                        message['tempo'] = 500000
            self.assertEqual(self.events(derived), expected)
            self.assertAlmostEqual(mido.MidiFile(derived).length, 1.75)
            self.assertGreater(mido.MidiFile(path).length, 1.75)
            good = derived.read_bytes()
            derived.write_bytes(b'corrupt derived cache')
            self.assertEqual(score_time_reference(path, profile, root), (derived, proof))
            self.assertEqual(derived.read_bytes(), good)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual((root / 'score.xml').read_bytes(), xml)

    def test_requires_exact_explicit_review_and_complete_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, profile = self.fixture(root)
            for review in [None, {}, {'version': True, 'reason': 'x'},
                           {'version': 1, 'reason': ''}, {'version': 1, 'reason': 'x', 'at': 2},
                           {'version': 1, 'reason': 'x', 'tempo': 500000}]:
                with self.assertRaises(ValueError):
                    score_time_reference(path, {**profile, 'terminalFermataTempo': review}, root)
            for complete in [None, {'version': True}, {'version': 1, 'margin': .1}]:
                with self.assertRaises(ValueError):
                    score_time_reference(path, {**profile, 'fullPerformanceCheck': complete}, root)
            for key in ['referenceDiscovery', 'referenceCycle', 'opticalReference']:
                with self.assertRaises(ValueError):
                    score_time_reference(path, {**profile, key: {}}, root)

    def test_rejects_nonterminal_or_unproven_changes(self):
        for mutation in ['early', 'faster', 'different', 'unclosed', 'no-restore',
                         'no-fermata', 'wrong-length', 'changed-xml', 'changed-midi']:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path, profile = self.fixture(root)
                midi = mido.MidiFile(path)
                if mutation == 'early':
                    midi.tracks[1][3].time = 0
                    midi.tracks[1][4].time = 200
                elif mutation == 'faster':
                    for track in midi.tracks[1:]:
                        track[3].tempo = 400000
                elif mutation == 'different':
                    midi.tracks[2][3].tempo = 2000000
                elif mutation == 'unclosed':
                    midi.tracks[1][4] = mido.MetaMessage('text', text='missing note off', time=50)
                elif mutation == 'no-restore':
                    for track in midi.tracks[1:]:
                        track.pop(5)
                if mutation in ['no-fermata', 'wrong-length', 'changed-xml']:
                    xml = root / 'score.xml'
                    xml.write_text(xml.read_text().replace('<fermata/>', '') if mutation != 'wrong-length'
                                   else xml.read_text().replace('<duration>8</duration>', '<duration>12</duration>'))
                    if mutation != 'changed-xml':
                        profile['scoreReferenceSha256'] = checksum(xml)
                else:
                    midi.save(path)
                    if mutation != 'changed-midi':
                        profile['sha256'] = checksum(path)
                    else:
                        path.write_bytes(path.read_bytes() + b'changed')
                with self.assertRaises(ValueError, msg=mutation):
                    score_time_reference(path, profile, root)

    def test_all_current_recordings_are_independent_and_failure_prevents_export(self):
        spec = importlib.util.spec_from_file_location('fermata_midi',
            Path(__file__).parents[1] / 'tools/midi-sync.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        import numpy as np
        for failure in [False, True]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'data').mkdir()
                (root / 'midi').mkdir()
                path, profile = self.fixture(root)
                path.rename(root / 'midi/original.mid')
                (root / 'data/song.txt').write_bytes(b'raw source')
                tracks = []
                for name in ['main.mp3', 'lead.mp3']:
                    (root / 'data' / name).write_bytes(name.encode())
                    tracks.append({'file': name, 'sha256': checksum(root / 'data' / name)})
                song = {'songId': 'test', 'source': 'song.txt', 'sha256': checksum(root / 'data/song.txt'),
                        'reference': 'lead.mp3', 'tracks': tracks}
                profile.update(targetSha256=song['sha256'], audioFile='lead.mp3', tracks=tracks,
                               file='original.mid', verseCount=3)
                source = {'verses': [[{'region': f'r{i}', 'text': '原文'}] for i in range(3)]}
                def measured(profile, source, audio, period, harmony, rows, offsets, cache, detail, limits):
                    if failure and audio.name == 'lead.mp3':
                        raise ValueError('failed complete melody')
                    detail.update(music={'period': 10}, completePerformance={key: {'best': {
                        'starts': [2], 'span': 30}} for key in ['harmony', 'melody']})
                    return [2, 12, 22]
                with patch.object(cli.batch, 'ROOT', root), \
                        patch.object(cli, 'text_source', return_value=source), \
                        patch.object(cli, 'midi_features', return_value=(10, None, {})), \
                        patch.object(cli, 'reading_rows', return_value=([0], [{}])), \
                        patch.object(cli, 'features', return_value=(np.arange(351) * .1, None)), \
                        patch.object(cli, 'midi_recording_times', side_effect=measured) as measurement, \
                        patch.object(cli, 'export_result') as export:
                    result = cli.generate(song, profile, root)
                    self.assertEqual(measurement.call_count, 2)
                    if failure:
                        self.assertIn('needs-calibration', result[1])
                        export.assert_not_called()
                    else:
                        evidence = export.call_args.args[4]
                        self.assertEqual(set(evidence['independentTimelines']), {'main.mp3', 'lead.mp3'})
                        self.assertIn('terminalFermataTempo', evidence['midiMatching'])


if __name__ == '__main__':
    unittest.main()
