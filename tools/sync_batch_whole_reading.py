"""Resolve a lost first word using a complete freely decoded English reading."""
import hashlib
import json
import math
from pathlib import Path
import re

from sync_batch_gap_prefix import words
from sync_batch_sources import clean


def validate_review(profile, language, model_dir):
    review = profile.get('freeWholeReading') if profile else None
    if review is None:
        return None
    if (not isinstance(review, dict) or set(review) != {'version', 'indices', 'reason', 'model', 'contextModel'} or
            type(review['version']) is not int or review['version'] != 1 or language != 'en' or
            not isinstance(review['reason'], str) or not review['reason'].strip() or
            not isinstance(review['indices'], list) or not review['indices'] or
            any(type(i) is not int or i <= 0 for i in review['indices']) or
            review['indices'] != sorted(set(review['indices'])) or 'longGapFreePrefix' in profile):
        raise ValueError('完整自由起句须明确英文区域、版本、原因及固定的两个模型')
    for key, directory, repository in [
            ('model', 'large-v3-turbo', 'dropbox-dash/faster-whisper-large-v3-turbo'),
            ('contextModel', 'medium', 'Systran/faster-whisper-medium')]:
        model = review[key]
        if (not isinstance(model, dict) or set(model) != {'repository', 'revision', 'sha256', 'tokenizerSha256'} or
                model.get('repository') != repository or not isinstance(model.get('revision'), str) or
                not re.fullmatch('[0-9a-f]{40}', model['revision']) or
                any(not isinstance(model.get(k), str) or not re.fullmatch('[0-9a-f]{64}', model[k])
                    for k in ['sha256', 'tokenizerSha256'])):
            raise ValueError('完整自由起句须固定原有上下文模型和无提示解码模型')
        for file, field in [('model.bin', 'sha256'), ('tokenizer.json', 'tokenizerSha256')]:
            with (Path(model_dir) / directory / file).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != model[field]:
                    raise ValueError('完整自由起句的模型或词表已变化')
    return review


def complete_candidate(decoded, definition, anchor):
    expected = words(definition['text'])
    if len(expected) < 8 or len(''.join(expected)) < 30 or expected[0] != anchor['firstWord']:
        raise ValueError('完整自由起句需要同一原文区域的至少八个完整词和三十个字母')
    heard = [word for segment in decoded['segments'] for word in segment.get('words', []) if clean(word['word'], 'en')]
    bounds = decoded['bounds']
    for word in heard:
        if (not all(type(word.get(k)) in (int, float) and math.isfinite(word[k]) for k in ['start', 'end', 'probability']) or
                not bounds[0] <= word['start'] <= word['end'] <= bounds[1] + .02 or not 0 <= word['probability'] <= 1):
            raise ValueError('完整自由起句词语超出原始窗口或含无效数值')
    tokens = [clean(word['word'], 'en') for word in heard]
    positions = [i for i in range(len(tokens) - len(expected) + 1) if tokens[i:i + len(expected)] == expected]
    if len(positions) != 1:
        raise ValueError('无提示解码未唯一确认当前区域的每一个完整原文词语')
    selected = heard[positions[0]:positions[0] + len(expected)]
    first, last = selected[0], selected[-1]
    if (any(w['end'] - w['start'] < .04 or w['probability'] < .12 for w in selected) or
            any(a['end'] > b['start'] + .02 for a, b in zip(selected, selected[1:])) or
            first['start'] <= bounds[0] + .1 or last['end'] >= bounds[1] - 1 or
            abs(first['start'] - anchor['at']) > .5 or abs(first['end'] - anchor['firstWordEnd']) > .5):
        raise ValueError('完整自由起句的逐词时长、似然或原始转写首词范围不一致')
    return {'at': first['start'], 'wordEnd': first['end'], 'sourceWords': expected,
            'words': selected, 'bounds': bounds, 'decoded': decoded}


def verify(index, definition, anchor, observations, decoded):
    if (not all(type(anchor.get(k)) in (int, float) and math.isfinite(anchor[k])
                for k in ['at', 'firstWordEnd']) or anchor['firstWordEnd'] - anchor['at'] < .04):
        raise ValueError('完整原始转写须具有实际首词起止范围')
    original = [row for row in observations if row['index'] == index]
    fixed = [(6, 16), (4, 14), (8, 18), (10, 16)]
    if (len(original) != 4 or {row['window'] for row in original} != {0, 1, 2, 3} or
            any(type(row['window']) is not int or row.get('firstWord') != anchor['firstWord'] or
                not all(type(row.get(k)) in (int, float) and math.isfinite(row[k]) for k in
                    ['at', 'firstWordStart', 'firstWordEnd', 'firstWordProbability', 'meanProbability', 'positiveDurationFraction']) or
                row['at'] != row['firstWordStart'] or row['firstWordEnd'] < row['firstWordStart'] or
                not 0 <= row['firstWordProbability'] <= 1 or row['positiveDurationFraction'] > 1 or
                row['meanProbability'] > 1 or
                row.get('meanProbability', 0) < .12 or row.get('positiveDurationFraction', 0) < .65 or
                row.get('bounds') != [round(max(0, anchor['at'] - fixed[row['window']][0]), 3),
                                      round(anchor['at'] + fixed[row['window']][1], 3)] or
                not row['bounds'][0] <= row['firstWordStart'] <= row['firstWordEnd'] <= row['bounds'][1] + .02
                for row in original)):
        raise ValueError('完整自由起句须保留四组原始固定上下文窗口及相同首词')
    canonical = [row for row in original if row['window'] in (0, 1)]
    if any(not 0 <= row['firstWordEnd'] - row['firstWordStart'] < .04 or
           abs(row['firstWordEnd'] - anchor['firstWordEnd']) > .5 for row in canonical):
        raise ValueError('只有两组原始窗口都退化在已知词尾的首词才可使用完整自由起句')
    if len(decoded) != 2 or any(data['bounds'] != [max(0, anchor['at'] - before), anchor['at'] + after]
                               for data, (before, after) in zip(decoded, fixed[:2])):
        raise ValueError('完整自由起句须复用两个不同的原始固定窗口')
    candidates = [complete_candidate(data, definition, anchor) for data in decoded]
    if (abs(candidates[0]['at'] - candidates[1]['at']) > .5 or
            abs(candidates[0]['wordEnd'] - candidates[1]['wordEnd']) > .5):
        raise ValueError('两个完整自由起句的首词起点或词尾不一致')
    at = round(sum(row['at'] for row in candidates) / 2, 2)
    end = sum(row['wordEnd'] for row in candidates) / 2
    witnesses, excluded = [], []
    for row in original:
        if (not row.get('valid') or row['firstWordEnd'] - row['firstWordStart'] < .04 or
                row.get('firstWordProbability', 0) < .12):
            continue
        if abs(row['at'] - anchor['at']) <= 1.5:
            if abs(row['at'] - at) > .5 or abs(row['firstWordEnd'] - end) > .5:
                raise ValueError('完整自由起句与原转写附近的有效上下文首词冲突')
            witnesses.append(row)
        elif (row['at'] < anchor['at'] - 1.5 and row['firstWordEnd'] >= anchor['at'] and
              abs(row['firstWordEnd'] - anchor['firstWordEnd']) <= .5):
            excluded.append({'window': row['window'], 'at': row['at'], 'wordEnd': row['firstWordEnd'],
                'reason': 'held-word candidate starts outside the strict full-transcript onset bound; two complete free readings and an in-range contextual word agree on the shorter word'})
        else:
            raise ValueError('补充上下文含不能由原始首词范围解释的有效冲突')
    if not witnesses:
        raise ValueError('完整自由起句另须原始 medium 的有效首词起点及词尾印证')
    return {'index': index, 'at': at, 'method': 'two complete prompt-free source readings plus in-range original contextual word edges',
            'asrWordRange': [anchor['at'], anchor['firstWordEnd']], 'windows': candidates,
            'contextWitnesses': witnesses, 'excludedHeldWordCandidates': excluded}


def recover(song, definitions, diagnostics, observations, transcript, audio, review, model_dir, cache, confirm, save):
    indices = review['indices']
    if (transcript.get('language') != 'en' or song.get('type') != 'text' or
            len(definitions) != len(diagnostics) or indices[-1] >= len(definitions) or
            not any(t['file'] == song['reference'] and t['sha256'] == transcript.get('sha256') for t in song['tracks'])):
        raise ValueError('完整自由起句的录音、英文全文或完整顺序不符')
    from faster_whisper import WhisperModel
    model = WhisperModel(str(Path(model_dir) / 'large-v3-turbo'), device='cpu', compute_type='int8',
                         cpu_threads=2, local_files_only=True)
    times = [0]
    decisions = [{'index': 0, 'at': 0, 'method': 'initial reading position; sung onset not measured'}]
    for index, anchor in enumerate(diagnostics[1:], 1):
        if index not in indices:
            current = [{**row, 'index': 0} for row in observations if row['index'] == index]
            at, detail = confirm(current, [anchor['at']], [(anchor['at'], anchor['firstWordEnd'])], [anchor['firstWord']])
            times.extend(at)
            decisions.extend({**row, 'index': index} for row in detail)
            continue
        decoded = []
        for before, after in [(6, 16), (4, 14)]:
            bounds = [max(0, anchor['at'] - before), anchor['at'] + after]
            if bounds[1] > len(audio) / 16000:
                raise ValueError('完整自由起句的固定窗口超出完整录音')
            parameters = {'version': 1, 'model': review['model'], 'audio': transcript['sha256'], 'language': 'en',
                          'bounds': bounds, 'beamSize': 5, 'temperature': 0, 'prompt': None,
                          'vadFilter': False, 'conditionOnPreviousText': False}
            signature = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
            path = cache / 'free-whole-readings' / (signature + '.json')
            data = json.loads(path.read_text()) if path.exists() else None
            if not data or data.get('signature') != signature:
                segments, _ = model.transcribe(audio[round(bounds[0] * 16000):round(bounds[1] * 16000)], language='en',
                    beam_size=5, temperature=0, word_timestamps=True, condition_on_previous_text=False, vad_filter=False)
                rows = [{'start': float(s.start + bounds[0]), 'end': float(s.end + bounds[0]), 'text': s.text,
                         'words': [{'word': w.word, 'start': float(w.start + bounds[0]), 'end': float(w.end + bounds[0]),
                                    'probability': float(w.probability)} for w in s.words or []]} for s in segments]
                data = {'signature': signature, 'parameters': parameters, 'bounds': bounds, 'segments': rows}
                save(path, data)
            if data.get('parameters') != parameters or data.get('bounds') != bounds:
                raise ValueError('完整自由起句缓存与固定模型或原始窗口不符')
            decoded.append(data)
        proof = verify(index, definitions[index], anchor, observations, decoded)
        proof.update(model=review['model'], contextModel=review['contextModel'])
        times.append(proof['at'])
        decisions.append(proof)
    if any(a + .5 >= b for a, b in zip(times, times[1:])):
        raise ValueError('完整自由起句核对后的阅读位置未递增')
    return times, decisions
