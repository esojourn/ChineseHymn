"""Conservative reading previews supported by independently decoded full lyric lines."""
import hashlib
import json
import math
from pathlib import Path
import re

from sync_batch_gap_prefix import words
from sync_batch_sources import clean, prepare


def bilingual_lines(lines, prior_region, next_region):
    p, n = prior_region['endLine'], next_region['startLine']
    if (p <= prior_region['startLine'] or n >= next_region['endLine'] or
            not re.search('[\u3400-\u9fff]', lines[p]) or clean(lines[p], 'en') or
            not re.search('[\u3400-\u9fff]', lines[n + 1]) or clean(lines[n + 1], 'en') or
            not clean(lines[p - 1], 'en') or not clean(lines[n], 'en')):
        raise ValueError('双语预读须使用相邻原始英文行及其中文译文行')
    return lines[p - 1], lines[n]


def verify(index, previous_text, current_text, anchors, decoded, bilingual=False):
    """Place a preview after all observed previous-line ends, never claim a sung onset."""
    previous, current = words(previous_text), words(current_text)
    if len(previous) < 4 or len(current) < 4 or len(anchors) != 2 or len(decoded) != 2:
        raise ValueError('预读须有两份完整转写和两组至少四词的原文行')
    if any(not all(type(a[key].get(field)) in (int, float) and math.isfinite(a[key][field])
                   for key, field in (('previous', 'end'), ('previous', 'matchedFraction'),
                                      ('current', 'at'), ('current', 'firstWordEnd'), ('current', 'matchedFraction'))) or
           a['current']['firstWord'] != current[0] or a['current']['firstWordEnd'] < a['current']['at']
           for a in anchors):
        raise ValueError('预读完整转写时间无效或首词不符')
    if any(not .8 <= row['previous']['matchedFraction'] <= 1 or not .8 <= row['current']['matchedFraction'] <= 1 for row in anchors):
        raise ValueError('预读前后区域未被两份完整转写覆盖')
    evidence = []
    for item, before in zip(decoded, (16, 18) if bilingual else (18, 20)):
        bounds = item['bounds']
        if (len(bounds) != 2 or abs(bounds[0] - max(0, anchors[0]['current']['at'] - before)) > .001 or
                abs(bounds[1] - (anchors[0]['current']['at'] + 30 - before)) > .001):
            raise ValueError('预读窗口与固定原始音频窗口不符')
        heard = [word for segment in item['segments'] for word in segment.get('words', []) if clean(word['word'], 'en')]
        for word in heard:
            if (not all(type(word.get(k)) in (int, float) and math.isfinite(word[k]) for k in ('start', 'end', 'probability')) or
                    not bounds[0] <= word['start'] <= word['end'] <= bounds[1] + .02 or not 0 <= word['probability'] <= 1):
                raise ValueError('预读窗口含无效时间或词语概率')
        tokens = [clean(word['word'], 'en') for word in heard]
        def matches(phrase):
            return [(i, heard[i:i + len(phrase)]) for i in range(len(tokens) - len(phrase) + 1)
                    if tokens[i:i + len(phrase)] == phrase]
        left = [(i, selected) for i, selected in matches(previous)
                if all(abs(selected[-1]['end'] - a['previous']['end']) <= (1.5 if bilingual else 1) for a in anchors)]
        right = [(i, selected) for i, selected in matches(current)
                 if all(abs(selected[0]['end'] - a['current']['firstWordEnd']) <= .5 for a in anchors)]
        if len(left) != 1 or len(right) != 1:
            raise ValueError('预读窗口未唯一识别上一行及下一行的完整原文')
        left_index, left_words = left[0]
        right_index, right_words = right[0]
        selected = left_words + right_words
        if (left_index + len(left_words) != right_index or
                any(w['end'] - w['start'] < .04 or w['probability'] < .12 for w in selected) or
                any(a['end'] > b['start'] + .02 for a, b in zip(selected, selected[1:])) or
                left_words[0]['start'] <= bounds[0] + .1 or right_words[-1]['end'] >= bounds[1] - .1):
            raise ValueError('预读两行有缺词、无实际时长、窗口截断或中间存在其他词')
        evidence.append({'bounds': bounds, 'previous': left_words, 'current': right_words})
    if (abs(evidence[0]['previous'][-1]['end'] - evidence[1]['previous'][-1]['end']) > .5 or
            abs(evidence[0]['current'][0]['end'] - evidence[1]['current'][0]['end']) > .5):
        raise ValueError('预读的两个无提示窗口词尾不一致')
    upper = max([a['previous']['end'] for a in anchors] + [e['previous'][-1]['end'] for e in evidence])
    at = round(upper + .5, 2)
    if any(at >= a['current']['firstWordEnd' if bilingual else 'at'] - .5 for a in anchors):
        raise ValueError('上一行结束后没有足够的预读间隔')
    method = ('bilingual reading position after conservative previous-line end; next translated line must already be visible; not a measured sung onset'
              if bilingual else 'reading preview after conservative previous-line end; not a measured next-line onset')
    return {'index': index, 'at': at, 'method': method,
            'previousWords': previous, 'currentWords': current, 'anchors': anchors, 'windows': evidence}


def recover(song, source, definitions, diagnostics, observations, transcript, audio, review, model_dir,
            cache, confirm, performed_order, save):
    from faster_whisper import WhisperModel
    import sync_batch_whole_reading as whole
    if type(review.get('version')) is not int or review['version'] not in (1, 2):
        raise ValueError('预读版本无效')
    bilingual = review['version'] == 2
    synthetic = {'freeWholeReading': {**{k: review[k] for k in ('indices', 'reason', 'model', 'contextModel')}, 'version': 1}}
    whole.validate_review(synthetic, 'en', model_dir)
    if set(review) != {'version', 'indices', 'reason', 'model', 'contextModel', 'independentTranscriptHash'}:
        raise ValueError('预读配置包含未识别字段')
    independent_path = cache / 'asr-independent' / (song['reference'] + '.json')
    if (not independent_path.is_file() or hashlib.sha256(independent_path.read_bytes()).hexdigest() != review['independentTranscriptHash']):
        raise ValueError('预读独立完整转写未固定或已变化')
    independent = json.loads(independent_path.read_text())
    expected_parameters = {'model': review['model']['sha256'], 'tokenizer': review['model']['tokenizerSha256'],
                           'audio': transcript.get('sha256'), 'language': 'en', 'beamSize': 5,
                           'temperature': 0, 'conditionOnPreviousText': False, 'vadFilter': False,
                           'prompt': None, 'wholeAudio': True}
    if (independent.get('sha256') != transcript.get('sha256') or independent.get('language') != 'en' or
            independent.get('parameters') != expected_parameters or transcript.get('language') != 'en' or
            song.get('type') != 'text'):
        raise ValueError('预读独立转写与英文原始录音不符')
    original = [item for verse in source['verses'] for item in verse]
    other_definitions, _, other_diagnostics = performed_order(original, independent['segments'], 'en')
    if other_definitions != definitions or len(other_diagnostics) != len(diagnostics) or review['indices'][-1] >= len(definitions):
        raise ValueError('预读两份转写的完整正文及演唱顺序不一致')
    lines = prepare.read_text(Path(__file__).resolve().parents[1] / 'data' / song['source']).split('\n')
    model = WhisperModel(str(Path(model_dir) / 'large-v3-turbo'), device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
    times, decisions = [0], [{'index': 0, 'at': 0, 'method': 'initial reading position; sung onset not measured'}]
    for index in range(1, len(definitions)):
        anchor = diagnostics[index]
        if index not in review['indices']:
            rows = [{**row, 'index': 0} for row in observations if row['index'] == index]
            at, detail = confirm(rows, [anchor['at']], [(anchor['at'], anchor['firstWordEnd'])], [anchor['firstWord']])
            times.extend(at)
            decisions.extend({**row, 'index': index} for row in detail)
            continue
        decoded = []
        for before in ((16, 18) if bilingual else (18, 20)):
            bounds = [max(0, anchor['at'] - before), anchor['at'] + 30 - before]
            if bounds[1] > len(audio) / 16000:
                raise ValueError('预读窗口超出录音')
            parameters = {'version': 1, 'model': review['model'], 'audio': transcript['sha256'], 'language': 'en',
                          'bounds': bounds, 'beamSize': 5, 'temperature': 0, 'prompt': None,
                          'vadFilter': False, 'conditionOnPreviousText': False}
            signature = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
            path = cache / 'free-preview-windows' / (signature + '.json')
            data = json.loads(path.read_text()) if path.exists() else None
            if not data or data.get('signature') != signature:
                segments, _ = model.transcribe(audio[round(bounds[0] * 16000):round(bounds[1] * 16000)], language='en',
                    beam_size=5, temperature=0, word_timestamps=True, condition_on_previous_text=False, vad_filter=False)
                rows = [{'text': s.text, 'words': [{'word': w.word, 'start': float(w.start + bounds[0]),
                         'end': float(w.end + bounds[0]), 'probability': float(w.probability)} for w in s.words or []]} for s in segments]
                data = {'signature': signature, 'parameters': parameters, 'bounds': bounds, 'segments': rows}
                save(path, data)
            if data.get('parameters') != parameters or data.get('bounds') != bounds:
                raise ValueError('预读缓存与固定模型或原始窗口不符')
            decoded.append(data)
        prior_region = source['regions'][definitions[index - 1]['region']]
        next_region = source['regions'][definitions[index]['region']]
        if bilingual:
            previous, current = bilingual_lines(lines, prior_region, next_region)
        else:
            previous, current = lines[prior_region['endLine']], lines[next_region['startLine']]
        anchors = [{'previous': rows[index - 1], 'current': rows[index]} for rows in (diagnostics, other_diagnostics)]
        proof = verify(index, previous, current, anchors, decoded, bilingual=bilingual)
        times.append(proof['at'])
        decisions.append(proof)
    if any(a + .5 >= b for a, b in zip(times, times[1:])):
        raise ValueError('预读及原有阅读位置未递增')
    return times, decisions, independent
