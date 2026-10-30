#!/usr/bin/env python3
"""Verify a long shared backing-only prelude before aligning complete printed lyrics."""
import argparse
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from scipy.signal import correlate
from sync_batch_sectional import check_match, performance_times
from sync_batch_full_arrangement import digest

spec = importlib.util.spec_from_file_location('paired_sectional', Path(__file__).with_name('sectional-midi-sync.py'))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
RATE = 16000


def window_match(backing, lead, at, span, shift=None):
    left, count = round(at * RATE), round(span * RATE)
    x = backing[left:left + count]
    if len(x) != count or count < RATE // 2:
        raise ValueError('前奏音频窗口不足半秒或超出原始录音')
    if shift is None:
        lo, hi = max(0, left - RATE), min(len(lead), left + count + RATE)
    else:
        lo, hi = left + shift - 2, left + shift + count + 2
    if lo < 0 or hi > len(lead) or hi - lo < count:
        raise ValueError('伴奏对应超出原始领唱录音')
    y = lead[lo:hi]
    power = float(x @ x)
    if power / count <= 1e-10:
        if shift is None:
            raise ValueError('不能用静音确定两条录音的时差')
        aligned = lead[left + shift:left + shift + count]
        if float(aligned @ aligned) / count > 1e-10:
            raise ValueError('伴奏静音期间领唱录音存在额外声音')
        return {'at': at, 'span': span, 'silent': True, 'shiftSamples': shift}
    sums = np.r_[0., np.cumsum(y * y)]
    energies = sums[count:] - sums[:-count]
    correlations = correlate(y, x, mode='valid', method='fft') / np.sqrt(np.maximum(1e-20, power * energies))
    index = int(np.argmax(correlations))
    aligned = y[index:index + count]
    gain = float(x @ aligned / power)
    residual = aligned - gain * x
    total = float(aligned @ aligned)
    if total <= 1e-10 * count:
        raise ValueError('领唱对应窗口无可核对的有效声音')
    result = {'at': at, 'span': span, 'silent': False, 'shiftSamples': lo + index - left,
              'correlation': float(correlations[index]), 'gain': gain,
              'residualEnergyFraction': float(residual @ residual / total)}
    if any(not np.isfinite(result[key]) for key in ['correlation', 'gain', 'residualEnergyFraction']):
        raise ValueError('前奏音频对应含无效数值')
    return result


def verify_pair(backing, lead, end, body_start, next_reading):
    if not 8 <= end < body_start < next_reading - 16.5:
        raise ValueError('前奏核对需要完整长前奏及至少16秒的随后正文')
    anchor = next((window_match(backing, lead, float(at), 2) for at in np.arange(0, end - 2, 2)
                   if np.mean(backing[round(at * RATE):round((at + 2) * RATE)] ** 2) > 1e-10), None)
    if anchor is None or anchor['correlation'] < .98 or anchor['gain'] <= 0:
        raise ValueError('没有可靠的原始伴奏波形可确定全局时差')
    shift = anchor['shiftSamples']
    if shift < 2:
        # Keep the initial samples inside both files when searching +/-2 samples.
        raise ValueError('当前双录音前奏协议需要领唱有可核对的正向采样时差')
    windows = []
    for at in np.arange(0, end, 2):
        span = min(2., end - float(at))
        if span < .5:
            at, span = end - .5, .5
        windows.append(window_match(backing, lead, float(at), span, shift))
    active = [row for row in windows if not row['silent']]
    gains = [row['gain'] for row in active]
    if (len(active) < 4 or any(row['correlation'] < .98 or row['residualEnergyFraction'] > .04 or
            abs(row['shiftSamples'] - shift) > 2 for row in active) or
            max(gains) > 1.05 * min(gains) or min(gains) <= 0):
        raise ValueError('完整前奏未能以固定时差及稳定增益重现原始伴奏，不能省略声乐结构')
    baseline = float(np.mean([row['residualEnergyFraction'] for row in active]))
    after = [window_match(backing, lead, body_start + at, 2, shift) for at in range(0, 16, 2)]
    foreground = [row for row in after if not row['silent'] and
                  row['residualEnergyFraction'] >= max(.06, 3 * baseline) and
                  .95 * min(gains) <= row['gain'] <= 1.05 * max(gains)]
    if len(foreground) < 3:
        raise ValueError('随后正文未出现至少三组显著高于编解码残差的领唱差异')
    return {'version': 1, 'sampleRate': RATE, 'shiftSamples': shift, 'endInBacking': end,
            'bodyStartInBacking': body_start, 'introWindows': windows, 'bodyWindows': after,
            'meanIntroResidual': baseline, 'foregroundWindowCount': len(foreground),
            'policy': 'same bound backing and lead recordings; complete prefix is silence or >=0.98 correlated backing, <=4% residual, <=2 sample drift, <=5% gain range; at least three body windows have >=6% and >=3x intro residual'}


def seed_alignment(material, target, window):
    melody, optical, reference = material
    one = cli.midi.match_template(melody, target[1], target[0], window['durationEstimate'], window['onsetEstimate'], 'spatialWeights')
    two = cli.midi.match_template(optical, target[1], target[0], window['durationEstimate'], window['onsetEstimate'], 'rhythmWeights')
    check_match(one, window['durationEstimate'])
    check_match(two, window['durationEstimate'], (one['best']['key'] + reference['symbolicRows']['semitoneShift']) % 12)
    a, b = one['best']['starts'], two['best']['starts']
    if len(a) != len(melody) or len(b) != len(a) or any(abs(x - y) > .5 for x, y in zip(a, b)):
        raise ValueError('未裁取录音的两条整曲路径未共同确认全部正文分界')
    return {'melody': one, 'optical': two}


def validate_profile(song, profile):
    review = profile.get('pairedPreludeReview')
    if (not isinstance(review, dict) or set(review) != {'version', 'reason'} or
            type(review['version']) is not int or review['version'] != 1 or
            not isinstance(review['reason'], str) or not review['reason'].strip() or
            profile.get('language', 'zh') != 'zh' or len(song['tracks']) != 2 or
            profile.get('previousReadingContext') != {'version': 1} or
            any(key in profile for key in ['audioFeatures', 'readingSectionReview', 'sourceVerseContext',
                'vocalInterludes', 'referenceEndingSequence', 'instrumentalOutroReview',
                'instrumentalOutroAlternatives', 'independentRecordings'])):
        raise ValueError('双录音前奏核对须显式绑定中文完整原文和原始领唱/伴奏，不可叠加结构例外')
    backing = [row for row in song['tracks'] if '-领唱' not in row['file']]
    lead = [row for row in song['tracks'] if '-领唱' in row['file']]
    if len(backing) != 1 or len(lead) != 1 or backing[0]['file'] != song['reference'].replace('-领唱', ''):
        raise ValueError('前奏只能核对同一首歌的原始同名伴奏与领唱')
    for track in song['tracks']:
        if digest(cli.batch.ROOT / 'data' / track['file']) != track['sha256']:
            raise ValueError('双录音前奏素材哈希已变化')
    return backing[0], lead[0]


def generate(song, profile, cache, prepare=False):
    started = time.monotonic()
    destination = cache / 'midi-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {'mode': 'paired-prelude-sectional-score',
                'algorithmVersion': 1, 'profile': profile, 'recordings': {}}}
    try:
        source, definitions = cli.validate_source(song, profile)
        backing, lead = validate_profile(song, profile)
        material = cli.reference_material(profile, cache, prepare)
        if (len(material[0]) != len(definitions) or material[2]['symbolicRows']['editCost'] != 0 or
                profile['midiReference']['origin'].get('kind') != 'printed-numbered-score'):
            raise ValueError('全部原文阅读区域须完整对应独立原谱及零编辑旋律音头')
        from faster_whisper.audio import decode_audio
        samples = [decode_audio(str(cli.batch.ROOT / 'data' / track['file']), sampling_rate=RATE).astype(np.float64)
                   for track in [backing, lead]]
        active = next((at for at in range(0, 8, 2) if np.mean(samples[0][at * RATE:(at + 2) * RATE] ** 2) > 1e-10), None)
        if active is None:
            raise ValueError('前八秒没有可确定固定时差的原始伴奏')
        shift = window_match(*samples, active, 2)['shiftSamples'] / RATE
        timelines = evidence['independentTimelines'] = {}
        for track in song['tracks']:
            detail = evidence['midiMatching']['recordings'][track['file']] = {}
            target = cli.recording_features(cli.batch.ROOT / 'data' / track['file'], cache, profile, detail)
            seed = detail['uncroppedFullScore'] = seed_alignment(material, target, profile['sampling'])
            starts = [seed[key]['best']['starts'] for key in ['melody', 'optical']]
            first = min(row[0] for row in starts)
            offset = shift if track == lead else 0
            intro_end, body, next_reading = first - .5 - offset, first - offset, min(row[1] for row in starts) - offset
            detail['pairedPrelude'] = verify_pair(*samples, intro_end, body, next_reading)
            cut = float(target[0][np.flatnonzero(target[0] >= first - .5)[0]])
            selected = target[0] >= cut
            cropped = target[0][selected] - np.float32(cut), target[1][:, selected]
            proof = detail['performance'] = {}
            times = performance_times(material[0], material[1], cropped, float(cropped[0][-1]), first - cut,
                material[2]['symbolicRows']['semitoneShift'], cli.midi.match_template, proof, previous_context=True)
            absolute = [round(value + cut, 2) for value in times]
            if any(abs(value - row[i]) > .5 for i, value in enumerate(absolute) for row in starts):
                raise ValueError('前奏核对后的正文分界与未裁取整曲的实测路径冲突')
            detail.update(analysisStart=cut, measuredAbsoluteTimes=list(absolute))
            absolute[0] = 0
            timelines[track['file']] = {'definitions': definitions, 'times': absolute, 'returnIndices': []}
        evidence['midiMatching']['reference'] = material[2]
        evidence['performanceOrder'] = definitions
        evidence['initialReadingView'] = {'version': 1, 'region': definitions[0]['region'], 'at': 0,
            'policy': 'initial reading position from playback start', 'lyricOnsetMeasured': False}
        return cli.export_result(song, cli.batch.ROOT, cache, destination, evidence, started, source,
            definitions, timelines[song['reference']]['times'], [0])
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3))
        cli.batch.save(destination, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name('sync-paired-prelude-profiles.json').read_text())
    for song in cli.batch.inventory():
        if (song['songId'] in args.songs and song['songId'] in profiles and not song['existing'] and
                song['lyricsAvailable'] and song['tracks']):
            print(generate(song, profiles[song['songId']], args.cache.resolve(), args.prepare), flush=True)
