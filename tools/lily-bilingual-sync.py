"""Generate reviewed reading previews for the original Korean/Mandarin performance."""

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import time
from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_align import export_result
from sync_batch_audio import features
from sync_batch_lily_bilingual import (
    chinese_preview,
    final_line_tail,
    korean_final_verse,
    korean_opening,
    score_reading_times,
    sounded_word,
)
from sync_batch_sectional import performance_times
from sync_batch_sources import clean, prepare as source_prepare, text_source

spec = importlib.util.spec_from_file_location('sectional', ROOT / 'tools/sectional-midi-sync.py')
sectional = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sectional)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rebuild_korean_window(song, model_dir, bounds, destination):
    import ctranslate2
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    audio_path = ROOT / 'data' / song['reference']
    audio = decode_audio(str(audio_path), sampling_rate=16000)
    model = WhisperModel(
        str(model_dir / 'large-v3-turbo'), device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True
    )
    ctranslate2.set_random_seed(42)
    lo, hi = bounds
    segments, _ = model.transcribe(
        audio[lo * 16000 : hi * 16000],
        language='ko',
        beam_size=5,
        temperature=0,
        word_timestamps=True,
        condition_on_previous_text=False,
        vad_filter=False,
    )
    rows = [
        {
            'text': segment.text,
            'words': [
                {'word': word.word, 'start': word.start + lo, 'end': word.end + lo, 'probability': word.probability}
                for word in segment.words or []
            ],
        }
        for segment in segments
    ]
    sectional.batch.save(
        destination,
        {
            'audioSha256': sha(audio_path),
            'language': 'ko',
            'model': 'large-v3-turbo/int8',
            'seed': 42,
            'bounds': bounds,
            'prompt': None,
            'segments': rows,
            'diagnosticOnly': True,
        },
    )


def load_transcripts(song, profile, cache, model_dir, prepare=False):
    models = profile['models']
    if set(models) != {'large-v3', 'large-v3-turbo'}:
        raise ValueError('必须固定两种独立识别模型')
    repositories = {
        'large-v3': 'Systran/faster-whisper-large-v3',
        'large-v3-turbo': 'dropbox-dash/faster-whisper-large-v3-turbo',
    }
    for name, model in models.items():
        if (
            set(model) != {'repository', 'revision', 'sha256', 'tokenizerSha256'}
            or model['repository'] != repositories[name]
            or not re.fullmatch('[0-9a-f]{40}', model['revision'])
        ):
            raise ValueError('识别模型版本不完整')
        for file, field in [('model.bin', 'sha256'), ('tokenizer.json', 'tokenizerSha256')]:
            if sha(model_dir / name / file) != model[field]:
                raise ValueError('识别模型或词表已改变')
    if len(profile['transcripts']) != 5:
        raise ValueError('需要三份完整转写与两份韩语局部转写')
    result = []
    for index, item in enumerate(profile['transcripts']):
        path = Path(item['file'])
        if set(item) != {'file', 'sha256'} or path.is_absolute() or '..' in path.parts:
            raise ValueError('固定的原始转写路径无效')
        if prepare and index >= 3 and not (cache / path).exists():
            rebuild_korean_window(song, model_dir, [[15, 65], [260, 325]][index - 3], cache / path)
        if sha(cache / path) != item['sha256']:
            raise ValueError('固定的原始转写路径或哈希不符')
        result.append(json.loads((cache / path).read_text()))
    for transcript, language, name in zip(result[:3], ['zh', 'zh', 'ko'], ['large-v3', 'large-v3-turbo', 'large-v3']):
        model = models[name]
        expected = {
            'model': model['sha256'],
            'tokenizer': model['tokenizerSha256'],
            'audio': song['tracks'][0]['sha256'],
            'language': language,
            'beamSize': 5,
            'temperature': 0,
            'conditionOnPreviousText': False,
            'vadFilter': False,
            'prompt': None,
            'wholeAudio': True,
        }
        if (
            transcript.get('sha256') != song['tracks'][0]['sha256']
            or transcript.get('language') != language
            or transcript.get('parameters') != expected
            or type(transcript.get('duration')) not in (int, float)
            or not math.isfinite(transcript['duration'])
            or abs(transcript['duration'] - MP3(ROOT / 'data' / song['reference']).info.length) > 0.1
        ):
            raise ValueError('完整转写未绑定当前录音和无提示识别参数')
    for transcript, bounds in zip(result[3:], [[15, 65], [260, 325]]):
        if (
            transcript.get('audioSha256') != song['tracks'][0]['sha256']
            or transcript.get('language') != 'ko'
            or transcript.get('model') != 'large-v3-turbo/int8'
            or transcript.get('seed') != 42
            or transcript.get('bounds') != bounds
            or transcript.get('prompt', 1) is not None
        ):
            raise ValueError('韩语局部转写未绑定当前原录音、模型及窗口')
    return result


def korean_final_line(transcripts, anchor):
    witnesses = []
    for transcript in transcripts:
        matches = [
            segment
            for segment in transcript['segments']
            if '주를' in segment['text']
            and '뵙기' in segment['text']
            and any(anchor + 5 <= word['start'] <= anchor + 15 for word in segment.get('words', []))
        ]
        if len(matches) != 1:
            raise ValueError('韩语末节未独立确认见主面的原文对应')
        word = next((word for word in matches[0]['words'] if '뵙기' in word['word']), None)
        if not word or not sounded_word(word):
            raise ValueError('韩语见主面的词语缺少有效音频')
        witnesses.append(word)
    if abs(witnesses[0]['start'] - witnesses[1]['start']) > 0.5:
        raise ValueError('韩语见主面两次识别位置不一致')
    return witnesses


def generate(song, profile, cache, model_dir, prepare=False):
    started = time.monotonic()
    dest = cache / 'transcript-alignment' / (song['source'] + '.json')
    (cache / 'ready' / (song['source'] + '.json')).unlink(missing_ok=True)
    evidence = {
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
            'score',
            'sampling',
            'readingSectionReview',
            'transcripts',
            'models',
            'review',
        }
        if (
            set(profile) != keys
            or type(profile['version']) is not int
            or profile['version'] != 1
            or song['type'] != 'text'
            or song['sha256'] != profile['sourceHash']
            or song['tracks'] != profile['tracks']
            or len(song['tracks']) != 1
            or song['reference'] != profile['audioFile']
            or sha(ROOT / 'data' / song['reference']) != song['tracks'][0]['sha256']
            or not isinstance(profile['review'], str)
            or not profile['review'].strip()
        ):
            raise ValueError('歌词、录音或分段配置已变化')
        transcripts = load_transcripts(song, profile, cache, model_dir, prepare)
        source = text_source(ROOT / 'data' / song['source'], 'zh')
        original = [item for verse in source['verses'] for item in verse]
        if [len(verse) for verse in source['verses']] != [2, 2, 2] or [item['region'] for item in original] != [
            f'r{i}' for i in range(1, 7)
        ]:
            raise ValueError('歌词正文必须保持三节各两个阅读区域')
        lines = source_prepare.read_text(ROOT / 'data' / song['source']).splitlines()
        end = source['regions']['r2']['endLine']
        if '晨星' not in lines[end - 1] or '百合花' not in lines[end - 1] or '万人中' not in lines[end]:
            raise ValueError('韩语副歌对应的两行原文已变化')
        source['regions']['refrain'] = {'kind': 'text', 'startLine': end - 1, 'endLine': end}
        refrain = {'region': 'refrain', 'text': lines[end - 1] + ' ' + lines[end], 'label': original[0]['label']}
        definitions = [original[0], refrain, *original]
        melody, optical, reference = sectional.reference_material(profile['score'], cache, prepare)
        if reference['symbolicRows']['editCost'] != 0:
            raise ValueError('完整原谱与独立记录的旋律不一致')
        sampling = profile['sampling']
        if (
            set(sampling) != {'durationEstimate', 'onsetEstimate'}
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in sampling.values())
            or sampling['durationEstimate'] <= 0
            or sampling['onsetEstimate'] < 0
        ):
            raise ValueError('完整乐曲搜索范围无效')
        target = features(ROOT / 'data' / song['reference'], cache)
        performance = {}
        try:
            performance_times(
                melody,
                optical,
                target,
                sampling['durationEstimate'],
                sampling['onsetEstimate'],
                reference['symbolicRows']['semitoneShift'],
                sectional.midi.match_template,
                performance,
                reading_sections=profile['readingSectionReview'],
                previous_context=True,
                previous_context_depth=2,
                edge_verses=[[0, 1], [2, 3], [4, 5], [6, 7]],
            )
        except ValueError as error:
            if str(error) != '录音首尾仍可容纳完整乐段，需核对演唱结构':
                raise
        else:
            raise ValueError('该曲须保留末句延长与独立录音尾句复核')
        measured = score_reading_times(performance)
        full_one = performance['fullMelody']['best']
        full_two = performance['fullOptical']['best']
        shortest = min(performance['completeVerseSpans'])
        if min(full_one['starts'][0], full_two['starts'][0]) >= 0.75 * shortest:
            raise ValueError('首段之前可能容纳完整乐段')
        score_end = min(full_one['starts'][0] + full_one['span'], full_two['starts'][0] + full_two['span'])
        tail = final_line_tail(original[-1], transcripts[:2], shortest, score_end)
        opening = korean_opening(measured[1], [transcripts[2], transcripts[3]])
        final_verse = korean_final_verse(measured[6], [transcripts[2], transcripts[4]])
        if not clean(original[-1]['text'], 'zh').startswith('那日在天见主面'):
            raise ValueError('韩语末节与原文见主面的阅读区域不符')
        final_words = korean_final_line([transcripts[2], transcripts[4]], measured[7])
        chinese = [
            chinese_preview(original[region], prefix, measured[index], transcripts[:2])
            for index, region, prefix in [(2, 0, '主耶稣是我'), (3, 1, '悲伤'), (4, 2, '主带走'), (5, 3, '纵然')]
        ]
        times = [0, opening['at'], *(item['at'] for item in chinese), final_verse['at'], measured[7]]
        if any(a + 0.5 >= b for a, b in zip(times, times[1:])):
            raise ValueError('实际阅读顺序或预读位置不一致')
        review = {
            'version': 6,
            'indices': [1, 3, 4, 5, 6, 7],
            'policy': 'early source-bound reading previews; sung onsets are not claimed',
        }
        evidence.update(
            performanceOrder=definitions,
            returnIndices=[2],
            initialReadingView={'version': 1, 'region': 'r1', 'at': 0, 'lyricOnsetMeasured': False},
            midiMatching={
                'mode': 'text-lily-bilingual',
                'profile': profile['score'],
                'reference': reference,
                'performance': performance,
                'scoreTimes': measured,
                'extendedFinalLine': tail,
            },
            transcriptMatching={
                'algorithmVersion': 13,
                'profile': profile,
                'independentTranscripts': transcripts,
                'previousLinePreview': review,
                'koreanOpening': opening,
                'chinesePreviews': chinese,
                'koreanFinalVerse': final_verse,
                'koreanFinalLine': final_words,
                'extendedFinalLine': tail,
            },
        )
        export_result(song, ROOT, cache, dest, evidence, started, source, definitions, times, [0])
        return song['songId'], 'ready'
    except (ValueError, FileNotFoundError, KeyError, TypeError, IndexError, StopIteration) as error:
        (cache / 'ready' / (song['source'] + '.json')).unlink(missing_ok=True)
        evidence.update(
            status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3)
        )
        sectional.batch.save(dest, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--model-dir', type=Path, default=ROOT / 'sync-drafts/models')
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    profiles = json.loads((ROOT / 'tools/sync-lily-bilingual-profiles.json').read_text())
    for song in sectional.batch.inventory():
        if song['songId'] in args.songs and not song['existing'] and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.model_dir, args.prepare), flush=True)
