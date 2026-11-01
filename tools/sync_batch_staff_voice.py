"""Extract the upper voice of pinned two-voice vector staves, retaining displaced seconds."""

from sync_batch_holy_vector import rhythm_rows
from sync_batch_wait_score import sha


def upper_voice(glyphs, system, key):
    selected = [
        g
        for g in glyphs
        if system['xmin'] <= g['x'] <= system['xmax']
        and system['staffBottom'] - 12 <= g['y'] <= system['staffBottom'] + 25
    ]
    excluded = []
    for x in system['lowerDisplacedSeconds']:
        candidates = [g for g in selected if abs(g['x'] - x) < 0.05]
        if len(candidates) != 1:
            raise ValueError('错位二度和声音头缺失或重复')
        lower = candidates[0]
        if not any(
            0 < abs(g['x'] - lower['x']) <= 5.5 and abs(g['y'] - lower['y'] - system['halfStaffSpace']) < 0.08
            for g in selected
        ):
            raise ValueError('只能排除与相邻高音形成错位二度的低声部')
        excluded.append(lower)
        selected.remove(lower)
    columns = {}
    for glyph in selected:
        x = round(glyph['x'], 2)
        if x not in columns or glyph['y'] > columns[x]['y']:
            columns[x] = glyph
    voice = [columns[x] for x in sorted(columns)]
    pitches = []
    for glyph in voice:
        step = (glyph['y'] - system['staffBottom']) / system['halfStaffSpace']
        if abs(step - round(step)) > 0.12:
            raise ValueError('音头未落在原谱固定线间位置')
        pitches.append(key[(2 + round(step)) % 7])
    if pitches != system['expectedPitches']:
        raise ValueError('原谱高声部与复核音列不一致')
    return pitches, {'voice': voice, 'excludedLowerSeconds': excluded}


def staff_melody(path, review):
    import pypdfium2 as pdf

    if sha(path) != review['sha256'] or str(pdf.PYPDFIUM_INFO) != review['pdfiumVersion']:
        raise ValueError('原始五线谱或解析器版本已变化')
    document = pdf.PdfDocument(path)
    if len(document) != 1 or review['keyPitchClasses'] != [0, 2, 4, 6, 7, 9, 11]:
        raise ValueError('复核的完整单页 G 大调原谱不符')
    text = document[0].get_textpage()
    glyphs = []
    # In this original CCEH font the filled notehead is U+000D. Synthetic line
    # breaks have zero-sized boxes and must not be mistaken for printed notes.
    for index in range(text.count_chars()):
        symbol = text.get_text_range(index, 1)
        left, bottom, right, top = text.get_charbox(index)
        if symbol in ('\r', '\x10') and 4.8 < right - left < 5.1 and 4 < top - bottom < 4.6:
            glyphs.append({'x': (left + right) / 2, 'y': (bottom + top) / 2, 'symbol': ord(symbol)})
    pitches, observations = [], []
    for system in review['systems']:
        values, detail = upper_voice(glyphs, system, review['keyPitchClasses'])
        pitches.extend(values)
        observations.append({'system': system, **detail})
    rows = rhythm_rows(pitches, review['segments'])
    if len(rows) != 1:
        raise ValueError('完整短调必须保留为一个完整阅读区域')
    return [{**rows[0], 'rhythmWeights': rows[0]['opticalWeights']}], observations
