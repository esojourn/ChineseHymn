"""Generate complete-variant score reading previews for Only a Holy God."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.request
from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_align import export_result
from sync_batch_audio import features, map_recordings
from sync_batch_cqt import cqt_features
from sync_batch_holy_vector import vector_rows
from sync_batch_score import match_template
from sync_batch_sectional import check_match
from sync_batch_sources import text_source
from sync_batch_wait_score import agreement, sha

spec = importlib.util.spec_from_file_location('batch', ROOT / 'tools/batch-sync.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


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
            or song['type'] != 'text'
            or profile['sourceHash'] != song['sha256']
            or profile['tracks'] != song['tracks']
            or profile['audioFile'] != song['reference']
            or profile['previewSeconds'] != 1.5
            or not profile['review'].strip()
        ):
            raise ValueError('完整变体配置未绑定当前素材')
        for track in song['tracks']:
            if sha(ROOT / 'data' / track['file']) != track['sha256']:
                raise ValueError('录音已变化')
        score = profile['score']
        relative = Path(score['cacheFile'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('出版谱缓存路径无效')
        pdf = cache / relative
        if prepare and not pdf.exists():
            if score['url'] != 'https://cityalight.com/wp-content/uploads/2022/07/Only-A-Holy-God_LS.pdf':
                raise ValueError('出版方下载地址已变化')
            pdf.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(score['url'], timeout=60) as response:
                content = response.read()
            pdf.write_bytes(content)
        rows, glyphs = vector_rows(pdf, score)
        source = text_source(ROOT / 'data' / song['source'])
        original = [item for verse in source['verses'] for item in verse]
        if profile['readings'] != [
            item['region'] for item in original if item['region'] not in profile['nonSungRegions']
        ]:
            raise ValueError('正文阅读顺序不完整')
        for region, text in profile['nonSungRegions'].items():
            if next(item['text'] for item in original if item['region'] == region) != text or text != '【间奏】':
                raise ValueError('只能单独保留原文明确标记的间奏')
        by_name = {item['region']: item for item in original}
        definitions = [by_name[name] for name in profile['readings']]
        lead = ROOT / 'data' / song['reference']
        representations = {'stft': features(lead, cache), 'cqt': cqt_features(lead, cache)[0]}
        sections, anchors, ends = [], {}, []
        expected_key = None
        for section in profile['sections']:
            matches = {}
            for name, (times, chroma) in representations.items():
                match = match_template(
                    rows[section['part']],
                    chroma,
                    times,
                    section['duration'],
                    section['estimate'],
                    'opticalWeights',
                    onset_bounds=[max(0, section['estimate'] - 4), section['estimate'] + 4],
                )
                check_match(match, section['duration'], expected_key)
                expected_key = match['best']['key'] if expected_key is None else expected_key
                matches[name] = match
            if any(len(match['best']['starts']) != len(rows[section['part']]) for match in matches.values()):
                raise ValueError('完整谱面路径遗漏阅读区域')
            positions = [
                agreement(list(values)) for values in zip(*(match['best']['starts'] for match in matches.values()))
            ]
            for index, region in section['readings'].items():
                if region in anchors or region not in profile['readings']:
                    raise ValueError('阅读区域重复或不存在')
                anchors[region] = positions[int(index)]
            ends.append(min(match['best']['starts'][0] + match['best']['span'] for match in matches.values()))
            sections.append({'section': section, 'matches': matches, 'positions': positions})
        measured = [anchors[item['region']] for item in definitions]
        if any(a + 0.5 >= b for a, b in zip(measured, measured[1:])):
            raise ValueError('完整变体的实际演唱顺序冲突')
        # A full missing verse is the relevant tail risk; the short coda has
        # its own matched phrase positions and does not define a verse cycle.
        verse_duration = next(section['duration'] for section in profile['sections'] if section['part'] == 'verse')
        tail = MP3(lead).info.length - ends[-1]
        if not 0 <= tail < verse_duration * 0.75:
            raise ValueError('结尾仍可能漏掉完整加唱')
        timelines = {song['reference']: [0] + [round(at - profile['previewSeconds'], 2) for at in measured[1:]]}
        maps = {}
        for track in song['tracks']:
            if track['file'] == song['reference']:
                continue
            target = ROOT / 'data' / track['file']
            pair = {}
            for name, provider in [('stft', features), ('cqt', lambda path, dest: cqt_features(path, dest)[0])]:
                mapped, detail = map_recordings(lead, target, measured, cache, feature_provider=provider)
                pair[name] = {'times': mapped, 'evidence': detail}
            if pair['stft']['evidence']['semitoneShift'] != pair['cqt']['evidence']['semitoneShift']:
                raise ValueError('两条录音对应路径的调性不一致')
            positions = [agreement(list(values)) for values in zip(*(row['times'] for row in pair.values()))]
            timelines[track['file']] = [0] + [round(at - profile['previewSeconds'], 2) for at in positions[1:]]
            maps[track['file']] = pair
        review = {
            'version': 1,
            'indices': list(range(1, len(definitions))),
            'policy': 'publisher score with complete variant and coda; early reading previews, not exact sung onsets',
        }
        evidence.update(
            performanceOrder=definitions,
            initialReadingView={'version': 1, 'region': definitions[0]['region'], 'at': 0, 'lyricOnsetMeasured': False},
            independentTimelines={
                name: {'definitions': definitions, 'times': values, 'returnIndices': []}
                for name, values in timelines.items()
                if name != song['reference']
            },
            scoreMatching={
                'algorithmVersion': 6,
                'mode': 'text-holy-vector-score',
                'profile': profile,
                'rows': rows,
                'glyphs': glyphs,
                'sections': sections,
                'measuredTimes': measured,
                'recordingMaps': maps,
                'tail': {
                    'remainingSeconds': tail,
                    'completeVerseSeconds': verse_duration,
                    'policy': 'remaining audio must be shorter than 75% of a complete verse',
                },
                'scoreReadingPreviews': review,
            },
        )
        export_result(song, ROOT, cache, dest, evidence, started, source, definitions, timelines[song['reference']], [])
        return song['songId'], 'ready'
    except (ValueError, KeyError, IndexError, TypeError, FileNotFoundError, StopIteration) as error:
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
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    profiles = json.loads((ROOT / 'tools/sync-holy-vector-profiles.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.prepare), flush=True)
