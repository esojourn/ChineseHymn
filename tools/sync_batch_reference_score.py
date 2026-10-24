"""Load a pinned printed reference and group its optical notes by verified lyric openings."""
import hashlib
import json
from pathlib import Path
from sync_batch_score import note_rows
from sync_batch_symbolic import score_row_offsets


def score_rows_hash(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def group_reference_rows(rows, ranges, starts):
    if not rows or len(starts) < 1 or starts[0] != [0, 0]:
        raise ValueError('原谱阅读区域必须从首个旋律音开始')
    keys = ['notes', 'pitchOffsets', 'rhythmWeights']
    selected = []
    for index, row in enumerate(rows):
        lo, hi = ranges.get(str(index), [0, 1])
        if not 0 <= lo < hi <= 1 or any(len(row[key]) != len(row['x']) for key in keys):
            raise ValueError('原谱音符范围或节奏数据不完整')
        indices = [i for i, x in enumerate(row['x']) if lo <= x <= hi]
        selected.append({key: [row[key][i] for i in indices] for key in keys})
    while selected[0]['notes'] and selected[0]['notes'][0] == 0:
        for key in keys:
            selected[0][key].pop(0)
    if any(not row['notes'] for row in selected):
        raise ValueError('原谱包含空谱行')
    cuts = []
    for system, note in starts:
        if (type(system) is not int or type(note) is not int or not 0 <= system < len(selected) or
                not 0 <= note < len(selected[system]['notes']) or selected[system]['notes'][note] == 0):
            raise ValueError('原谱阅读区域没有明确的旋律首音')
        cuts.append(sum(len(row['notes']) for row in selected[:system]) + note)
    if cuts != sorted(set(cuts)):
        raise ValueError('原谱阅读区域起点必须严格递增')
    flat = {key: [value for row in selected for value in row[key]] for key in keys}
    return [{key: values[a:b] for key, values in flat.items()}
            for a, b in zip(cuts, cuts[1:] + [len(flat['notes'])])]


def reference_rows(profile, cache, offsets, melody):
    if profile.get('referenceDiscovery'):
        raise ValueError('检索候选不能用原谱光学模式替代原有严格录音检查')
    reference = profile['opticalReference']
    if reference.get('reviewedSystems') is not None and reference.get('noteExtractorVersion') != 4:
        raise ValueError('人工核对的原谱版面需要绑定谱行提取版本4')
    paths = {key: Path(cache) / reference[key] for key in ['svgFile', 'imageFile', 'recognitionFile']}
    for kind, expected in [('svgFile', profile['scoreReferenceSha256']), ('imageFile', reference['imageSha256'])]:
        if hashlib.sha256(paths[kind].read_bytes()).hexdigest() != expected:
            raise ValueError('独立原谱或渲染图片已改变，需重新核对')
    recognition = json.loads(paths['recognitionFile'].read_text())
    if (recognition.get('sha256') != reference['imageSha256'] or
            recognition.get('source') != paths['imageFile'].name or
            recognition.get('recognizerVersion') != reference['recognizerVersion']):
        raise ValueError('独立原谱的光学识别已失效')
    rows = note_rows(paths['imageFile'], recognition, paths['recognitionFile'].parent.parent,
                     reviewed_systems=reference.get('reviewedSystems'))
    if score_rows_hash(rows) != reference['rowsSha256']:
        raise ValueError('原谱光学音符或节奏已改变，需重新核对')
    grouped = group_reference_rows(rows, reference.get('noteRanges', {}), reference['readingStarts'])
    matched, proof = score_row_offsets(grouped, melody['onsetTimes'], melody['onsetPitches'])
    if len(matched) != len(offsets) or any(round(abs(a - b), 3) > .1 for a, b in zip(matched, offsets)):
        raise ValueError('独立原谱与已核对的文字阅读区域不一致')
    return grouped, {'reference': reference, 'sourceRows': rows, 'readingRows': grouped, 'symbolicRows': proof}
