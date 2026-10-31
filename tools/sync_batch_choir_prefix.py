"""Diagnostic source-bound previews; time is an early reading position, not a sung onset."""

import math
from sync_batch_sources import clean
from sync_batch_segmented_preview import words

CONTRACTIONS = {'evry': 'every', 'powr': 'power'}


def fresh_first_word(definition, anchor, window):
    if words(definition['text'])[:5] != ['this', 'gift', 'of', 'love', 'and']:
        raise ValueError('句首独立复核仅适用于已绑定的原文')
    lo, hi = window['bounds']
    found = [w for segment in window['segments'] for w in segment.get('words', [])
             if clean(w['word'], 'en') == 'this' and abs(w['start'] - anchor) <= 2.5]
    if len(found) != 1:
        raise ValueError('新的原音频识别未唯一确认句首')
    word = found[0]
    if (not all(type(word.get(k)) in (int, float) and math.isfinite(word[k])
                for k in ('start', 'end', 'probability')) or
            not lo + .1 < word['start'] < word['end'] < hi - .1 or
            word['end'] - word['start'] < .04 or not .12 <= word['probability'] <= 1):
        raise ValueError('新的句首识别缺少有效时间或似然')
    return word


def candidate(index, definition, anchor, window, count):
    expected = words(definition['text'])[:count]
    if len(expected) != count or count < 3:
        raise ValueError('原文起句须至少三个连续词')
    normalize = lambda token: CONTRACTIONS.get(token, token)
    expected_normalized = [normalize(token) for token in expected]
    if any((token in CONTRACTIONS for token in expected)) and sum(map(len, expected_normalized)) < 10:
        raise ValueError('缩写复核须保留至少十个原文字母')
    bounds = window['bounds']
    heard = [w for segment in window['segments'] for w in segment.get('words', []) if clean(w['word'], 'en')]
    for word in heard:
        if (
            not all(
                (type(word.get(k)) in (int, float) and math.isfinite(word[k]) for k in ('start', 'end', 'probability'))
            )
            or not bounds[0] <= word['start'] <= word['end'] <= bounds[1] + 0.02
            or (not 0 <= word['probability'] <= 1)
        ):
            raise ValueError('原录音识别窗口含无效词语或时间')
    tokens = [normalize(clean(w['word'], 'en')) for w in heard]
    found = [
        heard[i : i + count]
        for i in range(len(tokens) - count + 1)
        if tokens[i : i + count] == expected_normalized and abs(heard[i]['start'] - anchor) <= 2.5
    ]
    if len(found) != 1:
        raise ValueError('原录音窗口未唯一识别完整起句')
    selected = found[0]
    if (
        any((w['end'] - w['start'] < 0.04 or w['probability'] < 0.12 for w in selected))
        or any((a['end'] > b['start'] + 0.02 for a, b in zip(selected, selected[1:])))
        or selected[0]['start'] <= bounds[0] + 0.1
        or (selected[-1]['end'] >= bounds[1] - 0.1)
    ):
        raise ValueError('原录音起句缺少有效时长、似然或上下文')
    return {'bounds': bounds, 'sourcePrefix': expected, 'normalizedPrefix': expected_normalized, 'words': selected}


def decision(index, candidates, previous_last_score):
    if len(candidates) < 2:
        raise ValueError('起句须有两组不同原录音窗口')
    first = [item['words'][0] for item in candidates]
    starts = [w['start'] for w in first]
    if max(starts) - min(starts) > 3:
        raise ValueError('起句预读候选范围过宽')
    at = round(min(starts), 2)
    if at < previous_last_score + 1:
        raise ValueError('预读早于上一阅读区域的末行谱面位置')
    return {
        'index': index,
        'at': at,
        'method': 'earliest corroborated source-prefix position used only as a reading preview; sung onset not claimed',
        'previousLastLineScoreStart': previous_last_score,
        'windows': candidates,
    }


def reviewed_gap(index, definition, anchor, window):
    expected = words(definition['text'])[:5]
    if index != 5 or expected != ['this', 'gift', 'of', 'love', 'and']:
        raise ValueError('遗漏单词核对仅适用于已绑定的原文起句')
    bounds = window['bounds']
    heard = [w for seg in window['segments'] for w in seg.get('words', []) if clean(w['word'], 'en')]
    tokens = [clean(w['word'], 'en') for w in heard]
    hits = [
        heard[i : i + 5]
        for i in range(len(tokens) - 4)
        if tokens[i] == 'this'
        and tokens[i + 2 : i + 5] == ['of', 'love', 'and']
        and (abs(heard[i]['start'] - anchor) <= 2.5)
    ]
    if len(hits) != 1:
        raise ValueError('未识别原词首词及其后连续三个词')
    selected = hits[0]
    for j, w in enumerate(selected):
        if (
            not all((type(w.get(k)) in (int, float) and math.isfinite(w[k]) for k in ('start', 'end', 'probability')))
            or not bounds[0] <= w['start'] < w['end'] <= bounds[1] + 0.02
            or (not 0 <= w['probability'] <= 1)
            or (w['end'] - w['start'] < 0.04)
            or (j != 1 and w['probability'] < 0.12)
        ):
            raise ValueError('起句中可识别词缺少有效音频或似然')
    if (
        any((a['end'] > b['start'] + 0.02 for a, b in zip(selected, selected[1:])))
        or selected[0]['start'] <= bounds[0] + 0.1
        or selected[-1]['end'] >= bounds[1] - 0.1
    ):
        raise ValueError('自由识别缺少完整音频上下文')
    return {
        'bounds': bounds,
        'sourcePrefix': expected,
        'words': selected,
        'unverifiedSourceWord': 'gift',
        'review': 'This and of love and are literal; the intervening word is unverified and its original decode is retained.',
    }


def reviewed_function_word(index, definition, anchor, window):
    expected = words(definition['text'])[:6]
    if index != 6 or expected != ['till', 'on', 'that', 'cross', 'as', 'jesus']:
        raise ValueError('冠词核对仅适用于已绑定的原文起句')
    bounds = window['bounds']
    heard = [w for seg in window['segments'] for w in seg.get('words', []) if clean(w['word'], 'en')]
    tokens = [clean(w['word'], 'en') for w in heard]
    hits = [
        heard[i : i + 6]
        for i in range(len(tokens) - 5)
        if tokens[i : i + 2] == ['till', 'on']
        and tokens[i + 2] in ('that', 'the')
        and (tokens[i + 3 : i + 6] == ['cross', 'as', 'jesus'])
        and (abs(heard[i]['start'] - anchor) <= 2.5)
    ]
    if len(hits) != 1:
        raise ValueError('未唯一识别前后五个原词')
    selected = hits[0]
    for w in selected:
        if (
            not all((type(w.get(k)) in (int, float) and math.isfinite(w[k]) for k in ('start', 'end', 'probability')))
            or not bounds[0] <= w['start'] < w['end'] <= bounds[1] + 0.02
            or (not 0 <= w['probability'] <= 1)
            or (w['end'] - w['start'] < 0.04)
            or (w['probability'] < 0.12)
        ):
            raise ValueError('起句词语缺少有效音频或似然')
    if (
        any((a['end'] > b['start'] + 0.02 for a, b in zip(selected, selected[1:])))
        or selected[0]['start'] <= bounds[0] + 0.1
        or selected[-1]['end'] >= bounds[1] - 0.1
    ):
        raise ValueError('自由识别缺少完整音频上下文')
    return {
        'bounds': bounds,
        'sourcePrefix': expected,
        'words': selected,
        'functionWord': clean(selected[2]['word'], 'en'),
        'review': 'Only that/the differs; the first two and following three source words remain literal.',
    }


def reviewed_clipped(index, definition, anchor, window, clipped):
    expected = words(definition['text'])[:4]
    if index != 12 or expected != ['no', 'guilt', 'in', 'life'] or type(clipped) is not bool:
        raise ValueError('窗口边缘预读仅适用于已绑定的原文首句')
    bounds = window['bounds']
    heard = [w for seg in window['segments'] for w in seg.get('words', []) if clean(w['word'], 'en')]
    tokens = [clean(w['word'], 'en') for w in heard]
    hits = [
        heard[i : i + 4]
        for i in range(len(tokens) - 3)
        if tokens[i : i + 4] == expected and abs(heard[i]['start'] - anchor) <= 2.5
    ]
    if len(hits) != 1:
        raise ValueError('未唯一识别完整的原文起句')
    selected = hits[0]
    for j, w in enumerate(selected):
        if (
            not all((type(w.get(k)) in (int, float) and math.isfinite(w[k]) for k in ('start', 'end', 'probability')))
            or not bounds[0] <= w['start'] < w['end'] <= bounds[1] + 0.02
            or (not 0 <= w['probability'] <= 1)
            or (w['end'] - w['start'] < 0.04)
            or (j > 0 and w['probability'] < 0.12)
            or (j == 0 and clipped and (w['probability'] < 0.12))
        ):
            raise ValueError('原文起句缺少有效音频或后续词似然')
    if (
        any((a['end'] > b['start'] + 0.02 for a, b in zip(selected, selected[1:])))
        or selected[-1]['end'] >= bounds[1] - 0.1
        or (clipped and abs(selected[0]['start'] - bounds[0]) > 0.02)
        or (not clipped and selected[0]['start'] <= bounds[0] + 0.1)
    ):
        raise ValueError('原文起句窗口边缘状态不符')
    return {
        'bounds': bounds,
        'sourcePrefix': expected,
        'words': selected,
        'clippedFirstWord': clipped,
        'review': 'Window-edge position is only an early preview; first-word onset is not claimed.',
    }


def clipped_decision(index, items, previous_last_score):
    clipped = [x for x in items if x['clippedFirstWord']]
    whole = [x for x in items if not x['clippedFirstWord']]
    if len(clipped) != 1 or len(whole) != 1 or abs(clipped[0]['words'][0]['end'] - whole[0]['words'][0]['end']) > 0.5:
        raise ValueError('窗口边缘首词须由独立完整转写确认词尾')
    at = round(clipped[0]['bounds'][0], 2)
    if at < previous_last_score + 1:
        raise ValueError('窗口边缘预读早于上一阅读区域末行')
    return {
        'index': index,
        'at': at,
        'method': 'clipped-window boundary used solely as an early reading preview; first-word onset is not claimed',
        'previousLastLineScoreStart': previous_last_score,
        'windows': items,
    }
