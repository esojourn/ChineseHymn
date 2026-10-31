from pathlib import Path
import importlib.util, json, math, sys
import numpy as np

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / 'tools'))
sp = importlib.util.spec_from_file_location('c', R / 'tools/sectional-midi-sync.py')
c = importlib.util.module_from_spec(sp)
sp.loader.exec_module(c)
from sync_batch_sectional import check_match, four_path_time
from sync_batch_symbolic import score_row_offsets


def measured_score(reference_profile, cache, target, sampling, prepare=False):
    rows, opt, proof = c.reference_material(reference_profile, cache, prepare)
    if len(rows) != 4 or len(opt) != 4 or proof['symbolicRows']['editCost'] != 0:
        raise ValueError('完整原谱必须逐音匹配四个连续阅读区域')
    _, _, mel = c.midi.midi_features(cache / reference_profile['midiReference']['file'])
    offsets = proof['readingOffsets']
    m8 = []
    o8 = []
    for i in range(4):
        r = opt[i]
        cuts = [j for j, n in enumerate(r['notes']) if n and sum((bool(v) for v in r['notes'][:j])) == 8]
        if len(cuts) != 1 or sum((bool(v) for v in r['notes'])) != 16:
            raise ValueError('原谱每行必须对应两个八音原文行')
        cut = cuts[0]
        o8.extend(
            [
                {k: r[k][:cut] for k in ['notes', 'pitchOffsets', 'rhythmWeights']},
                {k: r[k][cut:] for k in ['notes', 'pitchOffsets', 'rhythmWeights']},
            ]
        )
        first = next((j for j, t in enumerate(mel['onsetTimes']) if abs(t - offsets[i]) < 0.01))
        split = round((mel['onsetTimes'][first + 8] - offsets[i]) / 0.1)
        r = rows[i]
        m8.extend(
            [
                {k: r[k][:split] for k in ['notes', 'pitchOffsets', 'spatialWeights']},
                {k: r[k][split:] for k in ['notes', 'pitchOffsets', 'spatialWeights']},
            ]
        )
    _, lineproof = score_row_offsets(o8, mel['onsetTimes'], mel['onsetPitches'])
    if lineproof['editCost'] != 0 or len(lineproof['offsets']) != 8:
        raise ValueError('两行歌词分界未逐音对应原谱')
    if (
        not isinstance(sampling, list)
        or len(sampling) != 4
        or any(
            (
                set(x) != {'estimate', 'period'}
                or any((type(v) not in (int, float) or not math.isfinite(v) for v in x.values()))
                or x['estimate'] < 3
                or (x['period'] <= 0)
                for x in sampling
            )
        )
    ):
        raise ValueError('四个完整诗节取样配置无效')
    result = []
    base_key = None
    for j, item in enumerate(sampling):
        est, period = (item['estimate'], item['period'])
        full = [
            c.midi.match_template(x, target[1], target[0], period, est, w, onset_bounds=[est - 3, est + 3])
            for x, w in [(m8, 'spatialWeights'), (o8, 'rhythmWeights')]
        ]
        a, z = [x['best'] for x in full]
        check_match(full[0], period)
        check_match(full[1], period, (a['key'] + proof['symbolicRows']['semitoneShift']) % 12)
        if (
            len(a['starts']) != 8
            or len(z['starts']) != 8
            or max((abs(x - y) for x, y in zip(a['starts'], z['starts']))) > 0.5
        ):
            raise ValueError('两条完整原谱路径的诗句起点不一致')
        if base_key is None:
            base_key = a['key']
        if a['key'] != (base_key + [0, 0, 0, 1][j]) % 12:
            raise ValueError('四节录音调性与已复核转调不符')
        row = {'index': j, 'fullMidi': full[0], 'fullOptical': full[1], 'local': []}
        result.append(row)
        for i in range(4):
            at = (a['starts'][2 * i] + z['starts'][2 * i]) / 2
            end = (
                (a['starts'][2 * i + 2] + z['starts'][2 * i + 2]) / 2
                if i < 3
                else (a['starts'][0] + a['span'] + z['starts'][0] + z['span']) / 2
            )
            span = end - at
            if span <= 0.5:
                raise ValueError('完整原谱路径的阅读顺序无效')
            local = [
                c.midi.match_template([x[i]], target[1], target[0], span, at, w, onset_bounds=[max(0, at - 3), at + 3])
                for x, w in [(rows, 'spatialWeights'), (opt, 'rhythmWeights')]
            ]
            check_match(local[0], span, a['key'])
            check_match(local[1], span, z['key'])
            actual = four_path_time(
                [a['starts'][2 * i], z['starts'][2 * i], local[0]['best']['starts'][0], local[1]['best']['starts'][0]]
            )
            last = (a['starts'][2 * i + 1] + z['starts'][2 * i + 1]) / 2
            row['local'].append(
                {
                    'index': i,
                    'scoreAnchor': a['starts'][2 * i],
                    'at': actual,
                    'lastLineScoreStart': last,
                    'midi': local[0],
                    'optical': local[1],
                }
            )
    flat = [r for verse in result for r in verse['local']]
    if any((a['at'] + 0.5 >= b['at'] for a, b in zip(flat, flat[1:]))):
        raise ValueError('四节诗句位置未递增')
    edges = []
    for verse in result:
        x, y = (verse[k]['best'] for k in ('fullMidi', 'fullOptical'))
        edges.append(
            ((x['starts'][0] + y['starts'][0]) / 2, (x['starts'][0] + x['span'] + y['starts'][0] + y['span']) / 2)
        )
    minimum_span = min((end - start for start, end in edges))
    if (
        edges[0][0] >= 0.75 * minimum_span
        or float(target[0][-1]) - edges[-1][1] >= 0.75 * minimum_span
        or any((nxt - end >= 0.75 * minimum_span for (_, end), (nxt, _) in zip(edges, edges[1:])))
    ):
        raise ValueError('首尾或间奏可能容纳额外完整诗节')
    return {'reference': proof, 'lineProof': lineproof, 'verses': result}
