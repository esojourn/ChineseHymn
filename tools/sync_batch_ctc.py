"""Independent, freely decoded MMS prefixes for a clamped initial Whisper word."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unicodedata
import numpy as np

MODEL = {'repository': 'xycld/lyric-align-mms-fa', 'revision': '06e6cbfa5c257d23981c922f6406f5def958eb95',
         'file': 'mms_fa_uint8.onnx', 'sha256': '3949a7957375d802a9f813ef2a26315a44a30cd65da647f9405f82cd9f55d42a',
         'tokenizerSha256': 'e5a791edbc7d1daf221ae37c0b066957643024b28770a9cdb9d0009ef76cc20a'}
_session, _vocabulary = None, None


def letters(text):
    return ''.join(re.findall('[a-z]', unicodedata.normalize('NFKD', text.casefold())))


def decode_prefix(logp, vocabulary, first_word, following_word, bounds):
    if (logp.ndim != 2 or logp.shape[1] != 29 or not np.isfinite(logp).all() or
            logp.shape[0] < 2 or bounds[0] >= bounds[1]):
        raise ValueError('CTC 声学输出或音频窗口无效')
    word, following = letters(first_word), letters(following_word)[:1]
    if len(word) < 3 or not following:
        raise ValueError('独立 CTC 核对需要完整的至少三字母首词及下一词起音')
    prefix = word + following
    labels = {value: key for key, value in vocabulary.items()}
    # MMS_FA appends a constant wildcard column. It is not an acoustic label.
    ids = np.argmax(logp[:, :28], axis=1)
    tokens, index = [], 0
    scale = (bounds[1] - bounds[0]) / len(ids)
    while index < len(ids):
        end = index + 1
        while end < len(ids) and ids[end] == ids[index]:
            end += 1
        token = int(ids[index])
        if token:
            tokens.append({'letter': labels[token], 'start': round(bounds[0] + index * scale, 3),
                           'end': round(bounds[0] + end * scale, 3),
                           'score': round(float(np.exp(logp[index:end, token]).mean()), 4)})
        index = end
    decoded = ''.join(row['letter'] for row in tokens)
    candidates = []
    for index in range(len(tokens) - len(prefix) + 1):
        if decoded[index:index + len(prefix)] != prefix:
            continue
        selected = tokens[index:index + len(prefix)]
        if (min(row['score'] for row in selected) < .2 or
                float(np.mean([row['score'] for row in selected])) < .5):
            continue
        candidates.append({'at': selected[0]['start'], 'wordEnd': selected[len(word) - 1]['end'],
                           'followingStart': selected[len(word)]['start'],
                           'prefix': prefix, 'tokens': selected, 'bounds': list(bounds)})
    return candidates


def emissions(audio, bounds, model_dir, cache, audio_hash):
    global _session, _vocabulary
    signature = hashlib.sha256(json.dumps({'model': MODEL['sha256'], 'audio': audio_hash,
        'bounds': bounds, 'normalization': 'zero-mean/unit-variance/eps-1e-5', 'version': 1}, sort_keys=True).encode()).hexdigest()
    destination = cache / 'ctc-emissions' / (signature + '.npz')
    if destination.exists():
        with np.load(destination) as stored:
            if str(stored['signature']) == signature:
                return stored['logp'], json.loads(str(stored['vocabulary']))
    if _session is None:
        import onnxruntime as ort
        for name, expected in [(MODEL['file'], MODEL['sha256']), ('tokenizer.json', MODEL['tokenizerSha256'])]:
            with (model_dir / name).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('CTC 模型或词表哈希与固定版本不符')
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        _session = ort.InferenceSession(str(model_dir / MODEL['file']), sess_options=options,
                                       providers=['CPUExecutionProvider'])
        _vocabulary = json.loads((model_dir / 'tokenizer.json').read_text())
    segment = audio[round(bounds[0] * 16000):round(bounds[1] * 16000)].astype(np.float32)
    if len(segment) < 1600:
        raise ValueError('首句 CTC 音频窗口不足')
    segment = (segment - segment.mean()) / np.sqrt(segment.var() + 1e-5)
    output = _session.run(None, {'waveform': segment[np.newaxis, :]})[0]
    logp = output[0] if output.ndim == 3 else output
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.npz', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        np.savez_compressed(temporary, logp=logp, vocabulary=json.dumps(_vocabulary), signature=signature)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return logp, _vocabulary


def recover_initial(audio, audio_hash, definitions, estimates, ranges, anchor_words, observations,
                    model_dir, cache, confirm):
    words = re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)?", definitions[0]['text'], re.UNICODE)
    if len(words) < 2 or letters(words[0]) != letters(anchor_words[0]):
        raise ValueError('转写没有确认正文的实际首词')
    edges = [next((row for row in observations if row['index'] == 0 and row['window'] == window), {})
             for window in (0, 1)]
    if any(row.get('valid', True) or row.get('firstWord') != anchor_words[0] or
           row.get('firstWordProbability', 0) < .12 or row.get('positiveDurationFraction', 0) < .65 or
           row.get('meanProbability', 0) < .12 or 'bounds' not in row or
           abs(row.get('at', -10) - row['bounds'][0]) > .1 or
           abs(row.get('firstWordEnd', -10) - ranges[0][1]) > .5 for row in edges):
        raise ValueError('首词并非有转写词尾佐证的双窗口边缘问题')
    later = [{**row, 'index': row['index'] - 1} for row in observations if row['index'] > 0]
    times, decisions = confirm(later, estimates[1:], ranges[1:], anchor_words[1:])
    confirmed = []
    for edge in edges:
        logp, vocabulary = emissions(audio, edge['bounds'], model_dir, cache, audio_hash)
        candidates = [row for row in decode_prefix(logp, vocabulary, words[0], words[1], edge['bounds'])
                      if ranges[0][0] <= row['at'] <= ranges[0][1] and
                      abs(row['wordEnd'] - edge['firstWordEnd']) <= .5 and
                      abs(row['followingStart'] - edge['firstWordEnd']) <= .5 and
                      row['at'] > edge['bounds'][0] + .1 and row['wordEnd'] < edge['bounds'][1] - .1]
        if len(candidates) != 1:
            raise ValueError('独立 CTC 自由解码未能唯一确认首词及下一词前缀')
        confirmed.append(candidates[0])
    if abs(confirmed[0]['at'] - confirmed[1]['at']) > .5:
        raise ValueError('两个 CTC 音频窗口的首词起点不一致')
    at = round(sum(row['at'] for row in confirmed) / 2, 2)
    if any(row['index'] == 0 and row.get('valid') and abs(row['at'] - at) > .5
           for row in observations):
        raise ValueError('其他有效声学窗口与独立 CTC 起点存在冲突')
    if times and at + .5 >= times[0]:
        raise ValueError('CTC 首词起点与后续歌词顺序冲突')
    decisions = [{**row, 'index': row['index'] + 1} for row in decisions]
    decisions.append({'index': 0, 'at': at, 'method': 'independent MMS CTC free-decoded prefix in two windows',
                      'model': MODEL, 'windows': confirmed, 'asrWordRange': ranges[0]})
    return [at] + times, decisions
