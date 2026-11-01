"""Complete bilingual display order and conservative, explicitly labelled reading previews."""

import math
import re

from sync_batch_align import context
from sync_batch_sources import clean


def paired_source(lines, profile):
    order = list(range(len(lines)))
    regions, verses, pairs, occupied = {}, [], [], set()
    blocks = profile['blocks']
    for block_index, block in enumerate(blocks):
        english, translations = block['english'], block['translations']
        if (
            not english
            or len(english) != len(translations)
            or any(type(i) is not int or not 0 <= i < len(lines) for i in english + translations)
        ):
            raise ValueError('双语原文行号不完整')
        start, count = english[0], len(english)
        original = set(range(start, start + count * 2))
        if (
            english != list(range(start, start + count))
            or set(translations) != set(range(start + count, start + count * 2))
            or original & occupied
        ):
            raise ValueError('双语段落必须完整保留连续的原文及译文')
        occupied.update(original)
        arranged, verse = [], []
        for line_index, (en, zh) in enumerate(zip(english, translations)):
            if not re.search('[A-Za-z]', lines[en]) or not re.search('[\u3400-\u9fff]', lines[zh]):
                raise ValueError('双语行未对应英文及中文原文')
            arranged.extend([en, zh])
            region = f'b{block_index}_l{line_index}'
            regions[region] = {
                'kind': 'text',
                'startLine': start + 2 * line_index,
                'endLine': start + 2 * line_index + 1,
            }
            verse.append(
                {'region': region, 'text': lines[en], 'label': f'原文第{block_index + 1}段第{line_index + 1}行'}
            )
            pairs.append({'region': region, 'englishSourceLine': en, 'translationSourceLine': zh})
        order[start : start + count * 2] = arranged
        verses.append(verse)
    if (
        not occupied
        or set(order) != set(range(len(lines)))
        or len(order) != len(set(order))
        or any(lines[i].strip() for i in range(min(occupied), len(lines)) if i not in occupied)
    ):
        raise ValueError('双语排列遗漏或重复了原歌词正文')
    performance = profile['blockOrder']
    if set(performance) != set(range(len(blocks))) or any(
        type(i) is not int or not 0 <= i < len(blocks) for i in performance
    ):
        raise ValueError('实际演唱顺序必须覆盖全部原文段落')
    source = {
        'regions': regions,
        'verses': [verses[i] for i in performance],
        'language': 'en',
        'textLayout': {'version': 1, 'mode': 'paired-verse-lines', 'lineOrder': order},
    }
    definitions = [item for verse in source['verses'] for item in verse]
    adjacent, returns, discontinuous = [], [], []
    for index in range(1, len(definitions)):
        before, after = [regions[definitions[i]['region']] for i in (index - 1, index)]
        if 0 < after['startLine'] - before['endLine'] <= 3:
            adjacent.append(index)
        else:
            discontinuous.append(index)
            if after['startLine'] < before['startLine']:
                returns.append(index)
    return source, definitions, pairs, adjacent, returns, discontinuous


def current_words(row, definition):
    """The canonical aligner stores word times relative to the clip, unlike its summary edges."""
    words = [word for word in row['words'] if clean(word['word'], 'en')]
    expected = clean(context(definition['text'], 'en'), 'en')
    selected, value = [], ''
    for word in reversed(words):
        selected.insert(
            0,
            {
                **word,
                'start': round(word['start'] + row['bounds'][0], 3),
                'end': round(word['end'] + row['bounds'][0], 3),
            },
        )
        value = clean(word['word'], 'en') + value
        if len(value) >= len(expected):
            break
    if value != expected or not selected:
        raise ValueError('上下文窗口没有保留完整的本句原文')
    if (
        abs(selected[0]['start'] - row['firstWordStart']) > 0.011
        or abs(selected[0]['end'] - row['firstWordEnd']) > 0.011
    ):
        raise ValueError('原始词边界和窗口摘要不一致')
    return selected


def contextual_previews(definitions, estimates, observations, clipped, preview):
    if len(observations) != len(definitions) * 2 or len(estimates) != len(definitions):
        raise ValueError('完整演唱顺序的两组原始上下文窗口缺失')
    cues, decisions = [0], []
    used = set()
    for index, definition in enumerate(definitions):
        pair = sorted([r for r in observations if r['index'] == index], key=lambda r: r['window'])
        if [r['window'] for r in pair] != [0, 1]:
            raise ValueError('上下文窗口重复或缺失')
        words = [current_words(row, definition) for row in pair]
        if index == 0:
            continue
        if any(not r['valid'] or r['meanProbability'] < 0.12 or r['positiveDurationFraction'] < 0.65 for r in pair):
            raise ValueError(f'第{index}处双窗口整体检查未通过')
        native = all(r['firstWordEnd'] - r['firstWordStart'] >= 0.04 - 1e-6 for r in pair)
        if native and abs(pair[0]['at'] - pair[1]['at']) <= 0.5:
            position = min(r['at'] for r in pair)
            method = 'two canonical contextual starts; early reading preview, not exact sung onset'
        else:
            if index not in clipped or abs(pair[0]['firstWordEnd'] - pair[1]['firstWordEnd']) > 0.5:
                raise ValueError(f'第{index}处首词缺损且没有明确阅读预览复核')
            if (
                any(abs(r['firstWordEnd'] - estimates[index]) > 1.5 for r in pair)
                or len(words[0]) != len(words[1])
                or len(words[0]) < 5
            ):
                raise ValueError('缺损首词与原谱取样位置冲突或后续文字不足')
            supported = []
            for slot, (left, right) in enumerate(zip(*words)):
                if (
                    slot
                    and clean(left['word'], 'en') == clean(right['word'], 'en')
                    and min(left['end'] - left['start'], right['end'] - right['start']) >= 0.04 - 1e-6
                    and max(abs(left['start'] - right['start']), abs(left['end'] - right['end'])) <= 0.5
                ):
                    supported.append(slot)
            if len(supported) < 4:
                raise ValueError('缺损首词之后不足四处一致的上下文词边界')
            position = min(r['firstWordEnd'] for r in pair)
            used.add(index)
            method = 'reviewed clipped-prefix word-end guide; contextual following edges agree, literal recognition not claimed'
        cues.append(round(position - preview, 2))
        decisions.append(
            {
                'index': index,
                'at': cues[-1],
                'guideAt': position,
                'method': method,
                'firstWords': [
                    {
                        k: r[k]
                        for k in [
                            'firstWord',
                            'firstWordStart',
                            'firstWordEnd',
                            'firstWordProbability',
                            'meanProbability',
                            'positiveDurationFraction',
                        ]
                    }
                    for r in pair
                ],
            }
        )
    if used != set(clipped):
        raise ValueError('缺损首词配置与本次原始窗口不一致，须重新复核')
    return cues, decisions


def free_prefix(transcripts, text, count, estimate):
    expected = clean(' '.join(text.split()[:count]), 'en')
    witnesses = []
    for transcript in transcripts:
        words = [w for segment in transcript['segments'] for w in segment.get('words', []) if clean(w['word'], 'en')]
        candidates = []
        for start, word in enumerate(words):
            if abs(word['start'] - estimate) > 6:
                continue
            selected, value = [], ''
            for following in words[start:]:
                selected.append(following)
                value += clean(following['word'], 'en')
                if len(value) >= len(expected):
                    break
            if (
                value == expected
                and all(w['end'] - w['start'] >= 0.04 - 1e-6 for w in selected)
                and sum(w['probability'] for w in selected) / len(selected) >= 0.12
                and selected[0]['probability'] >= 0.12
            ):
                candidates.append(
                    {
                        'words': selected,
                        'at': selected[0]['start'],
                        'end': selected[0]['end'],
                        'precedingWordEnd': words[start - 1]['end'] if start else 0,
                    }
                )
        if len(candidates) != 1:
            raise ValueError('非相邻跳转未获独立完整转写的唯一原文开头确认')
        witnesses.append(candidates[0])
    if len(witnesses) != 2 or max(abs(witnesses[0][k] - witnesses[1][k]) for k in ['at', 'end']) > 0.5:
        raise ValueError('非相邻跳转的两种独立转写首词边界冲突')
    return witnesses


def timeline(definitions, estimates, observations, profile, transcripts, discontinuous, duration):
    times, decisions = contextual_previews(
        definitions, estimates, observations, profile['clippedPrefixReadings'], profile['previewSeconds']
    )
    reviews = profile['discontinuousPrefixes']
    if [r['index'] for r in reviews] != discontinuous:
        raise ValueError('所有非相邻跳转都须独立转写确认')
    proofs = []
    for review in reviews:
        index = review['index']
        witnesses = free_prefix(transcripts, definitions[index]['text'], review['words'], estimates[index])
        at = round(min(w['at'] for w in witnesses) - profile['previewSeconds'], 2)
        if any(at < w['precedingWordEnd'] + 0.3 for w in witnesses):
            raise ValueError('非相邻跳转会提前隐藏上一句')
        times[index] = at
        proofs.append({'index': index, 'at': at, 'witnesses': witnesses})
    if any(not math.isfinite(at) or at < 0 or at >= duration for at in times) or any(
        a + 0.5 >= b for a, b in zip(times, times[1:])
    ):
        raise ValueError('双语阅读预览未形成完整递增时间轴')
    return times, {'contextualDecisions': decisions, 'discontinuousPrefixes': proofs}
