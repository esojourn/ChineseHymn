#!/usr/bin/env python3
"""Transfer documented same-tune reading boundaries through measured audio correspondence."""
import argparse
import importlib.util
import json
from pathlib import Path
import time
import numpy as np
from sync_batch_sources import text_source
from sync_batch_audio import features, map_recordings, repeated_verses
from sync_batch_align import export_result

spec = importlib.util.spec_from_file_location('batch_io', Path(__file__).with_name('batch-sync.py'))
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def consistent_phases(mapped, music, verse_count, slots):
    if len(mapped) != verse_count * slots or len(music['shifts']) != verse_count:
        raise ValueError('参考边界与实际段落数量不一致')
    corrected, decisions = list(mapped), []
    for slot in range(slots):
        phases = [mapped[verse * slots + slot] - music['shifts'][verse] for verse in range(verse_count)]
        if max(phases) - min(phases) <= .5:
            continue
        # Full-recording DTW can confuse a first pickup with the similar intro.
        # Only the very first cue can be recovered from two later score anchors.
        if (slot != 0 or verse_count < 3 or music['similarity'] < .95 or
                max(phases[1:]) - min(phases[1:]) > .5):
            raise ValueError('参考谱边界在各段的音乐相位不一致')
        expected = float(np.median(phases[1:]))
        if expected < 0 or abs(expected - music['initialOnsetEstimate']) > 3:
            raise ValueError('首段参考位置与实际前奏不一致')
        corrected[0] = round(expected, 2)
        decisions.append({'index': 0, 'originalAt': mapped[0], 'at': corrected[0],
                          'laterMappedPhases': phases[1:]})
    if any(a + .5 >= b for a, b in zip(corrected, corrected[1:])):
        raise ValueError('参考时间点不递增')
    return corrected, decisions


def match_cycle(reference_features, target_features, reference_start, reference_period, period, onset):
    from sync_batch_score import subsequence_path
    reference_times, reference_chroma = reference_features
    times, chroma = target_features
    step = float(times[1] - times[0])
    length = round(period / step)
    samples = reference_start + np.arange(length) * reference_period / length
    query = np.stack([np.interp(samples, reference_times, row) for row in reference_chroma])
    query /= np.maximum(np.linalg.norm(query, axis=0), 1e-8)
    lo = max(0, round((onset - 10) / step))
    hi = min(len(times), round((onset + period + 10) / step))
    if length < 20 or hi <= lo:
        raise ValueError('缺少完整乐段的音频范围')
    candidates = []
    for key in range(12):
        shifted = np.roll(query, key, axis=0)
        cost = np.ascontiguousarray(np.maximum(0, 1 - shifted.T @ chroma[:, lo:hi]), dtype=np.float32)
        value, path = subsequence_path(cost)
        frame = round(.4 / reference_period * length)
        positions = path[path[:, 0] == frame, 1]
        candidates.append({'cost': float(value), 'key': key,
                           'at': round(float(times[lo + int(np.median(positions))]), 3),
                           'span': round(float(times[lo + path[-1, 1]] - times[lo + path[0, 1]]), 3)})
    candidates.sort(key=lambda row: row['cost'])
    return {'best': candidates[0], 'runnerUp': candidates[1]}


def cycle_times(audio, target_audio, cues, per_verse, reference_music, music, cache):
    reference_features, target_features = features(audio, cache), features(target_audio, cache)
    if len(reference_music['shifts']) < 2:
        raise ValueError('完整乐段参考需要至少两个已确认的实际演奏段落')
    matches, mapped = [], []
    for shift in music['shifts']:
        pair = [match_cycle(reference_features, target_features, cues[verse * per_verse]['at'] - .4,
                            reference_music['period'], music['period'], music['initialOnsetEstimate'] + shift)
                for verse in (0, 1)]
        for match in pair:
            best = match['best']
            if (best['cost'] > .35 or match['runnerUp']['cost'] - best['cost'] < .08 or
                    not .75 * music['period'] <= best['span'] <= 1.25 * music['period']):
                raise ValueError('完整乐段与目标录音匹配不足')
        if pair[0]['best']['key'] != pair[1]['best']['key'] or abs(pair[0]['best']['at'] - pair[1]['best']['at']) > .5:
            raise ValueError('两段独立参考的乐段起点不一致')
        mapped.append(round((pair[0]['best']['at'] + pair[1]['best']['at']) / 2, 2))
        matches.append(pair)
    return mapped, {'method': 'two complete score-referenced audio cycles; subsequence chroma-DTW',
                    'referenceMusic': reference_music, 'matches': matches}


def generate(song, profile, cache, songs):
    start = time.monotonic()
    destination = cache / 'reference-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'referenceMatching': {'profile': profile, 'algorithmVersion': 1}}
    try:
        if song['type'] != 'text' or song['sha256'] != profile['targetSha256']:
            raise ValueError('参考配置不匹配当前文字素材，需重新核对段落')
        reference_song = next(item for item in songs if item['songId'] == profile['referenceSong'])
        if reference_song['sha256'] != profile['referenceSha256']:
            raise ValueError('参考谱面已改变，需重新核对')
        name = reference_song['source'] + '.json'
        reference_path = batch.ROOT / 'data/sync' / name
        if not reference_path.exists():
            reference_path = cache / 'ready' / name
        reference = json.loads(reference_path.read_text())
        reference_evidence = cache / batch.evidence_directory(reference) / name
        reference_result = json.loads(reference_evidence.read_text())
        if reference_result['status'] != 'ready':
            raise ValueError('参考谱面未通过匹配复核')
        if reference['source']['sha256'] != reference_song['sha256']:
            raise ValueError('参考同步数据已失效')
        track = next(item for item in reference['tracks'] if item['file'] == reference_song['reference'] and item['reviewed'])
        audio = batch.ROOT / 'data' / track['file']
        if track['sha256'] != batch.checksum(audio):
            raise ValueError('参考音频已改变')
        source = text_source(batch.ROOT / 'data' / song['source'])
        slots, count, per_verse = profile['referenceSlots'], profile['verseCount'], profile['referenceCuesPerVerse']
        cues = track.get('cues', reference['cues'])
        reference_count = profile.get('referenceVerseCount', count)
        if (len(source['verses']) != count or any(len(verse) != len(slots) for verse in source['verses']) or
                len(cues) != reference_count * per_verse or slots != sorted(set(slots)) or not slots or
                slots[0] != 0 or slots[-1] >= per_verse):
            raise ValueError('参考配置的段落或阅读区域数量不匹配')
        indices = [verse * per_verse + slot for verse in range(reference_count) for slot in slots]
        times = [cues[index]['at'] + track['offset'] for index in indices]
        target_audio = batch.ROOT / 'data' / song['reference']
        music = repeated_verses(target_audio, count, cache)
        if profile.get('mode') == 'verse-cycle':
            if slots != [0]:
                raise ValueError('完整乐段匹配仅适用于每段单个文字阅读区域')
            reference_music = reference_result['scoreMatching']['music']
            adjusted_cues = [{**cue, 'at': cue['at'] + track['offset']} for cue in cues]
            mapped, metrics = cycle_times(audio, target_audio, adjusted_cues, per_verse, reference_music, music, cache)
        else:
            if reference_count != count:
                raise ValueError('不同段数的录音需要完整乐段参考')
            mapped, metrics = map_recordings(audio, target_audio, times, cache)
        corrected, decisions = consistent_phases(mapped, music, count, len(slots))
        if decisions:
            audio_times, chroma = features(target_audio, cache)
            step = float(audio_times[1] - audio_times[0])
            comparisons = []
            first = round(corrected[0] / step)
            for verse in range(1, count):
                later = round(mapped[verse * len(slots)] / step)
                length = min(20, chroma.shape[1] - first, chroma.shape[1] - later)
                similarity = float(np.mean(np.sum(chroma[:, first:first + length] * chroma[:, later:later + length], axis=0)))
                comparisons.append(similarity)
            if min(comparisons) < .9:
                raise ValueError('首段修正缺少局部音乐对应')
            decisions[0]['localRepeatSimilarities'] = comparisons
        evidence['referenceMatching'].update(referenceSync=reference, cueIndices=indices,
            referenceAudio=track['file'], referenceTimes=times, mappedTimes=mapped,
            recordingMap=metrics, music=music, phaseDecisions=decisions)
        definitions = [item for verse in source['verses'] for item in verse]
        evidence['performanceOrder'] = definitions
        return export_result(song, batch.ROOT, cache, destination, evidence, start, source,
                             definitions, corrected, list(range(0, len(definitions), len(slots))))
    except (ValueError, FileNotFoundError, KeyError, StopIteration) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - start, 3))
        batch.save(destination, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name('sync-reference-map.json').read_text())
    inventory = batch.inventory()
    for song in inventory:
        if song['songId'] in args.songs and not song['existing']:
            print(generate(song, profiles[song['songId']], args.cache, inventory), flush=True)
