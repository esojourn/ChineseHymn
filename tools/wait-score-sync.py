"""Generate score-supported reading previews for the two Psalm 130 recordings."""

import argparse
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
from sync_batch_audio import features, map_recordings
from sync_batch_cqt import cqt_features
from sync_batch_sectional import check_match
from sync_batch_sources import text_source
from sync_batch_wait_score import agreement, sha, source_phrase, vector_rows

spec = importlib.util.spec_from_file_location('sectional', ROOT / 'tools/sectional-midi-sync.py')
sectional = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sectional)


def transcripts(song, profile, cache, model_dir):
    models = profile['models']
    repositories = {
        'medium': 'Systran/faster-whisper-medium',
        'large-v3': 'Systran/faster-whisper-large-v3',
        'large-v3-turbo': 'dropbox-dash/faster-whisper-large-v3-turbo',
    }
    if set(models) != set(repositories):
        raise ValueError('必须固定三种独立识别模型')
    for name, model in models.items():
        if (
            set(model) != {'repository', 'revision', 'sha256', 'tokenizerSha256'}
            or model['repository'] != repositories[name]
            or not re.fullmatch('[0-9a-f]{40}', model['revision'])
        ):
            raise ValueError('识别模型版本无效')
        for file, key in [('model.bin', 'sha256'), ('tokenizer.json', 'tokenizerSha256')]:
            if sha(model_dir / name / file) != model[key]:
                raise ValueError('固定识别模型或词表已变化')
    if len(profile['fullTranscripts']) != 2 or len(profile['localTranscripts']) != 2:
        raise ValueError('须保留两份完整转写和两份独立局部转写')
    output = []
    for specs, directory in [(profile['fullTranscripts'], cache), (profile['localTranscripts'], ROOT)]:
        group = []
        for item in specs:
            path = Path(item['file'])
            if (
                set(item) != {'file', 'sha256'}
                or path.is_absolute()
                or '..' in path.parts
                or sha(directory / path) != item['sha256']
            ):
                raise ValueError('固定转写文件或哈希不符')
            group.append(json.loads((directory / path).read_text()))
        output.append(group)
    full, local = output
    track_hash = next(track['sha256'] for track in song['tracks'] if track['file'] == song['reference'])
    duration = MP3(ROOT / 'data' / song['reference']).info.length
    for item, name in zip(full, ['medium', 'large-v3-turbo/int8']):
        if (
            item.get('model') != name
            or item.get('language') != 'zh'
            or item.get('sha256') != track_hash
            or type(item.get('duration')) not in (int, float)
            or not math.isfinite(item['duration'])
            or abs(item['duration'] - duration) > 0.1
        ):
            raise ValueError('完整转写未绑定当前领唱录音')
    turbo = models['large-v3-turbo']
    expected = {
        'model': turbo['sha256'],
        'tokenizer': turbo['tokenizerSha256'],
        'audio': track_hash,
        'language': 'zh',
        'beamSize': 5,
        'temperature': 0,
        'conditionOnPreviousText': False,
        'vadFilter': False,
        'prompt': None,
        'wholeAudio': True,
    }
    if full[1].get('parameters') != expected:
        raise ValueError('完整独立转写不是固定无提示识别')
    for item, name in zip(local, ['large-v3', 'large-v3-turbo']):
        model = models[name]
        expected_local = {
            'model': model['sha256'],
            'tokenizer': model['tokenizerSha256'],
            'audio': track_hash,
            'language': 'zh',
            'beamSize': 5,
            'temperature': 0,
            'wordTimestamps': True,
            'conditionOnPreviousText': False,
            'vadFilter': False,
            'prompt': None,
        }
        if (
            item.get('model') != name
            or item.get('audioSha256') != track_hash
            or item.get('prompt', 1) is not None
            or item.get('parameters') != expected_local
            or [window.get('bounds') for window in item.get('windows', [])] != [[180, 213], [183, 216]]
        ):
            raise ValueError('末段局部转写与原录音、模型或窗口不符')
    return full, local


def score_match(rows, representation, duration, estimate, expected_key=None):
    times, chroma = representation
    result = sectional.midi.match_template(
        rows, chroma, times, duration, estimate, 'spatialWeights', onset_bounds=[max(0, estimate - 3), estimate + 3]
    )
    check_match(result, duration, expected_key)
    return result


def lyric_witnesses(definitions, anchors, full, local):
    medium, turbo = full
    witnesses = []
    for index, fragments in [
        (0, ['在黑暗中我恳求你', '愿你侧耳听我呼求']),
        (1, ['我怎能在你', '要叫我']),
        (3, ['你要专心', '强壮胆']),
        (5, ['生命', '慈爱我灵']),
    ]:
        bounds = [anchors[index] - 3, anchors[index + 1]]
        for fragment in fragments:
            witnesses.append(
                {
                    'index': index,
                    'source': 'medium-whole',
                    **source_phrase(definitions[index], fragment, medium['segments'], bounds),
                }
            )
    for index, source, fragment, bounds in [
        (2, medium, '我要等候你', [anchors[2] + 5, anchors[2] + 15]),
        (2, turbo, '我要等候你', [anchors[2] - 3, anchors[2] + 5]),
        (4, medium, '我要等候你', [anchors[4] - 3, anchors[4] + 5]),
        (4, turbo, '我要等候你', [anchors[4] - 3, anchors[4] + 5]),
        (6, medium, '我仰望你的话语', [anchors[6] - 3, anchors[6] + 10]),
        (6, local[0]['windows'][0], '直到天明', [anchors[6] + 10, anchors[7]]),
    ]:
        witnesses.append({'index': index, **source_phrase(definitions[index], fragment, source['segments'], bounds)})
    openings, endings = [], []
    for item, window in [(local[0], 0), (local[1], 1)]:
        openings.append(
            source_phrase(
                definitions[7], '我要等候你', item['windows'][window]['segments'], [anchors[7] - 3, anchors[7] + 5]
            )
        )
    for item in local:
        endings.append(
            source_phrase(
                definitions[7], '我深信不疑', item['windows'][0]['segments'], [anchors[7] + 10, anchors[7] + 25]
            )
        )
    agreement([item['start'] for item in openings])
    agreement([item['end'] for item in openings])
    agreement([item['start'] for item in endings])
    agreement([item['end'] for item in endings])
    return {'fragments': witnesses, 'finalOpening': openings, 'finalEnding': endings}


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
            'fullTranscripts',
            'localTranscripts',
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
            or len(song['tracks']) != 2
            or song['reference'] != profile['audioFile']
            or not isinstance(profile['review'], str)
            or not profile['review'].strip()
        ):
            raise ValueError('原文、录音或乐谱配置已变化')
        for track in song['tracks']:
            if sha(ROOT / 'data' / track['file']) != track['sha256']:
                raise ValueError('原录音已变化')
        source = text_source(ROOT / 'data' / song['source'], 'zh')
        definitions = [item for verse in source['verses'] for item in verse]
        if len(definitions) != 8 or [item['region'] for item in definitions] != [f'r{i}' for i in range(1, 9)]:
            raise ValueError('原文必须按已复核的八个阅读区域保留')
        full, local = transcripts(song, profile, cache, model_dir)
        pdf = profile['score']
        pdf_path = Path(pdf['file'])
        if pdf_path.is_absolute() or '..' in pdf_path.parts:
            raise ValueError('出版方曲谱路径无效')
        sectional.pinned_file(cache, pdf, prepare)
        rows = vector_rows(cache / pdf_path, pdf)
        sampling = profile['sampling']
        if (
            set(sampling) != {'parts', 'estimates', 'period', 'pairPeriod'}
            or sampling['parts'] != ['verse', 'verse', 'chorus', 'verse', 'chorus', 'verse', 'chorus', 'chorus']
            or len(sampling['estimates']) != 8
            or sampling['period'] != 20
            or sampling['pairPeriod'] != 42
            or any(type(x) not in (int, float) or not math.isfinite(x) or x < 3 for x in sampling['estimates'])
        ):
            raise ValueError('谱面搜索范围或阅读顺序无效')
        lead_path = ROOT / 'data' / song['reference']
        base_track = next(track for track in song['tracks'] if track['file'] != song['reference'])
        base_path = ROOT / 'data' / base_track['file']
        representations = {'stft': features(lead_path, cache), 'cqt': cqt_features(lead_path, cache)[0]}
        base_representations = {'stft': features(base_path, cache), 'cqt': cqt_features(base_path, cache)[0]}
        readings = []
        key = None
        for part, estimate in zip(sampling['parts'], sampling['estimates']):
            matches = {
                name: score_match(rows[part], feature, sampling['period'], estimate, key)
                for name, feature in representations.items()
            }
            if key is None:
                key = matches['stft']['best']['key']
                if matches['cqt']['best']['key'] != key:
                    raise ValueError('领唱两种音频特征的调性不一致')
            agreement([match['best']['starts'][0] for match in matches.values()])
            readings.append(matches)
        paired = {}
        for name, feature_set in [('lead', representations), ('base', base_representations)]:
            paired[name] = {
                kind: score_match(rows['verse'] * 2, feature, sampling['pairPeriod'], sampling['estimates'][0], key)
                for kind, feature in feature_set.items()
            }
            for row in [0, 2]:
                agreement([match['best']['starts'][row] for match in paired[name].values()])
        for index, row in [(0, 0), (1, 2)]:
            agreement(
                [m['best']['starts'][0] for m in readings[index].values()]
                + [m['best']['starts'][row] for m in paired['lead'].values()]
            )
        anchors = [agreement([match['best']['starts'][0] for match in entry.values()]) for entry in readings]
        anchors[1] = agreement(
            [m['best']['starts'][0] for m in readings[1].values()]
            + [m['best']['starts'][2] for m in paired['lead'].values()]
        )
        witnesses = lyric_witnesses(definitions, anchors, full, local)
        lead_times = [0] + [round(anchor - 2, 2) for anchor in anchors[1:]]
        previous = [max(match['best']['starts'][-1] for match in entry.values()) for entry in readings[:-1]]
        if any(a + 1 > b for a, b in zip(previous, lead_times[1:])):
            raise ValueError('预读早于前一阅读区域的末行')
        events = [value for pair in zip(previous, lead_times[1:]) for value in pair]
        maps = {}
        for name, provider in [('stft', features), ('cqt', lambda path, target: cqt_features(path, target)[0])]:
            mapped, detail = map_recordings(lead_path, base_path, events, cache, feature_provider=provider)
            maps[name] = {'times': mapped, 'evidence': detail}
        if maps['stft']['evidence']['semitoneShift'] != maps['cqt']['evidence']['semitoneShift']:
            raise ValueError('两种伴奏对应路径的调性不一致')
        for index, values in enumerate(zip(maps['stft']['times'], maps['cqt']['times'])):
            if index != 1:
                agreement(list(values))
        base_first = agreement([m['best']['starts'][2] for m in paired['base'].values()])
        base_times = [0, round(base_first - 2, 2)] + [
            agreement([maps['stft']['times'][index], maps['cqt']['times'][index]]) for index in range(3, len(events), 2)
        ]
        base_previous = [max(m['best']['starts'][1] for m in paired['base'].values())] + [
            max(maps['stft']['times'][index], maps['cqt']['times'][index]) for index in range(2, len(events), 2)
        ]
        if (
            len(base_times) != 8
            or any(a + 1 > b for a, b in zip(base_previous, base_times[1:]))
            or any(a + 0.5 >= b for a, b in zip(base_times, base_times[1:]))
        ):
            raise ValueError('伴奏预读与前一句或实际演唱顺序冲突')
        review = {
            'version': 7,
            'indices': list(range(1, 8)),
            'policy': 'source-bound score-supported early reading previews; sung onsets are not claimed',
        }
        evidence.update(
            performanceOrder=definitions,
            initialReadingView={'version': 1, 'region': 'r1', 'at': 0, 'lyricOnsetMeasured': False},
            independentTimelines={
                base_track['file']: {'definitions': definitions, 'times': base_times, 'returnIndices': []}
            },
            midiMatching={
                'mode': 'text-wait-vector-score',
                'source': pdf,
                'rows': rows,
                'leadReadings': readings,
                'firstTwoVerses': paired,
                'leadAnchors': anchors,
                'baseRepeatedMotifOverride': {
                    'reason': 'recording maps disagree; both direct complete-verse paths agree',
                    'conflictingMaps': [maps['stft']['times'][1], maps['cqt']['times'][1]],
                    'scorePosition': base_first,
                },
            },
            transcriptMatching={
                'algorithmVersion': 14,
                'profile': profile,
                'independentTranscripts': full,
                'localTranscripts': local,
                'sourceWitnesses': witnesses,
                'recordingEvents': events,
                'recordingMaps': maps,
                'previousLinePreview': review,
            },
        )
        export_result(song, ROOT, cache, dest, evidence, started, source, definitions, lead_times, [0])
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
    profiles = json.loads((ROOT / 'tools/sync-wait-score-profiles.json').read_text())
    for song in sectional.batch.inventory():
        if song['songId'] in args.songs and not song['existing'] and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.model_dir, args.prepare), flush=True)
