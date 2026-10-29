"""Corroborate a misrecognized Chinese refrain with two literal lyric repeats."""
from importlib.metadata import version
import numpy as np
from sync_batch_sources import clean


def validate_review(profile, language, source):
    review = profile.get('repeatHomophones')
    if review is None:
        return None
    if (not isinstance(review, dict) or set(review) != {'version', 'blocks', 'pinyinVersion', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            language != 'zh' or source.get('layout') != 'lyrics-only-text' or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            not isinstance(review.get('blocks'), list) or not review['blocks'] or
            any(type(index) is not int or not 0 <= index < len(profile['blocks']) for index in review['blocks']) or
            review['blocks'] != sorted(set(review['blocks'])) or
            review['pinyinVersion'] != '0.55.0' or version('pypinyin') != review['pinyinVersion']):
        raise ValueError('重复副歌同音复核需要原文、明确片段及固定拼音版本')
    return review


def verify(index, profile, source, all_words, match_order, lexical_positions):
    review = profile['repeatHomophones']
    if index not in review['blocks']:
        raise ValueError('未明确复核的片段不能采用同音对应')
    block = profile['blocks'][index]
    original = source['blocks'][block['region']]['text']
    text = block.get('sourceText', original)
    target = clean(text)
    if len(target) < 20 or target != clean(original):
        raise ValueError('重复同音复核须覆盖至少二十字的完整原文副歌')
    def selected(item):
        interval = item.get('wordRange')
        if (not isinstance(interval, list) or len(interval) != 2 or
                any(type(value) is not int for value in interval) or not 0 <= interval[0] < interval[1] <= len(all_words)):
            raise ValueError('重复副歌须绑定完整转写的明确词语范围')
        return all_words[interval[0]:interval[1]]
    current = selected(block)
    peers, intervals = [], [block['wordRange']]
    for peer, item in enumerate(profile['blocks']):
        if peer == index:
            continue
        other_original = source['blocks'][item['region']]['text']
        if clean(item.get('sourceText', other_original)) != target or clean(other_original) != target:
            continue
        if match_order([{'region': item['region'], 'text': other_original}], selected(item), 'zh')[0]:
            interval = item['wordRange']
            if any(max(interval[0], prior[0]) < min(interval[1], prior[1]) for prior in intervals):
                raise ValueError('重复副歌的印证必须来自不同且不重叠的实际演唱')
            intervals.append(interval)
            peers.append(peer)
    left, right = target, clean(''.join(word['word'] for word in current))
    positions = lexical_positions(np.array([ord(char) for char in left], dtype=np.int32),
                                  np.array([ord(char) for char in right], dtype=np.int32))
    literal = float(np.mean(positions >= 0))
    from pypinyin import lazy_pinyin, Style
    a, b = lazy_pinyin(left, style=Style.NORMAL), lazy_pinyin(right, style=Style.NORMAL)
    symbols = {value: i for i, value in enumerate(sorted(set(a + b)))}
    positions = lexical_positions(np.array([symbols[value] for value in a], dtype=np.int32),
                                  np.array([symbols[value] for value in b], dtype=np.int32))
    fraction = float(np.mean(positions >= 0))
    if len(peers) < 2 or literal < .5 or fraction < .8:
        raise ValueError('同音副歌未获两次逐字确认的完整重复及足够逐字、逐音节对应')
    return {'index': index, 'region': block['region'], 'literalMatchedFraction': literal,
            'syllableMatchedFraction': fraction, 'corroboratingBlocks': peers,
            'pinyinVersion': review['pinyinVersion'],
            'method': 'two literal-confirmed complete source refrains and tone-free syllable correspondence'}
