"""Numbered-score melody correspondence for recordings without aligned vocals."""
import json
from pathlib import Path
import re
import unicodedata
import numpy as np
from numba import njit
from sync_batch_audio import features, repeated_verses
from sync_batch_sources import image_source

_ocr = None


class ScoreMismatch(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def accidental_offsets(characters, indices):
    offsets = []
    for position, index in enumerate(indices):
        start = indices[position - 1] + 1 if position else 0
        prefix = ''.join(char for char, x in characters[start:index]).strip()
        offsets.append(1 if prefix.endswith(('#', '♯', '*')) else -1 if prefix.endswith(('b', '♭')) else 0)
    return offsets


def is_barline(components, column, height):
    # OCR character columns can miss a thin bar by several pixels. Inspect the
    # nearest printed component, while retaining a real short digit beside it.
    candidates = [item for item in components if item[3] >= height * .25 and item[2] <= height * 1.5]
    if not candidates:
        return False
    x, y, width, span, area = min(candidates, key=lambda item: abs(item[0] + item[2] / 2 - column))
    return (abs(x + width / 2 - column) <= height * .5 and width <= height * .25 and
            span >= height * .85 and y <= 2 and y + span >= height - 2)


def optical_rhythm(ink, positions, original_height, fallback):
    import cv2
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    components = stats[1:]
    glyphs = [s for s in components if original_height * .25 < s[3] < original_height * .95 and
              3 <= s[2] < s[3] * 1.8 and s[1] < original_height]
    weights, diagnostics = [], []
    height, width = ink.shape
    for x, old in zip(positions, fallback):
        candidates = [s for s in glyphs if abs(s[0] + s[2] / 2 - x * width) < original_height * .7]
        if not candidates:
            weights.append(old)
            diagnostics.append({'glyphFound': False})
            continue
        glyph = min(candidates, key=lambda s: abs(s[0] + s[2] / 2 - x * width))
        gx, gy, gw, gh, area = map(int, glyph)
        center, baseline = gx + gw // 2, gy + gh
        stripe = ink[baseline + 2:min(height, baseline + round(gh * .55)),
                     max(0, center - round(gh * .2)):min(width, center + round(gh * .2) + 1)]
        rows = np.flatnonzero(stripe.mean(axis=1) > .55) if stripe.size else np.array([])
        beams = min(2, sum(index == 0 or row - rows[index - 1] > 1 for index, row in enumerate(rows)))
        dots = [s for s in components if 1 < s[2] < gh * .3 and 1 < s[3] < gh * .3 and
                gx + gw + 2 < s[0] < gx + gw + gh * .8 and gy + gh * .2 < s[1] < gy + gh * .85]
        following = next((p * width for p in positions if p * width > center + gh * .5), width)
        dashes = [s for s in components if s[2] >= gh * .3 and s[3] <= gh * .25 and
                  gx + gw + 2 < s[0] and s[0] + s[2] < following and
                  gy + gh * .25 < s[1] < gy + gh * .75]
        value = (1 + len(dashes)) / (2 ** beams) * (1.5 if dots else 1)
        weights.append(value)
        diagnostics.append({'glyphFound': True, 'beams': int(beams), 'dot': bool(dots), 'dashes': len(dashes)})
    return weights, diagnostics


def reviewed_score_systems(systems, height):
    """Validate explicitly reviewed reference-score bands, including their lyric limits."""
    if not isinstance(systems, list) or not systems:
        raise ValueError('原谱人工核对的谱行范围为空')
    previous = -1
    for system in systems:
        if (not isinstance(system, dict) or not isinstance(system.get('notes'), dict) or
                not isinstance(system.get('lyrics'), list) or not system['lyrics']):
            raise ValueError('原谱谱行需要音符及歌词边界')
        bands = [system['notes'].get('recognitionBand')]
        bands.extend(row.get('recognitionBand') if isinstance(row, dict) else None for row in system['lyrics'])
        if any(not isinstance(band, list) or len(band) != 2 or
               any(type(value) is not int for value in band) or not 0 <= band[0] < band[1] <= height
               for band in bands):
            raise ValueError('原谱谱行坐标超出固定图片范围')
        if bands[0][0] <= previous or any(left[1] > right[0] for left, right in zip(bands, bands[1:])):
            raise ValueError('原谱音符及歌词边界重叠或顺序不一致')
        previous = max(band[1] for band in bands)
    return systems


def note_rows(source, recognition, cache, reviewed_systems=None, normalize_digits=False):
    global _ocr
    if reviewed_systems is None:
        definition = image_source(recognition)
    else:
        from PIL import Image
        with Image.open(source) as raster:
            definition = {'systems': reviewed_score_systems(reviewed_systems, raster.height)}
    if not definition['systems']:
        raise ValueError('纯歌词图片没有简谱音符，需要人声转写匹配')
    if type(normalize_digits) is not bool or normalize_digits and reviewed_systems is None:
        raise ValueError('数字规范化需要显式核对的原谱版面')
    version = 5 if normalize_digits else 3 if reviewed_systems is None else 4
    destination = cache / 'notes' / (recognition['source'] + '.json')
    if destination.exists():
        data = json.loads(destination.read_text())
        if (data['sha256'] == recognition['sha256'] and data.get('version') == version and
                data.get('recognizerVersion', 2) == recognition.get('recognizerVersion') and
                data.get('reviewedSystems') == reviewed_systems and len(data['rows']) == len(definition['systems'])):
            return data['rows']
    import cv2
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR
    if _ocr is None:
        _ocr = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    image = np.array(Image.open(source).convert('RGB'))
    h, w = image.shape[:2]
    # Reviewed bands are coordinates in the pinned original raster, whereas
    # detected bands are coordinates in the recognizer's deskewed image.
    angle = recognition.get('recognitionAngle', 0) if reviewed_systems is None else 0
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
    image = cv2.warpAffine(image, matrix, (w, h), borderValue=(255, 255, 255))
    rows = []
    for system in definition['systems']:
        band = system['notes'].get('recognitionBand')
        if not band:
            raise ValueError('需重做带坐标映射的谱行识别')
        crop = image[band[0]:band[1]]
        ink = np.mean(crop, axis=2) < 150
        _, _, components, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
        results, _ = _ocr.text_rec(crop, return_word_box=True)
        text, confidence, detail = results[0]
        length, words, columns, states, probabilities = detail
        characters = [(char, column / length) for group, group_columns in zip(words, columns)
                      for char, column in zip(group, group_columns)]
        notes, positions, char_indices = [], [], []
        for index, (char, x) in enumerate(characters):
            if normalize_digits:
                char = unicodedata.normalize('NFKC', char)
            char = char.replace('i', '1').replace('I', '1')
            if not re.fullmatch('[0-7]', char):
                continue
            # A tall bar mistaken for "1" must not become a melody note.
            column = round(x * w)
            stripe = ink[:, max(0, column - 3):min(w, column + 4)]
            if char == '1' and (is_barline(components[1:], column, crop.shape[0]) or
                                stripe.size and stripe.sum(axis=0).max() / crop.shape[0] > .85):
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
        # Recognition bands usually omit short beams below the digit baseline.
        # Read those pixels up to (but not into) the first printed lyric row.
        lyric_bands = [row['recognitionBand'][0] - 2 for row in system['lyrics'] if 'recognitionBand' in row]
        analysis_bottom = max(band[1], min(lyric_bands or [band[1] + round(crop.shape[0] * .6)]))
        ink = np.mean(image[band[0]:analysis_bottom], axis=2) < 150
        optical, diagnostics = optical_rhythm(ink, positions, crop.shape[0], rhythm)
        rows.append({'text': text, 'confidence': float(confidence), 'notes': notes,
                     'pitchOffsets': accidental_offsets(characters, char_indices),
                     'x': positions, 'spatialWeights': spatial, 'rhythmWeights': optical,
                     'ocrRhythmWeights': rhythm, 'opticalDiagnostics': diagnostics})
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = {'version': version, 'sha256': recognition['sha256'],
            'recognizerVersion': recognition.get('recognizerVersion'), 'rows': rows}
    if reviewed_systems is not None:
        data['reviewedSystems'] = reviewed_systems
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
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
    offsets = np.array([value for row in rows for value in row.get('pitchOffsets', [0] * len(row['notes']))])
    pitches = (scale[np.array(notes)] + offsets)[indices]
    rests = np.array(notes)[indices] == 0
    frame_labels = np.array(labels)[indices]
    lo = max(0, round((onset_estimate - 15) / step))
    hi = min(chroma.shape[1], round((onset_estimate + period + 12) / step))
    candidates = []
    for key in range(12):
        score = chroma[(pitches + key) % 12, lo:hi]
        score[rests, :] = 0
        cost = np.ascontiguousarray(np.maximum(.05, .65 - score), dtype=np.float32)
        value, path = subsequence_path(cost)
        starts = []
        for label in range(len(rows)):
            frame = np.flatnonzero(frame_labels == label)[0]
            js = path[path[:, 0] == frame, 1]
            starts.append(float(times[lo + int(np.median(js))]))
        candidates.append({'cost': float(value), 'key': key, 'starts': starts,
                           'span': float(times[lo + path[-1, 1]] - times[lo + path[0, 1]])})
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
    evidence = {'algorithmVersion': 3, 'method': 'numbered-score melody/chroma matching; optical beams/dots/dashes and printed accidentals', 'music': music,
                'rows': rows, 'spatialTemplate': one, 'rhythmTemplate': two}
    if one['best']['key'] != two['best']['key'] or max(abs(a - b) for a, b in zip(starts_a, starts_b)) > 1:
        raise ScoreMismatch('两组谱面节奏模板的定位不一致', evidence)
    if one['best']['cost'] > .35 or one['runnerUp']['cost'] - one['best']['cost'] < .06:
        raise ScoreMismatch('谱面旋律与实际录音匹配不足', evidence)
    phase = [(a + b) / 2 for a, b in zip(starts_a, starts_b)]
    cue_times = [round(at + shift, 2) for shift in music['shifts'] for at in phase]
    return definition, cue_times, evidence
