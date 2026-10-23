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
from sync_batch_symbolic import score_row_offsets

spec = importlib.util.spec_from_file_location('midi_reference', Path(__file__).with_name('midi-sync.py'))
midi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(midi)
batch = midi.batch


def image_recording_times(path, source, period, harmony, melody_rows, offsets, cache):
    music = repeated_verses(path, len(source['verses']), cache)
    target = features(path, cache)
    mapped, matches = [], []
    for shift in music['shifts']:
        onset = music['initialOnsetEstimate'] + shift
        harmonic = midi.same_tune.match_cycle(harmony, target, 0, period, music['period'], onset,
                                               cue_offset=0, cue_offsets=offsets)
        melodic = match_template(melody_rows, target[1], target[0], music['period'], onset, 'spatialWeights')
        one, two = harmonic['best'], melodic['best']
        if (one['cost'] > .35 or harmonic['runnerUp']['cost'] - one['cost'] < .08 or
                not .75 * music['period'] <= one['span'] <= 1.25 * music['period'] or
                two['cost'] > .35 or melodic['runnerUp']['cost'] - two['cost'] < .06 or
                one['key'] != two['key'] or len(one['starts']) != len(two['starts']) or
                max(abs(a - b) for a, b in zip(one['starts'], two['starts'])) > .5):
            raise ValueError('MIDI 和声与旋律未能共同确认每个谱行的起点')
        mapped.extend(round((a + b) / 2, 2) for a, b in zip(one['starts'], two['starts']))
        matches.append({'harmony': harmonic, 'melody': melodic})
    times, decisions = midi.same_tune.consistent_phases(mapped, music, len(source['verses']), len(offsets))
    if decisions:
        raise ValueError('独立 MIDI 模板的首段位置仍存在歧义')
    return times, {'music': music, 'matches': matches}


def generate(song, profile, cache):
    started = time.monotonic()
    destination = cache / 'midi-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {'algorithmVersion': 1, 'mode': 'image-rows', 'profile': profile}}
    try:
        if song['sha256'] != profile['targetSha256']:
            raise ValueError('图片素材与已经核对的 MIDI 配置不一致')
        path = cache / 'midi' / profile['file']
        if batch.checksum(path) != profile['sha256']:
            raise ValueError('MIDI 参考哈希不匹配')
        recognition = json.loads((cache / 'ocr' / (song['source'] + '.json')).read_text())
        if recognition['sha256'] != song['sha256']:
            raise ValueError('谱面识别已失效')
        source = image_source(recognition)
        if len(source['verses']) != profile['verseCount']:
            raise ValueError('谱面段数与参考配置不一致')
        rows = note_rows(batch.ROOT / 'data' / song['source'], recognition, cache)
        period, harmony, melody = midi.midi_features(path)
        offsets, symbolic = score_row_offsets(rows, melody['onsetTimes'], melody['onsetPitches'])
        evidence['midiMatching'].update(symbolicRows=symbolic, referencePeriod=period)
        if offsets[0] > .1 or len(offsets) != len(source['systems']):
            raise ValueError('参考 MIDI 的起拍或谱行数量不一致')
        cuts = [round(at / .1) for at in offsets] + [len(melody['notes'])]
        melody_rows = [{key: melody[key][a:b] for key in ['notes', 'pitchOffsets', 'spatialWeights']}
                       for a, b in zip(cuts, cuts[1:])]
        if any(not row['notes'] for row in melody_rows):
            raise ValueError('MIDI 谱行缺少完整旋律')
        definitions = [item for verse in source['verses'] for item in verse]
        first_indices = [index * len(offsets) for index in range(len(source['verses']))]
        timelines, recordings = {}, {}
        evidence['independentTimelines'] = timelines
        evidence['midiMatching']['recordings'] = recordings
        for track in song['tracks']:
            times, metrics = image_recording_times(batch.ROOT / 'data' / track['file'], source,
                                                   period, harmony, melody_rows, offsets, cache)
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
    for song in batch.inventory():
        if song['songId'] in args.songs and song['type'] == 'image' and song['tracks'] and not song['existing']:
            print(generate(song, profiles[song['songId']], args.cache), flush=True)
