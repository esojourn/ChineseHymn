#!/usr/bin/env python3
"""Locate each complete printed verse independently when arrangements contain interludes."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('separate_sectional', ROOT / 'tools/sectional-midi-sync.py')
sectional = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sectional)
from sync_batch_align import export_result
from sync_batch_audio import features
from sync_batch_sectional import check_match, four_path_time


def measure(target, rows, printed, proof, sampling):
    samples, period = sampling['verseEstimates'], sampling['periodEstimate']
    if (not isinstance(samples, list) or len(samples) != 3 or
            type(period) not in (int, float) or not math.isfinite(period) or period <= 0 or
            any(type(value) not in (float, int) or not math.isfinite(value) or value < 3 for value in samples) or
            any(a + period * .75 >= b for a, b in zip(samples, samples[1:]))):
        raise ValueError('完整诗节的取样范围不符合已复核的实际顺序')
    if len(rows) != 2 or len(printed) != 2 or proof['editCost'] != 0:
        raise ValueError('每节须完整包含两个原谱阅读区域，且 MIDI 与原谱逐音一致')
    decisions, measured, edges = [], [], []
    for estimate in samples:
        matches = [sectional.midi.match_template(material, target[1], target[0], period, estimate, weight,
                   onset_bounds=[estimate - 3, estimate + 3])
                   for material, weight in [(rows, 'spatialWeights'), (printed, 'rhythmWeights')]]
        a, b = [item['best'] for item in matches]
        check_match(matches[0], period)
        check_match(matches[1], period, (a['key'] + proof['semitoneShift']) % 12)
        if len(a['starts']) != 2 or len(b['starts']) != 2:
            raise ValueError('完整诗节未覆盖两个阅读区域')
        begin = (a['starts'][0] + b['starts'][0]) / 2
        middle = (a['starts'][1] + b['starts'][1]) / 2
        end = (a['starts'][0] + a['span'] + b['starts'][0] + b['span']) / 2
        if not begin < middle < end:
            raise ValueError('完整诗节的两个区域顺序无效')
        verse = {'estimate': estimate, 'fullMidi': matches[0], 'fullOptical': matches[1], 'local': []}
        for index, (at, after) in enumerate([(begin, middle), (middle, end)]):
            span = after - at
            local = [sectional.midi.match_template([material[index]], target[1], target[0], span, at, weight,
                     onset_bounds=[max(0, at - 3), at + 3])
                     for material, weight in [(rows, 'spatialWeights'), (printed, 'rhythmWeights')]]
            check_match(local[0], span, a['key'])
            check_match(local[1], span, b['key'])
            at = four_path_time([a['starts'][index], b['starts'][index],
                                 local[0]['best']['starts'][0], local[1]['best']['starts'][0]])
            measured.append(at)
            verse['local'].append({'index': index, 'span': span, 'midi': local[0], 'optical': local[1], 'at': at})
        decisions.append(verse)
        edges.append([begin, end])
    if any(a + .5 >= b for a, b in zip(measured, measured[1:])):
        raise ValueError('实测阅读位置顺序不正确')
    spans = [end - begin for begin, end in edges]
    # A gap large enough to contain another complete verse requires further form review.
    if (edges[0][0] >= .75 * min(spans) or float(target[0][-1]) - edges[-1][1] >= .75 * min(spans) or
            any(next_begin - end >= .75 * min(spans) for (_, end), (next_begin, _) in zip(edges, edges[1:]))):
        raise ValueError('录音首尾或间奏可能容纳另一个完整诗节')
    return measured, {'verses': decisions, 'verseEdges': edges}


def generate(song, profile, cache, prepare=False):
    start = time.monotonic()
    path = cache / 'midi-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {'algorithmVersion': 1,
                'mode': 'text-separate-verses', 'profile': profile}}
    try:
        source, definitions = sectional.validate_source(song, profile)
        if len(source['verses']) != 3 or any(len(verse) != 2 for verse in source['verses']):
            raise ValueError('正文须为三节、每节两个完整阅读区域')
        rows, printed, reference = sectional.reference_material(profile, cache, prepare)
        evidence['midiMatching'].update(reference)
        recordings = profile['separateRecordings']
        if ([record['file'] for record in recordings] != [track['file'] for track in song['tracks']] or
                any(record['sha256'] != track['sha256'] for record, track in zip(recordings, song['tracks']))):
            raise ValueError('每条录音均须独立绑定并校验')
        all_times = {}
        for record in recordings:
            file = sectional.batch.ROOT / 'data' / record['file']
            if sectional.batch.checksum(file) != record['sha256']:
                raise ValueError('录音哈希已改变')
            times, detail = measure(features(file, cache), rows, printed, reference['symbolicRows'], record['sampling'])
            all_times[record['file']] = [0] + times[1:]
            evidence['midiMatching'].setdefault('recordings', {})[record['file']] = detail
        evidence['performanceOrder'] = definitions
        evidence['initialReadingView'] = {'version': 1, 'at': 0, 'region': definitions[0]['region'], 'lyricOnsetMeasured': False}
        for track in song['tracks']:
            if track['file'] != song['reference']:
                evidence.setdefault('independentTimelines', {})[track['file']] = {'definitions': definitions,
                    'times': all_times[track['file']], 'returnIndices': []}
        return export_result(song, ROOT, cache, path, evidence, start, source, definitions, all_times[song['reference']], [0])
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - start, 3))
        sectional.batch.save(path, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    profiles = json.loads((ROOT / 'tools/sync-separate-verse-profiles.json').read_text())
    for song in sectional.batch.inventory():
        if song['songId'] in args.songs and song['songId'] in profiles and not song['existing']:
            print(generate(song, profiles[song['songId']], args.cache, args.prepare), flush=True)
