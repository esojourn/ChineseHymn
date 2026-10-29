"""Freely decode a long-gap opening when contextual alignment loses its first word."""
import hashlib
import json
import math
from pathlib import Path
import re
from sync_batch_sources import clean


def words(text):
    return [clean(word, 'en') for word in re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", text)]


def reviewed_prefix(text, review):
    prefix = words(text)[:10]
    if len(prefix) != 10 or len(''.join(prefix)) < 35:
        raise ValueError('长间奏自由核对需要十个完整正文词且至少35个字母')
    variants = review.get('inflections', [])
    if not isinstance(variants, list) or len(variants) > 1:
        raise ValueError('自由起句只允许显式复核的一处过去式词形差异')
    for item in variants:
        if not isinstance(item, dict) or set(item) != {'source', 'decoded'}:
            raise ValueError('词形复核配置无效')
        original, decoded = item['source'], item['decoded']
        if (not isinstance(original, str) or not re.fullmatch('[a-z]{3,}', original) or
                decoded != original + ('d' if original.endswith('e') else 'ed') or
                prefix[0] == original or prefix.count(original) != 1):
            raise ValueError('复核只能保留同一词干，且不能替换首词或未在起句出现的词')
        prefix[prefix.index(original)] = decoded
    return prefix


def prefix_candidate(decoded, definition, review, anchor):
    prefix = reviewed_prefix(definition['text'], review)
    bounds = decoded['bounds']
    heard = [word for segment in decoded['segments'] for word in segment.get('words', [])
             if clean(word['word'], 'en')]
    for word in heard:
        if (not all(math.isfinite(word[key]) for key in ['start', 'end', 'probability']) or
                not bounds[0] <= word['start'] <= word['end'] <= bounds[1] + .02):
            raise ValueError('自由转写词语超出音频窗口')
    tokens = [clean(word['word'], 'en') for word in heard]
    positions = [i for i in range(len(tokens) - len(prefix) + 1) if tokens[i:i + len(prefix)] == prefix]
    if len(positions) != 1:
        raise ValueError('自由转写未唯一确认完整十词起句及已复核词形')
    selected = heard[positions[0]:positions[0] + len(prefix)]
    first = selected[0]
    if (clean(first['word'], 'en') != anchor['firstWord'] or
            first['end'] - first['start'] < .04 or first['probability'] < .12 or
            first['start'] <= bounds[0] + .1 or selected[-1]['end'] >= bounds[1] - 1 or
            abs(first['start'] - anchor['at']) > 1.5 or abs(first['end'] - anchor['firstWordEnd']) > .5 or
            sum(word['end'] - word['start'] >= .04 for word in selected) / len(selected) < .65 or
            sum(word['probability'] for word in selected) / len(selected) < .12):
        raise ValueError('十词自由起句的首词、词尾、时长或似然未通过原有核对范围')
    return {'at': first['start'], 'wordEnd': first['end'], 'prefix': prefix,
            'sourcePrefix': words(definition['text'])[:10], 'bounds': bounds,
            'firstWord': first, 'decoded': decoded}


def gap_edges(index, definitions, diagnostics, observations, segments):
    anchor = diagnostics[index]
    prefix = words(definitions[index]['text'])
    edges = [next((row for row in observations if row['index'] == index and row['window'] == window), {})
             for window in (0, 1)]
    if (index < 1 or not prefix or prefix[0] != anchor['firstWord'] or
            any('bounds' not in row or row.get('firstWord') != anchor['firstWord'] or
                row.get('positiveDurationFraction', 0) < .65 or row.get('meanProbability', 0) < .12 or
                not (row.get('firstWordEnd', 0) - row.get('firstWordStart', 0) < .04 or
                     not row.get('valid', True) and abs(row.get('at', -10) - row['bounds'][0]) <= .1)
                for row in edges)):
        raise ValueError('长间奏复核需要两组首词退化或被窗口边缘截断的原始声学证据')
    previous_end = diagnostics[index - 1]['end']
    if previous_end + .1 >= min(row['bounds'][0] for row in edges):
        raise ValueError('前句尚在声学窗口内，不能按长间奏处理')
    if any(word['end'] > previous_end + .01 and word['start'] < anchor['at'] - .01
           for segment in segments for word in segment.get('words', []) if clean(word['word'], 'en')):
        raise ValueError('转写在两句间存在其他词语，不能确认无词间奏')
    if edges[0]['bounds'] == edges[1]['bounds']:
        raise ValueError('长间奏起句需要两个不同的原始窗口')
    return edges, previous_end


def recover(model, audio, song, definitions, diagnostics, observations, transcript, review,
            model_dir, cache, confirm, save):
    if (not isinstance(review, dict) or set(review) != {'version', 'reason', 'model', 'inflections'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            transcript.get('language') != 'en'):
        raise ValueError('长间奏自由起句复核需要显式版本、原因及固定模型')
    if (len(definitions) != len(diagnostics) or len(definitions) < 2 or song.get('type') != 'text' or
            not any(track['file'] == song['reference'] and track['sha256'] == transcript.get('sha256')
                    for track in song['tracks'])):
        raise ValueError('长间奏复核的完整顺序或音频绑定不符')
    model_info = review['model']
    if not isinstance(model_info, dict) or set(model_info) != {'repository', 'revision', 'sha256', 'tokenizerSha256'}:
        raise ValueError('长间奏自由转写模型信息不完整')
    for name, expected in [('model.bin', model_info['sha256']), ('tokenizer.json', model_info['tokenizerSha256'])]:
        with (Path(model_dir) / 'medium' / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('长间奏自由转写模型或词表与固定版本不符')
    times, decisions = [0], [{'index': 0, 'at': 0, 'method': 'initial reading position; sung onset not measured'}]
    for index, anchor in enumerate(diagnostics[1:], 1):
        current = [{**row, 'index': 0} for row in observations if row['index'] == index]
        try:
            at, detail = confirm(current, [anchor['at']], [(anchor['at'], anchor['firstWordEnd'])], [anchor['firstWord']])
        except ValueError:
            edges, previous_end = gap_edges(index, definitions, diagnostics, observations, transcript['segments'])
            reviewed_prefix(definitions[index]['text'], review)
            candidates = []
            for edge in edges:
                bounds = edge['bounds']
                parameters = {'version': 1, 'model': model_info, 'audio': transcript['sha256'], 'language': 'en',
                              'bounds': bounds, 'beamSize': 5, 'temperature': 0, 'prompt': None, 'vadFilter': False,
                              'conditionOnPreviousText': False}
                signature = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
                destination = cache / 'free-gap-windows' / (signature + '.json')
                decoded = json.loads(destination.read_text()) if destination.exists() else None
                if not decoded or decoded.get('signature') != signature:
                    segments, _ = model.transcribe(audio[round(bounds[0] * 16000):round(bounds[1] * 16000)], language='en',
                        beam_size=5, temperature=0, word_timestamps=True, condition_on_previous_text=False, vad_filter=False)
                    rows = [{'start': segment.start + bounds[0], 'end': segment.end + bounds[0], 'text': segment.text,
                             'words': [{'word': word.word, 'start': word.start + bounds[0], 'end': word.end + bounds[0],
                                        'probability': word.probability} for word in segment.words or []]} for segment in segments]
                    decoded = {'signature': signature, 'parameters': parameters, 'bounds': bounds, 'segments': rows}
                    save(destination, decoded)
                if decoded.get('parameters') != parameters or decoded.get('bounds') != bounds:
                    raise ValueError('长间奏自由转写缓存的参数或窗口不一致')
                candidates.append(prefix_candidate(decoded, definitions[index], review, anchor))
            if (abs(candidates[0]['at'] - candidates[1]['at']) > .5 or
                    abs(candidates[0]['wordEnd'] - candidates[1]['wordEnd']) > .5):
                raise ValueError('长间奏起句在两个自由窗口中的首词起点或词尾不一致')
            value = round(sum(row['at'] for row in candidates) / 2, 2)
            if any(row.get('valid') and row.get('firstWordEnd', 0) - row.get('firstWordStart', 0) >= .04 and
                   abs(row['at'] - value) > .5 for row in current):
                raise ValueError('长间奏自由起句与其他有效声学窗口冲突')
            at = [value]
            detail = [{'index': 0, 'at': value, 'method': 'two freely decoded ten-word prefixes after transcript-verified long gap',
                       'model': model_info, 'windows': candidates, 'asrWordRange': [anchor['at'], anchor['firstWordEnd']],
                       'previousLyricEnd': previous_end, 'reviewedInflections': review['inflections']}]
        times.extend(at)
        decisions.extend({**row, 'index': index} for row in detail)
    if any(a + .5 >= b for a, b in zip(times, times[1:])):
        raise ValueError('长间奏复核后歌词换句顺序未递增')
    return times, decisions
