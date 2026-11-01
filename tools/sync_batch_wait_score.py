"""Extract the reviewed melody from the publisher's fixed vector score."""

import hashlib
from pathlib import Path

import numpy as np
from sync_batch_sources import clean


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def vector_rows(path, profile):
    import pypdfium2 as pdf

    if (
        sha(path) != profile['sha256']
        or str(pdf.PYPDFIUM_INFO) != profile['pdfiumVersion']
        or set(profile['systems']) != {'verse', 'chorus'}
    ):
        raise ValueError('出版方曲谱或固定的解析器版本已变化')
    document = pdf.PdfDocument(path)
    if len(document) != 1:
        raise ValueError('出版方曲谱页数已变化')
    text = document[0].get_textpage()
    heads = []
    for index in range(text.count_chars()):
        character = text.get_text_range(index, 1)
        if character not in ('\x05', '\x08'):
            continue
        left, bottom, right, top = text.get_charbox(index)
        if 5.4 <= right - left <= 6.3 and 4 <= top - bottom <= 4.8:
            heads.append({'x': (left + right) / 2, 'y': (bottom + top) / 2, 'symbol': ord(character)})
    result = {}
    for name, systems in profile['systems'].items():
        result[name] = []
        for system in systems:
            if set(system) != {'staffBottom', 'xmin', 'xmax', 'excludeX', 'expectedPitches'}:
                raise ValueError('谱行复核配置无效')
            staff_bottom = system['staffBottom']
            grouped = {}
            for note in heads:
                if (
                    system['xmin'] <= note['x'] <= system['xmax']
                    and staff_bottom - 18 <= note['y'] <= staff_bottom + 22
                ):
                    key = round(note['x'], 1)
                    if key not in grouped or note['y'] > grouped[key]['y']:
                        grouped[key] = note
            ordered = sorted(grouped.values(), key=lambda note: note['x'])
            excluded = system['excludeX']
            kept = [note for note in ordered if not any(abs(note['x'] - x) < 0.2 for x in excluded)]
            if len(kept) != len(ordered) - len(excluded):
                raise ValueError('和声、延音或下一段起音的人工复核与原谱不符')
            pitches = []
            for note in kept:
                step = round((note['y'] - staff_bottom) / 2.1)
                degree = 4 * 7 + 2 + step
                pitches.append((12 * (degree // 7 + 1) + [0, 2, 4, 6, 7, 9, 11][degree % 7]) % 12)
            if pitches != system['expectedPitches']:
                raise ValueError('出版方五线谱音头与已复核旋律不一致')
            delta = np.diff([note['x'] for note in kept])
            if not len(delta) or any(value <= 0 for value in delta):
                raise ValueError('谱行音头位置无效')
            unit = float(np.median(delta))
            weights = np.clip(np.round(np.r_[delta, unit] / unit * 2) / 2, 0.5, 4).tolist()
            result[name].append(
                {
                    'notes': [1] * len(kept),
                    'pitchOffsets': pitches,
                    'spatialWeights': weights,
                    'glyphs': kept,
                    'staffBottom': staff_bottom,
                }
            )
    if [len(result['verse']), len(result['chorus'])] != [2, 3]:
        raise ValueError('原谱必须包含两行主歌和三行副歌旋律')
    return result


def source_phrase(definition, phrase, segments, bounds):
    if len(phrase) < 2 or phrase not in clean(definition['text'], 'zh'):
        raise ValueError('识别片段必须属于当前原文')
    matches = []
    for segment in segments:
        words = segment.get('words', [])
        characters = ''.join(clean(word['word'], 'zh') for word in words)
        mapping = [word for word in words for _ in clean(word['word'], 'zh')]
        start = 0
        while (position := characters.find(phrase, start)) >= 0:
            selected = mapping[position : position + len(phrase)]
            first, last = selected[0], selected[-1]
            if (
                bounds[0] <= first['start'] <= last['end'] <= bounds[1]
                and all(
                    type(word.get(key)) in (int, float) and np.isfinite(word[key])
                    for word in selected
                    for key in ('start', 'end', 'probability')
                )
                and all(word['end'] - word['start'] >= 0.04 and 0.12 <= word['probability'] <= 1 for word in selected)
            ):
                matches.append({'phrase': phrase, 'start': first['start'], 'end': last['end'], 'words': selected})
            start = position + 1
    if len(matches) != 1:
        raise ValueError('原音频未唯一识别所需的原文片段')
    return matches[0]


def agreement(values, limit=0.5):
    if (
        not values
        or any(type(value) not in (int, float, np.float32, np.float64) or not np.isfinite(value) for value in values)
        or round(max(values) - min(values), 3) > limit
    ):
        raise ValueError('独立音频路径的阅读位置不一致')
    return round(float(min(values)), 2)
