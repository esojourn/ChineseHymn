import copy
import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
spec = importlib.util.spec_from_file_location('paired_sync', Path(__file__).parents[1] / 'tools/paired-prelude-sync.py')
paired = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paired)


class PairedPreludeTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(439)
        self.delay = 1600
        self.backing = rng.normal(0, .1, paired.RATE * 48)
        self.backing[:paired.RATE * 2] = 0
        self.lead = np.zeros(len(self.backing) + self.delay)
        self.lead[self.delay:] = .6 * self.backing
        self.lead[self.delay + 2 * paired.RATE:] += rng.normal(0, .0005, len(self.backing) - 2 * paired.RATE)
        self.lead[self.delay + 13 * paired.RATE:] += rng.normal(0, .025, len(self.backing) - 13 * paired.RATE)

    def verify(self):
        return paired.verify_pair(self.backing, self.lead, 12, 13, 35)

    def test_complete_backing_prefix_and_added_body_foreground_are_both_required(self):
        before = self.backing.copy(), self.lead.copy()
        proof = self.verify()
        self.assertEqual(proof['shiftSamples'], self.delay)
        self.assertTrue(proof['introWindows'][0]['silent'])
        self.assertGreaterEqual(proof['foregroundWindowCount'], 3)
        self.assertLess(proof['meanIntroResidual'], .001)
        np.testing.assert_array_equal(self.backing, before[0])
        np.testing.assert_array_equal(self.lead, before[1])

    def test_voice_in_any_prefix_window_is_rejected(self):
        start, end = self.delay + 6 * paired.RATE, self.delay + 8 * paired.RATE
        self.lead[start:end] += np.random.default_rng(5).normal(0, .03, end - start)
        with self.assertRaises(ValueError):
            self.verify()

    def test_unstable_gain_or_timing_cannot_be_called_the_same_backing(self):
        original = self.lead.copy()
        start, end = self.delay + 6 * paired.RATE, self.delay + 8 * paired.RATE
        self.lead[start:end] *= 1.2
        with self.assertRaises(ValueError):
            self.verify()
        self.lead = original.copy()
        self.lead[start:end] = original[start + 8:end + 8]
        with self.assertRaises(ValueError):
            self.verify()

    def test_identical_backing_throughout_does_not_establish_a_vocal_entrance(self):
        self.lead = np.r_[np.zeros(self.delay), .6 * self.backing]
        with self.assertRaises(ValueError):
            self.verify()

    def test_silence_must_be_silent_in_both_files(self):
        self.lead[self.delay:self.delay + paired.RATE] = .01
        with self.assertRaises(ValueError):
            self.verify()

    def test_short_or_out_of_recording_checks_are_rejected(self):
        for end, body, after in [(2, 3, 30), (12, 13, 20), (12, 11, 35)]:
            with self.subTest(end=end, body=body, after=after):
                with self.assertRaises(ValueError):
                    paired.verify_pair(self.backing, self.lead, end, body, after)
        with self.assertRaises(ValueError):
            paired.window_match(self.backing, self.lead, 47, 3, self.delay)

    def test_review_cannot_supply_a_manual_crop_time_or_other_structural_exceptions(self):
        song = {'tracks': [{'file': '01-001测试.mp3'}, {'file': '01-001测试-领唱.mp3'}],
                'reference': '01-001测试-领唱.mp3'}
        profile = {'pairedPreludeReview': {'version': 1, 'reason': 'Original backing prefix.'},
                   'previousReadingContext': {'version': 1}}
        for change in [{'pairedPreludeReview': {'version': 1, 'reason': 'Manual', 'cut': 42}},
                       {'readingSectionReview': {}}, {'vocalInterludes': {}}, {'audioFeatures': {}},
                       {'language': 'en'}, {'independentRecordings': []}]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    paired.validate_profile(song, {**copy.deepcopy(profile), **change})


if __name__ == '__main__':
    unittest.main()
