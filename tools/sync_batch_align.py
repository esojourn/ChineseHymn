"""Known-lyric contextual alignment with two independent windows and musical checks."""
import json
from pathlib import Path
import time
import numpy as np
from sync_batch_sources import clean, image_source, text_source
from sync_batch_audio import map_recordings, repeated_verses

_model = None


def context(text, language, tail=False):
    if language == 'en':
        words = text.split()
        return ' '.join(words[-4:] if tail else words[:10])
    value = clean(text, language)
    return value[-6:] if tail else value[:16]


def align_windows(model, tokenizer, audio, definitions, estimates, language):
    observations = []
    from faster_whisper.audio import pad_or_trim
    duration = len(audio) / 16000
    for first in range(0, len(definitions), 4):
        features, frame_counts, tokens, items = [], [], [], []
        for index in range(first, min(first + 4, len(definitions))):
            preceding = context(definitions[index - 1]['text'], language, True) if index else ''
            current = context(definitions[index]['text'], language)
            text = preceding + (' ' if language == 'en' else '') + current
            for window, (before, after) in enumerate([(6, 13), (4, 10)]):
                start, end = max(0, estimates[index] - before), min(duration, estimates[index] + after)
                if end <= start:
                    continue
                samples = audio[round(start * 16000):round(end * 16000)]
                mel = model.feature_extractor(samples)
                frame_counts.append(mel.shape[-1])
                features.append(pad_or_trim(mel))
                tokens.append(tokenizer.encode(text))
                items.append((index, window, start, end, preceding, current))
        if not features:
            continue
        alignments = model.find_alignment(tokenizer, tokens, model.encode(np.stack(features)), frame_counts)
        for words, (index, window, start, end, preceding, current) in zip(alignments, items):
            characters = []
            for word in words:
                word = {key: value for key, value in word.items() if key != 'tokens'}
                word['start'] = round(float(word['start']) + start, 3)
                word['end'] = round(float(word['end']) + start, 3)
                word['probability'] = round(float(word['probability']), 4)
                characters.extend([word] * len(clean(word['word'], language)))
            offset = len(clean(preceding, language))
            expected_count = offset + len(clean(current, language))
            if len(characters) != expected_count or offset >= len(characters):
                observations.append({'index': index, 'window': window, 'valid': False, 'reason': '字符对齐不完整'})
                continue
            phrase = characters[offset:]
            positive = sum(word['end'] - word['start'] >= .04 for word in phrase) / len(phrase)
            probability = float(np.mean([word['probability'] for word in phrase]))
            at = phrase[0]['start']
            valid = positive >= .65 and probability >= .12 and at > start + .1 and at < end - 1
            observations.append({'index': index, 'window': window, 'at': at, 'valid': valid,
                                 'positiveDurationFraction': round(positive, 3), 'meanProbability': round(probability, 4),
                                 'bounds': [round(start, 3), round(end, 3)], 'words': words})
    return observations


def generate(job):
    global _model
    song, cache_name, model_dir, threads, method = job
    import importlib.util
    spec = importlib.util.spec_from_file_location('batch_io', Path(__file__).with_name('batch-sync.py'))
    batch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(batch)
    root, cache = batch.ROOT, Path(cache_name)
    evidence_path = cache / ('score-alignment' if method == 'score' else 'alignment') / (song['source'] + '.json')
    if evidence_path.exists():
        old = json.loads(evidence_path.read_text())
        valid_algorithm = method != 'score' or old.get('scoreMatching', {}).get('algorithmVersion') == 2
        if valid_algorithm and old.get('sourceHash') == song['sha256'] and old.get('trackHashes') == song['tracks']:
            return song['songId'], old['status'] + ' (cached)'
    start_time = time.monotonic()
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'], 'trackHashes': song['tracks']}
    try:
        reference = root / 'data' / song['reference']
        if method == 'score':
            from sync_batch_score import score_times
            recognition = json.loads((cache / 'ocr' / (song['source'] + '.json')).read_text())
            source, cue_times, score_evidence = score_times(root / 'data' / song['source'], reference, recognition, cache)
            evidence['scoreMatching'] = score_evidence
            definitions = [item for verse in source['verses'] for item in verse]
            first_indices = list(range(0, len(definitions), len(source['systems'])))
        else:
            source, definitions, cue_times, first_indices = vocal_times(song, root, cache, model_dir, threads, evidence)
        return export_result(song, root, cache, evidence_path, evidence, start_time, source, definitions, cue_times, first_indices)
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence['status'] = 'needs-calibration'
        evidence['reason'] = str(error)
    evidence['elapsedSeconds'] = round(time.monotonic() - start_time, 3)
    batch.save(evidence_path, evidence)
    return song['songId'], evidence['status'] + (': ' + evidence['reason'] if 'reason' in evidence else '')


def vocal_times(song, root, cache, model_dir, threads, evidence):
    global _model
    reference = root / 'data' / song['reference']
    if song['type'] == 'image':
        ocr = json.loads((cache / 'ocr' / (song['source'] + '.json')).read_text())
        source = image_source(ocr)
    else:
        source = text_source(root / 'data' / song['source'])
    verses = source['verses']
    if not verses or any(not verse for verse in verses):
        raise ValueError('歌词段落不完整')
    music = repeated_verses(reference, len(verses), cache)
    evidence['music'] = music
    definitions, estimates = [], []
    for verse, shift in zip(verses, music['shifts']):
        lengths = [len(clean(item['text'], source['language'])) for item in verse]
        if not all(lengths):
            raise ValueError('当前语言缺少歌词')
        for item, weight in zip(verse, np.cumsum([0] + lengths[:-1]) / sum(lengths)):
            definitions.append(item)
            # Estimates only select audio windows. Published times come from acoustic alignment.
            estimates.append(music['initialOnsetEstimate'] + shift + music['period'] * float(weight))
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel('medium', device='cpu', compute_type='int8', cpu_threads=threads,
                              download_root=model_dir, local_files_only=True)
    from faster_whisper.audio import decode_audio
    from faster_whisper.tokenizer import Tokenizer
    language = source['language']
    tokenizer = Tokenizer(_model.hf_tokenizer, _model.model.is_multilingual, task='transcribe', language=language)
    audio = decode_audio(str(reference), sampling_rate=16000)
    observations = align_windows(_model, tokenizer, audio, definitions, estimates, language)
    evidence['windows'] = observations
    evidence['estimates'] = estimates
    accepted, failed = {}, []
    for index in range(len(definitions)):
        pair = [row for row in observations if row['index'] == index]
        if len(pair) == 2 and all(row['valid'] for row in pair) and abs(pair[0]['at'] - pair[1]['at']) <= .5:
            accepted[index] = round((pair[0]['at'] + pair[1]['at']) / 2, 2)
        elif index:
            failed.append(index)
    if failed:
        raise ValueError('两个窗口未能共同确认时间点：' + ','.join(map(str, failed)))
    first_indices, cursor = [], 0
    for verse in verses:
        first_indices.append(cursor)
        cursor += len(verse)
    phases = [accepted[index] - shift for index, shift in zip(first_indices[1:], music['shifts'][1:]) if index in accepted]
    if not phases or max(phases) - min(phases) > 1:
        raise ValueError('各段起点与重复音乐位置不一致')
    accepted[0] = round(float(np.median(phases)), 2)
    cue_times = [accepted[index] for index in range(len(definitions))]
    if cue_times[0] < 0 or any(a + .5 >= b for a, b in zip(cue_times, cue_times[1:])):
        raise ValueError('候选时间点不递增')
    return source, definitions, cue_times, first_indices


def export_result(song, root, cache, evidence_path, evidence, start_time, source, definitions, cue_times, first_indices):
    import importlib.util
    spec = importlib.util.spec_from_file_location('batch_export_io', Path(__file__).with_name('batch-sync.py'))
    batch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(batch)
    reference = root / 'data' / song['reference']
    cues = []
    for index, (at, item) in enumerate(zip(cue_times, definitions)):
        cue = {'at': at, 'region': item['region'], 'label': item['label'], 'transition': 'reading'}
        if not index:
            cue.update(moveStart=0, transition='intro')
        elif song['type'] == 'image' and index in first_indices:
            cue['transition'] = 'return'
        cues.append(cue)
    from mutagen.mp3 import MP3
    tracks = []
    for item in song['tracks']:
        file = root / 'data' / item['file']
        track = {**item, 'duration': MP3(file).info.length, 'offset': 0, 'reviewed': False}
        if file == reference:
            track['reviewed'] = True
        else:
            try:
                mapped, metrics = map_recordings(reference, file, cue_times, cache)
                track['cues'] = [{**cue, 'at': at} for cue, at in zip(cues, mapped)]
                evidence.setdefault('recordingMaps', {})[item['file']] = metrics
                track['reviewed'] = True
            except ValueError as error:
                track['cues'] = []
                evidence.setdefault('trackExceptions', {})[item['file']] = str(error)
        tracks.append(track)
    data = {'version': 1, 'songId': song['songId'], 'status': 'reviewed',
            'source': {'file': song['source'], 'sha256': song['sha256'], 'type': song['type']},
            'tracks': tracks, 'regions': source['regions'], 'cues': cues,
            'activation': {'policy': 'automatic', 'listeningReviewRequired': False},
            'provenance': {'batch': 'bulk-2026-10', 'method': 'Numbered-score melody/chroma matching with two rhythm templates and measured repeats.' if 'scoreMatching' in evidence else 'Known lyrics; two contextual medium/int8 acoustic alignments; measured repeated-music checks; independent chroma-DTW recording correspondence.',
                           'performanceOrder': definitions, 'evidence': 'docs/sync-bulk-evidence/' + song['source'] + ('.score.json' if 'scoreMatching' in evidence else '.json'),
                           'review': 'Machine checks passed. Full human listening review and measured lyric-boundary error are not claimed.'}}
    if song['type'] == 'image':
        # These regions focus the performed lyric row, rather than all printed
        # stanzas of a staff; reveal its entire next region so glyphs are not cut.
        data['imageLayout'] = {'mode': 'center-preview', 'previewFraction': 1}
    batch.save(cache / 'ready' / (song['source'] + '.json'), data)
    evidence['status'] = 'ready'
    evidence['cueCount'] = len(cues)
    evidence['elapsedSeconds'] = round(time.monotonic() - start_time, 3)
    batch.save(evidence_path, evidence)
    return song['songId'], evidence['status']
