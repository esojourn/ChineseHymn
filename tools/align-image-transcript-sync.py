#!/usr/bin/env python3
"""Verify reviewed performed blocks in a lyrics image or an unchanged text source."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time
from sync_batch_sources import clean, image_source, text_source
from sync_batch_align import export_result

spec = importlib.util.spec_from_file_location('image_transcript_checks', Path(__file__).with_name('align-transcript-sync.py'))
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
batch = checks.batch
_model = None
_model_path = None


def transcript_blocks(profile, transcript, source):
    segments, used, definitions, ranges, words = transcript['segments'], set(), [], [], []
    language = profile['language']
    for block in profile['blocks']:
        indices = block['segmentIndices']
        region = block['region']
        if (region not in source['regions'] or not indices or indices != list(range(indices[0], indices[-1] + 1)) or
                indices[0] < 0 or indices[-1] >= len(segments) or used.intersection(indices)):
            raise ValueError('演唱片段范围或阅读区域无效')
        used.update(indices)
        selected = [segments[index] for index in indices]
        lexical = [word for segment in selected for word in segment.get('words', []) if clean(word['word'], language)]
        if not lexical:
            raise ValueError('指定段落没有可核对的人声词语')
        text = ' '.join(segment['text'].strip() for segment in selected)
        if source.get('layout') == 'lyrics-only-text':
            original = source['blocks'][region]['text']
            text = block.get('sourceText', original)
            if not clean(text, language) or clean(text, language) not in clean(original, language):
                raise ValueError('实际演唱片段不属于指定正文阅读区域')
            anchors, _ = checks.match_order([{'region': region, 'text': text}], lexical, language)
            if len(anchors) != 1:
                raise ValueError('指定演唱片段未覆盖正文阅读区域')
        definitions.append({'region': region, 'label': source['blocks'][region]['label'], 'text': text})
        first = lexical[0]
        ranges.append((float(first['start']), float(first['end'])))
        words.append(clean(first['word'], language))
    for excluded in profile.get('nonLyricSegments', []):
        index = excluded['index']
        if (index in used or not 0 <= index < len(segments) or
                segments[index]['text'].strip() != excluded['text'].strip()):
            raise ValueError('非歌词片段与已经复核的尾语不一致')
        used.add(index)
    if used != set(range(len(segments))):
        raise ValueError('实际转写仍有未说明的演唱片段')
    if source.get('layout') == 'lyrics-only-text':
        performed = {definition['region'] for definition in definitions}
        unsung = set()
        omissions = profile.get('unsungRegions', [])
        if not isinstance(omissions, list):
            raise ValueError('未演唱区域必须逐项明确配置')
        for item in omissions:
            if not isinstance(item, dict):
                raise ValueError('未演唱区域必须逐项明确配置')
            region = item.get('region')
            if (region not in source['regions'] or region in performed or region in unsung or
                    not isinstance(item.get('reason'), str) or not item['reason'].strip() or
                    not isinstance(item.get('sourceText'), str) or
                    clean(item.get('sourceText', ''), language) != clean(source['blocks'][region]['text'], language)):
                raise ValueError('未演唱区域未明确绑定正文、重复或已列入实际演唱')
            unsung.add(region)
        if performed | unsung != set(source['regions']):
            raise ValueError('实际演唱配置尚未覆盖全部正文阅读区域')
    estimates = [start for start, end in ranges]
    if (not estimates or any(not math.isfinite(value) or value < 0 for pair in ranges for value in pair) or
            any(end < start for start, end in ranges) or any(a + .5 >= b for a, b in zip(estimates, estimates[1:]))):
        raise ValueError('实际演唱段落起点不递增')
    return definitions, estimates, ranges, words


def generate(song, profile, cache, model_dir, threads, ctc_model_dir=None):
    global _model, _model_path
    started = time.monotonic()
    evidence_path = cache / 'transcript-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'transcriptMatching': {'algorithmVersion': 6 if ctc_model_dir else 5,
                'mode': 'image-translation-blocks' if song['type'] == 'image' else 'text-reviewed-performance',
                'profile': profile, 'model': 'medium/int8'}}
    try:
        track = next((track for track in song['tracks'] if track['file'] == profile['audioFile']), None)
        if (song['sha256'] != profile['sourceHash'] or not track or track['sha256'] != profile['audioHash'] or
                song['reference'] != profile['audioFile']):
            raise ValueError('歌词素材或录音已变化，需重新核对演唱段落')
        if song['type'] == 'image':
            recognition = json.loads((cache / 'ocr' / (song['source'] + '.json')).read_text())
            if recognition['sha256'] != song['sha256']:
                raise ValueError('图片识别已失效')
            source = image_source(recognition)
            if source.get('layout') != 'lyrics-only':
                raise ValueError('该配置仅用于无乐谱的歌词图片')
        else:
            source = text_source(batch.ROOT / 'data' / song['source'], profile['language'])
            source['layout'] = 'lyrics-only-text'
            source['blocks'] = {item['region']: item for verse in source['verses'] for item in verse}
            evidence['transcriptMatching']['algorithmVersion'] = 7
        asr_path = cache / 'asr' / (song['reference'] + '.json')
        if batch.checksum(asr_path) != profile['transcriptHash']:
            raise ValueError('转写已变化，需重新核对演唱段落')
        transcript = json.loads(asr_path.read_text())
        language = profile['language']
        if transcript['sha256'] != profile['audioHash'] or transcript['language'] != language:
            raise ValueError('缺少匹配当前录音和原歌词语言的转写')
        definitions, estimates, ranges, anchor_words = transcript_blocks(profile, transcript, source)
        evidence.update(performanceOrder=definitions, estimates=estimates)
        evidence['transcriptMatching']['transcript'] = transcript
        selected_model = str(Path(model_dir) / 'medium') if profile.get('freeInitialPrefix') else 'medium'
        if _model is None or _model_path != selected_model:
            from faster_whisper import WhisperModel
            _model = WhisperModel(selected_model, device='cpu', compute_type='int8', cpu_threads=threads,
                                  download_root=model_dir, local_files_only=True)
            _model_path = selected_model
        from faster_whisper.audio import decode_audio
        from faster_whisper.tokenizer import Tokenizer
        tokenizer = Tokenizer(_model.hf_tokenizer, _model.model.is_multilingual, task='transcribe', language=language)
        audio = decode_audio(str(batch.ROOT / 'data' / song['reference']), sampling_rate=16000)
        preceding = [definitions[index - 1]['text'] if index else '' for index in range(len(definitions))]
        signature = hashlib.sha256(json.dumps({'source': song['sha256'], 'profile': profile,
            'definitions': definitions, 'language': language, 'version': 1}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        checkpoint = cache / 'image-transcript-windows' / (song['source'] + '.json')
        previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        resume = previous.get('windows', []) if previous.get('signature') == signature else []
        evidence['windows'] = resume

        def save_windows(rows):
            evidence['windows'] = rows
            batch.save(checkpoint, {'signature': signature, 'windows': rows})

        try:
            observations, times, decisions = checks.verified_window_times(_model, tokenizer, audio, definitions, estimates,
                language, preceding, ranges, anchor_words, resume, save_windows)
        except ValueError:
            if profile.get('freeInitialPrefix'):
                from sync_batch_free_prefix import recover_initial
                observations = evidence['windows']
                times, decisions = recover_initial(_model, audio, profile['audioHash'], definitions, estimates, ranges,
                    anchor_words, observations, language, profile['freeInitialPrefix'], model_dir, cache,
                    checks.confirm_transcript_windows, batch.save)
            elif ctc_model_dir is None:
                raise
            else:
                from sync_batch_ctc import recover_initial
                observations = evidence['windows']
                times, decisions = recover_initial(audio, profile['audioHash'], definitions, estimates, ranges,
                    anchor_words, observations, ctc_model_dir, cache, checks.confirm_transcript_windows)
        evidence['windows'] = observations
        evidence['transcriptMatching']['boundaryDecisions'] = decisions
        coordinate = 'top' if song['type'] == 'image' else 'startLine'
        evidence['returnIndices'] = [index for index in range(1, len(definitions)) if
            source['regions'][definitions[index]['region']][coordinate] < source['regions'][definitions[index - 1]['region']][coordinate]]
        return export_result(song, batch.ROOT, cache, evidence_path, evidence, started, source,
                             definitions, times, [0])
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3))
        batch.save(evidence_path, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--model-dir', default=str(batch.ROOT / 'sync-drafts/models'))
    parser.add_argument('--ctc-model-dir', type=Path, help='Optional pinned MMS model for independently decoded initial words')
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name('sync-image-transcript-profiles.json').read_text())
    text_profiles = Path(__file__).with_name('sync-text-transcript-profiles.json')
    if text_profiles.exists():
        profiles.update(json.loads(text_profiles.read_text()))
    unknown = sorted(set(args.songs) - profiles.keys())
    if unknown:
        parser.error('这些曲目尚未核对素材与实际歌词对应：' + '、'.join(unknown))
    for song in batch.inventory():
        if song['songId'] in args.songs and not song['existing']:
            print(generate(song, profiles[song['songId']], args.cache, args.model_dir, args.threads, args.ctc_model_dir), flush=True)
