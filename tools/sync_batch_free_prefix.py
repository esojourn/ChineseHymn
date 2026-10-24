"""Verify a clamped initial lyric word through two freely decoded audio windows."""
import hashlib
import json
import math
from pathlib import Path
import re
from sync_batch_sources import clean


def initial_prefix(text, language):
    if language == 'zh':
        prefix = clean(text, language)[:10]
        if len(prefix) < 10:
            raise ValueError('自由首句核对需要至少十个连续中文正文字符')
        return prefix
    if language == 'en':
        words = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", text)
        prefix = clean(' '.join(words[:4]), language)
        if len(words) < 4 or len(prefix) < 10:
            raise ValueError('自由首句核对需要至少四个完整英文正文词语')
        return prefix
    raise ValueError('自由首句核对尚不支持当前语言')


def prefix_candidate(decoded, definition, language, edge, estimate):
    prefix = initial_prefix(definition['text'], language)
    heard, locations = '', []
    bounds = decoded['bounds']
    for segment in decoded['segments']:
        for word in segment.get('words', []):
            value = clean(word['word'], language)
            if not value:
                continue
            if (not all(math.isfinite(word[key]) for key in ['start', 'end', 'probability']) or
                    not bounds[0] <= word['start'] <= word['end'] <= bounds[1] + .02):
                raise ValueError('自由转写词语超出实际音频窗口')
            heard += value
            locations.extend([word] * len(value))
    positions = [index for index in range(len(heard)) if heard.startswith(prefix, index)]
    if len(positions) != 1:
        raise ValueError('自由转写没有唯一确认正文的完整起句')
    selected = locations[positions[0]:positions[0] + len(prefix)]
    first = selected[0]
    if (clean(first['word'], language) != edge['firstWord'] or
            first['end'] - first['start'] < .04 or first['probability'] < .12 or
            first['start'] <= bounds[0] + .1 or first['end'] >= bounds[1] - 1 or
            abs(first['start'] - estimate) > 1.5 or abs(first['end'] - edge['firstWordEnd']) > .5 or
            sum(word['end'] - word['start'] >= .04 for word in selected) / len(selected) < .65 or
            sum(word['probability'] for word in selected) / len(selected) < .12):
        raise ValueError('自由起句与原有声学首词或转写起点不一致')
    return {'at': first['start'], 'wordEnd': first['end'], 'prefix': prefix,
            'bounds': bounds, 'firstWord': first, 'decoded': decoded}


def recover_initial(model, audio, audio_hash, definitions, estimates, ranges, anchor_words, observations,
                    language, review, model_dir, cache, confirm, save):
    if not isinstance(review, dict) or type(review.get('version')) is not int or review['version'] != 1:
        raise ValueError('自由首句核对需要显式版本配置')
    model_info = review['model']
    for name, expected in [('model.bin', model_info['sha256']), ('tokenizer.json', model_info['tokenizerSha256'])]:
        with (Path(model_dir) / 'medium' / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('自由首句核对的模型或词表已变化')
    edges = [next((row for row in observations if row['index'] == 0 and row['window'] == window), {})
             for window in (0, 1)]
    for edge in edges:
        if (edge.get('valid', True) or 'bounds' not in edge or not edge.get('firstWord') or
                not clean(definitions[0]['text'], language).startswith(edge['firstWord']) or
                edge.get('firstWordProbability', 0) < .12 or edge.get('positiveDurationFraction', 0) < .65 or
                edge.get('meanProbability', 0) < .12 or abs(edge.get('at', -10) - edge['bounds'][0]) > .1):
            raise ValueError('首句不是可由自由转写复核的双窗口边缘问题')
    later = [{**row, 'index': row['index'] - 1} for row in observations if row['index'] > 0]
    times, decisions = confirm(later, estimates[1:], ranges[1:], anchor_words[1:])
    candidates = []
    for edge in edges:
        bounds = edge['bounds']
        parameters = {'version': 1, 'model': model_info, 'audio': audio_hash, 'language': language,
                      'bounds': bounds, 'beamSize': 5, 'temperature': 0, 'prompt': None, 'vadFilter': False,
                      'conditionOnPreviousText': False}
        signature = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
        destination = cache / 'free-initial-windows' / (signature + '.json')
        decoded = json.loads(destination.read_text()) if destination.exists() else None
        if not decoded or decoded.get('signature') != signature:
            segments, _ = model.transcribe(audio[round(bounds[0] * 16000):round(bounds[1] * 16000)], language=language,
                beam_size=5, temperature=0, word_timestamps=True, condition_on_previous_text=False, vad_filter=False)
            rows = [{'start': segment.start + bounds[0], 'end': segment.end + bounds[0], 'text': segment.text,
                     'words': [{'word': word.word, 'start': word.start + bounds[0], 'end': word.end + bounds[0],
                                'probability': word.probability} for word in segment.words or []]} for segment in segments]
            decoded = {'signature': signature, 'parameters': parameters, 'bounds': bounds, 'segments': rows}
            save(destination, decoded)
        candidates.append(prefix_candidate(decoded, definitions[0], language, edge, estimates[0]))
    if (edges[0]['bounds'] == edges[1]['bounds'] or abs(candidates[0]['at'] - candidates[1]['at']) > .5 or
            abs(candidates[0]['wordEnd'] - candidates[1]['wordEnd']) > .5):
        raise ValueError('两组自由转写首词起点或词尾不一致')
    at = round(sum(row['at'] for row in candidates) / 2, 2)
    if (times and at + .5 >= times[0] or any(row['index'] == 0 and row.get('valid') and abs(row['at'] - at) > .5
                                          for row in observations)):
        raise ValueError('自由首句与其他声学窗口或后续歌词顺序冲突')
    decisions = [{**row, 'index': row['index'] + 1} for row in decisions]
    decisions.append({'index': 0, 'at': at, 'method': 'two freely decoded windows exactly confirm complete source opening and clamped canonical word endings',
                      'model': model_info, 'windows': candidates, 'asrWordRange': ranges[0]})
    return [at] + times, decisions
