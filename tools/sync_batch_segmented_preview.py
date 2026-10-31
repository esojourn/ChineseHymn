"""Verify source-bound previews against a printed score and original-audio speech."""
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from sync_batch_gap_prefix import words
from sync_batch_sources import clean


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def printed_pitch_proof(svg, practice, ties):
    """Read the unaltered treble SVG notehead positions and verify each printed tie."""
    root = ET.parse(svg).getroot()
    staff_lines, heads, groups = [], [], []
    for group in root:
        match = re.fullmatch(r'translate\(([-\d.]+),\s*([-\d.]+)\)', group.get('transform', ''))
        if not match:
            continue
        x, y = map(float, match.groups())
        groups.append((x, y, group))
        for child in group:
            if child.tag.endswith('line') and float(child.get('x2', '0')) - float(child.get('x1', '0')) > 100:
                staff_lines.append(y)
            if child.tag.endswith('path'):
                data = child.get('d', '')
                if data.startswith('M0 -43c0 92 111 181 211 181') or data.startswith('M76 -103c39 0 197 107 224 139'):
                    heads.append({'x': x, 'y': y})
    if len(staff_lines) != 40 or len(heads) != 150:
        raise ValueError('原谱五线谱或音符数量发生变化')
    bottoms = sorted(staff_lines)[4::5]
    for head in heads:
        staff = min(range(len(bottoms)), key=lambda i: abs(head['y'] - (bottoms[i] - 2)))
        step = (bottoms[staff] - head['y']) * 2
        if abs(step - round(step)) >= .001 or abs(step) >= 12:
            raise ValueError('原谱音头无法定位到五线谱音高')
        degree = 2 + round(step)
        head.update(staff=staff, pitch=60 + 12 * (degree // 7) + [0, 2, 4, 5, 7, 9, 11][degree % 7])
    heads.sort(key=lambda h: (h['staff'], h['x']))
    if ties != [[32, 33], [68, 69], [107, 108], [146, 147]]:
        raise ValueError('原谱连音配置不符')
    checked = []
    for left, right in ties:
        first, second = heads[left], heads[right]
        if first['staff'] != second['staff'] or first['pitch'] != second['pitch']:
            raise ValueError('原谱连音的两个音头不同')
        candidates = []
        for x, y, group in groups:
            if abs(x - first['x']) > .01:
                continue
            for child in group:
                if not child.tag.endswith('path') or not child.get('stroke-width'):
                    continue
                data = child.get('d', '')
                curve = re.match(r'M([-\d.]+) ([-\d.]+)C.*?L([-\d.]+) ([-\d.]+)C', data)
                if curve:
                    x0, y0, x1, y1 = map(float, curve.groups())
                    if abs(x + x1 - second['x']) < 1 and abs(y + y0 - first['y']) < 1.5 and abs(y + y1 - second['y']) < 1.5:
                        candidates.append({'group': [x, y], 'curve': data})
        if len(candidates) != 1:
            raise ValueError('原谱连音曲线不能唯一对应两个音头')
        checked.append({'printedIndices': [left, right], 'geometry': candidates[0]})
    removed = {right for left, right in ties}
    printed = [row['pitch'] for i, row in enumerate(heads) if i not in removed]
    pitches = [int(row[2]) for row in practice]
    if printed != pitches:
        raise ValueError('原谱旋律与原始练习音符不完全一致')
    return {'staffBottoms': bottoms, 'noteheads': heads, 'ties': checked,
            'practicePitches': pitches, 'pitchSequenceExactAfterTies': True}


def common_gap_plan(medium, turbo, duration):
    transcripts = [medium, turbo]
    gaps = []
    for transcript in transcripts:
        segments = transcript['segments']
        if not segments or any(not all(type(s.get(k)) in (int, float) and math.isfinite(s[k]) for k in ('start', 'end')) or
                               not 0 <= s['start'] <= s['end'] <= duration + .1 for s in segments):
            raise ValueError('完整转写的时间范围无效')
        if any(a['start'] > b['start'] for a, b in zip(segments, segments[1:])):
            raise ValueError('完整转写的时间顺序无效')
        gaps.append([(a['end'], b['start']) for a, b in zip(segments, segments[1:]) if b['start'] - a['end'] >= 7])
    common = [(a, b) for a in gaps[0] for b in gaps[1] if min(a[1], b[1]) - max(a[0], b[0]) >= 5]
    common.sort(key=lambda pair: pair[0][0])
    if len(common) != 2 or common[0][0][1] >= common[1][0][0]:
        raise ValueError('两份原始转写未一致发现两处段间空隙')
    anchors = [(medium['segments'][0]['start'], turbo['segments'][0]['start'])]
    anchors.extend((left[1], right[1]) for left, right in common)
    ends = [common[0][0][0], common[1][0][0], medium['segments'][-1]['end']]
    plans = []
    for i, ((first, second), end) in enumerate(zip(anchors, ends)):
        if end <= first or abs(first - second) > 1.5:
            raise ValueError('分段转写的起点或结束不一致')
        plans.append({'index': i, 'estimate': first, 'span': round(end - first, 1),
                      'onsetBounds': [round(min(first, second) - 1.5, 2), round(max(first, second) + 1.5, 2)]})
    return plans, common


def prefix_candidate(index, definition, anchor, window, count, override=None):
    """Find a unique source prefix in an unprompted original-audio decode."""
    expected = words(definition['text'])[:count]
    if len(expected) != count or count < 2:
        raise ValueError('原文起句词数不足')
    decoded = expected
    if override is not None:
        if expected != ['to', 'thee', 'lord', 'my', 'all', 'i'] or override != ['through', 'the', 'lord', 'my', 'all', 'i']:
            raise ValueError('起句读音复核与固定原文不符')
        decoded = override
    heard = [w for segment in window['segments'] for w in segment.get('words', []) if clean(w['word'], 'en')]
    bounds = window['bounds']
    for word in heard:
        if (not all(type(word.get(k)) in (int, float) and math.isfinite(word[k]) for k in ('start', 'end', 'probability')) or
                not bounds[0] <= word['start'] <= word['end'] <= bounds[1] + .02 or not 0 <= word['probability'] <= 1):
            raise ValueError('自由识别窗口含无效词或时间')
    tokens = [clean(word['word'], 'en') for word in heard]
    limit = 1.5 if count == 2 else 2.5
    candidates = [heard[i:i + len(decoded)] for i in range(len(tokens) - len(decoded) + 1)
                  if tokens[i:i + len(decoded)] == decoded and abs(heard[i]['start'] - anchor) <= limit]
    if len(candidates) != 1:
        raise ValueError(f'第 {index + 1} 个区域未唯一识别原文起句')
    selected = candidates[0]
    if (any(w['end'] - w['start'] < .04 or w['probability'] < .12 for w in selected) or
            any(a['end'] > b['start'] + .02 for a, b in zip(selected, selected[1:])) or
            selected[0]['start'] <= bounds[0] + .1 or selected[-1]['end'] >= bounds[1] - .1):
        raise ValueError('起句词语缺少实际时长、似然或完整音频上下文')
    return {'bounds': bounds, 'sourcePrefix': expected, 'decodedPrefix': decoded, 'words': selected}


def preview_decision(index, candidates, previous_line_score):
    if len(candidates) != 2:
        raise ValueError('每个阅读位置须有两组原始录音起句')
    first = [candidate['words'][0] for candidate in candidates]
    if (abs(first[0]['start'] - first[1]['start']) > .5 or abs(first[0]['end'] - first[1]['end']) > .5):
        raise ValueError('两组起句的首词起止不一致')
    at = round(min(word['start'] for word in first), 2)
    if at < previous_line_score + 1:
        raise ValueError('预读早于上一阅读区域末行的谱面位置')
    return {'index': index, 'at': at, 'method': 'earlier of two audio-derived prefix starts, used as a reading preview; sung onset not claimed',
            'previousLastLineScoreStart': previous_line_score, 'windows': candidates}


def override_context_witness(rows, anchor, candidates):
    """Require two original contextual To/Thee spans to corroborate the reviewed free decode."""
    if len(rows) != 2 or {r.get('window') for r in rows} != {0, 1} or len(candidates) != 2:
        raise ValueError('起句读音复核缺少两组原文上下文')
    fixed = [(6, 16), (4, 14)]
    for row in rows:
        bounds = [anchor - fixed[row['window']][0], anchor + fixed[row['window']][1]]
        if (row.get('index') != 0 or row.get('bounds') != bounds or not row.get('valid') or
                row.get('firstWord') != 'to' or row.get('firstWordProbability', 0) < .12 or
                row.get('meanProbability', 0) < .12 or row.get('positiveDurationFraction', 0) < .65 or
                row.get('firstWordEnd', 0) - row.get('firstWordStart', 0) < .04):
            raise ValueError('起句读音复核的原文上下文无效')
        heard = [w for w in row.get('words', []) if clean(w['word'], 'en')]
        positions = [i for i in range(len(heard) - 1)
                     if [clean(heard[i]['word'], 'en'), clean(heard[i + 1]['word'], 'en')] == ['to', 'thee'] and
                     abs(heard[i]['start'] + bounds[0] - row['firstWordStart']) < .03]
        if len(positions) != 1:
            raise ValueError('上下文未唯一识别原文 To Thee')
        selected = heard[positions[0]:positions[0] + 2]
        if any(w['end'] - w['start'] < .04 or w['probability'] < .12 for w in selected):
            raise ValueError('上下文 To Thee 缺少实际时长或似然')
        for candidate in candidates:
            free = candidate['words'][0]
            if (not free['start'] - .02 <= row['firstWordStart'] <= free['end'] + .02 or
                    abs(row['firstWordEnd'] - free['end']) > .5):
                raise ValueError('原文上下文与无提示起句的声学范围不一致')
    return rows


def match_score(practice, plans, target, midi_cli):
    notes, chords = practice['notes'], practice['chords']
    if len(notes) != 146 or not chords or len(plans) != 3:
        raise ValueError('原谱音符、和弦或演唱段落数量不符')
    out = []
    for plan in plans:
        index = plan['index']
        parts = [(start, min(start + 8, 129)) for start in range(0, 129, 8)]
        if index == 2:
            parts += [(112, 120), (120, 128), (128, 129)]
        rows = []
        for left, right in parts:
            pitches = []
            for at in np.arange(left, right, .125):
                active = [pitch for onset, length, pitch, _ in notes if onset <= at < onset + length]
                pitches.append(max(active) if active else -1)
            rows.append({'notes': [0 if pitch < 0 else 1 for pitch in pitches],
                         'pitchOffsets': [max(0, pitch) % 12 for pitch in pitches], 'spatialWeights': [.125] * len(pitches)})
        melody = midi_cli.match_template(rows, target[1], target[0], plan['span'], plan['estimate'],
                                        'spatialWeights', onset_bounds=plan['onsetBounds'])
        total = 146 if index == 2 else 129
        times = np.arange(0, total, .1, dtype=np.float32)
        chroma = np.zeros((12, len(times)), dtype=np.float32)
        for i, at in enumerate(times):
            pos = at if at < 129 else 112 + at - 129
            for root, intervals, bass, onset, length in chords:
                if onset <= pos < onset + length:
                    for pitch in [root] + [(root + n) % 12 for n in intervals]:
                        chroma[pitch, i] += 1
                    if bass >= 0:
                        chroma[bass % 12, i] += 1
        chroma = np.sqrt(chroma)
        chroma -= chroma.mean(axis=0)
        chroma /= np.maximum(np.linalg.norm(chroma, axis=0), 1e-8)
        offsets = list(range(0, 129, 8)) + ([129, 137, 145] if index == 2 else [])
        harmony = midi_cli.same_tune.match_cycle((times, chroma), target, 0, total, plan['span'],
                    plan['estimate'], cue_offsets=offsets, onset_bounds=plan['onsetBounds'])
        for match in (melody, harmony):
            best, runner = match['best'], match['runnerUp']
            if (best['cost'] > .35 or runner['cost'] - best['cost'] < .06 or
                    not .75 * plan['span'] <= best['span'] <= 1.25 * plan['span']):
                raise ValueError('独立分段旋律或和弦未通过成本、调性及跨度核对')
        if melody['best']['key'] != harmony['best']['key']:
            raise ValueError('独立分段旋律与和弦的调性不一致')
        mel, harm = melody['best']['starts'], harmony['best']['starts']
        if len(mel) != len(harm) or abs(mel[0] - harm[0]) > .75:
            raise ValueError('分段旋律与和弦的首音范围不一致')
        # Initial chord can be held across the vocal pickup. Its difference is
        # retained as evidence; it is never treated as a measured lyric onset.
        if any(abs(a - b) > .5 for a, b in zip(mel[1:19], harm[1:19])):
            raise ValueError('旋律与和弦的逐行位置不一致')
        out.append({'plan': plan, 'melody': melody, 'harmony': harmony})
    return out
