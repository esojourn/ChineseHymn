"""Recover a zero-duration verse opening using free speech and a repeated phrase."""
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import re

from sync_batch_sources import clean


def syllables(text):
    from pypinyin import Style, lazy_pinyin
    return lazy_pinyin(clean(text), style=Style.NORMAL)


def hex_value(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def validate_review(profile, source):
    review = profile.get('cyclicFreeOnsets') if profile else None
    if review is None:
        return None
    context = profile.get('extendedContext', {})
    if (not isinstance(review, dict) or set(review) != {
            'version', 'indices', 'transcriptHash', 'pinyinVersion', 'model', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            source.get('language') != 'zh' or review.get('pinyinVersion') != '0.55.0' or
            version('pypinyin') != review['pinyinVersion'] or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            not isinstance(review.get('indices'), list) or not review['indices'] or
            any(type(i) is not int or i <= 0 for i in review['indices']) or
            review['indices'] != sorted(set(review['indices'])) or
            any(i >= sum(map(len, source.get('verses', []))) for i in review['indices']) or
            'sourceCycle' in profile or not isinstance(context, dict) or
            set(context) != {'version'} or type(context.get('version')) is not int or context['version'] != 1 or
            not hex_value(review.get('transcriptHash'), 64)):
        raise ValueError('循环首字复核须绑定中文原文、固定拼音版本、完整转写及明确区域')
    model = review['model']
    if (not isinstance(model, dict) or set(model) != {'repository', 'revision', 'sha256', 'tokenizerSha256'} or
            model.get('repository') != 'Systran/faster-whisper-medium' or
            not hex_value(model.get('revision'), 40) or
            any(not hex_value(model.get(key), 64) for key in ['sha256', 'tokenizerSha256'])):
        raise ValueError('循环首字复核须固定原有 medium 模型及词表')
    return review


def verify_model(directory, review):
    for name, key in [('model.bin', 'sha256'), ('tokenizer.json', 'tokenizerSha256')]:
        with (Path(directory) / 'medium' / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != review['model'][key]:
                raise ValueError('循环首字自由转写模型或词表已变化')


def valid_word(word):
    return (all(type(word.get(key)) in (int, float) and math.isfinite(word[key])
                for key in ['start', 'end', 'probability']) and
            0 <= word['start'] < word['end'] and word['end'] - word['start'] >= .04 and
            .12 <= word['probability'] <= 1)


def prefix_at(words, position, prefix):
    selected = words[position:position + 5]
    if (len(prefix) != 5 or len(selected) != 5 or
            any(len(clean(w.get('word', ''))) != 1 or not valid_word(w) for w in selected) or
            clean(selected[0]['word']) != prefix[0] or
            syllables(''.join(w['word'] for w in selected)) != syllables(prefix) or
            any(a['end'] > b['start'] + .02 for a, b in zip(selected, selected[1:]))):
        return None
    return selected


def anchor_word(words, estimate, character):
    matches = [w for w in words if clean(w.get('word', '')) == character and valid_word(w) and
               abs(w['start'] - estimate) <= 1.5]
    if len(matches) != 1:
        raise ValueError('完整转写未唯一确认当前首字的实际起止范围')
    return matches[0]


def verify(index, source, estimates, music, observations, transcript, decoded, cluster):
    verses = source['verses']
    similarity = music.get('similarity')
    if (len(verses) < 2 or len({len(v) for v in verses}) != 1 or len(verses[0]) < 2 or
            len(estimates) != sum(map(len, verses)) or index % len(verses[0]) or
            not 0 < index < len(estimates) or type(similarity) not in (int, float) or
            not math.isfinite(similarity) or similarity < .9):
        raise ValueError('循环首字须位于后续完整歌段开头，并有独立可靠音乐反复')
    size, verse = len(verses[0]), index // len(verses[0])
    shifts = music.get('shifts', [])
    if (len(shifts) != len(verses) or shifts[0] != 0 or
            any(type(t) not in (int, float) or not math.isfinite(t) for t in shifts) or
            any(a >= b for a, b in zip(shifts, shifts[1:]))):
        raise ValueError('循环首字缺少完整、递增的实测音乐相位')
    prefix = clean(verses[verse][0]['text'])[:5]
    if len(prefix) != 5:
        raise ValueError('循环首句不足五个完整汉字音节')
    words = [w for s in transcript['segments'] for w in s.get('words', []) if clean(w.get('word', ''))]
    current = anchor_word(words, estimates[index], prefix[0])
    original = [w for w in observations if w['index'] == index]
    fixed = [(6, 16), (4, 14), (8, 18), (10, 16)]
    if (len(original) != 4 or {w['window'] for w in original} != {0, 1, 2, 3} or
            any(type(w['window']) is not int or w.get('firstWord') != prefix[0] or
                w.get('bounds') != [round(max(0, estimates[index] - fixed[w['window']][0]), 3),
                                    round(min(transcript['duration'], estimates[index] + fixed[w['window']][1]), 3)] or
                not all(type(w.get(k)) in (int, float) and math.isfinite(w[k])
                        for k in ['firstWordStart', 'firstWordEnd', 'meanProbability', 'positiveDurationFraction']) or
                not 0 <= w['firstWordEnd'] - w['firstWordStart'] < .04 or
                w['meanProbability'] < .12 or w['positiveDurationFraction'] < .65 or
                abs(w['firstWordEnd'] - current['end']) > .5 for w in original)):
        raise ValueError('循环首字只可恢复四个原始窗口共同退化在已知词尾的零时长首字')
    peers = []
    for other in range(verse):
        if clean(verses[other][0]['text'])[:5] != prefix:
            continue
        peer_index = other * size
        candidates = [prefix_at(words, i, prefix) for i, w in enumerate(words)
                      if valid_word(w) and abs(w['start'] - estimates[peer_index]) <= 1.5]
        candidates = [p for p in candidates if p is not None]
        if len(candidates) != 1:
            continue
        anchors = []
        for slot in range(1, size):
            one = cluster([w for w in observations if w['index'] == peer_index + slot])[0]
            two = cluster([w for w in observations if w['index'] == index + slot])[0]
            if one is not None and two is not None and abs(two - one - shifts[verse] + shifts[other]) <= .5:
                anchors.append({'slot': slot, 'peer': one, 'current': two})
        if anchors:
            peers.append({'verse': other, 'words': candidates[0], 'otherReadingAnchors': anchors,
                          'shift': shifts[verse] - shifts[other]})
    if not peers:
        raise ValueError('循环首字缺少前段相同五音节及另一个阅读区域的独立相位印证')
    if len(decoded) != 2:
        raise ValueError('循环首字需要两组无歌词提示的不同原始窗口')
    expected_bounds = [[max(0, estimates[index] - before), min(transcript['duration'], estimates[index] + after)]
                       for before, after in [(6, 16), (4, 14)]]
    proofs = []
    for data, bounds in zip(decoded, expected_bounds):
        if data.get('bounds') != bounds:
            raise ValueError('自由核对必须使用两组原始固定上下文窗口')
        heard = [w for s in data['segments'] for w in s.get('words', []) if clean(w.get('word', ''))]
        if any(not all(type(w.get(k)) in (int, float) and math.isfinite(w[k])
                       for k in ['start', 'end', 'probability']) or
               not bounds[0] <= w['start'] <= w['end'] <= bounds[1] + .02 for w in heard):
            raise ValueError('自由转写词语超出当前音频窗口')
        candidates = [prefix_at(heard, i, prefix) for i in range(len(heard))]
        candidates = [p for p in candidates if p is not None]
        if len(candidates) != 1:
            raise ValueError('自由转写未唯一确认相同的五音节起句及字面首字')
        selected, first = candidates[0], candidates[0][0]
        if first['start'] <= bounds[0] + .1 or selected[-1]['end'] >= bounds[1] - 1:
            raise ValueError('自由起句被取样窗口截断')
        for edge in ['start', 'end']:
            if (abs(first[edge] - current[edge]) > .5 or
                    any(abs(first[edge] - p['words'][0][edge] - p['shift']) > .5 for p in peers)):
                raise ValueError('自由首字起止与完整转写或重复音乐相位不一致')
        proofs.append({'bounds': bounds, 'prefixWords': selected, 'decoded': data})
    if any(abs(proofs[0]['prefixWords'][0][edge] - proofs[1]['prefixWords'][0][edge]) > .5
           for edge in ['start', 'end']):
        raise ValueError('两个自由窗口的首字起点或词尾不一致')
    at = round(sum(p['prefixWords'][0]['start'] for p in proofs) / 2, 2)
    return {'index': index, 'at': at, 'method': 'two free five-syllable verse prefixes, literal first word, whole-ASR word edges and independent cyclic phase',
            'sourcePrefix': prefix, 'syllables': syllables(prefix), 'wholeAsrWord': current,
            'peers': peers, 'windows': proofs, 'originalContextualWindows': original}


def recover(model, audio, song, source, estimates, music, observations, review, model_dir, cache, cluster, save):
    if song.get('type') != 'text':
        raise ValueError('循环首字复核只用于原始中文文字正文')
    verify_model(model_dir, review)
    transcript_path = cache / 'asr' / (song['reference'] + '.json')
    with transcript_path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != review['transcriptHash']:
            raise ValueError('循环首字的完整转写已变化')
    transcript = json.loads(transcript_path.read_text())
    if (transcript.get('model') != 'medium' or transcript.get('language') != 'zh' or
            transcript.get('file') != song['reference'] or not any(
                t['file'] == song['reference'] and t['sha256'] == transcript.get('sha256') for t in song['tracks'])):
        raise ValueError('循环首字必须绑定当前完整录音及原始中文转写')
    results = []
    for index in review['indices']:
        if cluster([w for w in observations if w['index'] == index])[0] is not None:
            continue
        decoded = []
        for before, after in [(6, 16), (4, 14)]:
            bounds = [max(0, estimates[index] - before), min(len(audio) / 16000, estimates[index] + after)]
            parameters = {'version': 1, 'model': review['model'], 'audio': transcript['sha256'], 'language': 'zh',
                          'bounds': bounds, 'beamSize': 5, 'temperature': 0, 'prompt': None,
                          'vadFilter': False, 'conditionOnPreviousText': False}
            signature = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
            path = cache / 'free-cyclic-windows' / (signature + '.json')
            data = json.loads(path.read_text()) if path.exists() else None
            if data is None:
                segments, _ = model.transcribe(audio[round(bounds[0] * 16000):round(bounds[1] * 16000)], language='zh',
                    beam_size=5, temperature=0, word_timestamps=True, condition_on_previous_text=False, vad_filter=False)
                rows = [{'start': float(s.start + bounds[0]), 'end': float(s.end + bounds[0]), 'text': s.text,
                         'words': [{'word': w.word, 'start': float(w.start + bounds[0]), 'end': float(w.end + bounds[0]),
                                    'probability': float(w.probability)} for w in s.words or []]} for s in segments]
                data = {'signature': signature, 'parameters': parameters, 'bounds': bounds, 'segments': rows}
                save(path, data)
            if data.get('signature') != signature or data.get('parameters') != parameters:
                raise ValueError('循环首字自由转写缓存的固定参数不一致')
            decoded.append(data)
        results.append(verify(index, source, estimates, music, observations, transcript, decoded, cluster))
    return results
