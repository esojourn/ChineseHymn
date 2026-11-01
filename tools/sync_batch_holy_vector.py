"""Read pinned monophonic noteheads and reviewed rhythms from a vector score."""

import math
from sync_batch_wait_score import sha


def rhythm_rows(pitches, segments):
    rows, cursor = [], 0
    for segment in segments:
        weights, rests = segment['weights'], segment['rests']
        if (
            not weights
            or any(type(w) not in (int, float) or not math.isfinite(w) or w <= 0 for w in weights)
            or any(
                set(rest) != {'after', 'beats'}
                or type(rest['after']) is not int
                or not 0 <= rest['after'] <= len(weights)
                or type(rest['beats']) not in (int, float)
                or not math.isfinite(rest['beats'])
                or rest['beats'] <= 0
                for rest in rests
            )
            or len({rest['after'] for rest in rests}) != len(rests)
        ):
            raise ValueError('复核节奏及印刷休止无效')
        selected = pitches[cursor : cursor + len(weights)]
        if len(selected) != len(weights):
            raise ValueError('节奏不得超出原谱音头')
        cursor += len(weights)
        row = {'notes': [], 'pitchOffsets': [], 'opticalWeights': []}
        for index in range(len(weights) + 1):
            for rest in rests:
                if rest['after'] == index:
                    row['notes'].append(0)
                    row['pitchOffsets'].append(0)
                    row['opticalWeights'].append(rest['beats'])
            if index < len(weights):
                row['notes'].append(1)
                row['pitchOffsets'].append(selected[index])
                row['opticalWeights'].append(weights[index])
        rows.append(row)
    if cursor != len(pitches):
        raise ValueError('复核节奏遗漏原谱音头')
    return rows


def vector_rows(path, profile):
    import pypdfium2 as pdf

    if sha(path) != profile['sha256'] or str(pdf.PYPDFIUM_INFO) != profile['pdfiumVersion']:
        raise ValueError('出版谱或 PDF 解析器已变化')
    document = pdf.PdfDocument(path)
    if len(document) != profile['pages'] or set(profile['parts']) != {'verse', 'verse4', 'chorus', 'coda'}:
        raise ValueError('出版谱缺少完整变体或结尾')
    result, observations = {}, {}
    for name, part in profile['parts'].items():
        pitches, systems = [], []
        for system in part['systems']:
            page = document[system['page']]
            text = page.get_textpage()
            chars = text.get_text_range()
            glyphs = []
            bottom = system['staffBottom']
            for index, character in enumerate(chars):
                if character not in ('œ', '˙'):
                    continue
                left, low, right, high = text.get_charbox(index)
                x, y = (left + right) / 2, (low + high) / 2
                if system['xmin'] <= x <= system['xmax'] and bottom - 12 <= y <= bottom + 25:
                    glyphs.append({'x': x, 'y': y, 'symbol': character})
            glyphs.sort(key=lambda row: row['x'])
            values = []
            for glyph in glyphs:
                step = round((glyph['y'] - bottom) / (4.1101 / 2))
                degree = 4 * 7 + 2 + step
                values.append([1, 2, 4, 6, 7, 9, 11][degree % 7])
            if values != system['expectedPitches']:
                raise ValueError('印刷音头与已复核 D 大调主旋律不符')
            pitches.extend(values)
            systems.append({'system': system, 'glyphs': glyphs, 'pitches': values})
        result[name] = rhythm_rows(pitches, part['segments'])
        observations[name] = systems
    return result, observations
