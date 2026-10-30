#!/usr/bin/env python3
"""Verify reading positions against an unchanged MIDI supplied with the song."""
import argparse
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from sync_batch_full_arrangement import digest, recording_features, reference_times
from sync_batch_midi_boundaries import quarter_second
from sync_batch_sources import clean, text_source

spec = importlib.util.spec_from_file_location('repository_midi', Path(__file__).with_name('midi-sync.py'))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def validate_source(song, profile, source, root):
    definitions = [item for verse in source['verses'] for item in verse]
    if (type(profile.get('version')) is not int or profile['version'] != 1 or
            song.get('type') != 'text' or source.get('language') != 'zh' or
            profile.get('targetSha256') != song['sha256'] or
            digest(root / 'data' / song['source']) != song['sha256'] or
            profile.get('tracks') != song['tracks'] or
            profile.get('sourceVerseShape') != [len(v) for v in source['verses']] or
            len(definitions) < 2 or len({d['region'] for d in definitions}) != len(definitions) or
            any(len(clean(d['text'])) < 8 for d in definitions) or
            sum(len(clean(d['text'])) for d in definitions) < 40 or
            not isinstance(profile.get('review'), str) or not profile['review'].strip() or
            profile.get('file') != Path(song['source']).with_suffix('.mid').name):
        raise ValueError('仓库 MIDI 须绑定同名原始素材、完整中文阅读顺序及全部录音')
    methods = profile.get('trackMethods', {})
    if (not isinstance(methods, dict) or set(methods) != {track['file'] for track in song['tracks']} or
            any(value not in ('four-paths', 'chroma-dtw') for value in methods.values()) or
            methods.get(profile.get('referenceAudioFile')) != 'four-paths'):
        raise ValueError('每条录音须明确采用完整四路径或原有音乐对应，参考录音须通过四路径')
    for track in song['tracks']:
        if digest(root / 'data' / track['file']) != track['sha256']:
            raise ValueError('仓库 MIDI 对应录音已变化')
    return definitions


def reference_sections(path, profile, definitions):
    import mido
    if digest(path) != profile['sha256']:
        raise ValueError('同名原始 MIDI 哈希已变化')
    score = mido.MidiFile(path)
    voices = [i for i, track in enumerate(score.tracks) if any(
        e.type == 'note_on' and e.velocity and e.channel != 9 for e in track)]
    if score.type != 1 or not voices:
        raise ValueError('仓库参考必须是完整原始多声部 MIDI')
    events, first_quarter = [], None
    for i, track in enumerate(score.tracks):
        tick, active = 0, {}
        for event in track:
            tick += event.time
            if event.type not in ('note_on', 'note_off') or event.channel == 9:
                continue
            key = (event.channel, event.note)
            sounding = event.type == 'note_on' and bool(event.velocity)
            events.append((tick, sounding, i, key))
            if sounding:
                if i == voices[0] and first_quarter is None:
                    first_quarter = tick / score.ticks_per_beat
                active[key] = active.get(key, 0) + 1
            elif not active.get(key):
                raise ValueError('原始 MIDI 存在未配对音符')
            else:
                active[key] -= 1
        if any(active.values()):
            raise ValueError('原始 MIDI 存在未闭合音符')
    active, polyphony = {}, 0
    for tick, sounding, track, key in sorted(events):
        key = (track, *key)
        active[key] = active.get(key, 0) + (1 if sounding else -1)
        polyphony = max(polyphony, len({key[-1] for key, count in active.items() if count > 0}))
    if polyphony < 4:
        raise ValueError('同名 MIDI 须保留至少四音原始复调，不能以单旋律冒充和声')
    period, harmony, melody = cli.midi_features(path)
    sections = profile.get('sections', [])
    regions = [part.get('region') for part in sections if part.get('region') is not None]
    if (regions != [d['region'] for d in definitions] or
            len(sections) not in (len(definitions), len(definitions) + 1)):
        raise ValueError('MIDI 分界须依次覆盖全部原文，只允许额外核对完整前奏')
    offsets, previous, cursor = [], -1, 0
    for index, part in enumerate(sections):
        quarter, onset, pitches = part.get('quarter'), part.get('onsetIndex'), part.get('pitches')
        if (type(quarter) not in (int, float) or not np.isfinite(quarter) or quarter < first_quarter or
                type(onset) is not int or not previous < onset < len(melody['onsetTimes']) or
                not isinstance(pitches, list) or len(pitches) < 5 or
                melody['onsetPitches'][onset:onset + len(pitches)] != pitches):
            raise ValueError('仓库 MIDI 分界必须绑定原始拍位和至少五个连续旋律起音')
        at = quarter_second(score, quarter) - quarter_second(score, first_quarter)
        if abs(at - melody['onsetTimes'][onset]) > 1e-6:
            raise ValueError('原始拍位与原始旋律起音不一致')
        at = melody['onsetTimes'][onset]
        if part.get('region') is None:
            if index != 0 or at != 0 or part.get('opening') != '':
                raise ValueError('只能单独核对首个旋律起音开始的完整前奏')
        else:
            opening = clean(part.get('opening', ''))
            if len(opening) < 6 or not clean(definitions[cursor]['text']).startswith(opening):
                raise ValueError('MIDI 分界对应的完整原文起句已变化')
            cursor += 1
        offsets.append(at)
        previous = onset
    if not offsets or offsets[0] != 0:
        raise ValueError('完整四路径须覆盖首个旋律音，不能省略前奏')
    cuts = [int(np.searchsorted(harmony[0], at)) for at in offsets] + [len(harmony[0])]
    rows = [{key: melody[key][a:b] for key in ['notes', 'pitchOffsets', 'spatialWeights']}
            for a, b in zip(cuts, cuts[1:])]
    if any(not row['notes'] for row in rows):
        raise ValueError('原始 MIDI 含空阅读乐段')
    proof = {'unmodifiedOriginal': True, 'sourceFile': 'data/' + path.name,
             'sourceSha256': profile['sha256'], 'analysisSha256': digest(path),
             'maximumDistinctSoundingPitches': polyphony, 'firstMelodyQuarter': first_quarter,
             'sections': sections,
             'policy': 'original repository MIDI; preserve every original note, track and tempo; full score plus every complete section independently checked'}
    return period, harmony, rows, offsets, proof


def generate(song, profile, cache):
    start = time.monotonic()
    destination = cache / 'midi-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {
                    'mode': 'repository-original-midi', 'algorithmVersion': 1, 'profile': profile}}
    try:
        source = text_source(cli.batch.ROOT / 'data' / song['source'])
        definitions = validate_source(song, profile, source, cli.batch.ROOT)
        song = {**song, 'reference': profile['referenceAudioFile']}
        path = cli.batch.ROOT / 'data' / profile['file']
        period, harmony, rows, offsets, proof = reference_sections(path, profile, definitions)
        detail = evidence['midiMatching']
        detail.update(referenceDerivation=proof, recordings={}, maximumCosts={'harmony': .35, 'melody': .35})
        timelines = evidence['independentTimelines'] = {}
        for track in song['tracks']:
            if profile['trackMethods'][track['file']] == 'chroma-dtw':
                continue  # export_result applies the unchanged full recording correspondence checks.
            measured = detail['recordings'][track['file']] = {}
            times, key = reference_times(cli, period, harmony, rows, offsets,
                recording_features(cli.batch.ROOT / 'data' / track['file'], cache), measured)
            measured['key'] = key
            values = [at for at, part in zip(times, profile['sections']) if part['region'] is not None]
            values[0] = 0
            timelines[track['file']] = {'definitions': definitions, 'times': values, 'returnIndices': []}
        evidence['initialReadingView'] = {'version': 1, 'region': definitions[0]['region'], 'at': 0,
            'policy': 'initial reading position from playback start', 'lyricOnsetMeasured': False}
        evidence['performanceOrder'] = definitions
        return cli.export_result(song, cli.batch.ROOT, cache, destination, evidence, start,
            source, definitions, timelines[song['reference']]['times'], [0])
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - start, 3))
        cli.batch.save(destination, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name('sync-repository-midi-profiles.json').read_text())
    for song in cli.batch.inventory():
        if (song['songId'] in args.songs and song['songId'] in profiles and not song['existing'] and
                song['lyricsAvailable'] and song['tracks']):
            print(generate(song, profiles[song['songId']], args.cache), flush=True)
