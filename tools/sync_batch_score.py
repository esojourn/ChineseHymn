"""Numbered-score melody correspondence for recordings without aligned vocals."""
import json
from pathlib import Path
import re
import numpy as np
from numba import njit
from sync_batch_audio import features, repeated_verses
from sync_batch_sources import image_source

_ocr = None


class ScoreMismatch(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def note_rows(source, recognition, cache):
    global _ocr
    destination = cache / 'notes' / (recognition['source'] + '.json')
    if destination.exists():
        data = json.loads(destination.read_text())
        if data['sha256'] == recognition['sha256'] and data.get('version') == 1:
            return data['rows']
    import cv2
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR
    if _ocr is None:
        _ocr = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    image = np.array(Image.open(source).convert('RGB'))
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), recognition.get('recognitionAngle', 0), 1)
    image = cv2.warpAffine(image, matrix, (w, h), borderValue=(255, 255, 255))
    definition = image_source(recognition)
    rows = []
    for system in definition['systems']:
        band = system['notes'].get('recognitionBand')
        if not band:
            raise ValueError('需重做带坐标映射的谱行识别')
        crop = image[band[0]:band[1]]
        results, _ = _ocr.text_rec(crop, return_word_box=True)
        text, confidence, detail = results[0]
        length, words, columns, states, probabilities = detail
        characters = [(char, column / length) for group, group_columns in zip(words, columns)
                      for char, column in zip(group, group_columns)]
        notes, positions, char_indices = [], [], []
        for index, (char, x) in enumerate(characters):
            char = char.replace('i', '1').replace('I', '1')
            if not re.fullmatch('[0-7]', char):
                continue
            # A tall bar mistaken for "1" must not become a melody note.
            column = round(x * w)
            stripe = np.mean(crop[:, max(0, column - 3):min(w, column + 4)], axis=2) < 150
            if char == '1' and stripe.size and stripe.sum(axis=0).max() / crop.shape[0] > .85:
                continue
            notes.append(int(char))
            positions.append(x)
            char_indices.append(index)
        if len(notes) < 4:
            raise ValueError('谱行音符不足，不能匹配纯伴奏')
        deltas = np.diff(positions + [min(1, positions[-1] + .07)])
        unit = float(np.median(deltas))
        spatial = np.clip(np.round(deltas / max(unit, .001) * 2) / 2, .5, 4).tolist()
        rhythm = []
        for i, index in enumerate(char_indices):
            end = char_indices[i + 1] if i + 1 < len(char_indices) else len(characters)
            suffix = ''.join(char for char, x in characters[index + 1:end])
            rhythm.append(1 + len(re.findall('[-—]', suffix)))
        rows.append({'text': text, 'confidence': float(confidence), 'notes': notes,
                     'x': positions, 'spatialWeights': spatial, 'rhythmWeights': rhythm})
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'version': 1, 'sha256': recognition['sha256'], 'rows': rows}, ensure_ascii=False, indent=2) + '\n')
    return rows


@njit(cache=True)
def subsequence_path(cost):
    n, m = cost.shape
    total = np.full((n + 1, m + 1), np.inf, dtype=np.float32)
    total[0, :] = 0
    back = np.zeros((n, m), dtype=np.uint8)
    for i in range(n):
        for j in range(m):
            diagonal, vertical, horizontal = total[i, j], total[i, j + 1] + .4, total[i + 1, j] + .4
            direction = 0 if diagonal <= vertical and diagonal <= horizontal else 1 if vertical <= horizontal else 2
            total[i + 1, j + 1] = cost[i, j] + (diagonal if direction == 0 else vertical if direction == 1 else horizontal)
            back[i, j] = direction
    end = np.argmin(total[n, :]) - 1
    i, j = n - 1, end
    path = []
    while i >= 0 and j >= 0:
        path.append((i, j))
        direction = back[i, j]
        if direction == 0:
            i -= 1
            j -= 1
        elif direction == 1:
            i -= 1
        else:
            j -= 1
    return total[n, end + 1] / n, np.array(path[::-1], dtype=np.int32)


def match_template(rows, chroma, times, period, onset_estimate, weight_key):
    step = float(times[1] - times[0])
    weights = [value for row in rows for value in row[weight_key]]
    notes = [value for row in rows for value in row['notes']]
    labels = [index for index, row in enumerate(rows) for value in row['notes']]
    total = sum(weights)
    ends = np.cumsum(weights) * period / total / step
    length = round(ends[-1])
    indices = np.minimum(np.searchsorted(ends, np.arange(length)), len(notes) - 1)
    scale = np.array([-1, 0, 2, 4, 5, 7, 9, 11])
    pitches = scale[np.array(notes)[indices]]
    frame_labels = np.array(labels)[indices]
    lo = max(0, round((onset_estimate - 15) / step))
    hi = min(chroma.shape[1], round((onset_estimate + period + 12) / step))
    candidates = []
    for key in range(12):
        score = chroma[(pitches + key) % 12, lo:hi]
        score[pitches == -1, :] = 0
        cost = np.ascontiguousarray(np.maximum(.05, .65 - score), dtype=np.float32)
        value, path = subsequence_path(cost)
        starts = []
        for label in range(len(rows)):
            frame = np.flatnonzero(frame_labels == label)[0]
            js = path[path[:, 0] == frame, 1]
            starts.append(float(times[lo + int(np.median(js))]))
        candidates.append({'cost': float(value), 'key': key, 'starts': starts})
    candidates.sort(key=lambda item: item['cost'])
    return {'best': candidates[0], 'runnerUp': candidates[1]}


def score_times(source, audio, recognition, cache):
    definition = image_source(recognition)
    rows = note_rows(source, recognition, cache)
    times, chroma = features(audio, cache)
    candidates = []
    for count in range(1, len(definition['verses']) + 1):
        try:
            music = repeated_verses(audio, count, cache) if count > 1 else {
                'period': float(times[-1]) * .85, 'initialOnsetEstimate': float(times[-1]) * .05,
                'shifts': [0.0], 'similarity': None}
            one = match_template(rows, chroma, times, music['period'], music['initialOnsetEstimate'], 'spatialWeights')
            candidates.append((one['best']['cost'], count, music, one))
        except ValueError:
            continue
    if not candidates:
        raise ValueError('未找到谱面与音乐对应')
    cost, count, music, one = min(candidates, key=lambda item: (round(item[0], 4), -item[1]))
    # Different printed verse counts may yield the same measured music period. Count
    # complete performed cycles using the matched final system, not a tie in that search.
    performed = min(len(definition['verses']), max(1, int((float(times[-1]) - one['best']['starts'][-1] - .5) // music['period']) + 1))
    if performed > 1:
        corroboration = repeated_verses(audio, performed, cache)
        if abs(corroboration['period'] - music['period']) > .5:
            raise ValueError('完整谱面匹配与实际反复次数不一致')
        music['shifts'] = corroboration['shifts']
    else:
        music['shifts'] = [0.0]
    definition['verses'] = definition['verses'][:performed]
    two = match_template(rows, chroma, times, music['period'], music['initialOnsetEstimate'], 'rhythmWeights')
    starts_a, starts_b = one['best']['starts'], two['best']['starts']
    evidence = {'algorithmVersion': 2, 'method': 'numbered-score melody/chroma matching', 'music': music,
                'rows': rows, 'spatialTemplate': one, 'rhythmTemplate': two}
    if one['best']['key'] != two['best']['key'] or max(abs(a - b) for a, b in zip(starts_a, starts_b)) > 1:
        raise ScoreMismatch('两组谱面节奏模板的定位不一致', evidence)
    if one['best']['cost'] > .35 or one['runnerUp']['cost'] - one['best']['cost'] < .06:
        raise ScoreMismatch('谱面旋律与实际录音匹配不足', evidence)
    phase = [(a + b) / 2 for a, b in zip(starts_a, starts_b)]
    cue_times = [round(at + shift, 2) for shift in music['shifts'] for at in phase]
    return definition, cue_times, evidence
