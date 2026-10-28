import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import numpy as np
from sync_batch_cqt import audio_feature_review, cqt_features


class ConstantQTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('cqt_sectional_cli',
            Path(__file__).parents[1] / 'tools/sectional-midi-sync.py')
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def review(self):
        return {'version': 1, 'kind': 'constant-q', 'librosaVersion': '0.11.0',
                'reason': '完整原始录音分别核对，不填写音频截段或修改验收标准。'}

    def test_opt_in_is_strict_and_default_uses_existing_features(self):
        self.assertIsNone(audio_feature_review({}))
        review = self.review()
        self.assertIs(audio_feature_review({'audioFeatures': review}), review)
        for value in [None, {}, {**review, 'version': True}, {**review, 'kind': 'stft'},
                      {**review, 'librosaVersion': 'latest'}, {**review, 'reason': ' '},
                      {**review, 'start': 30}, {**review, 'binsPerOctave': 12}]:
            with self.assertRaises(ValueError):
                audio_feature_review({'audioFeatures': value})
        marker = object()
        with patch.object(self.cli, 'features', return_value=marker) as legacy, \
                patch.object(self.cli, 'cqt_features') as cqt:
            detail = {}
            self.assertIs(self.cli.recording_features(Path('audio'), Path('cache'), {}, detail), marker)
            legacy.assert_called_once()
            cqt.assert_not_called()
            self.assertEqual(detail, {})

    def test_new_feature_requires_every_current_recording_and_preserves_hash_checks(self):
        song = {'reference': 'lead.mp3', 'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'},
                                                  {'file': 'main.mp3', 'sha256': 'main'}]}
        item = {'file': 'main.mp3', 'sha256': 'main',
                'sampling': {'durationEstimate': 30, 'onsetEstimate': 2}}
        profile = {'audioFeatures': self.review(), 'independentRecordings': [item]}
        before = copy.deepcopy(profile)
        self.assertEqual(self.cli.independent_recordings(song, profile), [item])
        self.assertEqual(profile, before)
        for bad in [{**profile, 'independentRecordings': []},
                    {**profile, 'independentRecordings': [{**item, 'sha256': 'stale'}]}]:
            with self.assertRaises(ValueError):
                self.cli.independent_recordings(song, bad)

    def test_generate_measures_both_recordings_and_rejects_any_failed_path(self):
        song = {'songId': 'test', 'source': 'song.txt', 'sha256': 'source', 'reference': 'lead.mp3',
                'tracks': [{'file': 'lead.mp3', 'sha256': 'lead'}, {'file': 'main.mp3', 'sha256': 'main'}]}
        window = {'durationEstimate': 30, 'onsetEstimate': 2}
        profile = {'sampling': window, 'tracks': song['tracks'], 'audioFeatures': self.review(), 'independentRecordings': [
            {'file': 'main.mp3', 'sha256': 'main', 'sampling': window}]}
        definitions = [{'region': 'r' + str(i)} for i in range(3)]
        material = ([{}] * 3, [{}] * 3, {'symbolicRows': {'semitoneShift': 2}})
        target = object()
        for failure in [False, True]:
            paths = [[2, 12, 22], ValueError('independent path failed') if failure else [3, 13, 23]]
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(self.cli, 'validate_source', return_value=({}, definitions)), \
                    patch.object(self.cli, 'reference_material', return_value=material), \
                    patch.object(self.cli, 'features') as legacy, \
                    patch.object(self.cli, 'cqt_features', side_effect=[
                        (target, {'version': 1, 'sourceSha256': digest}) for digest in ['lead', 'main']]) as cqt, \
                    patch.object(self.cli, 'performance_times', side_effect=paths) as matching, \
                    patch.object(self.cli, 'export_result') as export:
                result = self.cli.generate(song, profile, Path(directory))
                legacy.assert_not_called()
                self.assertEqual(cqt.call_count, 2)
                self.assertEqual(matching.call_count, 2)
                for call in matching.call_args_list:
                    self.assertIs(call.args[2], target)
                if failure:
                    self.assertIn('needs-calibration', result[1])
                    export.assert_not_called()
                else:
                    evidence = export.call_args.args[4]
                    self.assertEqual(evidence['midiMatching']['audioFeatures']['version'], 1)
                    self.assertEqual(evidence['midiMatching']['independentRecordings'][0]['audioFeatures']['version'], 1)
                    self.assertEqual(evidence['independentTimelines']['main.mp3']['times'], [3, 13, 23])

    def test_changed_audio_hash_cannot_reach_alignment(self):
        audio = self.cli.batch.ROOT / 'data/main.mp3'
        profile = {'audioFeatures': self.review(), 'tracks': [{'file': 'main.mp3', 'sha256': 'expected'}]}
        detail = {}
        with patch.object(self.cli, 'cqt_features', return_value=(None, {'sourceSha256': 'changed'})):
            with self.assertRaisesRegex(ValueError, '哈希'):
                self.cli.recording_features(audio, Path('.'), profile, detail)
        self.assertEqual(detail, {})

    @unittest.skipIf(importlib.util.find_spec('librosa') is None, 'requires optional librosa')
    def test_real_audio_pitch_time_axis_cache_identity_and_corruption(self):
        import librosa
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sr = 16000
            t = np.arange(sr * 2) / sr
            pieces = [np.zeros(sr // 2)]
            for midi in [48, 64, 66, 71]:
                f = 440 * 2 ** ((midi - 69) / 12)
                pieces.append(.5 * np.sin(2 * np.pi * f * t) + .2 * np.sin(4 * np.pi * f * t))
            pieces.append(np.zeros(sr // 2))
            path = root / 'four-notes.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((1, 2, sr, 0, 'NONE', 'not compressed'))
                stream.writeframes((np.concatenate(pieces) * 32767).astype('<i2').tobytes())
            (times, chroma), proof = cqt_features(path, root / 'first')
            self.assertEqual(times[0], 0)
            self.assertAlmostEqual(float(times[-1]), 9, places=4)
            for index, pitch in enumerate([0, 4, 6, 11]):
                selected = (times >= 1 + 2 * index) & (times < 2 + 2 * index)
                self.assertEqual(int(np.argmax(np.mean(chroma[:, selected], axis=1))), pitch)
            with patch.object(librosa.feature, 'chroma_cqt', side_effect=AssertionError('cache was not reused')):
                cached, cached_proof = cqt_features(path, root / 'first')
            np.testing.assert_array_equal(cached[0], times)
            np.testing.assert_array_equal(cached[1], chroma)
            self.assertEqual(cached_proof, proof)
            fresh, fresh_proof = cqt_features(path, root / 'second')
            np.testing.assert_array_equal(fresh[1], chroma)
            self.assertEqual(fresh_proof, proof)
            with patch.object(librosa, '__version__', 'unreviewed'):
                with self.assertRaises(ValueError):
                    cqt_features(path, root / 'first')
            cache = next((root / 'first/chroma-cqt-v1').glob('*.npz'))
            with np.load(cache, allow_pickle=False) as original:
                data = {key: original[key] for key in original.files}
            data['chroma'][0, 0] += .25
            np.savez_compressed(cache, **data)
            with self.assertRaisesRegex(ValueError, '哈希'):
                cqt_features(path, root / 'first')
            metadata = json.loads(data['metadata'].item())
            metadata['sourceSha256'] = 'stale'
            data['metadata'] = json.dumps(metadata)
            np.savez_compressed(cache, **data)
            with self.assertRaisesRegex(ValueError, '录音或固定参数'):
                cqt_features(path, root / 'first')


if __name__ == '__main__':
    unittest.main()
