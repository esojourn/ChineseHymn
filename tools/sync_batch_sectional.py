"""Verify an explicitly reviewed, nonuniform musical form against MIDI and print."""
import io
import math
import numpy as np


def reviewed_transpositions(review, key, count):
    if key not in review:
        return [0] * count
    values, detail = review[key], review.get('transpositionReview')
    if (not isinstance(values, list) or len(values) != count or
            any(type(value) is not int or not -12 <= value <= 12 for value in values) or
            not any(values) or not isinstance(detail, dict) or type(detail.get('version')) is not int or
            detail['version'] != 1 or not isinstance(detail.get('reason'), str) or not detail['reason'].strip()):
        raise ValueError('转调须逐段记录整数半音、明确版本及独立核对说明')
    return values


def arranged_midi_bytes(origin, recipe):
    import mido
    score = mido.MidiFile(file=origin) if hasattr(origin, 'read') else mido.MidiFile(origin)
    melody = recipe['melodyTrack']
    tracks = [i for i, track in enumerate(score.tracks) if any(
        event.type == 'note_on' and event.velocity and event.channel != 9 for event in track)]
    tempos = [event.tempo for event in mido.merge_tracks(score.tracks) if event.type == 'set_tempo']
    if (type(melody) is not int or melody not in tracks or
            score.tracks[melody].name != recipe['melodyTrackName'] or len(set(tempos)) != 1):
        raise ValueError('分段参考需要明确的旋律声部及单一原始速度')
    length, sections = recipe['quarterLength'], recipe['sections']
    maximum_tick = max(sum(event.time for event in track) for track in score.tracks)
    if (type(length) not in (int, float) or not math.isfinite(length) or
            not maximum_tick <= length * score.ticks_per_beat < maximum_tick + 4 * score.ticks_per_beat or
            not isinstance(sections, list) or not sections):
        raise ValueError('原始乐段范围或乐曲结构无效')
    for section in sections:
        if (not isinstance(section, list) or len(section) != 2 or
                any(type(at) not in (int, float) or not math.isfinite(at) or
                    abs(at * score.ticks_per_beat - round(at * score.ticks_per_beat)) > 1e-8 for at in section) or
                not 0 <= section[0] < section[1] <= length):
            raise ValueError('乐段必须引用原 MIDI 中完整、明确的拍数范围')
    transpositions = reviewed_transpositions(recipe, 'transposeSections', len(sections))
    result = mido.MidiFile(ticks_per_beat=score.ticks_per_beat)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage('set_tempo', tempo=tempos[0]))
    result.tracks.append(meta)
    for index in [melody] + [i for i in tracks if i != melody]:
        tick, active, notes = 0, {}, []
        for event in score.tracks[index]:
            tick += event.time
            if event.type not in ('note_on', 'note_off') or event.channel == 9:
                continue
            key = event.channel, event.note
            if event.type == 'note_on' and event.velocity:
                active.setdefault(key, []).append((tick, event.velocity))
            elif active.get(key):
                start, velocity = active[key].pop(0)
                notes.append((start, tick, event.channel, event.note, velocity))
        if any(active.values()):
            raise ValueError('原 MIDI 存在未结束的音符')
        offset, events = 0, []
        for (lo, hi), shift in zip(sections, transpositions):
            lo, hi = round(lo * score.ticks_per_beat), round(hi * score.ticks_per_beat)
            for start, end, channel, pitch, velocity in notes:
                if start < hi and end > lo:
                    if not 0 <= pitch + shift <= 127:
                        raise ValueError('转调后的音高超出 MIDI 范围')
                    events.extend([(offset + max(start, lo) - lo, 1, channel, pitch + shift, velocity),
                                   (offset + min(end, hi) - lo, 0, channel, pitch + shift, 0)])
            offset += hi - lo
        track, previous = mido.MidiTrack(), 0
        track.append(mido.MetaMessage('track_name', name=score.tracks[index].name))
        for at, sounding, channel, pitch, velocity in sorted(events):
            track.append(mido.Message('note_on' if sounding else 'note_off', channel=channel,
                                      note=pitch, velocity=velocity, time=at - previous))
            previous = at
        track.append(mido.MetaMessage('end_of_track', time=offset - previous))
        result.tracks.append(track)
    stream = io.BytesIO()
    result.save(file=stream)
    return stream.getvalue()


def optical_performance_rows(rows, review):
    keys = ['notes', 'pitchOffsets', 'rhythmWeights']
    ties, tuplets = review.get('ties', {}), review.get('tuplets', [])
    if any(str(i) not in {str(j) for j in range(len(rows))} for i in ties):
        raise ValueError('连音标记超出原谱行')
    multipliers = {}
    for group in tuplets:
        system, indices = group['system'], group['noteIndices']
        if (type(system) is not int or not 0 <= system < len(rows) or
                not isinstance(indices, list) or len(indices) != 3 or any(type(i) is not int for i in indices) or
                indices != list(range(indices[0], indices[0] + 3)) or
                group.get('actualNotes') != 3 or group.get('normalNotes') != 2 or
                indices[-1] >= len(rows[system]['notes']) or indices[0] < 0):
            raise ValueError('三连音须明确引用连续三个原谱音符')
        for index in indices:
            if (system, index) in multipliers:
                raise ValueError('三连音标记重复')
            multipliers[system, index] = 2 / 3
    for system, row in enumerate(rows):
        indices = ties.get(str(system), [])
        if indices != sorted(set(indices)) or any(type(i) is not int or not 0 < i < len(row['notes']) or
                not row['notes'][i] or row['notes'][i] != row['notes'][i - 1] or
                row['pitchOffsets'][i] != row['pitchOffsets'][i - 1] for i in indices):
            raise ValueError('连音只能合并已经目视核对的相邻同音音头')
    parts = {}
    for name, ranges in review['parts'].items():
        part = {key: [] for key in keys}
        for item in ranges:
            if not isinstance(item, list) or len(item) != 3:
                raise ValueError('印刷乐段必须明确引用谱行的音符范围')
            system, lo, hi = item
            if (any(type(value) is not int for value in item) or not 0 <= system < len(rows) or
                    not 0 <= lo < hi <= len(rows[system]['notes'])):
                raise ValueError('印刷乐段范围超出原谱音符')
            for group in tuplets:
                if group['system'] == system:
                    used = set(range(lo, hi)) & set(group['noteIndices'])
                    if used and used != set(group['noteIndices']):
                        raise ValueError('印刷乐段不能截断三连音')
            for index in range(lo, hi):
                row = rows[system]
                weight = row['rhythmWeights'][index] * multipliers.get((system, index), 1)
                if index in ties.get(str(system), []):
                    if index == lo:
                        raise ValueError('印刷乐段不能从连音的后半音头开始')
                    part['rhythmWeights'][-1] += weight
                else:
                    for key in keys:
                        part[key].append(weight if key == 'rhythmWeights' else row[key][index])
        if not part['notes']:
            raise ValueError('印刷乐段为空')
        parts[name] = part
    flat, starts = {key: [] for key in keys}, []
    transpositions = reviewed_transpositions(review, 'transposeParts', len(review['order']))
    for name, shift in zip(review['order'], transpositions):
        if name not in parts:
            raise ValueError('实际音乐结构引用了未定义的原谱乐段')
        starts.append(len(flat['notes']))
        for key in keys:
            flat[key].extend([value + shift if note and key == 'pitchOffsets' else value
                              for value, note in zip(parts[name][key], parts[name]['notes'])])
    cuts = []
    for section, note in review['readingStarts']:
        if (type(section) is not int or type(note) is not int or not 0 <= section < len(starts) or
                not 0 <= note < len(parts[review['order'][section]]['notes']) or
                parts[review['order'][section]]['notes'][note] == 0):
            raise ValueError('阅读区域必须从明确的原谱旋律音开始')
        cuts.append(starts[section] + note)
    if (not cuts or cuts != sorted(set(cuts)) or any(flat['notes'][:cuts[0]])):
        raise ValueError('阅读区域顺序无效或遗漏了首个旋律音')
    return [{key: values[a:b] for key, values in flat.items()}
            for a, b in zip(cuts, cuts[1:] + [len(flat['notes'])])]


def midi_reading_rows(melody, boundaries):
    offsets, previous = [], -1
    for item in boundaries:
        index, pitches = item['onsetIndex'], item['pitches']
        if (type(index) is not int or not previous < index < len(melody['onsetTimes']) or
                len(pitches) < 5 or melody['onsetPitches'][index:index + len(pitches)] != pitches):
            raise ValueError('阅读分界与已核对的 MIDI 旋律起音不符')
        offsets.append(melody['onsetTimes'][index])
        previous = index
    if not offsets or offsets[0] > .1:
        raise ValueError('阅读区域没有覆盖首个 MIDI 旋律音')
    cuts = [round(at / .1) for at in offsets] + [len(melody['notes'])]
    return offsets, [{key: melody[key][a:b] for key in ['notes', 'pitchOffsets', 'spatialWeights']}
                     for a, b in zip(cuts, cuts[1:])]


def check_match(match, duration, key=None):
    best = match['best']
    if (not all(np.isfinite(best[field]) for field in ['cost', 'span', 'key']) or
            not np.isfinite(match['runnerUp']['cost']) or not all(np.isfinite(best['starts'])) or
            best['cost'] > .35 or match['runnerUp']['cost'] - best['cost'] < .06 or
            not .75 * duration <= best['span'] <= 1.25 * duration or
            key is not None and best['key'] != key):
        raise ValueError('分段旋律路径未通过成本、调性或完整跨度核对')


def four_path_time(values):
    if len(values) != 4 or not all(np.isfinite(values)) or round(max(values) - min(values), 3) > .5:
        raise ValueError('整曲及逐段四条路径的阅读起点不一致')
    return round(float(np.mean(values)), 2)


def stable_outro_times(timelines):
    if (not timelines or not timelines[0] or any(len(row) != len(timelines[0]) for row in timelines) or
            any(not np.isfinite(at) for row in timelines for at in row) or
            any(round(max(values) - min(values), 3) > .5 for values in zip(*timelines))):
        raise ValueError('器乐尾奏参考导致正文阅读位置发生争议')
    return timelines[0]


def reading_section_groups(count, review=None):
    """Bind each reading to complete performed sections; explicitly account for instrumental gaps."""
    if review is None:
        return [[index] for index in range(count)]
    if (not isinstance(review, dict) or type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            not isinstance(review.get('groups'), list) or len(review['groups']) < 3):
        raise ValueError('正文乐段分组需要明确版本、完整分组及器乐间隔说明')
    groups = review['groups']
    for group in groups:
        if (not isinstance(group, list) or not group or
                any(type(index) is not int or not 0 <= index < count for index in group) or
                group != list(range(group[0], group[-1] + 1))):
            raise ValueError('每个正文阅读区域须引用连续、完整的实际乐段')
    used = [index for group in groups for index in group]
    omitted = review.get('instrumentalSections')
    if (used[0] != 0 or used != sorted(set(used)) or not isinstance(omitted, list) or
            any(type(index) is not int for index in omitted) or
            omitted != [index for index in range(count) if index not in used]):
        raise ValueError('正文乐段顺序重复或遗漏，未显示的乐段须逐项标明为器乐')
    return groups


def performance_times(melody_rows, optical_rows, target, duration, onset, reference_key, match, evidence,
                      local_match=None, reading_sections=None):
    if len(melody_rows) != len(optical_rows) or len(melody_rows) < 3:
        raise ValueError('完整结构至少需要三个明确的正文阅读区域')
    groups = reading_section_groups(len(melody_rows), reading_sections)
    one = match(melody_rows, target[1], target[0], duration, onset, 'spatialWeights')
    two = match(optical_rows, target[1], target[0], duration, onset, 'rhythmWeights')
    evidence.update(fullMelody=one, fullOptical=two, local=[])
    a, b = one['best'], two['best']
    check_match(one, duration)
    check_match(two, duration, (a['key'] + reference_key) % 12)
    if len(a['starts']) != len(melody_rows) or len(b['starts']) != len(melody_rows):
        raise ValueError('整曲路径没有覆盖全部阅读区域')
    if any(round(abs(a['starts'][group[0]] - b['starts'][group[0]]), 3) > .5 for group in groups):
        raise ValueError('两条整曲路径的阅读起点不一致')
    if reading_sections is not None:
        evidence['readingSectionReview'] = reading_sections
    times, spans = [], []
    verify_local = local_match or match
    for index, group in enumerate(groups):
        first, after = group[0], group[-1] + 1
        start = (a['starts'][first] + b['starts'][first]) / 2
        end = ((a['starts'][after] + b['starts'][after]) / 2 if after < len(melody_rows)
               else (a['starts'][0] + a['span'] + b['starts'][0] + b['span']) / 2)
        span = end - start
        if span <= .5:
            raise ValueError('整曲路径没有保持实际乐段顺序')
        def joined(rows, weight):
            return {key: [value for section in group for value in rows[section][key]]
                    for key in ['notes', 'pitchOffsets', weight]}
        local_rows = [melody_rows[index]] if reading_sections is None else [joined(melody_rows, 'spatialWeights')]
        printed_rows = [optical_rows[index]] if reading_sections is None else [joined(optical_rows, 'rhythmWeights')]
        local = verify_local(local_rows, target[1], target[0], span, start, 'spatialWeights')
        optical = verify_local(printed_rows, target[1], target[0], span, start, 'rhythmWeights')
        evidence['local'].append({'index': index, 'estimate': start, 'duration': span,
                                  'melody': local, 'optical': optical})
        if reading_sections is not None:
            evidence['local'][-1]['sections'] = group
        check_match(local, span, a['key'])
        check_match(optical, span, b['key'])
        times.append(four_path_time([a['starts'][first], b['starts'][first],
                                    local['best']['starts'][0], optical['best']['starts'][0]]))
        spans.append(span)
    if any(x + .5 >= y for x, y in zip(times, times[1:])):
        raise ValueError('分段时间轴未递增')
    if min(a['starts'][0], b['starts'][0]) >= .75 * min(spans) or (
            float(target[0][-1]) - min(a['starts'][0] + a['span'], b['starts'][0] + b['span']) >= .75 * min(spans)):
        raise ValueError('录音首尾仍可容纳完整乐段，需核对演唱结构')
    return times
