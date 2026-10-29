"""Resolve degenerate lyric starts with a publisher's complete numbered MIDI verse."""
import hashlib
import importlib.util
from pathlib import Path
import re
import numpy as np
from sync_batch_sources import clean


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quarter_second(score, quarter):
    import mido
    target, tick, seconds, tempo = quarter * score.ticks_per_beat, 0, 0., 500000
    for message in mido.merge_tracks(score.tracks):
        if tick + message.time >= target:
            return seconds + mido.tick2second(target - tick, score.ticks_per_beat, tempo)
        seconds += mido.tick2second(message.time, score.ticks_per_beat, tempo)
        tick += message.time
        if message.type == 'set_tempo':
            tempo = message.tempo
    raise ValueError('原始 MIDI 未覆盖完整主歌分界')


def verse_reference(review, cache, midi_features):
    import mido
    path, printed = cache / review['file'], cache / review['scoreFile']
    if (checksum(path) != review['sha256'] or checksum(printed) != review['scoreSha256'] or
            not printed.read_bytes().startswith(b'%PDF-')):
        raise ValueError('主歌 MIDI 或出版方配套乐谱已变化')
    score = mido.MidiFile(path)
    melodic = [i for i, track in enumerate(score.tracks) if any(
        message.type == 'note_on' and message.velocity and message.channel != 9 for message in track)]
    if (score.type != 1 or not melodic or type(review['melodyTrack']) is not int or
            melodic[0] != review['melodyTrack'] or score.tracks[melodic[0]].name != review['melodyTrackName']):
        raise ValueError('完整主歌须使用原始 MIDI 的首个旋律声部')
    lyrics, tick = [], 0
    for message in score.tracks[melodic[0]]:
        tick += message.time
        if message.type == 'lyrics' and message.text.strip():
            lyrics.append((tick / score.ticks_per_beat, message.text))
    numbered = [(at, text, int(match[1])) for at, text in lyrics
                if (match := re.match(r'^\s*([12])[.．]\s*\S', text))]
    if (len(numbered) != 2 or numbered[0][0] != 0 or [row[2] for row in numbered] != [1, 2]):
        raise ValueError('参考须从原始编号第1节起音完整延伸到第2节起音')
    end = numbered[1][0]
    middle = [at for at, text in lyrics if 0 < at < end and text.strip() == review['openingLyric'].strip()]
    if len(middle) != 1 or middle[0] * 2 != end:
        raise ValueError('主歌前半段须在原始词音对应的正中分句结束')
    notes = []
    for track_index, track in enumerate(score.tracks):
        active, tick = {}, 0
        for message in track:
            tick += message.time
            if message.type not in ('note_on', 'note_off') or message.channel == 9:
                continue
            key = message.channel, message.note
            if message.type == 'note_on' and message.velocity:
                active.setdefault(key, []).append(tick / score.ticks_per_beat)
            elif active.get(key):
                start = active[key].pop()
                notes.append((start, tick / score.ticks_per_beat, message.note, track_index))
            else:
                raise ValueError('原始 MIDI 有未配对的音符')
        if any(active.values()):
            raise ValueError('原始 MIDI 有未闭合的音符')
    if any(start < end < stop or track == melodic[0] and start < middle[0] < stop
           for start, stop, pitch, track in notes):
        raise ValueError('完整主歌或其首旋律分句跨越未结束的旋律音符')
    # A lower vocal part may intentionally hold the previous word under the
    # soprano's next phrase. Analyze the original time window without editing it.
    sustains = [{'track': track, 'pitch': pitch, 'startQuarter': start, 'endQuarter': stop}
                for start, stop, pitch, track in notes if start < middle[0] < stop]
    polyphony = max(len({pitch for start, stop, pitch, track in notes if start <= at < stop})
                    for at in sorted({start for start, stop, pitch, track in notes if start < end}))
    if polyphony < 4:
        raise ValueError('主歌和声核对需要原始四音复调，不能以单旋律代替')
    _, harmony, melody = midi_features(path)
    templates = []
    for name, quarter in [('verse', end), ('opening', middle[0])]:
        period = quarter_second(score, quarter)
        count = sum(at < period for at in melody['onsetTimes'])
        if count < (12 if name == 'verse' else 6):
            raise ValueError('原始主歌及开头分句的完整旋律起音不足')
        selected = harmony[0] < period
        templates.append((name, period, (harmony[0][selected], harmony[1][:, selected]),
                          {key: np.asarray(melody[key])[selected].tolist()
                           for key in ['notes', 'pitchOffsets', 'spatialWeights']}))
    return templates, {'originalMidiSha256': review['sha256'], 'scoreSha256': review['scoreSha256'],
        'verseQuarterLength': end, 'openingQuarterLength': middle[0],
        'numberedLyrics': numbered, 'maximumDistinctSoundingPitches': polyphony, 'openingAccompanimentSustains': sustains,
        'policy': 'analyze original complete numbered first verse and its complete opening phrase; preserve all original notes and tempo changes; original file unchanged'}


def check_match(match, span, margin):
    best = match['best']
    if (not all(np.isfinite(best.get(key, np.nan)) for key in ['cost', 'span', 'key']) or
            not np.isfinite(match['runnerUp']['cost']) or best['cost'] > .35 or
            match['runnerUp']['cost'] - best['cost'] < margin or
            not .75 * span <= best['span'] <= 1.25 * span or len(best.get('starts', [])) != 1 or
            not np.isfinite(best['starts'][0])):
        raise ValueError('主歌 MIDI 的成本、调性区分或完整跨度未通过原有标准')
    return best


def region_groups(review, source, definitions):
    regions = review['regions']
    if (not isinstance(regions, list) or len(regions) < 3 or len(set(regions)) != len(regions) or
            any(region not in source['regions'] for region in regions)):
        raise ValueError('主歌边界复核须绑定至少三个不同的完整原文段落')
    groups, previous = [], -1
    for region in regions:
        indices = [i for i, item in enumerate(definitions) if item['region'] == region]
        if (not indices or indices != list(range(indices[0], indices[-1] + 1)) or
                indices[0] <= previous or indices[-1] + 1 >= len(definitions) or
                ''.join(clean(definitions[i]['text']) for i in indices) != clean(source['blocks'][region]['text'])):
            raise ValueError('主歌边界复核不能漏词、重排或合并非连续的原文片段')
        groups.append(indices)
        previous = indices[-1]
    prefixes = [clean(source['blocks'][region]['text'])[:4] for region in regions]
    if len(set(prefixes)) != 1 or len(prefixes[0]) < 4:
        raise ValueError('各节须保留相同的完整原文起句以交叉核对主歌旋律')
    return groups


def validate_degenerate_windows(rows, value):
    canonical = [row for row in rows if row['window'] in (0, 1)]
    if (len(canonical) != 2 or {row['window'] for row in canonical} != {0, 1} or any(
            row.get('positiveDurationFraction', 0) < .65 or row.get('meanProbability', 0) < .12 or
            row.get('firstWordEnd', 0) - row.get('firstWordStart', 0) >= .04 and
            row.get('firstWordProbability', 0) >= .12 for row in canonical)):
        raise ValueError('未通过的歌词起点不是有同旋律主歌印证的退化首词')
    if any(row.get('valid') and row.get('firstWordProbability', 0) >= .12 and
           row.get('firstWordEnd', 0) - row.get('firstWordStart', 0) >= .04 and
           round(abs(row['at'] - value), 3) > .5 for row in rows):
        raise ValueError('主歌 MIDI 边界与其他有效首词窗口冲突')


def recover(song, source, definitions, estimates, ranges, anchor_words, observations, review, cache, confirm):
    keys = {'version', 'reason', 'regions', 'file', 'sha256', 'url', 'scoreFile', 'scoreSha256', 'scoreUrl',
            'melodyTrack', 'melodyTrackName', 'openingLyric'}
    if (not isinstance(review, dict) or set(review) != keys or type(review['version']) is not int or review['version'] != 1 or
            not isinstance(review['reason'], str) or not review['reason'].strip() or song['type'] != 'text' or
            source.get('language') != 'zh'):
        raise ValueError('主歌 MIDI 边界复核需要明确版本、原文及出版方完整参考')
    spec = importlib.util.spec_from_file_location('boundary_midi', Path(__file__).with_name('midi-sync.py'))
    midi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(midi)
    groups = region_groups(review, source, definitions)
    templates, proof = verse_reference(review, cache, midi.midi_features)
    target = midi.features(midi.batch.ROOT / 'data' / song['reference'], cache)
    # Match the stable stored precision during both cold and warm extraction.
    target = np.asarray(target[0], dtype=np.float32), np.asarray(target[1], dtype=np.float32)
    matches, values, peers, key = [], {}, [], None
    for group in groups:
        index, after = group[0], group[-1] + 1
        span, estimate = estimates[after] - estimates[index], estimates[index]
        bounds = [estimate - 1.5, estimate + 1.5]
        record = {'index': index, 'region': definitions[index]['region'], 'asrOnsetBounds': bounds, 'paths': []}
        onsets = []
        for name, period, harmony, melody in templates:
            duration = span if name == 'verse' else span / 2
            harmonic = midi.same_tune.match_cycle(harmony, target, 0, period, duration, estimate,
                cue_offsets=[0], onset_bounds=bounds)
            melodic = midi.match_template([melody], target[1], target[0], duration, estimate,
                'spatialWeights', onset_bounds=bounds)
            a, b = check_match(harmonic, duration, .08), check_match(melodic, duration, .06)
            if a['key'] != b['key'] or key is not None and key != a['key']:
                raise ValueError('主歌完整及前半段的和声、旋律调性不一致')
            key = a['key']
            onsets.extend([a['starts'][0], b['starts'][0]])
            record['paths'].append({'part': name, 'expectedDuration': duration, 'harmony': harmonic, 'melody': melodic})
        if round(max(onsets) - min(onsets), 3) > .5 or any(not bounds[0] <= at <= bounds[1] for at in onsets):
            raise ValueError('主歌四条路径未在0.5秒内共同确认转写附近的起点')
        value = round(float(np.mean(onsets)), 2)
        values[index] = value
        record.update(fourPathOnsets=onsets, at=value)
        current = [{**row, 'index': 0} for row in observations if row['index'] == index]
        if index:
            try:
                original, _ = confirm(current, [estimate], [ranges[index]], [anchor_words[index]])
            except ValueError:
                pass
            else:
                if round(abs(original[0] - value), 3) > .5:
                    raise ValueError('主歌乐谱边界与已通过声学核对的同旋律段落冲突')
                peers.append({'index': index, 'acousticAt': original[0], 'midiAt': value})
        matches.append(record)
    if not peers:
        raise ValueError('主歌 MIDI 复核还须由另一节已通过原声学核对的主歌印证')
    times, decisions = [0], [{'index': 0, 'at': 0, 'method': 'initial reading position; sung onset not measured'}]
    for index in range(1, len(definitions)):
        current = [{**row, 'index': 0} for row in observations if row['index'] == index]
        try:
            at, detail = confirm(current, [estimates[index]], [ranges[index]], [anchor_words[index]])
        except ValueError:
            if index not in values:
                raise ValueError('未通过的歌词起点不是有同旋律主歌印证的退化首词')
            validate_degenerate_windows(current, values[index])
            at = [values[index]]
            detail = [{'at': at[0], 'method': 'original numbered MIDI verse and opening phrase; harmony/melody four-path confirmation',
                       'asrWordRange': ranges[index]}]
        times.extend(at)
        decisions.extend({**row, 'index': index} for row in detail)
    if any(a + .5 >= b for a, b in zip(times, times[1:])):
        raise ValueError('主歌 MIDI 复核后歌词起点未递增')
    return times, decisions, {'version': 1, 'review': review, 'reference': proof, 'matches': matches,
                              'acousticPeerChecks': peers, 'changedIndices': [row['index'] for row in decisions
                                  if row.get('method', '').startswith('original numbered MIDI verse')]}
