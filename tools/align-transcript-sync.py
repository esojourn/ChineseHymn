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
from sync_batch_sources import clean, prepare, text_source
from sync_batch_align import align_windows, export_result

spec = importlib.util.spec_from_file_location('batch_io', Path(__file__).with_name('batch-sync.py'))
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
_model = None


def match_order(definitions, words, language):
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
    for block in difflib.SequenceMatcher(None, expected, heard, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            matched[block.a + offset] = block.b + offset
    anchors = []
    for index, (start, end) in enumerate(spans):
        positions = [matched[position] for position in range(start, end) if position in matched]
        ratio = len(positions) / max(1, end - start)
        if not positions or ratio < .6:
            continue
        anchors.append({'index': index, 'region': definitions[index]['region'],
                        'matchedFraction': round(ratio, 3), 'matchedCharacters': len(positions),
                        'at': float(locations[positions[0]]['start']),
                        'end': float(locations[positions[-1]]['end'])})
    return anchors, len(heard)


def performed_order(definitions, segments, language):
    words = [word for segment in segments for word in segment.get('words', [])
             if 'start' in word and 'end' in word and clean(word['word'], language)]
    anchors, count = match_order(definitions, words, language)
    if [anchor['index'] for anchor in anchors] != list(range(len(definitions))):
        raise ValueError('转写尚未覆盖全部正文阅读区域')
    order, diagnostics = list(definitions), list(anchors)
    cursor = max(anchor['end'] for anchor in anchors)
    for repeat in range(4):
        tail = [word for word in words if word['start'] >= cursor - .01]
        remaining = sum(len(clean(word['word'], language)) for word in tail)
        if remaining < (35 if language == 'en' else 15):
            break
        additional, count = match_order(definitions, tail, language)
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


def confirm_transcript_windows(observations, estimates):
    confirmed, edge_decisions = [], []
    for index, estimate in enumerate(estimates):
        pair = [row for row in observations if row['index'] == index]
        if len(pair) != 2:
            raise ValueError(f'第 {index + 1} 个歌词起点缺少两组声学核对')
        accepted = []
        for row in pair:
            at_start = (index == 0 and 0 <= estimate <= .1 and 0 <= row.get('at', 1) <= .1 and
                        row.get('positiveDurationFraction', 0) >= .65 and row.get('meanProbability', 0) >= .12)
            accepted.append(row['valid'] or at_start)
            if at_start and not row['valid']:
                edge_decisions.append({'index': 0, 'window': row['window'], 'reason': '转写与声学均从音频首帧起唱'})
        if (not all(accepted) or abs(pair[0]['at'] - pair[1]['at']) > .5 or
                abs((pair[0]['at'] + pair[1]['at']) / 2 - estimate) > 1.5):
            raise ValueError(f'第 {index + 1} 个歌词起点未通过转写及双窗口核对')
        confirmed.append(round((pair[0]['at'] + pair[1]['at']) / 2, 2))
    if any(a + .5 >= b for a, b in zip(confirmed, confirmed[1:])):
        raise ValueError('核对后的歌词起点未递增')
    return confirmed, edge_decisions


def generate(song, cache, model_dir, threads):
    global _model
    start = time.monotonic()
    destination = cache / 'transcript-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'transcriptMatching': {'algorithmVersion': 1, 'model': 'medium/int8'}}
    try:
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
        evidence.update(performanceOrder=definitions, estimates=estimates)
        preceding = [definitions[index - 1]['text'] if index and estimate - diagnostics[index - 1]['end'] <= 3.5 else ''
                     for index, estimate in enumerate(estimates)]
        evidence['transcriptMatching'].update(transcript=transcript, diagnostics=diagnostics, language=language)
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel('medium', device='cpu', compute_type='int8', cpu_threads=threads,
                                  download_root=model_dir, local_files_only=True)
        from faster_whisper.audio import decode_audio
        from faster_whisper.tokenizer import Tokenizer
        tokenizer = Tokenizer(_model.hf_tokenizer, _model.model.is_multilingual, task='transcribe', language=language)
        audio = decode_audio(str(reference), sampling_rate=16000)
        checkpoint = cache / 'transcript-windows' / (song['source'] + '.json')
        signature = hashlib.sha256(json.dumps({'source': song['sha256'], 'tracks': song['tracks'],
            'definitions': definitions, 'estimates': estimates, 'preceding': preceding, 'language': language, 'windowVersion': 1},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        observations = align_windows(_model, tokenizer, audio, definitions, estimates, language,
            old.get('windows', []) if old.get('signature') == signature else [],
            lambda rows: batch.save(checkpoint, {'signature': signature, 'windows': rows}), preceding_texts=preceding)
        evidence['windows'] = observations
        times, decisions = confirm_transcript_windows(observations, estimates)
        evidence['transcriptMatching']['edgeDecisions'] = decisions
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
    parser.add_argument('--model-dir', default='/tmp/chinese-hymn-models')
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    for song in batch.inventory():
        if song['songId'] in args.songs and song['type'] == 'text' and song['lyricsAvailable'] and song['tracks'] and not song['existing']:
            print(generate(song, args.cache, args.model_dir, args.threads), flush=True)
