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
        if json.loads(reference_evidence.read_text())['status'] != 'ready':
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
        if (len(source['verses']) != count or any(len(verse) != len(slots) for verse in source['verses']) or
                len(cues) != count * per_verse or slots != sorted(set(slots)) or not slots or
                slots[0] != 0 or slots[-1] >= per_verse):
            raise ValueError('参考配置的段落或阅读区域数量不匹配')
        indices = [verse * per_verse + slot for verse in range(count) for slot in slots]
        times = [cues[index]['at'] + track['offset'] for index in indices]
        target_audio = batch.ROOT / 'data' / song['reference']
        mapped, metrics = map_recordings(audio, target_audio, times, cache)
        music = repeated_verses(target_audio, count, cache)
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
