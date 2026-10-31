#!/usr/bin/env python3
"""Generate opt-in lyric previews from complete score and original-audio checks."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import time

from sync_batch_audio import features
from sync_batch_sources import text_source
from sync_batch_align import export_result
from sync_batch_segmented_preview import (common_gap_plan, match_score, override_context_witness, prefix_candidate,
                                          preview_decision, printed_pitch_proof, sha256)

ROOT = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


batch = module('segmented_preview_batch', 'batch-sync.py')
align = module('segmented_preview_align', 'align-transcript-sync.py')
midi = module('segmented_preview_midi', 'midi-sync.py')


def load_bound(path, expected):
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected) or sha256(path) != expected:
        raise ValueError('原始谱面、转写或模型哈希不符')
    return json.loads(path.read_text())


def validate_profile(song, profile, cache, model_dir):
    keys = {'version', 'sourceHash', 'tracks', 'audioFile', 'regions', 'score', 'transcripts', 'models', 'readings', 'review'}
    if (not isinstance(profile, dict) or set(profile) != keys or type(profile['version']) is not int or profile['version'] != 1 or
            profile['sourceHash'] != song['sha256'] or profile['tracks'] != song['tracks'] or
            profile['audioFile'] != song['reference'] or song['type'] != 'text' or len(song['tracks']) != 1 or
            not isinstance(profile['review'], str) or not profile['review'].strip()):
        raise ValueError('分段预读配置与歌词或唯一录音不符')
    score = profile['score']
    if set(score) != {'svg', 'svgSha256', 'practice', 'practiceSha256', 'ties', 'url', 'practiceUrl'}:
        raise ValueError('分段预读须固定原始曲谱及练习数据')
    for field in ('svg', 'practice'):
        path = Path(score[field])
        if path.is_absolute() or '..' in path.parts or sha256(cache / path) != score[field + 'Sha256']:
            raise ValueError('原始谱面或练习数据已变化')
    models = profile['models']
    if set(models) != {'large-v3', 'large-v3-turbo', 'medium'}:
        raise ValueError('分段预读须固定无提示识别及原文上下文模型')
    for name, model in models.items():
        repositories = {'large-v3': 'Systran/faster-whisper-large-v3', 'medium': 'Systran/faster-whisper-medium',
                        'large-v3-turbo': 'dropbox-dash/faster-whisper-large-v3-turbo'}
        if (set(model) != {'repository', 'revision', 'sha256', 'tokenizerSha256'} or
                model['repository'] != repositories[name] or not re.fullmatch('[0-9a-f]{40}', model['revision'])):
            raise ValueError('识别模型版本不完整')
        for file, field in [('model.bin', 'sha256'), ('tokenizer.json', 'tokenizerSha256')]:
            if sha256(Path(model_dir) / name / file) != model[field]:
                raise ValueError('识别模型或词表已变化')
    inputs = profile['transcripts']
    if not isinstance(inputs, list) or len(inputs) != 4:
        raise ValueError('分段预读须保留四份原始完整转写')
    transcripts = []
    for item in inputs:
        if set(item) != {'file', 'sha256', 'kind'} or Path(item['file']).is_absolute() or '..' in Path(item['file']).parts:
            raise ValueError('转写路径或哈希配置无效')
        transcripts.append(load_bound(cache / item['file'], item['sha256']))
    if [item['kind'] for item in inputs] != ['medium-whole', 'turbo-whole', 'medium-contiguous', 'turbo-contiguous']:
        raise ValueError('分段预读的转写类型或顺序不符')
    for transcript in transcripts[:2]:
        if transcript.get('sha256') != song['tracks'][0]['sha256'] or transcript.get('language') != 'en':
            raise ValueError('完整转写与原始英文录音不符')
    expected_params = {'model': models['large-v3-turbo']['sha256'], 'tokenizer': models['large-v3-turbo']['tokenizerSha256'],
                       'audio': song['tracks'][0]['sha256'], 'language': 'en', 'beamSize': 5, 'temperature': 0,
                       'conditionOnPreviousText': False, 'vadFilter': False, 'prompt': None, 'wholeAudio': True}
    if transcripts[1].get('parameters') != expected_params:
        raise ValueError('独立完整转写模型不符')
    expected_medium = {'repository': 'Systran/faster-whisper-medium', 'revision': '08e178d48790749d25932bbc082711ddcfdfbc4f',
                       'sha256': '9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae',
                       'tokenizerSha256': 'fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab'}
    if models['medium'] != expected_medium:
        raise ValueError('原文上下文模型与完整转写模型不符')
    for transcript, model in zip(transcripts[2:], (expected_medium, models['large-v3-turbo'])):
        if (transcript.get('song', {}).get('tracks') != song['tracks'] or transcript.get('sourcePrompt') is not None or
                'sourcePrompt' not in transcript or transcript.get('model') != model or
                transcript.get('gapTranscriptHashes') != [inputs[0]['sha256'], inputs[1]['sha256']] or
                transcript.get('completeOriginalAudio') is not True or transcript.get('language') != 'en'):
            raise ValueError('补充分段转写未覆盖无提示原始录音')
    return score, transcripts


def make_windows(song, profile, cache, model_dir, audio, score_starts, turbo):
    from faster_whisper import WhisperModel
    models, output = {}, {}
    readings = profile['readings']
    if not isinstance(readings, list) or [r.get('index') for r in readings] != list(range(1, 13)):
        raise ValueError('分段预读必须逐一覆盖全部后续阅读区域')
    for item in readings:
        if set(item) != {'index', 'wordCount', 'windows', 'decodedPrefix'}:
            raise ValueError('起句配置包含未识别字段')
        index = item['index']
        if type(item['wordCount']) is not int or not 2 <= item['wordCount'] <= 6 or len(item['windows']) != 2:
            raise ValueError('每个阅读区域需要两组固定音频窗口')
        if item['windows'][0] == item['windows'][1]:
            raise ValueError('两个起句窗口不能重复')
        decoded = []
        for window in item['windows']:
            if window == {'model': 'turbo-whole'}:
                if index != 1:
                    raise ValueError('完整转写窗口只能用于已核对的首个后续区域')
                decoded.append({'bounds': [0, float(turbo['duration'])], 'segments': turbo['segments'],
                                'model': 'turbo-whole'})
                continue
            if (set(window) != {'model', 'before', 'after'} or window['model'] not in ('large-v3', 'large-v3-turbo') or
                    (window['before'], window['after']) not in ((6, 16), (4, 14), (8, 14), (10, 12))):
                raise ValueError('起句窗口必须来自固定模型及固定原始音频范围')
            name = window['model']
            bounds = [score_starts[index] - window['before'], score_starts[index] + window['after']]
            if bounds[0] < 0 or bounds[1] > len(audio) / 16000:
                raise ValueError('起句窗口超出完整录音')
            parameters = {'version': 1, 'model': profile['models'][name], 'audio': song['tracks'][0]['sha256'],
                          'language': 'en', 'bounds': bounds, 'beamSize': 5, 'temperature': 0,
                          'prompt': None, 'vadFilter': False, 'conditionOnPreviousText': False}
            signature = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
            path = cache / 'segmented-preview-windows' / (signature + '.json')
            data = json.loads(path.read_text()) if path.exists() else None
            if not data or data.get('signature') != signature:
                if name not in models:
                    models[name] = WhisperModel(str(Path(model_dir) / name), device='cpu', compute_type='int8',
                                                cpu_threads=2, local_files_only=True)
                segments, _ = models[name].transcribe(audio[round(bounds[0] * 16000):round(bounds[1] * 16000)],
                    language='en', beam_size=5, temperature=0, word_timestamps=True,
                    condition_on_previous_text=False, vad_filter=False)
                rows = [{'text': s.text, 'words': [{'word': w.word, 'start': float(w.start + bounds[0]),
                         'end': float(w.end + bounds[0]), 'probability': float(w.probability)} for w in s.words or []]} for s in segments]
                data = {'signature': signature, 'parameters': parameters, 'bounds': bounds, 'segments': rows}
                batch.save(path, data)
            if data.get('parameters') != parameters or data.get('bounds') != bounds:
                raise ValueError('起句缓存与固定模型、音频或窗口不符')
            decoded.append(data)
        output[index] = decoded
    return output


def make_override_context(song, profile, cache, model_dir, audio, definitions, anchor, candidates):
    from faster_whisper import WhisperModel
    from faster_whisper.tokenizer import Tokenizer
    parameters = {'version': 1, 'source': song['sha256'], 'audio': song['tracks'][0]['sha256'],
                  'model': profile['models']['medium'], 'anchor': anchor,
                  'previous': definitions[8]['text'], 'current': definitions[9]['text'],
                  'bounds': [[anchor - 6, anchor + 16], [anchor - 4, anchor + 14]], 'language': 'en'}
    signature = hashlib.sha256(json.dumps(parameters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = cache / 'segmented-preview-context' / (signature + '.json')
    data = json.loads(path.read_text()) if path.exists() else None
    if not data or data.get('signature') != signature:
        model = WhisperModel(str(Path(model_dir) / 'medium'), device='cpu', compute_type='int8',
                             cpu_threads=2, local_files_only=True)
        tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual, task='transcribe', language='en')
        rows = align.align_windows(model, tokenizer, audio, [definitions[9]], [anchor], 'en',
                                  preceding_texts=[definitions[8]['text']])
        data = {'signature': signature, 'parameters': parameters, 'windows': rows}
        batch.save(path, data)
    if data.get('parameters') != parameters:
        raise ValueError('原文上下文缓存与素材或模型不符')
    return override_context_witness(data['windows'], anchor, candidates)


def generate(song, profile, cache, model_dir):
    from faster_whisper.audio import decode_audio
    from mutagen.mp3 import MP3
    start = time.monotonic()
    evidence_path = cache / 'transcript-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'], 'trackHashes': song['tracks']}
    try:
        score_profile, transcripts = validate_profile(song, profile, cache, model_dir)
        source = text_source(ROOT / 'data' / song['source'], 'en')
        definitions = [row for verse in source['verses'] for row in verse]
        if ([len(verse) for verse in source['verses']] != [4, 4, 5] or
                profile['regions'] != [row['region'] for row in definitions]):
            raise ValueError('正文的完整区域和演唱顺序不符')
        reference = ROOT / 'data' / song['reference']
        if sha256(reference) != song['tracks'][0]['sha256']:
            raise ValueError('原始录音发生变化')
        duration = MP3(reference).info.length
        plans, gaps = common_gap_plan(transcripts[0], transcripts[1], duration)
        cuts = [round((max(a[0], b[0]) + min(a[1], b[1])) / 2, 3) for a, b in gaps]
        expected_clips = [0, cuts[0], cuts[0], cuts[1], cuts[1], duration]
        if any(len(t.get('clips', [])) != 6 or any(abs(x - y) > .001 for x, y in zip(t['clips'], expected_clips))
               for t in transcripts[2:]):
            raise ValueError('补充转写未按共同间奏连续覆盖完整录音')
        practice = load_bound(cache / score_profile['practice'], score_profile['practiceSha256'])['practice']
        printed = printed_pitch_proof(cache / score_profile['svg'], practice['notes'], score_profile['ties'])
        matches = match_score(practice, plans, features(reference, cache), midi)
        lines = [row['melody']['best']['starts'] for row in matches]
        score_starts = [*[lines[0][i] for i in (0, 4, 8, 12)], *[lines[1][i] for i in (0, 4, 8, 12)],
                        *[lines[2][i] for i in (0, 4, 8, 12, 14)]]
        previous_last = [None, *[lines[0][i] for i in (3, 7, 11, 15)],
                         *[lines[1][i] for i in (3, 7, 11, 15)], *[lines[2][i] for i in (3, 7, 11, 13)]]
        coverage = []
        for i, definition in enumerate(definitions):
            lo = max(0, score_starts[i] - 3)
            hi = score_starts[i + 1] + 1 if i + 1 < len(definitions) else duration
            results = []
            for transcript in transcripts:
                observed = [word for segment in transcript['segments'] for word in segment.get('words', [])
                            if lo <= word['start'] < hi and align.clean(word['word'], 'en')]
                found, _ = align.match_order([definition], observed, 'en')
                results.append(found)
            if not any(rows and rows[0]['matchedFraction'] >= .6 for rows in results):
                raise ValueError(f'正文阅读区域 {definition["region"]} 未被原始转写覆盖')
            coverage.append({'index': i, 'region': definition['region'], 'bounds': [lo, hi], 'matches': results})
        audio = decode_audio(str(reference), sampling_rate=16000)
        decoded = make_windows(song, profile, cache, model_dir, audio, score_starts, transcripts[1])
        times = [0]
        decisions = [{'index': 0, 'at': 0, 'method': 'initial reading position; sung onset not measured'}]
        for reading in profile['readings']:
            i = reading['index']
            candidates = [prefix_candidate(i, definitions[i], score_starts[i], window, reading['wordCount'],
                                           reading['decodedPrefix']) for window in decoded[i]]
            for candidate, window in zip(candidates, decoded[i]):
                candidate['decoded'] = window['segments']
            decision = preview_decision(i, candidates, previous_last[i])
            if reading['decodedPrefix'] is not None:
                if i != 9:
                    raise ValueError('起句读音例外只能用于已核对的原文位置')
                decision['contextWitness'] = make_override_context(song, profile, cache, model_dir, audio,
                                                                    definitions, score_starts[i], candidates)
            times.append(decision['at'])
            decisions.append(decision)
        if any(a + .5 >= b for a, b in zip(times, times[1:])):
            raise ValueError('分段预读位置未递增')
        review = {'version': 3, 'indices': list(range(1, 13)),
                  'policy': 'source-bound score-supported previews from two prompt-free lyric openings',
                  'spellingReview': 'To Thee is rendered as Through the in two free decodes; the following four original words match exactly.'}
        evidence.update(performanceOrder=definitions, estimates=score_starts, returnIndices=[],
            initialReadingView={'version': 1, 'region': definitions[0]['region'], 'at': 0,
                                'policy': 'initial reading position from playback start', 'lyricOnsetMeasured': False},
            transcriptMatching={'algorithmVersion': 11, 'previousLinePreview': review, 'profile': profile,
                                'independentTranscripts': transcripts, 'coverage': coverage, 'boundaryDecisions': decisions},
            segmentedScore={'source': score_profile, 'printedPitchProof': printed, 'commonGaps': gaps, 'matches': matches})
        export_result(song, ROOT, cache, evidence_path, evidence, start, source, definitions, times, [0])
        return song['songId'], 'ready'
    except (ValueError, FileNotFoundError, KeyError, IndexError, TypeError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - start, 3))
        batch.save(evidence_path, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--model-dir', default=str(ROOT / 'sync-drafts/models'))
    args = parser.parse_args()
    profiles = json.loads((ROOT / 'tools/sync-segmented-preview-profiles.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and not song['existing'] and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.model_dir), flush=True)
