#!/usr/bin/env python3
"""Locate printed image rows using a documented MIDI and independent audio templates."""
import argparse
import importlib.util
import json
from pathlib import Path
import time
from sync_batch_align import export_result
from sync_batch_audio import features, repeated_verses
from sync_batch_score import match_template, note_rows
from sync_batch_sources import image_source
from sync_batch_symbolic import score_row_offsets, documented_row_offsets

spec = importlib.util.spec_from_file_location('midi_reference', Path(__file__).with_name('midi-sync.py'))
midi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(midi)
batch = midi.batch


def paired_melody_times(symbolic, optical, period, reference_key):
    one, two = symbolic['best'], optical['best']
    if (any(match['best']['cost'] > .35 or match['runnerUp']['cost'] - match['best']['cost'] < .06 or
            not .75 * period <= match['best'].get('span', 0) <= 1.25 * period
            for match in (symbolic, optical)) or
            (one['key'] + reference_key) % 12 != two['key'] or
            not midi.onsets_agree(one['starts'], two['starts'])):
        raise ValueError('外部 MIDI 旋律与原谱光学节奏未能共同确认全部谱行')
    return [round((a + b) / 2, 2) for a, b in zip(one['starts'], two['starts'])]


def complete_optical_recordings(profile, tracks):
    review = profile.get('completeOpticalCheck')
    if review is None:
        return set()
    if (not isinstance(review, dict) or type(review.get('version')) is not int or review['version'] != 1 or
            profile.get('referenceDiscovery') or not isinstance(review.get('recordings'), list) or not review['recordings']):
        raise ValueError('整曲光学复核需要明确核对的原谱及录音配置')
    available = {track['file']: track['sha256'] for track in tracks}
    selected = set()
    for track in review['recordings']:
        if (not isinstance(track, dict) or track.get('file') not in available or track['file'] in selected or
                track.get('sha256') != available[track['file']]):
            raise ValueError('整曲光学复核的录音配置已失效或重复')
        selected.add(track['file'])
    return selected


def complete_optical_recording_times(path, source, melody_rows, optical_rows, offsets, cache,
                                     reference_key=0, evidence=None):
    """Confirm changing stanza tempos with complete and local measured score paths."""
    count, size = len(source['verses']), len(offsets)
    if count < 3 or not size or len(melody_rows) != size or len(optical_rows) != size:
        raise ValueError('整曲光学复核需要至少三个完整且逐区域对应的乐段')
    target = features(path, cache)
    duration = float(target[0][-1])
    metrics = evidence if evidence is not None else {}
    complete_melody = match_template(melody_rows * count, target[1], target[0], duration, 0, 'spatialWeights')
    complete_optical = match_template(optical_rows * count, target[1], target[0], duration, 0, 'rhythmWeights')
    metrics.update(completeMelody=complete_melody, completeOptical=complete_optical)
    complete = paired_melody_times(complete_melody, complete_optical, duration, reference_key)
    if len(complete) != count * size:
        raise ValueError('整曲光学复核没有覆盖全部阅读区域')
    end = sum(match['best']['starts'][0] + match['best']['span']
              for match in (complete_melody, complete_optical)) / 2
    beginnings = complete[::size]
    spans = [stop - start for start, stop in zip(beginnings, beginnings[1:] + [end])]
    mean = (end - beginnings[0]) / count
    if mean <= 0 or any(not .75 * mean <= span <= 1.25 * mean for span in spans):
        raise ValueError('整曲光学复核的实际完整乐段跨度不一致')
    if beginnings[0] >= .75 * mean or duration - end >= .75 * mean:
        raise ValueError('录音首尾仍可容纳完整乐段，需核对实际演唱顺序')
    matches = []
    metrics.update(matches=matches, measuredVerseSpans=spans)
    for onset, span in zip(beginnings, spans):
        melodic = match_template(melody_rows, target[1], target[0], span, onset, 'spatialWeights')
        optical = match_template(optical_rows, target[1], target[0], span, onset, 'rhythmWeights')
        matches.append({'melody': melodic, 'opticalMelody': optical})
        paired_melody_times(melodic, optical, span, reference_key)
        if (melodic['best']['key'] != complete_melody['best']['key'] or
                optical['best']['key'] != complete_optical['best']['key']):
            raise ValueError('逐段与整曲光学复核的调性对应不一致')
    paths = [[at for match in matches for at in match[kind]['best']['starts']]
             for kind in ['melody', 'opticalMelody']]
    paths.extend([complete_melody['best']['starts'], complete_optical['best']['starts']])
    metrics['onsetEstimates'] = paths
    if (any(len(row) != count * size for row in paths) or
            any(not midi.onsets_agree(row, other) for index, row in enumerate(paths) for other in paths[index + 1:])):
        raise ValueError('逐段与整曲光学复核的全部阅读起点未在0.5秒内一致')
    times = [round(sum(values) / len(values), 2) for values in zip(*paths)]
    if times[0] < 0 or times[-1] >= duration or any(a + .5 >= b for a, b in zip(times, times[1:])):
        raise ValueError('整曲光学复核的阅读顺序超出实际录音')
    metrics.update(version=1, method='paired local and complete MIDI melody and printed optical rhythm paths; all pairwise onsets within 0.5 seconds',
                   times=times)
    return times, metrics


def image_recording_times(path, source, period, harmony, melody_rows, offsets, cache, weight_key='spatialWeights', reference_key=0, evidence=None, optical_rows=None):
    music = repeated_verses(path, len(source['verses']), cache)
    target = features(path, cache)
    mapped, matches = [], []
    metrics = evidence if evidence is not None else {}
    metrics.update(music=music, matches=matches)
    for shift in music['shifts']:
        onset = music['initialOnsetEstimate'] + shift
        harmonic = midi.same_tune.match_cycle(harmony, target, 0, period, music['period'], onset,
                                               cue_offset=0, cue_offsets=offsets)
        melodic = match_template(melody_rows, target[1], target[0], music['period'], onset, weight_key)
        one, two = harmonic['best'], melodic['best']
        matches.append({'harmony': harmonic, 'melody': melodic, 'melodyTemplate': weight_key})
        if optical_rows is not None:
            optical = match_template(optical_rows, target[1], target[0], music['period'], onset, 'rhythmWeights')
            matches[-1]['opticalMelody'] = optical
            mapped.extend(paired_melody_times(melodic, optical, music['period'], reference_key))
            continue
        if (one['cost'] > .35 or harmonic['runnerUp']['cost'] - one['cost'] < .08 or
                not .75 * music['period'] <= one['span'] <= 1.25 * music['period'] or
                two['cost'] > .35 or melodic['runnerUp']['cost'] - two['cost'] < .06 or
                (one['key'] + reference_key) % 12 != two['key'] or
                not midi.onsets_agree(one['starts'], two['starts'])):
            raise ValueError('MIDI 和声与旋律未能共同确认每个谱行的起点')
        mapped.extend(round((a + b) / 2, 2) for a, b in zip(one['starts'], two['starts']))
    times, decisions = midi.same_tune.consistent_phases(mapped, music, len(source['verses']), len(offsets))
    if decisions:
        raise ValueError('独立 MIDI 模板的首段位置仍存在歧义')
    return times, metrics


def generate(song, profile, cache):
    started = time.monotonic()
    destination = cache / 'midi-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {'algorithmVersion': 4, 'mode': 'image-rows', 'profile': profile}}
    try:
        if song['sha256'] != profile['targetSha256']:
            raise ValueError('图片素材与已经核对的 MIDI 配置不一致')
        path = cache / 'midi' / profile['file']
        if 'printedRhythmReference' in profile:
            from sync_batch_printed_midi import materialize_printed_reference
            materialize_printed_reference(profile, song['sha256'], path)
        if batch.checksum(path) != profile['sha256']:
            raise ValueError('MIDI 参考哈希不匹配')
        recognition = json.loads((cache / 'ocr' / (song['source'] + '.json')).read_text())
        if recognition.get('sparseRowRecovery'):
            evidence['sparseRowRecovery'] = recognition['sparseRowRecovery']
        if recognition['sha256'] != song['sha256']:
            raise ValueError('谱面识别已失效')
        source = image_source(recognition)
        if len(source['verses']) != profile['verseCount']:
            raise ValueError('谱面段数与参考配置不一致')
        rows = note_rows(batch.ROOT / 'data' / song['source'], recognition, cache)
        period, harmony, melody = midi.midi_features(path, profile.get('referenceCycle'))
        weight_key, reference_key, optical_rows = 'spatialWeights', 0, None
        if profile.get('mode') == 'documented-rows-optical-melody':
            reference_key = profile['referenceKey']
            offsets, symbolic = documented_row_offsets(rows, melody['onsetTimes'], melody['onsetPitches'],
                                                        profile['rowAnchors'], reference_key)
            evidence['midiMatching'].update(mode='image-optical-with-harmony', sourceRows=rows)
            melody_rows, weight_key = rows, 'rhythmWeights'
        else:
            offsets, symbolic = score_row_offsets(rows, melody['onsetTimes'], melody['onsetPitches'])
            cuts = [round(at / .1) for at in offsets] + [len(melody['notes'])]
            melody_rows = [{key: melody[key][a:b] for key in ['notes', 'pitchOffsets', 'spatialWeights']}
                           for a, b in zip(cuts, cuts[1:])]
            if profile.get('mode') == 'symbolic-melody-and-optical':
                optical_rows, reference_key = rows, symbolic['semitoneShift']
                evidence['midiMatching'].update(mode='image-symbolic-with-optical', sourceRows=rows)
        evidence['midiMatching'].update(symbolicRows=symbolic, referencePeriod=period)
        if offsets[0] > .1 or len(offsets) != len(source['systems']):
            raise ValueError('参考 MIDI 的起拍或谱行数量不一致')
        if any(not row['notes'] for row in melody_rows):
            raise ValueError('MIDI 谱行缺少完整旋律')
        definitions = [item for verse in source['verses'] for item in verse]
        first_indices = [index * len(offsets) for index in range(len(source['verses']))]
        timelines, recordings = {}, {}
        evidence['independentTimelines'] = timelines
        evidence['midiMatching']['recordings'] = recordings
        for track in song['tracks']:
            recordings[track['file']] = {}
            times, metrics = image_recording_times(batch.ROOT / 'data' / track['file'], source,
                                                   period, harmony, melody_rows, offsets, cache, weight_key, reference_key,
                                                   recordings[track['file']], optical_rows)
            timelines[track['file']] = {'definitions': definitions, 'times': times, 'returnIndices': first_indices}
            recordings[track['file']] = metrics
        evidence['performanceOrder'] = definitions
        return export_result(song, batch.ROOT, cache, destination, evidence, started, source,
                             definitions, timelines[song['reference']]['times'], first_indices)
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3))
        batch.save(destination, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name('sync-midi-image-references.json').read_text())
    unknown = sorted(set(args.songs) - profiles.keys())
    if unknown:
        parser.error('这些曲目尚未核对 MIDI 参考：' + '、'.join(unknown))
    for song in batch.inventory():
        if song['songId'] in args.songs and song['type'] == 'image' and song['tracks'] and not song['existing']:
            print(generate(song, profiles[song['songId']], args.cache), flush=True)
