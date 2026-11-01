from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_paired_reading import contextual_previews, free_prefix, paired_source, timeline
from sync_batch_sources import prepare


class PairedReading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads((ROOT / 'tools/sync-paired-reading-profiles.json').read_text())['06-004']
        cls.record = json.loads((ROOT / cls.profile['acousticRecord']['file']).read_text())
        cls.transcripts = [json.loads((ROOT / item['file']).read_text()) for item in cls.profile['fullTranscripts']]
        cls.lines = prepare.read_text(ROOT / 'data/06-004我要跟从你.txt').split('\n')

    def source(self, profile=None):
        return paired_source(self.lines, profile or self.profile)

    def test_display_retains_every_original_line_and_all_performed_pairs(self):
        source, definitions, pairs, adjacent, returns, jumps = self.source()
        order = source['textLayout']['lineOrder']
        self.assertEqual(sorted(order), list(range(len(self.lines))))
        self.assertEqual((len(pairs), len(definitions), len(adjacent)), (40, 48, 45))
        self.assertEqual(returns, [24])
        self.assertEqual(jumps, [24, 32])
        for pair in pairs:
            region = source['regions'][pair['region']]
            self.assertEqual(
                order[region['startLine'] : region['endLine'] + 1],
                [pair['englishSourceLine'], pair['translationSourceLine']],
            )
        self.assertEqual(order[15:19], [9, 18, 10, 17])
        self.assertEqual(definitions, self.record['definitions'])

    def test_missing_translations_and_unperformed_source_blocks_are_rejected(self):
        profile = deepcopy(self.profile)
        profile['blocks'][0]['translations'][-1] = profile['blocks'][0]['translations'][0]
        with self.assertRaises(ValueError):
            self.source(profile)
        profile = deepcopy(self.profile)
        profile['blockOrder'] = [0, 1, 2, 1, 3]
        with self.assertRaises(ValueError):
            self.source(profile)
        with self.assertRaises(ValueError):
            paired_source(self.lines + ['An omitted lyric line'], self.profile)

    def previews(self, observations=None, estimates=None, profile=None):
        return contextual_previews(
            self.record['definitions'],
            estimates or self.record['estimates'],
            observations or self.record['observations'],
            (profile or self.profile)['clippedPrefixReadings'],
            self.profile['previewSeconds'],
        )

    def test_reviewed_clipped_words_remain_explicit_previews(self):
        times, decisions = self.previews()
        self.assertEqual(len(times), 48)
        self.assertEqual(times[0], 0)
        clipped = [d['index'] for d in decisions if d['method'].startswith('reviewed clipped-prefix')]
        self.assertEqual(clipped, [5, 15, 18, 22])
        for index in clipped:
            pair = [r for r in self.record['observations'] if r['index'] == index]
            self.assertEqual(times[index], round(min(r['firstWordEnd'] for r in pair) - 1, 2))

    def test_clipped_prefix_needs_explicit_review_and_consistent_following_edges(self):
        profile = deepcopy(self.profile)
        profile['clippedPrefixReadings'].remove(15)
        with self.assertRaises(ValueError):
            self.previews(profile=profile)
        estimates = list(self.record['estimates'])
        estimates[15] -= 2
        with self.assertRaises(ValueError):
            self.previews(estimates=estimates)
        observations = deepcopy(self.record['observations'])
        row = next(r for r in observations if r['index'] == 15 and r['window'] == 1)
        for word in row['words'][-5:]:
            word['end'] = word['start']
        with self.assertRaises(ValueError):
            self.previews(observations=observations)

    def test_original_words_and_two_complete_windows_are_required(self):
        observations = deepcopy(self.record['observations'])
        row = next(r for r in observations if r['index'] == 10)
        row['words'][-2]['word'] = 'unrelated'
        with self.assertRaises(ValueError):
            self.previews(observations=observations)
        with self.assertRaises(ValueError):
            self.previews(observations=self.record['observations'][:-1])

    def test_nonadjacent_jumps_require_unique_free_original_prefixes(self):
        _, definitions, _, _, _, jumps = self.source()
        times, proofs = timeline(
            definitions,
            self.record['estimates'],
            self.record['observations'],
            self.profile,
            self.transcripts,
            jumps,
            313,
        )
        self.assertEqual(times[24], 150.1)
        self.assertEqual(times[32], 214.1)
        self.assertEqual(len(proofs['discontinuousPrefixes']), 2)
        ambiguous = deepcopy(self.transcripts)
        ambiguous[0]['segments'] += deepcopy(ambiguous[0]['segments'])
        with self.assertRaises(ValueError):
            free_prefix(ambiguous, definitions[24]['text'], 5, self.record['estimates'][24])
        conflicting = deepcopy(self.transcripts)
        for segment in conflicting[1]['segments']:
            for word in segment.get('words', []):
                if 150 < word['start'] < 156.5:
                    word['start'] += 1
                    word['end'] += 1
        with self.assertRaises(ValueError):
            free_prefix(conflicting, definitions[24]['text'], 5, self.record['estimates'][24])


if __name__ == '__main__':
    unittest.main()
