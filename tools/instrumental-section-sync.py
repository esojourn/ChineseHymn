"""Locate complete printed sections in an instrumental recording with key changes."""

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
from sync_batch_audio import features
from sync_batch_cqt import cqt_features
from sync_batch_instrumental_sections import agreeing_positions, harmonic_cqt, stable_tempo_band
from sync_batch_score import ScoreMismatch, match_template
from sync_batch_sectional import check_match, repeated_source_readings
from sync_batch_sources import clean, text_source
from sync_batch_wait_score import sha

spec = importlib.util.spec_from_file_location('instrumental_reference', ROOT / 'tools/sectional-midi-sync.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)
batch = reference.batch


def section_matches(rows, optical, representations, section, duration, opening_key, shift):
    matches = {}
    for name, representation in representations.items():
        times, chroma = representation['features']
        origin = representation.get('origin', 0)
        window = section['sampling']
        for kind, all_rows, weight in [('midi', rows, 'spatialWeights'), ('optical', optical, 'rhythmWeights')]:
            match = match_template(
                [all_rows[index] for index in section['rows']],
                chroma,
                times,
                duration,
                window['onsetEstimate'] - origin,
                weight,
                onset_bounds=[at - origin for at in window['onsetBounds']],
            )
            for candidate in ['best', 'runnerUp']:
                match[candidate]['starts'] = [round(at + origin, 3) for at in match[candidate]['starts']]
            expected = None if opening_key is None else (opening_key + section['transposeFromOpening']) % 12
            if kind == 'optical':
                expected = (matches[name + '-midi']['best']['key'] + shift) % 12
            matches[name + '-' + kind] = match
            try:
                check_match(match, duration, expected)
            except ValueError as error:
                raise ScoreMismatch(str(error), {'matches': matches}) from error
    agreeing_positions(matches)
    return matches


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
            or len(song['tracks']) != 1
            or profile['previewSeconds'] != 1.5
        ):
            raise ValueError('器乐分段配置未绑定当前文字及完整录音')
        audio = ROOT / 'data' / song['reference']
        if sha(audio) != song['tracks'][0]['sha256']:
            raise ValueError('器乐原录音已变化')
        source = text_source(ROOT / 'data' / song['source'])
        original = [item for verse in source['verses'] for item in verse]
        if (
            profile['sourceRegions'] != [item['region'] for item in original]
            or len(profile['regionOpenings']) != len(original)
            or any(
                not clean(item['text']).startswith(prefix) for item, prefix in zip(original, profile['regionOpenings'])
            )
        ):
            raise ValueError('器乐分段未保留完整原文阅读顺序')
        source = repeated_source_readings(source, profile)
        definitions = [item for verse in source['verses'] for item in verse]
        rows, optical, symbolic = reference.reference_material(profile, cache, prepare)
        if symbolic['symbolicRows']['editCost'] != 0 or len(rows) != 4 or len(optical) != 4:
            raise ValueError('器乐分段原谱或人工节奏转写不完整')
        standard = {'stft': {'features': features(audio, cache)}, 'cqt': {'features': cqt_features(audio, cache)[0]}}
        separated, feature_review = harmonic_cqt(audio, cache, profile['harmonicRange'])
        tail = {'raw-cqt': standard['cqt'], 'harmonic-cqt': {'features': separated, 'origin': 170}}
        sections, anchors, ranges, opening_key = [], [], [], None
        if [section['mode'] for section in profile['sections']] != ['stft-cqt', 'stft-cqt', 'raw-and-harmonic-cqt']:
            raise ValueError('器乐分段复核方式不完整')
        for section in profile['sections']:
            detail = {'section': section}
            if section['mode'] == 'stft-cqt':
                duration = section['sampling']['durationEstimate']
                matches = section_matches(
                    rows, optical, standard, section, duration, opening_key, symbolic['symbolicRows']['semitoneShift']
                )
                if opening_key is None:
                    opening_key = matches['stft-midi']['best']['key']
                    # The other representation must confirm the opening key too.
                    if matches['cqt-midi']['best']['key'] != opening_key:
                        raise ValueError('器乐开头两种特征的调性不一致')
                positions = agreeing_positions(matches)
                detail['matches'] = matches
                used = [matches]
            else:
                diagnostic = {}
                for kind, all_rows, weight in [('midi', rows, 'spatialWeights'), ('optical', optical, 'rhythmWeights')]:
                    diagnostic[kind] = match_template(
                        [all_rows[index] for index in section['rows']],
                        standard['stft']['features'][1],
                        standard['stft']['features'][0],
                        50,
                        section['sampling']['onsetEstimate'],
                        weight,
                        onset_bounds=section['sampling']['onsetBounds'],
                    )
                detail['unfilteredStftDiagnostic'] = diagnostic
                attempts = []
                for duration in section['sampling']['durationEstimates']:
                    attempt = {'durationEstimate': duration, 'accepted': False}
                    try:
                        attempt['matches'] = section_matches(
                            rows,
                            optical,
                            tail,
                            section,
                            duration,
                            opening_key,
                            symbolic['symbolicRows']['semitoneShift'],
                        )
                        attempt['accepted'] = True
                    except ValueError as error:
                        attempt['reason'] = str(error)
                        if hasattr(error, 'evidence'):
                            attempt['failedMatches'] = error.evidence
                    attempts.append(attempt)
                positions, supported = stable_tempo_band(attempts, section['minimumConsecutiveEstimates'])
                detail.update(attempts=attempts, supportedDurationEstimates=supported)
                used = [attempt['matches'] for attempt in attempts if attempt['durationEstimate'] in supported]
            if len(positions) != 2:
                raise ValueError('器乐完整段落必须覆盖两个原文区域')
            range_end = min(
                match['best']['starts'][0] + match['best']['span'] for matches in used for match in matches.values()
            )
            ranges.append([positions[0], range_end])
            detail['positions'] = positions
            sections.append(detail)
            anchors.extend(positions)
        duration = MP3(audio).info.length
        if (
            len(anchors) != len(definitions)
            or any(a + 0.5 >= b for a, b in zip(anchors, anchors[1:]))
            or not 0 <= ranges[0][0] < 37.5
            or not 0 <= duration - ranges[-1][1] < 37.5
            or any(not 0 <= later[0] - prior[1] < 42 for prior, later in zip(ranges, ranges[1:]))
        ):
            raise ValueError('器乐正文顺序或首尾及间隔仍可能缺少完整歌段')
        times = [0] + [round(at - profile['previewSeconds'], 2) for at in anchors[1:]]
        review = {
            'version': 1,
            'indices': [1, 2, 3, 5],
            'policy': 'complete printed sections and stable ending variation; early reading previews, no sung onsets',
        }
        evidence.update(
            performanceOrder=definitions,
            returnIndices=[4],
            initialReadingView={'version': 1, 'region': definitions[0]['region'], 'at': 0, 'lyricOnsetMeasured': False},
            scoreMatching={
                'algorithmVersion': 8,
                'mode': 'instrumental-complete-sections',
                'profile': profile,
                'symbolicReference': symbolic,
                'sections': sections,
                'measuredTimes': anchors,
                'sectionRanges': ranges,
                'harmonicFeatures': feature_review,
                'scoreReadingPreviews': review,
                'unmatchedInstrumentalIntervals': [[ranges[i][1], ranges[i + 1][0]] for i in range(2)],
            },
        )
        export_result(song, ROOT, cache, dest, evidence, started, source, definitions, times, [])
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
    profiles = json.loads((ROOT / 'tools/sync-instrumental-section-profiles.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and song['songId'] in profiles:
            print(generate(song, profiles[song['songId']], args.cache, args.prepare), flush=True)
