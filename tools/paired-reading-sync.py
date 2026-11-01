"""Generate bilingual reading previews from original practice notes and pinned acoustic observations.

Default reconstruction uses archived source-bound raw observations. --measure reruns the
same two contextual windows with the pinned local model; it never supplies manual cue times.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import numpy as np
from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_align import align_windows, export_result
from sync_batch_audio import features
from sync_batch_paired_reading import paired_source, timeline
from sync_batch_score import match_template
from sync_batch_sectional import check_match
from sync_batch_sources import prepare
from sync_batch_wait_score import sha

spec = importlib.util.spec_from_file_location('paired_batch', ROOT / 'tools/batch-sync.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def pinned_json(descriptor, base=ROOT):
    path = base / descriptor['file']
    if sha(path) != descriptor['sha256']:
        raise ValueError('原始参考或声学记录哈希已变化：' + str(path))
    return json.loads(path.read_text())


def practice_rows(practice, profile):
    notes = practice['practice']['notes']
    parts = [{'bounds': profile['instrumentalBeats'], 'instrumental': True}]
    cursor = 0
    for block in profile['blockOrder']:
        if block == 3:
            parts.append({'bounds': profile['instrumentalBeats'], 'instrumental': True})
        edges = profile['chorusBeats'] if block in (1, 4) else profile['verseBeats']
        if len(edges) != len(profile['blocks'][block]['english']) + 1:
            raise ValueError('原出版方旋律取样未覆盖全部阅读行')
        for left, right in zip(edges, edges[1:]):
            parts.append({'bounds': [left, right], 'reading': cursor})
            cursor += 1
    rows = []
    for part in parts:
        left, right = part['bounds']
        if not 0 <= left < right <= 133:
            raise ValueError('原始练习旋律取样越界')
        pitches = []
        for at in np.arange(left, right, 0.125):
            active = [pitch for onset, length, pitch, index in notes if onset <= at < onset + length]
            pitches.append(max(active) if active else -1)
        rows.append(
            {
                'notes': [0 if pitch < 0 else 1 for pitch in pitches],
                'pitchOffsets': [max(0, pitch) % 12 for pitch in pitches],
                'spatialWeights': [0.125] * len(pitches),
            }
        )
    return rows, parts


def measured_record(profile, definitions, estimates, audio, cache):
    import importlib.metadata
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    from faster_whisper.tokenizer import Tokenizer

    parameters = profile['acousticParameters']
    model_path = ROOT / 'sync-drafts/models/medium'
    if (
        sha(model_path / 'model.bin') != parameters['modelSha256']
        or sha(model_path / 'tokenizer.json') != parameters['tokenizerSha256']
        or importlib.metadata.version('faster-whisper') != parameters['fasterWhisperVersion']
        or importlib.metadata.version('ctranslate2') != parameters['ctranslate2Version']
    ):
        raise ValueError('双窗口固定的识别器版本或原模型哈希不一致')
    base = {'version': 1, 'parameters': parameters, 'definitions': definitions, 'estimates': estimates}
    fingerprint = hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest()
    checkpoint = cache / 'paired-acoustic' / (fingerprint + '.json')
    old = json.loads(checkpoint.read_text()) if checkpoint.exists() else []
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
    tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual, task='transcribe', language='en')
    observations = align_windows(
        model,
        tokenizer,
        decode_audio(str(audio), sampling_rate=16000),
        definitions,
        estimates,
        'en',
        resume=old,
        on_chunk=lambda rows: batch.save(checkpoint, rows),
        window_bounds=parameters['windowBounds'],
    )
    return {**base, 'observations': observations}


def generate(song, profile, cache, measure=False):
    started = time.monotonic()
    dest = cache / 'transcript-alignment' / (song['source'] + '.json')
    ready = cache / 'ready' / (song['source'] + '.json')
    ready.unlink(missing_ok=True)
    evidence = {
        'songId': song['songId'],
        'source': song['source'],
        'sourceHash': song['sha256'],
        'trackHashes': song['tracks'],
    }
    try:
        if (
            profile['version'] != 1
            or song['type'] != 'text'
            or profile['sourceHash'] != song['sha256']
            or profile['tracks'] != song['tracks']
            or profile['audioFile'] != song['reference']
            or len(song['tracks']) != 1
            or profile['previewSeconds'] != 1.0
        ):
            raise ValueError('双语配置未绑定原文及唯一完整录音')
        audio = ROOT / 'data' / song['reference']
        if sha(audio) != song['tracks'][0]['sha256'] or sha(ROOT / 'data' / song['source']) != song['sha256']:
            raise ValueError('原文或原录音发生变化')
        lines = prepare.read_text(ROOT / 'data' / song['source']).split('\n')
        source, definitions, pairs, adjacent, returns, discontinuous = paired_source(lines, profile)
        practice = pinned_json(profile['practice'], cache)
        rows, parts = practice_rows(practice, profile)
        times, chroma = features(audio, cache)
        duration = MP3(audio).info.length
        match = match_template(rows, chroma, times, duration, 0, 'spatialWeights')
        evidence['scoreMatching'] = {
            'mode': 'paired-reading-window-estimates',
            'profile': profile,
            'practiceScore': match,
            'parts': parts,
            'scoreReadingPreviews': {
                'version': 2,
                'indices': adjacent,
                'policy': 'paired original lines; early reading guides, not exact sung onsets',
            },
            'limitations': 'Practice melody only establishes ordered sampling windows. '
            'Constant-Q and harmonic diagnostics did not pass and are not accepted musical paths.',
        }
        check_match(match, duration)
        estimates = [match['best']['starts'][i] for i, part in enumerate(parts) if 'reading' in part]
        record = (
            measured_record(profile, definitions, estimates, audio, cache)
            if measure
            else pinned_json(profile['acousticRecord'])
        )
        if (
            record['version'] != 1
            or record['parameters'] != profile['acousticParameters']
            or record['parameters']['sourceHash'] != song['sha256']
            or record['parameters']['audioHash'] != song['tracks'][0]['sha256']
            or record['definitions'] != definitions
            or len(record['estimates']) != len(estimates)
            or any(abs(a - b) > 0.001 for a, b in zip(record['estimates'], estimates))
        ):
            raise ValueError('原始声学记录与重新构建的完整原文、旋律窗口不一致')
        transcripts = [pinned_json(item) for item in profile['fullTranscripts']]
        if len(transcripts) != 2 or len({item['parameters']['model'] for item in transcripts}) != 2:
            raise ValueError('非相邻跳转需要两个独立的完整转写模型')
        for transcript in transcripts:
            parameters = transcript['parameters']
            if (
                transcript['sha256'] != song['tracks'][0]['sha256']
                or parameters['audio'] != song['tracks'][0]['sha256']
                or parameters['language'] != 'en'
                or parameters['prompt'] is not None
                or not parameters['wholeAudio']
                or parameters['conditionOnPreviousText']
                or parameters['vadFilter']
            ):
                raise ValueError('完整转写未绑定原录音或并非无提示完整识别')
        cue_times, decisions = timeline(
            definitions, estimates, record['observations'], profile, transcripts, discontinuous, duration
        )
        evidence.update(
            performanceOrder=definitions,
            sourceLinePairs=pairs,
            returnIndices=returns,
            initialReadingView={'version': 1, 'region': definitions[0]['region'], 'at': 0, 'lyricOnsetMeasured': False},
            transcriptMatching={
                'mode': 'paired-reading-previews',
                'acousticRecord': record,
                'fullTranscriptInputs': profile['fullTranscripts'],
                **decisions,
            },
            reconstruction={
                'acousticInput': 'fresh canonical windows' if measure else 'pinned original raw windows',
                'practiceNotesRebuilt': True,
                'scoreFeaturesRecomputedOrHashCached': True,
            },
        )
        export_result(song, ROOT, cache, dest, evidence, started, source, definitions, cue_times, [])
        return song['songId'], 'ready'
    except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
        ready.unlink(missing_ok=True)
        evidence.update(
            status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3)
        )
        batch.save(dest, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument(
        '--measure', action='store_true', help='rerun pinned local acoustic windows instead of archived raw inputs'
    )
    args = parser.parse_args()
    profiles = json.loads((ROOT / 'tools/sync-paired-reading-profiles.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.measure), flush=True)
