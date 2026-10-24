#!/usr/bin/env python3
"""Verify text reading regions against documented MIDI harmony and melody."""
import argparse
import importlib.util
import json
from pathlib import Path
import time
import numpy as np
from sync_batch_sources import text_source
from sync_batch_audio import features, repeated_verses, onsets_agree
from sync_batch_score import match_template
from sync_batch_align import export_result

spec = importlib.util.spec_from_file_location('batch_io', Path(__file__).with_name('batch-sync.py'))
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
spec = importlib.util.spec_from_file_location('same_tune', Path(__file__).with_name('transfer-sync.py'))
same_tune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(same_tune)


def midi_features(path, cycle=None):
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
    if cycle is not None:
        if cycle != 'first-melody-track':
            raise ValueError('未知的 MIDI 乐段范围')
        duration = max(end for track, start, end, pitch, velocity in notes if track == melodic_tracks[0])
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


def reference_limits(song, profile):
    discovery = profile.get('referenceDiscovery')
    if not discovery:
        return .35, .35
    matches = discovery.get('matches', [])
    recording = next((track for track in song['tracks'] if track['file'] == song['reference']), None)
    if (discovery.get('sourceHash') != song['sha256'] or not recording or
            discovery.get('audioHash') != recording['sha256'] or discovery.get('audio') != song['reference'] or
            len(matches) < 2 or matches[0]['file'] != profile['file'] or matches[0]['similarity'] < .7 or
            matches[0]['similarity'] - matches[1]['similarity'] < .1):
        raise ValueError('音频检索参考尚未确认或已失效')
    # Retrieval only proposes a tune. Actual publication requires stronger
    # independent harmonic and melodic matches than a documented lyric reference.
    return .25, .20


def check_extra_cycle(harmony, melody_rows, target, period, music, last, limits):
    expected = last + music['period']
    if float(target[0][-1]) < expected + .75 * music['period']:
        return {'checked': False, 'reason': '没有额外完整乐段的音频范围'}
    harmonic = same_tune.match_cycle(harmony, target, 0, period, music['period'], expected, cue_offsets=[0])
    melodic = match_template(melody_rows, target[1], target[0], music['period'], expected, 'spatialWeights')
    one, two = harmonic['best'], melodic['best']
    if (one['cost'] <= limits[0] and two['cost'] <= limits[1] and
            harmonic['runnerUp']['cost'] - one['cost'] >= .08 and
            melodic['runnerUp']['cost'] - two['cost'] >= .06 and one['key'] == two['key'] and
            onsets_agree(one['starts'], two['starts']) and
            min(one['starts'][0], two['starts'][0]) >= last + .75 * music['period'] and
            all(.75 * music['period'] <= span <= 1.25 * music['period'] for span in [one['span'], two['span']])):
        raise ValueError('录音还有额外完整同曲乐段，需核对实际演唱顺序')
    return {'checked': True, 'harmony': harmonic, 'melody': melodic}


def full_performance_times(profile, harmony, melody_rows, offsets, period, music, target, matches, limits, evidence):
    """Corroborate every local opening with two complete, ordered score paths."""
    review = profile.get('fullPerformanceCheck', {})
    if not isinstance(review, dict) or review.get('version') != 1 or profile.get('referenceDiscovery'):
        raise ValueError('整曲复核需要明确核对的原谱配置，不能替代检索候选的严格检查')
    count = len(music['shifts'])
    if count < 3 or len(matches) != count or not offsets or len(melody_rows) != len(offsets):
        raise ValueError('整曲复核缺少至少三个完整且逐区域核对的实际乐段')
    full_harmony = (np.concatenate([harmony[0] + verse * period for verse in range(count)]),
                    np.tile(harmony[1], count))
    full_offsets = [verse * period + offset for verse in range(count) for offset in offsets]
    harmonic = same_tune.match_cycle(full_harmony, target, 0, count * period,
        count * music['period'], music['initialOnsetEstimate'], cue_offsets=full_offsets)
    melodic = match_template(melody_rows * count, target[1], target[0], count * music['period'],
                             music['initialOnsetEstimate'], 'spatialWeights')
    evidence.update(harmony=harmonic, melody=melodic)
    pairs = [(row['harmony'], row['melody'], music['period']) for row in matches]
    pairs.append((harmonic, melodic, count * music['period']))
    keys = []
    for first, second, span in pairs:
        for result, limit, margin in [(first, limits[0], .08), (second, limits[1], .06)]:
            best = result['best']
            if (best['cost'] > limit or result['runnerUp']['cost'] - best['cost'] < margin or
                    not .75 * span <= best['span'] <= 1.25 * span):
                raise ValueError('逐段与整曲模板未能共同确认完整的演奏乐段')
            keys.append(best['key'])
    if len(set(keys)) != 1:
        raise ValueError('逐段与整曲模板的调性对应不一致')
    estimates = [[at for match in matches for at in match[kind]['best']['starts']]
                 for kind in ['harmony', 'melody']]
    estimates.extend([harmonic['best']['starts'], melodic['best']['starts']])
    evidence['onsetEstimates'] = estimates
    if (any(len(row) != count * len(offsets) for row in estimates) or
            any(not onsets_agree(row, other) for index, row in enumerate(estimates) for other in estimates[index + 1:])):
        raise ValueError('逐段与整曲模板的全部阅读起点未在0.5秒内一致')
    times = [round(sum(values) / len(values), 2) for values in zip(*estimates)]
    beginnings = times[::len(offsets)]
    if (any(a + .5 >= b for a, b in zip(times, times[1:])) or
            any(not .75 * music['period'] <= b - a <= 1.25 * music['period']
                for a, b in zip(beginnings, beginnings[1:]))):
        raise ValueError('整曲阅读顺序或相邻实际乐段跨度不一致')
    evidence.update(version=1, method='paired local and complete ordered MIDI score paths; all pairwise onsets within 0.5 seconds',
                    times=times)
    return times


def generate(song, profile, cache):
    start = time.monotonic()
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {'algorithmVersion': 7, 'profile': profile}}
    destination = cache / 'midi-alignment' / (song['source'] + '.json')
    try:
        if song['sha256'] != profile['targetSha256']:
            raise ValueError('文字素材与已经核对的 MIDI 配置不一致')
        limits = reference_limits(song, profile)
        evidence['midiMatching']['maximumCosts'] = {'harmony': limits[0], 'melody': limits[1]}
        source = text_source(batch.ROOT / 'data' / song['source'])
        if len(source['verses']) != profile['verseCount']:
            raise ValueError('MIDI 参考与正文段数不一致')
        path = cache / 'midi' / profile['file']
        if batch.checksum(path) != profile['sha256']:
            raise ValueError('MIDI 参考哈希不匹配')
        period, harmony, melody = midi_features(path, profile.get('referenceCycle'))
        offsets, melody_rows = reading_rows(source, profile, melody)
        if 'opticalReference' in profile:
            from sync_batch_reference_score import reference_rows
            optical_rows, optical_evidence = reference_rows(profile, cache, offsets, melody)
            image_spec = importlib.util.spec_from_file_location('printed_reference_alignment',
                Path(__file__).with_name('midi-image-sync.py'))
            image_alignment = importlib.util.module_from_spec(image_spec)
            image_spec.loader.exec_module(image_alignment)
            definitions = [item for verse in source['verses'] for item in verse]
            first_indices = list(range(0, len(definitions), len(offsets)))
            timelines, recordings = {}, {}
            evidence['independentTimelines'] = timelines
            evidence['midiMatching'].update(mode='text-symbolic-with-optical',
                opticalReference=optical_evidence, referencePeriod=period, readingOffsets=offsets,
                recordings=recordings)
            complete_recordings = image_alignment.complete_optical_recordings(profile, song['tracks'])
            for track in song['tracks']:
                recordings[track['file']] = {}
                if track['file'] in complete_recordings:
                    times, _ = image_alignment.complete_optical_recording_times(batch.ROOT / 'data' / track['file'],
                        source, melody_rows, optical_rows, offsets, cache,
                        reference_key=optical_evidence['symbolicRows']['semitoneShift'], evidence=recordings[track['file']])
                else:
                    times, _ = image_alignment.image_recording_times(batch.ROOT / 'data' / track['file'],
                        source, period, harmony, melody_rows, offsets, cache,
                        reference_key=optical_evidence['symbolicRows']['semitoneShift'],
                        evidence=recordings[track['file']], optical_rows=optical_rows)
                timelines[track['file']] = {'definitions': definitions, 'times': times, 'returnIndices': []}
            evidence['performanceOrder'] = definitions
            return export_result(song, batch.ROOT, cache, destination, evidence, start, source, definitions,
                timelines[song['reference']]['times'], first_indices)
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
            if (one['cost'] > limits[0] or harmonic['runnerUp']['cost'] - one['cost'] < .08 or
                    not .75 * music['period'] <= one['span'] <= 1.25 * music['period'] or
                    two['cost'] > limits[1] or melodic['runnerUp']['cost'] - two['cost'] < .06 or
                    one['key'] != two['key'] or not onsets_agree(one['starts'], two['starts'])):
                raise ValueError('MIDI 和声与旋律未能共同确认目标乐段的起点')
            if profile.get('referenceDiscovery') and not .75 * music['period'] <= two.get('span', 0) <= 1.25 * music['period']:
                raise ValueError('音频检索参考的旋律没有覆盖完整乐段')
            mapped.extend(round((a + b) / 2, 2) for a, b in zip(one['starts'], two['starts']))
        if profile.get('fullPerformanceCheck'):
            complete = {}
            evidence['midiMatching'].update(mode='text-local-and-complete-score', completePerformance=complete)
            corrected = full_performance_times(profile, harmony, melody_rows, offsets, period,
                music, target, matches, limits, complete)
        else:
            corrected, decisions = same_tune.consistent_phases(mapped, music, len(source['verses']), len(offsets))
            # Both symbolic templates already locate every verse independently.
            if decisions:
                raise ValueError('MIDI 独立模板的首段起点仍有歧义')
        if profile.get('referenceDiscovery'):
            if len(offsets) != 1:
                raise ValueError('音频检索参考还需逐项核对段内分句')
            evidence['midiMatching']['trailingCycle'] = check_extra_cycle(harmony, melody_rows, target,
                period, music, corrected[-1], limits)
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
