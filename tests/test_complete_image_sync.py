import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'tools' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


complete = load('complete_image_test', 'image-complete-repeat-sync.py')
transfer = load('complete_transfer_test', 'transfer-sync.py')


class CompleteImageSync(unittest.TestCase):
    def test_local_paths_must_agree_with_independent_repeat_phase(self):
        self.assertEqual(complete.phase_time([31.1, 31.2], 31.1), 31.13)
        with self.assertRaises(ValueError):
            complete.phase_time([32.1, 32.2], 31.1)
        with self.assertRaises(ValueError):
            complete.phase_time([31.1, 31.8], 31.5)
        with self.assertRaises(ValueError):
            complete.phase_time([float('nan'), 31.2], 31.1)

    def test_transfers_use_measured_boundaries_and_omit_instrumental_tail(self):
        cues = [{'at': 3.5, 'region': 'v1'}, {'at': 30.7, 'region': 'v2'}, {'at': 57.9, 'region': 'v3'}]
        reference = {'cues': cues}
        track = {'file': 'score.mp3'}
        evidence = {'scoreMatching': {'completeImageCycles': {'printedVerses': 3, 'systemsPerVerse': 1},
            'recordings': {'score.mp3': {'measuredTimes': [5, 32.2, 59.4, 86.6]}}}}
        actual = transfer.measured_reference_cues(reference, evidence, track)
        self.assertEqual([cue['at'] for cue in actual], [5, 32.2, 59.4])
        self.assertEqual([cue['region'] for cue in actual], ['v1', 'v2', 'v3'])
        self.assertEqual(cues[0]['at'], 3.5)
        evidence['scoreMatching']['recordings']['score.mp3']['measuredTimes'] = [5, 32.2]
        with self.assertRaises(ValueError):
            transfer.measured_reference_cues(reference, evidence, track)

    def test_ordinary_references_keep_original_cues(self):
        reference = {'cues': [{'at': 4.7}]}
        self.assertIs(transfer.measured_reference_cues(reference, {}, {'file': 'score.mp3'}), reference['cues'])
