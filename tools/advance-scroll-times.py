#!/usr/bin/env python3
"""Advance existing scroll times once, without rerunning recognition or alignment."""

import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUEST = 'user-preview-2026-11-01'
SECONDS = Decimal('1.5')
EXCLUDED = {'05-099'}


def advance(data, baseline_hash):
    if data['songId'] in EXCLUDED:
        return None
    previous = data.get('provenance', {}).get('timingAdjustment')
    if previous is not None:
        if previous.get('requestId') == REQUEST and previous.get('advanceSeconds') == float(SECONDS):
            return None
        raise ValueError('已有其他时间调整，须先核对：' + data['songId'])
    result = deepcopy(data)
    timelines = [result['cues']] + [track['cues'] for track in result['tracks'] if 'cues' in track]
    for cues in timelines:
        for cue in cues:
            for key in ('at', 'moveStart'):
                if key in cue:
                    cue[key] = float(max(Decimal(0), Decimal(str(cue[key])) - SECONDS))
        if any(a['at'] >= b['at'] for a, b in zip(cues, cues[1:])):
            raise ValueError('提前后出现重叠时间点：' + data['songId'])
    result.setdefault('provenance', {})['timingAdjustment'] = {
        'version': 1,
        'requestId': REQUEST,
        'advanceSeconds': float(SECONDS),
        'clampAtZero': True,
        'baselineSha256': baseline_hash,
        'reason': '用户试听后要求在现有时间点基础上统一提前1.5秒；不重新识别。',
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='write changes; otherwise only report proposed changes')
    args = parser.parse_args()
    proposals, points, transitions = [], 0, 0
    for path in sorted((ROOT / 'data/sync').glob('*.json')):
        raw = path.read_bytes()
        result = advance(json.loads(raw), hashlib.sha256(raw).hexdigest())
        if result is None:
            continue
        timelines = [result['cues']] + [track['cues'] for track in result['tracks'] if 'cues' in track]
        points += sum(len(cues) for cues in timelines)
        transitions += sum('moveStart' in cue for cues in timelines for cue in cues)
        proposals.append((path, result))
    if args.write:
        for path, result in proposals:
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'written': args.write, 'files': len(proposals), 'cuePoints': points,
                      'transitionStarts': transitions, 'advanceSeconds': float(SECONDS),
                      'excludedSongs': sorted(EXCLUDED)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
