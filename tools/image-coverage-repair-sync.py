"""Repair source-pinned omissions in earlier numbered-score image timelines."""

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_align import export_result
from sync_batch_score import note_rows
from sync_batch_sources import image_source
from sync_batch_wait_score import sha
from sync_batch_audio import features, repeated_verses
from sync_batch_cqt import cqt_features
from sync_batch_score import match_template
from sync_batch_sectional import check_match, four_path_time

spec = importlib.util.spec_from_file_location('complete_image', ROOT / 'tools/image-complete-repeat-sync.py')
complete = importlib.util.module_from_spec(spec)
spec.loader.exec_module(complete)
batch = complete.batch


def optical_cycles(audio, rows, count, cache, diagnostic):
    rejected = diagnostic.get('rejectedCounts', [])
    if (
        len(rows[0]['notes']) < 4
        or rows[0]['notes'][0] == 0
        or len(set(rows[0]['notes'][:4])) != 1
        or len(rejected) != 1
        or rejected[0].get('reason') != '整曲两种节奏路径的完整段落不一致'
    ):
        raise ValueError('仅重复同音开头允许独立音频特征复核')
    previous = rejected[0].get('fullTemplates', [])
    if (
        len(previous) != 2
        or previous[0]['best']['key'] != previous[1]['best']['key']
        or any(
            round(abs(a - b), 3) > 0.5
            for index, (a, b) in enumerate(zip(previous[0]['best']['starts'], previous[1]['best']['starts']))
            if index % len(rows)
        )
    ):
        raise ValueError('空间路径冲突不局限于同音首行')
    music = repeated_verses(audio, count, cache)
    period = music['period']
    representations = {'stft': features(audio, cache), 'cqt': cqt_features(audio, cache)[0]}
    full = {}
    for name, (times, chroma) in representations.items():
        match = match_template(
            rows * count,
            chroma,
            times,
            period * count,
            music['initialOnsetEstimate'],
            'rhythmWeights',
            onset_bounds=[0, period * 0.75],
        )
        check_match(match, period * count, previous[0]['best']['key'])
        full[name] = match
    if (
        any(len(match['best']['starts']) != count * len(rows) for match in full.values())
        or MP3(audio).info.length - min(match['best']['starts'][0] + match['best']['span'] for match in full.values())
        >= period * 0.75
    ):
        raise ValueError('独立音频特征未覆盖完整反复')
    local, measured = [], []
    for verse in range(count):
        index = verse * len(rows)
        estimate = min(match['best']['starts'][index] for match in full.values())
        matches = {}
        for name, (times, chroma) in representations.items():
            match = match_template(
                rows,
                chroma,
                times,
                period,
                estimate,
                'rhythmWeights',
                onset_bounds=[max(0, estimate - 2), estimate + 2],
            )
            check_match(match, period, full[name]['best']['key'])
            matches[name] = match
        positions = [
            four_path_time(
                [
                    *[match['best']['starts'][index + row] for match in full.values()],
                    *[match['best']['starts'][row] for match in matches.values()],
                ]
            )
            for row in range(len(rows))
        ]
        if verse and any(
            round(abs(at - (measured[row] + music['shifts'][verse])), 3) > 0.5 for row, at in enumerate(positions)
        ):
            raise ValueError('独立音频特征与实际音乐反复相位冲突')
        measured.extend(positions)
        local.append(matches)
    return {
        'count': count,
        'music': music,
        'spatialDiagnostic': diagnostic,
        'fullOptical': full,
        'localOptical': local,
        'measuredTimes': measured,
        'instrumentalTailCycles': 0,
    }


def reviewed_body(rows, profile):
    if [row['notes'] for row in rows] != profile['rawNotes'] or len(rows) != len(profile['bodyRhythms']):
        raise ValueError('原始识别音符与逐音复核不符')
    result = []
    for index, (row, rhythms) in enumerate(zip(rows, profile['bodyRhythms'])):
        count = len(rhythms)
        omitted = row['notes'][count:]
        if (
            not count
            or count > len(row['notes'])
            or any(type(w) not in (int, float) or not math.isfinite(w) or w <= 0 for w in rhythms)
            or omitted
            and (
                index != len(rows) - 1
                or omitted != [6, 1]
                or profile['review']['trailingAmen']
                != '最后两音是末尾阿们，不属于每段正文；只在重复模板中排除，原图和全部歌词区域保留。'
            )
        ):
            raise ValueError('正文模板不得删改音符，明确的末尾阿们除外')
        result.append(
            {
                **row,
                **{
                    key: row[key][:count]
                    for key in [
                        'notes',
                        'pitchOffsets',
                        'x',
                        'spatialWeights',
                        'ocrRhythmWeights',
                        'opticalDiagnostics',
                    ]
                },
                'rhythmWeights': rhythms,
            }
        )
    return result


def generate(song, profile, cache, prepare=False):
    started = time.monotonic()
    dest = cache / 'score-alignment' / (song['source'] + '.json')
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
            or song['type'] != 'image'
            or profile['sourceHash'] != song['sha256']
            or profile['tracks'] != song['tracks']
            or profile['referenceFile'] != song['reference']
            or profile['previewSeconds'] != 1.5
        ):
            raise ValueError('覆盖修复必须绑定原图及全部录音')
        for track in song['tracks']:
            if sha(ROOT / 'data' / track['file']) != track['sha256']:
                raise ValueError('原录音已变化')
        prior = profile['prior']
        relative = Path(prior['file'])
        if relative.is_absolute() or '..' in relative.parts or sha(ROOT / relative) != prior['sha256']:
            raise ValueError('修复前原数据记录不符')
        old = json.loads((ROOT / relative).read_text())
        if old['status'] != 'reviewed' or old['source']['sha256'] != song['sha256']:
            raise ValueError('修复前记录不是同一原图的已发布数据')
        if any(profile['regions'].get(key) != value for key, value in old['regions'].items()):
            raise ValueError('覆盖修复须保留已有阅读区域的位置')
        if prepare:
            batch.recognize_image((song, str(cache), 1))
        recognition = json.loads((cache / 'ocr' / (song['source'] + '.json')).read_text())
        normalized = {key: value for key, value in recognition.items() if key != 'elapsedSeconds'}
        digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if recognition['recognizerVersion'] != 5 or digest != profile['recognitionSha256']:
            raise ValueError('完整谱面须由固定新版识别重新核对')
        source = image_source(recognition)
        if set(source['regions']) != set(profile['regions']) or len(source['verses']) != profile['printedVerses']:
            raise ValueError('完整印刷阅读区域与复核记录不符')
        source['regions'] = profile['regions']
        raw = note_rows(ROOT / 'data' / song['source'], recognition, cache)
        rows = reviewed_body(raw, profile)
        recordings = {}
        for track in song['tracks']:
            try:
                detail = complete.measure(ROOT / 'data' / track['file'], rows, profile['printedVerses'], False, cache)
            except complete.ScoreMismatch as error:
                review = profile.get('dualFeatureOptical')
                if not review or review.get('version') != 1 or not review.get('reason', '').strip():
                    raise
                detail = optical_cycles(
                    ROOT / 'data' / track['file'], rows, profile['printedVerses'], cache, error.evidence
                )
            recordings[track['file']] = detail
        definitions = [item for verse in source['verses'] for item in verse]
        returns, cursor = [], 0
        for verse in source['verses']:
            if cursor:
                returns.append(cursor)
            cursor += len(verse)
        times = {
            name: [round(max(0, at - profile['previewSeconds']), 2) for at in detail['measuredTimes']]
            for name, detail in recordings.items()
        }
        evidence.update(
            performanceOrder=definitions,
            returnIndices=returns,
            independentTimelines={
                name: {'definitions': definitions, 'times': positions, 'returnIndices': returns}
                for name, positions in times.items()
                if name != song['reference']
            },
            scoreMatching={
                'algorithmVersion': 7,
                'profile': profile,
                'rawRows': raw,
                'rows': rows,
                'recordings': recordings,
                'music': recordings[song['reference']]['music'],
                'completeImageCycles': {
                    'version': 1,
                    'printedVerses': profile['printedVerses'],
                    'systemsPerVerse': len(rows),
                    'previewSeconds': profile['previewSeconds'],
                    'bodyReview': profile['review'],
                    'matchingMethod': (
                        'dual-feature-optical' if profile.get('dualFeatureOptical') else 'spatial-and-printed-rhythm'
                    ),
                    'instrumentalTailPolicy': 'retain final printed reading',
                },
            },
        )
        export_result(
            song, ROOT, cache, dest, evidence, started, source, definitions, times[song['reference']], returns
        )
        return song['songId'], 'ready'
    except (ValueError, KeyError, TypeError, IndexError, FileNotFoundError) as error:
        ready.unlink(missing_ok=True)
        evidence.update(
            status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3)
        )
        if hasattr(error, 'evidence'):
            evidence['scoreMatchingFailure'] = error.evidence
        batch.save(dest, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    profiles = json.loads((ROOT / 'tools/sync-image-coverage-profiles.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.prepare), flush=True)
