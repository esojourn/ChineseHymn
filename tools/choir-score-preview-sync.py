"""Generate score-supported bilingual reading previews from original recordings."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / 'tools'))


def module(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


score = module('score', R / 'tools/sync_batch_choir_score.py')
prefix = module('prefix', R / 'tools/sync_batch_choir_prefix.py')
align = module('align', R / 'tools/align-transcript-sync.py')
from sync_batch_audio import features
from sync_batch_sources import text_source, clean, prepare
from sync_batch_align import export_result
from faster_whisper.audio import decode_audio
from faster_whisper import WhisperModel


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_window(song, profile, model_name, bounds, cache, model_dir, audio, models):
    spec = profile['models'][model_name]
    params = {
        'version': 1,
        'model': spec,
        'audio': song['tracks'][0]['sha256'],
        'language': 'en',
        'bounds': bounds,
        'beamSize': 5,
        'temperature': 0,
        'prompt': None,
        'vadFilter': False,
        'conditionOnPreviousText': False,
    }
    signature = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    path = cache / 'choir-preview-windows' / (signature + '.json')
    data = json.loads(path.read_text()) if path.exists() else None
    if data is None:
        if model_name not in models:
            models[model_name] = WhisperModel(
                str(model_dir / model_name), device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True
            )
        lo, hi = bounds
        segs, _ = models[model_name].transcribe(
            audio[round(lo * 16000) : round(hi * 16000)],
            language='en',
            beam_size=5,
            temperature=0,
            word_timestamps=True,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        rows = [
            {
                'text': s.text,
                'words': [
                    {
                        'word': w.word,
                        'start': float(w.start + lo),
                        'end': float(w.end + lo),
                        'probability': float(w.probability),
                    }
                    for w in s.words or []
                ],
            }
            for s in segs
        ]
        data = {'signature': signature, 'parameters': params, 'bounds': bounds, 'segments': rows}
        score.c.batch.save(path, data)
    if data.get('parameters') != params or data.get('bounds') != bounds or data.get('signature') != signature:
        raise ValueError('自由识别缓存与固定音频、模型或窗口不符')
    return data


def generate(song, profile, cache, model_dir):
    started = time.monotonic()
    dest = cache / 'transcript-alignment' / (song['source'] + '.json')
    ev = {
        'songId': song['songId'],
        'source': song['source'],
        'sourceHash': song['sha256'],
        'trackHashes': song['tracks'],
    }
    try:
        keys = {
            'version',
            'sourceHash',
            'tracks',
            'audioFile',
            'regions',
            'score',
            'sampling',
            'transcripts',
            'models',
            'readings',
            'review',
            'historicalGap',
        }
        if (
            set(profile) != keys
            or type(profile['version']) is not int
            or profile['version'] != 1
            or song['type'] != 'text'
            or not isinstance(profile['review'], str)
            or not profile['review'].strip()
        ):
            raise ValueError('合唱预读配置不完整')
        if (
            song['sha256'] != profile['sourceHash']
            or song['tracks'] != profile['tracks']
            or len(song['tracks']) != 1
            or (song['reference'] != profile['audioFile'])
            or (sha(R / 'data' / song['reference']) != song['tracks'][0]['sha256'])
        ):
            raise ValueError('歌词或原始录音已变化')
        source = text_source(R / 'data' / song['source'], 'en')
        definitions = [x for v in source['verses'] for x in v]
        if [len(v) for v in source['verses']] != [4, 4, 4, 4] or [x['region'] for x in definitions] != profile[
            'regions'
        ]:
            raise ValueError('原文必须为四节各四个阅读区域')
        # Credits preceding the numbered verses are not lyric verses.
        for index, definition in enumerate(definitions):
            definition['label'] = f'第{index // 4 + 1}段'
        transcripts = []
        for spec in profile['transcripts']:
            if set(spec) != {'file', 'sha256'} or Path(spec['file']).is_absolute() or '..' in Path(spec['file']).parts:
                raise ValueError('完整转写路径无效')
            path = cache / spec['file']
            if sha(path) != spec['sha256']:
                raise ValueError('完整转写原件哈希不符')
            item = json.loads(path.read_text())
            if item.get('sha256') != song['tracks'][0]['sha256'] or item.get('language') != 'en':
                raise ValueError('完整转写与当前录音不符')
            transcripts.append(item)
        if len(transcripts) != 2 or [x['model'] for x in transcripts] != ['medium', 'large-v3-turbo/int8']:
            raise ValueError('必须保留两份固定完整原始转写')
        if set(profile['models']) != {'medium', 'large-v3', 'large-v3-turbo'}:
            raise ValueError('必须固定全部使用的自由识别模型')
        repositories = {
            'large-v3': 'Systran/faster-whisper-large-v3',
            'medium': 'Systran/faster-whisper-medium',
            'large-v3-turbo': 'dropbox-dash/faster-whisper-large-v3-turbo',
        }
        for name, spec in profile['models'].items():
            if (
                set(spec) != {'repository', 'revision', 'sha256', 'tokenizerSha256'}
                or spec['repository'] != repositories[name]
                or not re.fullmatch('[0-9a-f]{40}', spec['revision'])
            ):
                raise ValueError('识别模型版本不完整')
            for file, key in [('model.bin', 'sha256'), ('tokenizer.json', 'tokenizerSha256')]:
                if sha(model_dir / name / file) != spec[key]:
                    raise ValueError('自由识别模型或词表已改变')
        turbo = profile['models']['large-v3-turbo']
        expected_medium = {
            'repository': 'Systran/faster-whisper-medium',
            'revision': '08e178d48790749d25932bbc082711ddcfdfbc4f',
            'sha256': '9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae',
            'tokenizerSha256': 'fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab',
        }
        if profile['models']['medium'] != expected_medium:
            raise ValueError('完整转写与识别模型不一致')
        expected = {
            'model': turbo['sha256'],
            'tokenizer': turbo['tokenizerSha256'],
            'audio': song['tracks'][0]['sha256'],
            'language': 'en',
            'beamSize': 5,
            'temperature': 0,
            'conditionOnPreviousText': False,
            'vadFilter': False,
            'prompt': None,
            'wholeAudio': True,
        }
        if transcripts[1].get('parameters') != expected:
            raise ValueError('独立完整转写的模型和识别参数不符')
        target = features(R / 'data' / song['reference'], cache)
        musical = score.measured_score(profile['score'], cache, target, profile['sampling'])
        flat = [x for v in musical['verses'] for x in v['local']]
        coverage = []
        for i, d in enumerate(definitions):
            lo = flat[i]['scoreAnchor'] - 3
            hi = flat[i + 1]['scoreAnchor'] + 1 if i + 1 < len(flat) else float(target[0][-1])
            results = []
            for t in transcripts:
                words = [
                    w
                    for seg in t['segments']
                    for w in seg.get('words', [])
                    if lo <= w['start'] < hi and clean(w['word'], 'en')
                ]
                match, _ = align.match_order([d], words, 'en')
                results.append(match)
            if not any((v and v[0]['matchedFraction'] >= 0.6 for v in results)):
                raise ValueError(f'原文区域{i}未获完整转写覆盖')
            coverage.append({'index': i, 'bounds': [lo, hi], 'matches': results})
        if [x['index'] for x in profile['readings']] != list(range(1, 16)):
            raise ValueError('每个后续阅读区域均须指定窗口')
        historical_spec = profile['historicalGap']
        historical_path = Path(historical_spec['file'])
        if (set(historical_spec) != {'file', 'sha256'} or historical_path.is_absolute() or
                '..' in historical_path.parts or sha(R / historical_path) != historical_spec['sha256']):
            raise ValueError('原始局部识别记录或哈希不符')
        historical = json.loads((R / historical_path).read_text())
        audio = decode_audio(str(R / 'data' / song['reference']), sampling_rate=16000)
        models = {}
        times = [0]
        decisions = [{'index': 0, 'at': 0, 'method': 'initial reading position; sung onset not measured'}]
        for item in profile['readings']:
            if set(item) != {'index', 'wordCount', 'windows'}:
                raise ValueError('阅读位置配置无效')
            i = item['index']
            anchor = flat[i]['scoreAnchor']
            last = flat[i - 1]['lastLineScoreStart']
            decoded = []
            if len(item['windows']) != len({tuple(x) for x in item['windows']}):
                raise ValueError('自由识别窗口重复')
            for model, before, after in item['windows']:
                bounds = [anchor - before, anchor + after]
                ordinary = (before, after) in [(4, 14), (6, 16), (8, 14), (10, 12)]
                reviewed_short = i == 12 and model == 'large-v3-turbo' and ((before, after) == (1, 12))
                reviewed_gap = (
                    i == 5 and model == 'large-v3' and all((abs(a - b) < 0.001 for a, b in zip(bounds, [89, 99])))
                )
                if model not in profile['models'] or not (ordinary or reviewed_short or reviewed_gap):
                    raise ValueError('识别只允许已固定的模型与窗口')
                if bounds[0] < 0 or bounds[1] > len(audio) / 16000:
                    raise ValueError('识别窗口超出录音')
                decoded.append(
                    {
                        'name': model + f'-{before}-{after}',
                        'local': True,
                        **load_window(song, profile, model, bounds, cache, model_dir, audio, models),
                    }
                )
            fresh_first = None
            if i == 5:
                fresh = [w for w in decoded if w['name'] == 'medium-6-16']
                if len(fresh) != 1:
                    raise ValueError('遗漏词需要新的原音频句首识别')
                fresh_first = prefix.fresh_first_word(definitions[i], anchor, fresh[0])
                if (historical.get('parameters') != fresh[0]['parameters'] or
                        historical.get('bounds') != fresh[0]['bounds'] or
                        historical.get('signature') != fresh[0]['signature']):
                    raise ValueError('已保留的原始局部识别与当前音频或模型不符')
                if historical['segments'] != fresh[0]['segments']:
                    decoded.append({'name': 'medium-historical', 'local': True, **historical})
            for model, t in zip(['medium-whole', 'turbo-whole'], transcripts):
                decoded.append({'name': model, 'local': False, 'bounds': [0, t['duration']], 'segments': t['segments']})
            candidates = []
            rejected = []
            for w in decoded:
                try:
                    if i == 5:
                        found = prefix.reviewed_gap(i, definitions[i], anchor, w)
                    elif i == 6:
                        found = prefix.reviewed_function_word(i, definitions[i], anchor, w)
                    elif i == 12:
                        if w['name'] not in ('large-v3-turbo-1-12', 'medium-whole'):
                            continue
                        found = prefix.reviewed_clipped(i, definitions[i], anchor, w, w['local'])
                    else:
                        found = prefix.candidate(i, definitions[i], anchor, w, item['wordCount'])
                    candidates.append({'name': w['name'], 'local': w['local'], **found, 'decoded': w['segments']})
                except ValueError as error:
                    rejected.append(
                        {'name': w['name'], 'reason': str(error), 'decoded': w['segments'] if w['local'] else None}
                    )
            if not any((w['local'] for w in candidates)):
                raise ValueError(f'第{i}个起句缺少局部自由识别')
            if i == 5 and len({w['name'].split('-')[0] for w in candidates}) < 2:
                raise ValueError('遗漏词预读须由不同模型的原始录音共同核对')
            if i == 6 and (not any((w['functionWord'] == 'that' for w in candidates))):
                raise ValueError('冠词复核缺少自由识别的原文用词')
            decision = prefix.clipped_decision(i, candidates, last) if i == 12 else prefix.decision(i, candidates, last)
            if fresh_first is not None:
                decision['freshFirstWord'] = fresh_first
                decision['historicalWindow'] = historical_spec
            decision['rejectedWindows'] = rejected
            times.append(decision['at'])
            decisions.append(decision)
        if any((a + 0.5 >= b for a, b in zip(times, times[1:]))):
            raise ValueError('预读时间未严格递增')
        expected = ['here', 'in', 'the', 'power', 'of', 'christ', 'ill', 'stand']
        ending = []
        for t in transcripts:
            words = [
                w
                for s in t['segments']
                for w in s.get('words', [])
                if w['start'] >= flat[-1]['scoreAnchor'] and clean(w['word'], 'en')
            ]
            tokens = [clean(w['word'], 'en') for w in words]
            hits = [
                words[j : j + len(expected)]
                for j in range(len(tokens) - len(expected) + 1)
                if tokens[j : j + len(expected)] == expected
            ]
            if len(hits) != 2 or any((any((w['end'] - w['start'] < 0.04 for w in h)) for h in hits)):
                raise ValueError('最后一行的完整重复未获两份原始转写确认')
            ending.append(hits)
        if (
            clean(prepare.read_text(R / 'data' / song['source']).splitlines()[76], 'en')
            != 'hereinthepowrofchristillstand'
        ):
            raise ValueError('重复的末行与当前原文不符')
        review = {
            'version': 4,
            'indices': list(range(1, 16)),
            'policy': 'earliest source-bound reading previews from independent original-audio decodes; sung onsets are not claimed',
            'exceptions': 'ev’ry/pow’r normalize to every/power. One gift is not recognized: both windows must identify This and the next three original words, while the uncertain word remains explicitly unverified. That/the requires an exact that witness. One clipped No uses the crop boundary only as an early preview and requires independent confirmation of its word end; no clipped onset is claimed.',
        }
        ev.update(
            performanceOrder=definitions,
            initialReadingView={'version': 1, 'region': definitions[0]['region'], 'at': 0, 'lyricOnsetMeasured': False},
            transcriptMatching={
                'algorithmVersion': 12,
                'previousLinePreview': review,
                'profile': profile,
                'independentTranscripts': transcripts,
                'coverage': coverage,
                'boundaryDecisions': decisions,
                'endingRepeat': ending,
            },
            choirScore=musical,
        )
        export_result(song, R, cache, dest, ev, started, source, definitions, times, [0])
        return (song['songId'], 'ready')
    except (ValueError, FileNotFoundError, KeyError, TypeError, IndexError, StopIteration) as error:
        # A failed recheck must not leave an earlier candidate eligible for release.
        (cache / 'ready' / (song['source'] + '.json')).unlink(missing_ok=True)
        ev.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3))
        score.c.batch.save(dest, ev)
        return (song['songId'], 'needs-calibration: ' + str(error))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--model-dir', type=Path, default=R / 'sync-drafts/models')
    args = parser.parse_args()
    profiles = json.loads((R / 'tools/sync-choir-score-preview-profiles.json').read_text())
    for song in score.c.batch.inventory():
        if song['songId'] in args.songs and not song['existing'] and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.model_dir), flush=True)
