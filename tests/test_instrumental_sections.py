from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sync_batch_instrumental_sections import stable_tempo_band


def attempt(duration, accepted=True, starts=(184, 208)):
    return {
        'durationEstimate': duration,
        'accepted': accepted,
        'matches': {
            name: {'best': {'starts': list(starts)}}
            for name in ['raw-midi', 'raw-optical', 'harmonic-midi', 'harmonic-optical']
        },
    }


class InstrumentalSections(unittest.TestCase):
    def test_a_single_favourable_tempo_does_not_confirm_a_variation(self):
        with self.assertRaises(ValueError):
            stable_tempo_band([attempt(48, False), attempt(50), attempt(52, False)], 3)

    def test_three_consecutive_estimates_must_share_the_same_positions(self):
        positions, estimates = stable_tempo_band([attempt(48, False), attempt(50), attempt(52), attempt(54)], 3)
        self.assertEqual(positions, [184, 208])
        self.assertEqual(estimates, [50, 52, 54])
        with self.assertRaises(ValueError):
            stable_tempo_band([attempt(50), attempt(52, starts=(185, 208)), attempt(54)], 3)

    def test_disjoint_supported_tempos_remain_ambiguous(self):
        with self.assertRaises(ValueError):
            stable_tempo_band(
                [attempt(44), attempt(46), attempt(48), attempt(50, False), attempt(52), attempt(54), attempt(56)], 3
            )
        with self.assertRaises(ValueError):
            stable_tempo_band([attempt(50), attempt(54), attempt(56)], 3)


if __name__ == '__main__':
    unittest.main()
