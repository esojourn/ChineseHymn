import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sync_batch_staff_voice import upper_voice


class StaffVoice(unittest.TestCase):
    def setUp(self):
        self.key = [0, 2, 4, 6, 7, 9, 11]
        self.glyphs = [{'x': 5, 'y': -2.04}, {'x': 10, 'y': 0}, {'x': 25, 'y': -4.08}, {'x': 25, 'y': 4.08}]
        self.system = {
            'xmin': 0,
            'xmax': 40,
            'staffBottom': 0,
            'halfStaffSpace': 2.04,
            'lowerDisplacedSeconds': [5],
            'expectedPitches': [4, 7],
        }

    def test_displaced_lower_second_does_not_add_a_false_melody_onset(self):
        pitches, evidence = upper_voice(self.glyphs, self.system, self.key)
        self.assertEqual(pitches, [4, 7])
        self.assertEqual(evidence['excludedLowerSeconds'], [self.glyphs[0]])
        self.assertEqual(evidence['voice'], [self.glyphs[1], self.glyphs[3]])

    def test_review_cannot_remove_the_higher_voice_or_an_unrelated_note(self):
        with self.assertRaises(ValueError):
            upper_voice(self.glyphs, {**self.system, 'lowerDisplacedSeconds': [10]}, self.key)
        with self.assertRaises(ValueError):
            upper_voice([g for g in self.glyphs if g['x'] != 10], self.system, self.key)
        with self.assertRaises(ValueError):
            upper_voice(self.glyphs, {**self.system, 'lowerDisplacedSeconds': [5, 5]}, self.key)

    def test_unreviewed_added_or_missing_melody_notes_are_rejected(self):
        with self.assertRaises(ValueError):
            upper_voice(self.glyphs, {**self.system, 'lowerDisplacedSeconds': []}, self.key)
        with self.assertRaises(ValueError):
            upper_voice(self.glyphs[:-1], self.system, self.key)
        with self.assertRaises(ValueError):
            upper_voice(self.glyphs + [{'x': 35, 'y': 3.4}], self.system, self.key)


if __name__ == '__main__':
    unittest.main()
