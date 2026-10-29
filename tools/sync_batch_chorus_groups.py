"""Recover numbered stanzas whose identical choruses are separate source paragraphs."""
import copy
from sync_batch_sources import clean, prepare, strip_verse


def source_chorus_groups(source, path, profile):
    if 'sourceChorusGroups' not in profile:
        return source
    review = profile['sourceChorusGroups']
    verses = source.get('verses', [])
    complete = profile.get('fullPerformanceCheck')
    if (not isinstance(review, dict) or set(review) != {'version', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            source.get('language') != 'zh' or len(verses) < 6 or len(verses) % 2 or
            any(len(verse) != 1 for verse in verses) or
            len(source.get('regions', {})) != len(verses) or
            len({verse[0]['region'] for verse in verses}) != len(verses) or
            not isinstance(complete, dict) or set(complete) != {'version'} or
            type(complete.get('version')) is not int or complete['version'] != 1 or
            'referenceDiscovery' in profile or 'opticalReference' in profile):
        raise ValueError('主副歌分组须绑定至少三节原编号主歌和相同副歌，完整四路径检查必须启用')
    lines = prepare.read_text(path).split('\n')
    bounds, paragraphs = [], []
    previous = -1
    for verse in verses:
        region = source['regions'][verse[0]['region']]
        lo, hi = region.get('startLine'), region.get('endLine')
        if (region.get('kind') != 'text' or type(lo) is not int or type(hi) is not int or
                not previous < lo <= hi < len(lines)):
            raise ValueError('主副歌只能依次引用完整原始文本区域')
        paragraph = [line.strip() for line in lines[lo:hi + 1]]
        if (len(paragraph) < 2 or any(not clean(line) for line in paragraph) or
                ' '.join(strip_verse(line) for line in paragraph) != verse[0]['text']):
            raise ValueError('主副歌分组不能改变原始歌词或跨越非歌词行')
        bounds.append((lo, hi))
        paragraphs.append(paragraph)
        previous = hi
    chorus = paragraphs[1]
    if (len(clean(' '.join(chorus))) < 8 or
            any(paragraphs[i] != chorus for i in range(1, len(paragraphs), 2)) or
            any(paragraphs[i] == chorus for i in range(0, len(paragraphs), 2))):
        raise ValueError('每节副歌须逐行完全相同且与独立主歌不同')
    number_lines = []
    for stanza, index in enumerate(range(0, len(verses), 2), 1):
        before = bounds[index - 1][1] + 1 if index else 0
        preceding = [(i, line.strip()) for i, line in enumerate(lines[before:bounds[index][0]], before)
                     if line.strip()]
        if (not preceding or preceding[-1][1] != str(stanza) or
                index and len(preceding) != 1 or
                any(line.strip() for line in lines[bounds[index][1] + 1:bounds[index + 1][0]])):
            raise ValueError('主副歌分组须从原文连续独立编号1、2、3等自动取得，不能指定分组或时间')
        number_lines.append(preceding[-1][0])
    if any(line.strip() for line in lines[bounds[-1][1] + 1:]):
        raise ValueError('主副歌分组不能遗漏末尾原文内容')
    result = copy.deepcopy(source)
    copied = result['verses']
    result['verses'] = [copied[i] + copied[i + 1] for i in range(0, len(verses), 2)]
    result['chorusGroups'] = {'version': 1, 'sourceVerseCount': len(verses),
        'performedVerseCount': len(result['verses']), 'numberLines': number_lines,
        'originalGroups': [[i, i + 1] for i in range(0, len(verses), 2)],
        'chorusRegions': [verses[i][0]['region'] for i in range(1, len(verses), 2)]}
    return result
