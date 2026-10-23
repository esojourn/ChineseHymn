#!/usr/bin/env python3
"""Verify text reading regions against documented MIDI harmony and melody."""
import argparse
import importlib.util
import json
from pathlib import Path
import time
import numpy as np
from sync_batch_sources import text_source
from sync_batch_audio import features, repeated_verses
from sync_batch_score import match_template
from sync_batch_align import export_result

spec = importlib.util.spec_from_file_location('batch_io', Path(__file__).with_name('batch-sync.py'))
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
spec = importlib.util.spec_from_file_location('same_tune', Path(__file__).with_name('transfer-sync.py'))
same_tune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(same_tune)


def midi_features(path):
    import mido
    score = mido.MidiFile(path)
    duration = score.length
    if duration < 2:
        raise ValueError('MIDI 缺少完整旋律')
    ticks, seconds, tempo = 0, 0., 500000
    tick_map, second_map, tempos = [0], [0.], [tempo]
    for message in mido.merge_tracks(score.tracks):
        seconds += mido.tick2second(message.time, score.ticks_per_beat, tempo)
        ticks += message.time
        if message.type == 'set_tempo':
            tempo = message.tempo
            tick_map.append(ticks)
            second_map.append(seconds)
            tempos.append(tempo)

    def at(tick):
        index = max(0, int(np.searchsorted(tick_map, tick, side='right')) - 1)
        return second_map[index] + mido.tick2second(tick - tick_map[index], score.ticks_per_beat, tempos[index])

    melodic_tracks = [index for index, track in enumerate(score.tracks) if any(
        message.type == 'note_on' and message.velocity and message.channel != 9 for message in track)]
    if not melodic_tracks:
        raise ValueError('MIDI 只有打击乐或没有有效旋律')
    notes = []
    for index in melodic_tracks:
        tick, active = 0, {}
        for message in score.tracks[index]:
            tick += message.time
            if message.type not in ('note_on', 'note_off') or message.channel == 9:
                continue
            key = message.channel, message.note
            if message.type == 'note_on' and message.velocity:
                active.setdefault(key, []).append((tick, message.velocity))
            elif key in active:
                start, velocity = active[key].pop()
                if not active[key]:
                    del active[key]
                notes.append((index, at(start), at(tick), message.note, velocity))
        for (channel, pitch), pending in active.items():
            for start, velocity in pending:
                notes.append((index, at(start), duration, pitch, velocity))
    times = np.arange(0, duration, .1)
    harmony = np.zeros((12, len(times)), dtype=np.float32)
    melody = np.full(len(times), -1, dtype=int)
    first_note = min(start for track, start, end, pitch, velocity in notes if track == melodic_tracks[0])
    for track, start, end, pitch, velocity in notes:
        left, right = np.searchsorted(times, [start, end])
        for tone, gain in [(pitch % 12, 1.8), ((pitch + 7) % 12, .4), ((pitch + 4) % 12, .1)]:
            harmony[tone, left:right] += velocity * gain
        if track == melodic_tracks[0]:
            melody[left:right] = np.maximum(melody[left:right], pitch)
    harmony = np.sqrt(harmony)
    harmony -= harmony.mean(axis=0)
    harmony /= np.maximum(np.linalg.norm(harmony, axis=0), 1e-8)
    chosen = times >= first_note
    times, harmony, melody = times[chosen] - first_note, harmony[:, chosen], melody[chosen]
    duration -= first_note
    row = {'notes': [0 if pitch < 0 else 1 for pitch in melody],
           'pitchOffsets': [0 if pitch < 0 else int(pitch % 12) for pitch in melody],
           'spatialWeights': [1] * len(melody)}
    # Emit the soprano only after all simultaneous note-ons have been considered;
    # an alto note-on earlier in the same tick is not a second melody onset.
    top_staff = [note for note in notes if note[0] == melodic_tracks[0]]
    onsets = []
    for start in sorted({note[1] for note in top_staff}):
        active = [note[3] for note in top_staff if note[1] <= start < note[2]]
        if not active:
            continue
        highest = max(active)
        held = any(note[1] < start < note[2] and note[3] == highest for note in top_staff)
        if not held and any(note[1] == start and note[3] == highest for note in top_staff):
            onsets.append((start - first_note, highest % 12))
    row['onsetTimes'] = [at for at, pitch in onsets]
    row['onsetPitches'] = [pitch for at, pitch in onsets]
    return duration, (times, harmony), row


def reading_rows(source, profile, melody):
    boundaries = profile.get('readingBoundaries')
    if boundaries is None:
        if any(len(verse) != 1 for verse in source['verses']):
            raise ValueError('多个文字阅读区域需要已经核对的 MIDI 分句配置')
        return [0], [melody]
    if (not boundaries or any(len(verse) != len(boundaries) for verse in source['verses']) or
            len(profile.get('regionOpenings', [])) != len(source['verses'])):
        raise ValueError('MIDI 分句配置与正文阅读区域数量不一致')
    from sync_batch_sources import clean
    for verse, openings in zip(source['verses'], profile['regionOpenings']):
        if len(openings) != len(verse) or any(len(clean(text)) < 4 or not clean(item['text']).startswith(clean(text))
                                            for item, text in zip(verse, openings)):
            raise ValueError('MIDI 分句配置与正文起句不一致')
    offsets, previous = [], -1
    for boundary in boundaries:
        index, pitches = boundary['onsetIndex'], boundary['pitches']
        if (not isinstance(index, int) or index <= previous or len(pitches) < 5 or index < 0 or
                melody['onsetPitches'][index:index + len(pitches)] != pitches):
            raise ValueError('MIDI 分句首音与已核对的旋律不一致')
        offsets.append(melody['onsetTimes'][index])
        previous = index
    if offsets[0] > .1:
        raise ValueError('MIDI 首个阅读区域没有从首音开始')
    cuts = [round(at / .1) for at in offsets] + [len(melody['notes'])]
    rows = [{key: melody[key][a:b] for key in ['notes', 'pitchOffsets', 'spatialWeights']}
            for a, b in zip(cuts, cuts[1:])]
    if any(not row['notes'] for row in rows):
        raise ValueError('MIDI 阅读区域缺少完整旋律')
    return offsets, rows


def generate(song, profile, cache):
    start = time.monotonic()
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {'algorithmVersion': 3, 'profile': profile}}
    destination = cache / 'midi-alignment' / (song['source'] + '.json')
    try:
        if song['sha256'] != profile['targetSha256']:
            raise ValueError('文字素材与已经核对的 MIDI 配置不一致')
        source = text_source(batch.ROOT / 'data' / song['source'])
        if len(source['verses']) != profile['verseCount']:
            raise ValueError('MIDI 参考与正文段数不一致')
        path = cache / 'midi' / profile['file']
        if batch.checksum(path) != profile['sha256']:
            raise ValueError('MIDI 参考哈希不匹配')
        period, harmony, melody = midi_features(path)
        offsets, melody_rows = reading_rows(source, profile, melody)
        reference = batch.ROOT / 'data' / song['reference']
        music = repeated_verses(reference, len(source['verses']), cache)
        target = features(reference, cache)
        mapped, matches = [], []
        evidence['midiMatching'].update(referencePeriod=period, music=music, matches=matches)
        for shift in music['shifts']:
            onset = music['initialOnsetEstimate'] + shift
            harmonic = same_tune.match_cycle(harmony, target, 0, period, music['period'], onset, cue_offsets=offsets)
            melodic = match_template(melody_rows, target[1], target[0], music['period'], onset, 'spatialWeights')
            one, two = harmonic['best'], melodic['best']
            matches.append({'harmony': harmonic, 'melody': melodic})
            if (one['cost'] > .35 or harmonic['runnerUp']['cost'] - one['cost'] < .08 or
                    not .75 * music['period'] <= one['span'] <= 1.25 * music['period'] or
                    two['cost'] > .35 or melodic['runnerUp']['cost'] - two['cost'] < .06 or
                    one['key'] != two['key'] or len(one['starts']) != len(two['starts']) or
                    max(abs(a - b) for a, b in zip(one['starts'], two['starts'])) > .5):
                raise ValueError('MIDI 和声与旋律未能共同确认目标乐段的起点')
            mapped.extend(round((a + b) / 2, 2) for a, b in zip(one['starts'], two['starts']))
        corrected, decisions = same_tune.consistent_phases(mapped, music, len(source['verses']), len(offsets))
        # Both symbolic templates already locate every verse independently.
        if decisions:
            raise ValueError('MIDI 独立模板的首段起点仍有歧义')
        evidence['midiMatching'].update(referencePeriod=period, music=music, matches=matches)
        evidence['midiMatching']['readingOffsets'] = offsets
        definitions = [item for verse in source['verses'] for item in verse]
        evidence['performanceOrder'] = definitions
        return export_result(song, batch.ROOT, cache, destination, evidence, start, source,
                             definitions, corrected, list(range(0, len(definitions), len(offsets))))
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - start, 3))
        batch.save(destination, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name('sync-midi-references.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and song['type'] == 'text' and song['tracks'] and song['lyricsAvailable'] and not song['existing']:
            print(generate(song, profiles[song['songId']], args.cache), flush=True)
