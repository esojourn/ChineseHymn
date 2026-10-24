#!/usr/bin/env python3
"""Align performed lyrics from a real transcript, then verify every cue in two audio windows."""
import argparse
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
import numpy as np
from sync_batch_transcript import lexical_positions
from sync_batch_sources import clean, prepare, text_source
from sync_batch_align import align_windows, context, export_result

spec = importlib.util.spec_from_file_location('batch_io', Path(__file__).with_name('batch-sync.py'))
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
_model = None
_model_path = None


def match_order(definitions, words, language, partial=False):
    expected, spans = '', []
    for definition in definitions:
        text = clean(definition['text'], language)
        spans.append((len(expected), len(expected) + len(text)))
        expected += text
    heard, locations = '', []
    for word in words:
        text = clean(word['word'], language)
        heard += text
        locations.extend([word] * len(text))
    matched = {}
    if partial:
        for block in difflib.SequenceMatcher(None, expected, heard, autojunk=False).get_matching_blocks():
            for offset in range(block.size):
                matched[block.a + offset] = block.b + offset
    else:
        positions = lexical_positions(np.array([ord(char) for char in expected], dtype=np.int32),
                                      np.array([ord(char) for char in heard], dtype=np.int32))
        matched = {index: int(position) for index, position in enumerate(positions) if position >= 0}
    anchors = []
    for index, (start, end) in enumerate(spans):
        positions = [matched[position] for position in range(start, end) if position in matched]
        ratio = len(positions) / max(1, end - start)
        if not positions or ratio < .6:
            continue
        anchors.append({'index': index, 'region': definitions[index]['region'],
                        'matchedFraction': round(ratio, 3), 'matchedCharacters': len(positions),
                        'at': float(locations[positions[0]]['start']),
                        'firstWord': clean(locations[positions[0]]['word'], language),
                        'firstWordEnd': float(locations[positions[0]]['end']),
                        'end': float(locations[positions[-1]]['end'])})
    return anchors, len(heard)


def performed_order(definitions, segments, language):
    words = [word for segment in segments for word in segment.get('words', [])
             if 'start' in word and 'end' in word and clean(word['word'], language)]
    anchors, count = match_order(definitions, words, language)
    if [anchor['index'] for anchor in anchors] != list(range(len(definitions))):
        anchors, count = match_order(definitions, words, language, partial=True)
        if [anchor['index'] for anchor in anchors] != list(range(len(definitions))):
            raise ValueError('转写尚未覆盖全部正文阅读区域')
    order, diagnostics = list(definitions), list(anchors)
    cursor = max(anchor['end'] for anchor in anchors)
    for repeat in range(4):
        tail = [word for word in words if word['start'] >= cursor - .01]
        remaining = sum(len(clean(word['word'], language)) for word in tail)
        if remaining < (35 if language == 'en' else 15):
            break
        additional, count = match_order(definitions, tail, language, partial=True)
        indices = [anchor['index'] for anchor in additional]
        if (not indices or indices != list(range(len(indices))) or
                sum(anchor['matchedCharacters'] for anchor in additional) / max(1, count) < .75):
            raise ValueError('录音尾部存在未确认的歌词顺序')
        order.extend(definitions[index] for index in indices)
        diagnostics.extend(additional)
        next_cursor = max(anchor['end'] for anchor in additional)
        if next_cursor <= cursor + .5:
            raise ValueError('重复歌词顺序没有向后推进')
        cursor = next_cursor
    else:
        raise ValueError('录音重复次数超出自动核对范围')
    estimates = [anchor['at'] for anchor in diagnostics]
    if any(a + .5 >= b for a, b in zip(estimates, estimates[1:])):
        raise ValueError('转写对应的歌词起点未递增')
    return order, estimates, diagnostics


def confirm_transcript_windows(observations, estimates, anchor_ranges=None, anchor_words=None):
    confirmed, edge_decisions = [], []
    for index, estimate in enumerate(estimates):
        pair = [row for row in observations if row['index'] == index]
        if len(pair) < 2:
            raise ValueError(f'第 {index + 1} 个歌词起点缺少两组声学核对')
        accepted = []
        for row in pair:
            at_start = (index == 0 and 0 <= estimate <= .1 and 0 <= row.get('at', 1) <= .1 and
                        row.get('positiveDurationFraction', 0) >= .65 and row.get('meanProbability', 0) >= .12)
            # Decoder token likelihood is not an onset confidence. A word must
            # still have an actual span before its start can support a boundary.
            first_word_valid = ('firstWordStart' not in row or
                                row.get('firstWordEnd', 0) - row['firstWordStart'] >= .04)
            if row['valid'] and first_word_valid or at_start:
                accepted.append(row)
            if at_start and not row['valid']:
                edge_decisions.append({'index': 0, 'window': row['window'], 'reason': '转写与声学均从音频首帧起唱'})
        lo, hi = anchor_ranges[index] if anchor_ranges is not None else (estimate, estimate)
        def held_word(row):
            return (anchor_words is not None and row.get('firstWord') == anchor_words[index] and
                    row.get('firstWordProbability', 0) >= .12 and
                    row.get('firstWordEnd', 0) - row.get('firstWordStart', 0) >= .04 and
                    row['at'] <= lo <= row.get('firstWordEnd', 0) and abs(row.get('firstWordEnd', 0) - hi) <= .5)
        choices = [(abs(left['at'] - right['at']), (left['at'] + right['at']) / 2, left, right)
                   for first, left in enumerate(accepted) for right in accepted[first + 1:]
                   if left['window'] != right['window'] and abs(left['at'] - right['at']) <= .5 and
                   (lo - 1.5 <= (left['at'] + right['at']) / 2 <= hi + 1.5 or held_word(left) and held_word(right))]
        supported = sorted({row['at'] for delta, at, left, right in choices for row in (left, right)})
        if (not supported or supported[-1] - supported[0] > 1 or
                any(right - left > .5 for left, right in zip(supported, supported[1:]))):
            raise ValueError(f'第 {index + 1} 个歌词起点未通过转写及双窗口核对')
        at = round(min(choices, key=lambda choice: (choice[0], choice[1]))[1], 2)
        confirmed.append(at)
        if not lo - 1.5 <= at <= hi + 1.5:
            edge_decisions.append({'index': index, 'at': at, 'reason': '两个窗口的同一延音首词词尾与转写相符',
                                   'word': anchor_words[index], 'asrWordRange': [lo, hi]})
    if any(a + .5 >= b for a, b in zip(confirmed, confirmed[1:])):
        raise ValueError('核对后的歌词起点未递增')
    return confirmed, edge_decisions


def verified_window_times(model, tokenizer, audio, definitions, estimates, language, preceding,
                          anchor_ranges, anchor_words, resume, save_windows):
    observations = align_windows(model, tokenizer, audio, definitions, estimates, language,
        resume, save_windows, preceding_texts=preceding)
    for row in observations:
        if 'firstWordEnd' not in row and row.get('words'):
            offset = len(clean(context(preceding[row['index']], language, True), language))
            letters = [word for word in row['words'] for character in clean(word['word'], language)]
            if offset < len(letters):
                word = letters[offset]
                row.update(firstWord=clean(word['word'], language), firstWordStart=word['start'] + row['bounds'][0],
                           firstWordEnd=word['end'] + row['bounds'][0], firstWordProbability=float(word['probability']))
    for index, estimate in enumerate(estimates):
        for window, bounds in enumerate([(8, 18), (10, 16)], 2):
            pair = [{**row, 'index': 0} for row in observations if row['index'] == index]
            try:
                confirm_transcript_windows(pair, [estimate], [anchor_ranges[index]], [anchor_words[index]])
                break
            except ValueError:
                pass
            if any(row['index'] == index and row['window'] == window for row in observations):
                continue
            additional = align_windows(model, tokenizer, audio, [definitions[index]], [estimate], language,
                preceding_texts=[preceding[index]], window_bounds=[bounds])
            observations.extend({**row, 'index': index, 'window': window} for row in additional)
            save_windows(observations)
    times, decisions = confirm_transcript_windows(observations, estimates, anchor_ranges, anchor_words)
    return observations, times, decisions


def validate_initial_view(song, profile, transcript_path, definitions, diagnostics):
    if (not isinstance(profile, dict) or type(profile.get('version')) is not int or profile['version'] != 1 or
            song['type'] != 'text' or profile.get('sourceHash') != song['sha256'] or
            profile.get('tracks') != song['tracks'] or profile.get('audioFile') != song['reference'] or
            profile.get('transcriptHash') != batch.checksum(transcript_path) or len(definitions) < 2 or
            profile.get('regions') != [item['region'] for item in definitions] or
            len(diagnostics) != len(definitions) or any(row.get('matchedFraction', 0) < .6 for row in diagnostics)):
        raise ValueError('首屏位置配置与正文、录音、完整转写或已确认的演唱顺序不符')
    return {'version': 1, 'region': definitions[0]['region'], 'at': 0,
            'policy': 'initial reading position from playback start', 'lyricOnsetMeasured': False,
            'profile': profile}


def initial_view_window_times(model, tokenizer, audio, definitions, estimates, language, preceding,
                              anchor_ranges, anchor_words, resume, save_windows):
    later = [{**row, 'index': row['index'] - 1} for row in resume if row['index'] > 0]
    def save_later(rows):
        save_windows([{**row, 'index': row['index'] + 1} for row in rows])
    observations, times, decisions = verified_window_times(model, tokenizer, audio, definitions[1:], estimates[1:],
        language, preceding[1:], anchor_ranges[1:], anchor_words[1:], later, save_later)
    if times and times[0] <= .5:
        raise ValueError('首屏位置与实际后续换句顺序冲突')
    return ([{**row, 'index': row['index'] + 1} for row in observations], [0] + times,
            [{'index': 0, 'at': 0, 'method': 'initial reading position; sung onset not measured'}] +
            [{**row, 'index': row['index'] + 1} for row in decisions])


def generate(song, cache, model_dir, threads, ctc_model_dir=None, free_initial_profile=None, initial_view_profile=None):
    global _model, _model_path
    start = time.monotonic()
    destination = cache / 'transcript-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'transcriptMatching': {'algorithmVersion': 5 if ctc_model_dir else 4, 'model': 'medium/int8'}}
    try:
        if initial_view_profile and (free_initial_profile or ctc_model_dir):
            raise ValueError('首屏位置与首词实测应分别配置')
        if free_initial_profile:
            track = next((item for item in song['tracks'] if item['file'] == song['reference']), None)
            if (free_initial_profile.get('sourceHash') != song['sha256'] or
                    free_initial_profile.get('audioFile') != song['reference'] or not track or
                    free_initial_profile.get('audioHash') != track['sha256']):
                raise ValueError('自由首句配置与当前歌词或录音不一致')
            evidence['transcriptMatching'].update(algorithmVersion=6, freeInitialProfile=free_initial_profile)
        text = prepare.read_text(batch.ROOT / 'data' / song['source'])
        language = 'en' if len(re.findall('[A-Za-z]', text)) > 2 * len(re.findall('[\u3400-\u9fff]', text)) else 'zh'
        source = text_source(batch.ROOT / 'data' / song['source'], language)
        transcript_path = cache / 'asr' / (song['reference'] + '.json')
        transcript = json.loads(transcript_path.read_text())
        reference = batch.ROOT / 'data' / song['reference']
        if transcript['sha256'] != batch.checksum(reference) or transcript['language'] != language:
            raise ValueError('缺少匹配当前音频及歌词语言的转写')
        original = [item for verse in source['verses'] for item in verse]
        definitions, estimates, diagnostics = performed_order(original, transcript['segments'], language)
        if initial_view_profile:
            evidence['initialReadingView'] = validate_initial_view(song, initial_view_profile, transcript_path,
                                                                  definitions, diagnostics)
            evidence['transcriptMatching']['algorithmVersion'] = 7
        evidence.update(performanceOrder=definitions, estimates=estimates)
        preceding = [definitions[index - 1]['text'] if index else '' for index in range(len(definitions))]
        anchor_ranges = [(row['at'], row['firstWordEnd']) for row in diagnostics]
        anchor_words = [row['firstWord'] for row in diagnostics]
        evidence['transcriptMatching'].update(transcript=transcript, diagnostics=diagnostics, language=language)
        selected_model = str(Path(model_dir) / 'medium') if free_initial_profile or initial_view_profile else 'medium'
        if _model is None or (free_initial_profile or initial_view_profile) and _model_path != selected_model:
            from faster_whisper import WhisperModel
            _model = WhisperModel(selected_model, device='cpu', compute_type='int8', cpu_threads=threads,
                                  download_root=model_dir, local_files_only=True)
            _model_path = selected_model
        from faster_whisper.audio import decode_audio
        from faster_whisper.tokenizer import Tokenizer
        tokenizer = Tokenizer(_model.hf_tokenizer, _model.model.is_multilingual, task='transcribe', language=language)
        audio = decode_audio(str(reference), sampling_rate=16000)
        checkpoint = cache / 'transcript-windows' / (song['source'] + '.json')
        parameters = {'source': song['sha256'], 'tracks': song['tracks'], 'definitions': definitions,
                      'estimates': estimates, 'preceding': preceding, 'language': language,
                      'windowVersion': 2, 'extraWindows': [(8, 18), (10, 16)]}
        normal_parameters = dict(parameters)
        if initial_view_profile:
            parameters['initialReadingViewVersion'] = 1
        fingerprint = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        signature = fingerprint(parameters)
        old = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        resume = old.get('windows', []) if old.get('signature') == signature else []
        if not resume and initial_view_profile and old.get('signature') == fingerprint(normal_parameters):
            resume = [row for row in old.get('windows', []) if row['index'] > 0]
        # Retain only windows whose actual token context is unchanged by the
        # previous gap-handling rule; source/audio/sequence signatures still match.
        if not resume and old.get('windows'):
            legacy_context = [definitions[index - 1]['text'] if index and estimate - diagnostics[index - 1]['end'] <= 3.5 else ''
                              for index, estimate in enumerate(estimates)]
            legacy = {**parameters, 'preceding': legacy_context, 'windowVersion': 1}
            legacy.pop('extraWindows')
            legacy.pop('initialReadingViewVersion', None)
            if old.get('signature') == fingerprint(legacy):
                resume = [row for row in old['windows'] if legacy_context[row['index']] == preceding[row['index']]]
        evidence['windows'] = resume
        def save_windows(rows):
            evidence['windows'] = rows
            batch.save(checkpoint, {'signature': signature, 'windows': rows})
        if initial_view_profile:
            observations, times, decisions = initial_view_window_times(_model, tokenizer, audio, definitions, estimates,
                language, preceding, anchor_ranges, anchor_words, resume, save_windows)
            save_windows(observations)
        else:
            try:
                observations, times, decisions = verified_window_times(_model, tokenizer, audio, definitions, estimates,
                    language, preceding, anchor_ranges, anchor_words, resume, save_windows)
            except ValueError:
                if free_initial_profile:
                    from sync_batch_free_prefix import recover_initial
                    observations = evidence['windows']
                    times, decisions = recover_initial(_model, audio, batch.checksum(reference), definitions, estimates,
                        anchor_ranges, anchor_words, observations, language, free_initial_profile['freeInitialPrefix'],
                        model_dir, cache, confirm_transcript_windows, batch.save)
                elif ctc_model_dir is None or language != 'en':
                    raise
                else:
                    from sync_batch_ctc import recover_initial
                    observations = evidence['windows']
                    times, decisions = recover_initial(audio, batch.checksum(reference), definitions, estimates, anchor_ranges,
                        anchor_words, observations, ctc_model_dir, cache, confirm_transcript_windows)
        evidence['windows'] = observations
        evidence['transcriptMatching']['boundaryDecisions'] = decisions
        starts = {region: bounds['startLine'] for region, bounds in source['regions'].items()}
        evidence['returnIndices'] = [index for index in range(1, len(definitions))
                                    if starts[definitions[index]['region']] < starts[definitions[index - 1]['region']]]
        export_result(song, batch.ROOT, cache, destination, evidence, start, source, definitions, times, [0])
        return song['songId'], 'ready'
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - start, 3))
        batch.save(destination, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--model-dir', default=str(Path(__file__).resolve().parents[1] / 'sync-drafts/models'))
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--ctc-model-dir', type=Path, help='Optional pinned MMS model for independently decoded initial words')
    args = parser.parse_args()
    profile_path = Path(__file__).with_name('sync-transcript-prefix-profiles.json')
    profiles = json.loads(profile_path.read_text()) if profile_path.exists() else {}
    view_path = Path(__file__).with_name('sync-initial-reading-profiles.json')
    view_profiles = json.loads(view_path.read_text()) if view_path.exists() else {}
    for song in batch.inventory():
        if song['songId'] in args.songs and song['type'] == 'text' and song['lyricsAvailable'] and song['tracks'] and not song['existing']:
            print(generate(song, args.cache, args.model_dir, args.threads, args.ctc_model_dir,
                           profiles.get(song['songId']), view_profiles.get(song['songId'])), flush=True)
