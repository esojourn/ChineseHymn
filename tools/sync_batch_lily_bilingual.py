"""Source-bound previews for the reviewed Korean and Mandarin performance."""

import math

from sync_batch_sources import clean
from sync_batch_sectional import four_path_time


def sounded_word(word):
    return (
        all(type(word.get(k)) in (int, float) and math.isfinite(word[k]) for k in ('start', 'end', 'probability'))
        and word['start'] >= 0
        and word['end'] - word['start'] >= 0.04
        and 0.12 <= word['probability'] <= 1
    )


def chinese_preview(definition, prefix, anchor, transcripts):
    if len(transcripts) != 2 or prefix not in clean(definition['text'], 'zh'):
        raise ValueError('中文预读必须绑定当前原文及两份录音转写')
    witnesses = []
    for transcript in transcripts:
        found = [
            segment
            for segment in transcript['segments']
            if abs(segment['start'] - anchor) <= 3 and clean(segment['text'], 'zh').startswith(prefix)
        ]
        if len(found) != 1 or not found[0].get('words'):
            raise ValueError('中文起句缺少唯一且有时长的原音频识别')
        selected, recognized = [], ''
        for word in found[0]['words']:
            recognized += clean(word['word'], 'zh')
            selected.append(word)
            if len(recognized) >= len(prefix):
                break
        if (
            recognized != prefix
            or not all(sounded_word(word) for word in selected)
            or any(a['end'] > b['start'] + 0.02 for a, b in zip(selected, selected[1:]))
        ):
            raise ValueError('中文原文起句缺少完整且有时长的识别')
        witnesses.append(selected)
    if abs(witnesses[0][0]['start'] - witnesses[1][0]['start']) > 0.5:
        raise ValueError('两份中文起句识别相差超过0.5秒')
    return {
        'at': round(min(words[0]['start'] for words in witnesses) - 0.8, 2),
        'sourcePrefix': prefix,
        'words': witnesses,
        'method': 'early reading preview, not a sung onset',
    }


def korean_opening(anchor, transcripts):
    if len(transcripts) != 2:
        raise ValueError('韩语副歌须有两份独立原音频识别')
    words = []
    for transcript in transcripts:
        found = [
            segment
            for segment in transcript['segments']
            if all(token in segment['text'] for token in ('산', '백합', '새벽별'))
            and any(abs(word['start'] - anchor) < 2 for word in segment.get('words', []))
        ]
        if len(found) != 1:
            raise ValueError('韩语副歌未唯一识别山谷百合与晨星')
        first = next((word for word in found[0]['words'] if '산' in word['word']), None)
        important = [
            next((word for word in found[0]['words'] if token in word['word']), None)
            for token in ('산', '백합', '새벽별')
        ]
        if not first or any(not word or not sounded_word(word) for word in important):
            raise ValueError('韩语副歌句首缺少有效音频')
        words.append(first)
    if abs(words[0]['start'] - words[1]['start']) > 0.5:
        raise ValueError('韩语副歌句首识别相差超过0.5秒')
    return {
        'at': round(min(word['start'] for word in words), 2),
        'words': words,
        'meaning': '山谷中的百合与明亮晨星',
        'method': 'early reading preview, not a sung onset',
    }


def korean_final_verse(anchor, transcripts):
    if len(transcripts) != 2:
        raise ValueError('末节韩语须由两份独立原音频识别核对')
    first, common = [], []
    for transcript in transcripts:
        found = [
            segment
            for segment in transcript['segments']
            if all(token in segment['text'] for token in ('맘을', '주님을', '따르며'))
            and any(abs(word['start'] - anchor) < 3 for word in segment.get('words', []))
        ]
        if len(found) != 1:
            raise ValueError('末节韩语起句未唯一识别')
        words = found[0]['words']
        heart = next((word for word in words if '맘을' in word['word']), None)
        initial = next((word for word in words if '내' in word['word']), None)
        if (
            not heart
            or not sounded_word(heart)
            or not initial
            or any(
                type(initial.get(key)) not in (int, float) or not math.isfinite(initial[key])
                for key in ('start', 'end', 'probability')
            )
            or not 0 <= initial['start'] <= initial['end']
            or not 0 <= initial['probability'] <= 1
        ):
            raise ValueError('末节韩语共同词缺少有效时长')
        common.append(heart)
        first.append(initial)
    if abs(common[0]['end'] - common[1]['end']) > 0.5 or not any(sounded_word(word) for word in first):
        raise ValueError('末节韩语共同词尾或起句时长不一致')
    return {
        'at': round(min(word['start'] for word in first) - 0.8, 2),
        'initialWords': first,
        'commonWords': common,
        'meaning': '心里跟随主',
        'method': 'early reading preview; first-word onset not claimed',
    }


def final_line_tail(definition, transcripts, minimum_verse_span, score_end):
    if (
        len(transcripts) != 2
        or not clean(definition['text'], 'zh').endswith('万人中救主最美好我爱他')
        or not math.isfinite(minimum_verse_span)
        or minimum_verse_span <= 0
    ):
        raise ValueError('延长尾句须绑定当前原文及完整歌段')
    if abs(transcripts[0]['duration'] - transcripts[1]['duration']) > 0.01:
        raise ValueError('两份完整转写的原录音时长不符')
    expected = '最美好我爱她'
    found = []
    for transcript in transcripts:
        words = [
            word
            for segment in transcript['segments']
            for word in segment.get('words', [])
            if score_end - 10 <= word['start'] < score_end + 20
        ]
        hits = [
            words[i : i + len(expected)]
            for i in range(len(words) - len(expected) + 1)
            if ''.join(clean(word['word'], 'zh') for word in words[i : i + len(expected)]) == expected
        ]
        if (
            len(hits) != 1
            or not all(sounded_word(word) for word in hits[0])
            or any(a['end'] > b['start'] + 0.02 for a, b in zip(hits[0], hits[0][1:]))
        ):
            raise ValueError('最后原文尾句未由原音频完整识别')
        found.append(hits[0])
    if abs(found[0][-1]['end'] - found[1][-1]['end']) > 0.5:
        raise ValueError('两份末词结束时间相差超过0.5秒')
    end = min(row[-1]['end'] for row in found)
    if (
        not score_end <= end <= min(score_end + 10, transcripts[0]['duration'])
        or transcripts[0]['duration'] - end >= 0.75 * minimum_verse_span
    ):
        raise ValueError('最后唱词后的剩余录音可能容纳完整乐段')
    return {
        'sourceSuffix': '最美好我爱祂',
        'recognizedSuffix': expected,
        'homophone': '祂/她',
        'words': found,
        'conservativeEnd': end,
        'remaining': transcripts[0]['duration'] - end,
        'completeVerseThreshold': 0.75 * minimum_verse_span,
    }


def score_reading_times(performance):
    one, two = performance['fullMelody']['best'], performance['fullOptical']['best']
    times = []
    for item in performance['local']:
        section = item['sections'][0]
        target = item.get('previousReadingContext', {}).get('targetRow', 0)
        times.append(
            four_path_time(
                [
                    one['starts'][section],
                    two['starts'][section],
                    item['melody']['best']['starts'][target],
                    item['optical']['best']['starts'][target],
                ]
            )
        )
    if len(times) != 8 or any(a + 0.5 >= b for a, b in zip(times, times[1:])):
        raise ValueError('完整乐曲未依次核对八个阅读位置')
    return times
