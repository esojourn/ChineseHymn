"""Repair image timelines that skipped an opening verse in repeated music."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_align import export_result
from sync_batch_audio import features, repeated_verses
from sync_batch_score import ScoreMismatch, match_template, note_rows
from sync_batch_sectional import check_match, four_path_time
from sync_batch_sources import image_source

spec = importlib.util.spec_from_file_location('batch', ROOT / 'tools/batch-sync.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def phase_time(local, expected):
    values = [*local, expected]
    if (len(local) != 2 or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
            or round(max(values) - min(values), 3) > .5):
        raise ValueError('逐段谱面起点与独立音乐反复相位不一致')
    return round(sum(values) / 3, 2)


def measure(audio, rows, printed_count, allow_extra, cache):
    times, chroma = features(audio, cache)
    duration = MP3(audio).info.length
    candidates, rejected = [], []
    for count in range(printed_count, printed_count + (2 if allow_extra else 1)):
        detail = {'count': count}
        try:
            music = repeated_verses(audio, count, cache)
            period = music['period']
            matches = [match_template(rows * count, chroma, times, period * count,
                                      music['initialOnsetEstimate'], weight,
                                      onset_bounds=[0, period * .75])
                       for weight in ['spatialWeights', 'rhythmWeights']]
            detail.update(music=music, fullTemplates=matches)
            check_match(matches[0], period * count)
            check_match(matches[1], period * count, matches[0]['best']['key'])
            one, two = [match['best'] for match in matches]
            if (len(one['starts']) != len(rows) * count or len(two['starts']) != len(one['starts']) or
                    any(round(abs(a - b), 3) > .5 for a, b in zip(one['starts'], two['starts']))):
                raise ValueError('整曲两种节奏路径的完整段落不一致')
            if (max(one['starts'][0], two['starts'][0]) >= .75 * period or
                    duration - min(one['starts'][0] + one['span'], two['starts'][0] + two['span']) >= .75 * period):
                raise ValueError('首尾仍可容纳完整乐段，不能省略实际反复')
            detail['countConfirmed'] = True
            candidates.append(detail)
        except ValueError as error:
            detail['reason'] = str(error)
            rejected.append(detail)
    if len(candidates) != 1:
        raise ScoreMismatch('完整反复次数未能唯一确认', {'candidates': candidates, 'rejectedCounts': rejected})
    detail = candidates[0]
    detail['rejectedCounts'] = rejected
    count, music = detail['count'], detail['music']
    one, two = [match['best'] for match in detail['fullTemplates']]
    anchor = (one['starts'][0] + two['starts'][0]) / 2
    measured, first_positions = [], []
    detail['localVerses'] = []
    for verse in range(count):
        start = anchor + music['shifts'][verse]
        local = [match_template(rows, chroma, times, music['period'], start, weight,
                                onset_bounds=[max(0, start - 3), start + 3])
                 for weight in ['spatialWeights', 'rhythmWeights']]
        check_match(local[0], music['period'], one['key'])
        check_match(local[1], music['period'], two['key'])
        positions = []
        for index in range(len(rows)):
            estimates = [match['best']['starts'][index] for match in local]
            if verse == 0:
                at = four_path_time([one['starts'][index], two['starts'][index], *estimates])
                first_positions.append(at)
            else:
                at = phase_time(estimates, first_positions[index] + music['shifts'][verse])
            positions.append(at)
        detail['localVerses'].append({'index': verse, 'templates': local, 'measuredTimes': positions})
        measured.extend(positions)
    if any(a + .5 >= b for a, b in zip(measured, measured[1:])):
        raise ValueError('完整曲谱阅读位置未递增')
    detail['measuredTimes'] = measured
    detail['instrumentalTailCycles'] = count - printed_count
    return detail


def generate(song, profile, cache, prepare=False):
    started = time.monotonic()
    dest = cache / 'score-alignment' / (song['source'] + '.json')
    (cache / 'ready' / (song['source'] + '.json')).unlink(missing_ok=True)
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks']}
    try:
        keys = {'version', 'sourceHash', 'tracks', 'referenceFile', 'priorSidecar', 'regions',
                'recognitionSha256', 'recognizerVersion', 'expectedNotes', 'printedVerses',
                'systemsPerVerse', 'previewSeconds', 'allowAdditionalInstrumentalCycle', 'review'}
        if (set(profile) != keys or type(profile['version']) is not int or profile['version'] != 1 or
                song['type'] != 'image' or song['sha256'] != profile['sourceHash'] or
                song['tracks'] != profile['tracks'] or song['reference'] != profile['referenceFile'] or
                profile['previewSeconds'] != 1.5 or not isinstance(profile['review'], str) or not profile['review'].strip()):
            raise ValueError('完整图片反复配置与当前素材不符')
        prior = profile['priorSidecar']
        prior_path = Path(prior['file'])
        if (set(prior) != {'file', 'sha256'} or prior_path.is_absolute() or '..' in prior_path.parts or
                sha(ROOT / prior_path) != prior['sha256']):
            raise ValueError('用户标记的原同步数据或哈希不符')
        rejected = json.loads((ROOT / prior_path).read_text())
        if rejected['status'] != 'error' or rejected['source']['sha256'] != song['sha256']:
            raise ValueError('修复必须保留用户标记的原始错误记录')
        for track in song['tracks']:
            if sha(ROOT / 'data' / track['file']) != track['sha256']:
                raise ValueError('原录音已变化')
        recognition_path = cache / 'ocr' / (song['source'] + '.json')
        if prepare:
            batch.recognize_image((song, str(cache), 1))
        recognition = json.loads(recognition_path.read_text())
        normalized = {key: value for key, value in recognition.items() if key != 'elapsedSeconds'}
        digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if (digest != profile['recognitionSha256'] or recognition['sha256'] != song['sha256'] or
                recognition['recognizerVersion'] != profile['recognizerVersion']):
            raise ValueError('完整谱面识别与复核记录不一致')
        source = image_source(recognition)
        count, size = profile['printedVerses'], profile['systemsPerVerse']
        if ([len(verse) for verse in source['verses']] != [size] * count or
                set(source['regions']) != set(profile['regions']) or rejected['regions'] != profile['regions']):
            raise ValueError('全部印刷歌词或原有阅读区域已变化')
        source['regions'] = profile['regions']
        rows = note_rows(ROOT / 'data' / song['source'], recognition, cache)
        if len(rows) != size or [row['notes'] for row in rows] != profile['expectedNotes']:
            raise ValueError('完整谱面旋律与复核音符不一致')
        extra = profile['allowAdditionalInstrumentalCycle']
        if (not isinstance(extra, list) or any(name not in [track['file'] for track in song['tracks']] or
                '-领唱' in name for name in extra)):
            raise ValueError('额外器乐反复只能明确绑定伴奏录音')
        recordings = {}
        for track in song['tracks']:
            recordings[track['file']] = measure(ROOT / 'data' / track['file'], rows, count,
                                                track['file'] in extra, cache)
        definitions = [item for verse in source['verses'] for item in verse]
        timelines = {name: [round(max(0, at - profile['previewSeconds']), 2)
                           for at in detail['measuredTimes'][:len(definitions)]]
                     for name, detail in recordings.items()}
        reference = song['reference']
        evidence.update(performanceOrder=definitions, returnIndices=list(range(size, len(definitions), size)),
            independentTimelines={name: {'definitions': definitions, 'times': times,
                                          'returnIndices': list(range(size, len(definitions), size))}
                                  for name, times in timelines.items() if name != reference},
            scoreMatching={'algorithmVersion': 5, 'profile': profile, 'rows': rows,
                'music': recordings[reference]['music'], 'recordings': recordings,
                'completeImageCycles': {'version': 1, 'printedVerses': count, 'systemsPerVerse': size,
                    'previewSeconds': profile['previewSeconds'],
                    'instrumentalTailPolicy': 'hold final printed reading after all printed verses'}})
        export_result(song, ROOT, cache, dest, evidence, started, source, definitions, timelines[reference],
                      list(range(0, len(definitions), size)))
        return song['songId'], 'ready'
    except (ValueError, FileNotFoundError, KeyError, TypeError, IndexError, StopIteration) as error:
        (cache / 'ready' / (song['source'] + '.json')).unlink(missing_ok=True)
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3))
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
    profiles = json.loads((ROOT / 'tools/sync-image-complete-profiles.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.prepare), flush=True)
