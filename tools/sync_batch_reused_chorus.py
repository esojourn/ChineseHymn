"""Reuse a literally repeated original chorus omitted from the final stanza."""
import copy
import math
from sync_batch_sources import clean, prepare, strip_verse
from sync_batch_sectional import reading_section_groups


def source_chorus_reuse(source, path, profile):
    if 'sourceChorusReuse' not in profile:
        return source
    review = profile['sourceChorusReuse']
    verses = source.get('verses', [])
    if (not isinstance(review, dict) or set(review) != {'version', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            source.get('language') != 'zh' or len(verses) < 3 or any(len(verse) != 1 for verse in verses) or
            len(source.get('regions', {})) != len(verses) or
            len({verse[0]['region'] for verse in verses}) != len(verses) or
            any(key in profile for key in ['shortFormReview', 'sourceVerseContext', 'initialVerseContext',
                'musicalVerseEdges', 'sourceVerseMusicRange', 'vocalInterludes', 'referenceEndingSequence'])):
        raise ValueError('末节副歌复用须绑定中文原文、至少三节及每节一个原有区域，不能叠加其他演唱结构')
    lines = prepare.read_text(path).split('\n')
    paragraphs, bounds = [], []
    for verse in verses:
        region = source['regions'][verse[0]['region']]
        lo, hi = region.get('startLine'), region.get('endLine')
        if (region.get('kind') != 'text' or type(lo) is not int or type(hi) is not int or
                not 0 <= lo <= hi < len(lines)):
            raise ValueError('副歌复用只能引用原始歌词的完整文本行')
        paragraph = [strip_verse(line.strip()) for line in lines[lo:hi + 1]]
        if any(not clean(line) for line in paragraph):
            raise ValueError('副歌复用不能跨越原文空行或非歌词行')
        paragraphs.append(paragraph)
        bounds.append((lo, hi))
    previous = paragraphs[:-1]
    count = 0
    for size in range(1, min(map(len, previous)) + 1):
        if any(paragraph[-size:] != previous[0][-size:] for paragraph in previous[1:]):
            break
        count = size
    if (count < 2 or any(count >= len(paragraph) for paragraph in previous) or
            len(clean(' '.join(previous[0][-count:]))) < 8 or
            paragraphs[-1][-1] == previous[0][-1]):
        raise ValueError('副歌须在所有前节逐行完全相同、保留独立主歌，且末节确实省略该副歌')
    region_id = f'r{len(source["regions"]) + 1}'
    if region_id in source['regions']:
        raise ValueError('复用副歌的区域编号与原有区域冲突')
    result = copy.deepcopy(source)
    first, after = bounds[0][1] - count + 1, bounds[0][1]
    result['regions'][region_id] = {'kind': 'text', 'startLine': first, 'endLine': after}
    result['verses'][-1].append({'region': region_id, 'text': ' '.join(previous[0][-count:]), 'label': '副歌'})
    result['reusedChorus'] = {'sourceRegion': verses[0][0]['region'], 'addedRegion': region_id,
        'sourceLines': [first, after], 'matchingOriginalVerses': list(range(len(previous))),
        'performedAfterVerse': len(verses)}
    return result


def validate_reused_chorus(source, profile, material):
    if 'sourceChorusReuse' not in profile:
        return None
    proof = source.get('reusedChorus')
    melody, optical, reference = material
    groups = reading_section_groups(len(optical), profile.get('readingSectionReview'))
    verses = source['verses']
    origin = profile.get('midiReference', {}).get('origin', {})
    rows = origin.get('printedRhythmReference', {}).get('numberedRowsAndQuarterBeats', [])
    offsets = origin.get('printedRhythmReference', {}).get('accidentals', {}).get(
        'offsets', [[0] * len(row) for row in rows])
    expected = [(note, shift, beat) for row, shifts in zip(rows, offsets, strict=True)
                for (note, beat), shift in zip(row, shifts, strict=True)]
    if (not proof or len(melody) != len(optical) or len(groups) != len(verses) + 1 or
            [len(verse) for verse in verses] != [1] * (len(verses) - 1) + [2] or
            origin.get('kind') != 'printed-numbered-score' or not any(note for note, shift, beat in expected) or
            any(not math.isfinite(beat) or beat <= 0 for note, shift, beat in expected) or
            reference.get('symbolicRows', {}).get('editCost') != 0):
        raise ValueError('复用副歌须通过完整独立原谱及全部旋律音头零编辑核对')
    def music(indices):
        return [(note, shift, beat) for index in indices for section in groups[index]
                for note, shift, beat in zip(optical[section]['notes'], optical[section]['pitchOffsets'],
                                            optical[section]['rhythmWeights'], strict=True)]
    if any(music([index]) != expected for index in range(len(verses) - 1)) or music(
            [len(verses) - 1, len(verses)]) != expected:
        raise ValueError('末节主歌加复用副歌须与每个前节的完整原谱音高和时值完全一致')
    recipe = profile['midiReference']['arrangement']
    quarters = sum(beat for note, shift, beat in expected)
    if (not math.isclose(recipe.get('quarterLength', 0), quarters, abs_tol=1e-8) or
            recipe.get('sections', [])[:len(verses)] != [[0, recipe.get('quarterLength')]] * len(verses)):
        raise ValueError('副歌复用不得改变各原文诗节的完整原谱演奏范围')
    return {**proof, 'completePrintedSoundedNotes': sum(bool(note) for note, shift, beat in expected),
            'performedWholeVerses': len(verses)}
