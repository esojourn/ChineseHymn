"""Match every complete original-text stanza to MIDI and a pinned two-voice printed score."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_align import export_result
from sync_batch_audio import features, repeated_verses
from sync_batch_cqt import cqt_features
from sync_batch_score import match_template
from sync_batch_sectional import performance_times
from sync_batch_sources import clean, text_source
from sync_batch_staff_voice import staff_melody
from sync_batch_symbolic import score_row_offsets
from sync_batch_wait_score import agreement, sha

spec = importlib.util.spec_from_file_location('staff_reference', ROOT / 'tools/sectional-midi-sync.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)
batch = reference.batch


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
        ):
            raise ValueError('五线谱循环配置未绑定当前原文和全部录音')
        source = text_source(ROOT / 'data' / song['source'])
        definitions = [item for verse in source['verses'] for item in verse]
        if (
            len(source['verses']) < 3
            or any(len(verse) != 1 for verse in source['verses'])
            or profile['sourceOpenings'] != [clean(d['text'])[:8] for d in definitions]
            or sha(ROOT / 'data' / song['source']) != song['sha256']
        ):
            raise ValueError('原文全部完整诗节未依次保留')
        midi_file = reference.pinned_file(cache, profile['midi'], prepare)
        pdf = reference.pinned_file(cache, profile['score'], prepare)
        period, _, melody = reference.midi.midi_features(midi_file)
        optical, glyphs = staff_melody(pdf, profile['score'])
        offsets, symbolic = score_row_offsets(optical, melody['onsetTimes'], melody['onsetPitches'])
        if symbolic['editCost'] != 0 or offsets != [0] or symbolic['semitoneShift'] != 0:
            raise ValueError('完整原谱必须与原始 MIDI 全部音头零编辑一致')
        count = len(definitions)
        recordings, timelines = {}, {}
        for track in song['tracks']:
            audio = ROOT / 'data' / track['file']
            if sha(audio) != track['sha256']:
                raise ValueError('原始录音哈希变化')
            music = repeated_verses(audio, count, cache)
            detail = recordings[track['file']] = {'sha256': track['sha256'], 'music': music, 'features': {}}
            representations = {'stft': features(audio, cache), 'cqt': cqt_features(audio, cache)[0]}
            paths = []
            for name, target in representations.items():

                def match(rows, chroma, times, duration, onset, weight):
                    bounds = [0, 0.75 * music['period']] if len(rows) == count else [max(0, onset - 3), onset + 3]
                    return match_template(rows, chroma, times, duration, onset, weight, onset_bounds=bounds)

                feature_detail = detail['features'][name] = {}
                performance_times(
                    [melody] * count,
                    optical * count,
                    target,
                    music['period'] * count,
                    music['initialOnsetEstimate'],
                    0,
                    match,
                    feature_detail,
                    edge_verses=[[index] for index in range(count)],
                )
                paths.extend(
                    [
                        feature_detail['fullMelody']['best']['starts'],
                        feature_detail['fullOptical']['best']['starts'],
                        [item['melody']['best']['starts'][0] for item in feature_detail['local']],
                        [item['optical']['best']['starts'][0] for item in feature_detail['local']],
                    ]
                )
            if len(paths) != 8 or any(len(path) != count for path in paths):
                raise ValueError('全部录音的八条旋律路径未覆盖所有诗节')
            keys = [
                value[key]['best']['key']
                for value in detail['features'].values()
                for key in ['fullMelody', 'fullOptical']
            ]
            keys.extend(
                item[key]['best']['key']
                for value in detail['features'].values()
                for item in value['local']
                for key in ['melody', 'optical']
            )
            if len(set(keys)) != 1:
                raise ValueError('两种音频特征的完整及局部曲谱调性不一致')
            positions = [agreement(list(values)) for values in zip(*paths)]
            if any(
                abs((b - a) - shift) > 0.5
                for a, b, shift in zip(
                    positions, positions[1:], [b - a for a, b in zip(music['shifts'], music['shifts'][1:])]
                )
            ):
                raise ValueError('完整原谱起点与独立重复相位不一致')
            duration = MP3(audio).info.length
            ends = [
                value[key]['best']['starts'][0] + value[key]['best']['span']
                for value in detail['features'].values()
                for key in ['fullMelody', 'fullOptical']
            ]
            if (
                not 0 <= min(positions) < 0.75 * music['period']
                or not 0 <= duration - min(ends) < 0.75 * music['period']
            ):
                raise ValueError('录音首尾仍可能遗漏完整诗节')
            detail.update(allOnsetPaths=paths, measuredPositions=positions, remainingSeconds=duration - min(ends))
            timelines[track['file']] = [0] + [round(at - profile['previewSeconds'], 2) for at in positions[1:]]
        failed = ROOT / profile['harmonyDiagnostic']['file']
        if sha(failed) != profile['harmonyDiagnostic']['sha256']:
            raise ValueError('未通过的原始和声诊断应完整保留')
        evidence.update(
            performanceOrder=definitions,
            initialReadingView={'version': 1, 'region': definitions[0]['region'], 'at': 0, 'lyricOnsetMeasured': False},
            independentTimelines={
                name: {'definitions': definitions, 'times': times, 'returnIndices': []}
                for name, times in timelines.items()
                if name != song['reference']
            },
            scoreMatching={
                'algorithmVersion': 9,
                'mode': 'text-staff-complete-cycles',
                'profile': profile,
                'referencePeriod': period,
                'opticalRows': optical,
                'glyphs': glyphs,
                'symbolicRows': symbolic,
                'recordings': recordings,
                'harmonyDiagnostic': profile['harmonyDiagnostic'],
                'scoreReadingPreviews': {
                    'version': 1,
                    'indices': list(range(1, count)),
                    'policy': 'all original stanzas; score-based early reading previews, no sung onset claim',
                },
            },
        )
        export_result(song, ROOT, cache, dest, evidence, started, source, definitions, timelines[song['reference']], [])
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
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    profiles = json.loads((ROOT / 'tools/sync-staff-cycle-profiles.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.prepare), flush=True)
