"""Source-pinned complete MIDI forms with local checks and recording correspondence."""
import hashlib
import io
import math

import numpy as np
from sync_batch_audio import features, map_recordings, onsets_agree
from sync_batch_sectional import arranged_midi_bytes, midi_reading_rows
from sync_batch_sources import clean

VOICE_NAME = 'Combined source vocal voices'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recording_features(path, cache):
    times, chroma = features(path, cache)
    # Legacy extraction returns float64 on its first call and stores float32.
    # Normalize this explicit mode to the stored representation on both paths.
    return np.asarray(times, dtype=np.float32), np.asarray(chroma, dtype=np.float32)


def validate_source(song, profile, source, root, cache):
    review = profile['fullArrangementReview']
    definitions = [item for verse in source['verses'] for item in verse]
    openings = profile.get('regionOpenings', [])
    if (not isinstance(review, dict) or set(review) != {'version', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            song.get('type') != 'text' or source.get('language') != 'zh' or len(definitions) < 4 or
            song['sha256'] != profile.get('targetSha256') or
            digest(root / 'data' / song['source']) != song['sha256'] or
            profile.get('tracks') != song['tracks'] or
            profile.get('sourceVerseShape') != [len(verse) for verse in source['verses']] or
            profile.get('sourceRegions') != [item['region'] for item in definitions] or
            len(set(profile['sourceRegions'])) != len(definitions) or
            len(openings) != len(source['verses']) or
            any(key in profile for key in ['referenceDiscovery', 'referenceCycle', 'opticalReference',
                'sourceChorusGroups', 'terminalFermataTempo', 'audioFeatures'])):
        raise ValueError('完整编排须绑定当前中文原文、全部录音、全部阅读区域及明确版本')
    for verse, texts in zip(source['verses'], openings):
        if len(verse) != len(texts) or any(not isinstance(text, str) or len(clean(text)) < 4 or
                not clean(item['text']).startswith(clean(text)) for item, text in zip(verse, texts)):
            raise ValueError('完整编排的原文起句已变化')
    reference = profile.get('referenceAudioFile')
    if reference not in {item['file'] for item in song['tracks']}:
        raise ValueError('完整编排参考录音必须是当前绑定音轨')
    for item in song['tracks']:
        if digest(root / 'data' / item['file']) != item['sha256']:
            raise ValueError('完整编排录音哈希已变化')
    printed = cache / profile['scoreReferenceFile']
    if digest(printed) != profile['scoreReferenceSha256'] or not printed.read_bytes().startswith(b'%PDF-'):
        raise ValueError('完整编排的原始配套谱面已变化')
    return definitions, reference


def reference_midi(profile, cache):
    import mido
    origin = profile['midiOrigin']
    path = cache / 'midi' / origin['file']
    if digest(path) != origin['sha256']:
        raise ValueError('完整编排的原始 MIDI 哈希不匹配')
    review = profile['midiArrangement']
    if (not isinstance(review, dict) or set(review) != {'version', 'voiceTracks', 'voiceTrackNames', 'recipe', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip()):
        raise ValueError('声部合并须明确绑定原始声部和原谱说明')
    score = mido.MidiFile(path)
    voices, names = review['voiceTracks'], review['voiceTrackNames']
    recipe = review['recipe']
    if (score.type != 1 or not isinstance(voices, list) or len(voices) < 2 or
            any(type(index) is not int or not 0 <= index < len(score.tracks) for index in voices) or
            voices != sorted(set(voices)) or not isinstance(names, list) or len(names) != len(voices) or
            any(score.tracks[index].name != name or not any(event.type == 'note_on' and
                event.velocity and event.channel != 9 for event in score.tracks[index])
                for index, name in zip(voices, names)) or
            not isinstance(recipe, dict) or set(recipe) != {'melodyTrack', 'melodyTrackName', 'quarterLength', 'sections'} or
            recipe.get('melodyTrack') != 1 or type(recipe['melodyTrack']) is not int or
            recipe.get('melodyTrackName') != VOICE_NAME):
        raise ValueError('合并声部与已绑定原谱不一致，不能改写音高、速度或音符')
    # Merge the reviewed vocal parts without changing any note, channel, pitch,
    # velocity or absolute tick. All remaining source tracks retain their notes.
    combined = mido.MidiFile(type=1, ticks_per_beat=score.ticks_per_beat)
    merged = mido.merge_tracks([score.tracks[index] for index in voices])
    merged.name = VOICE_NAME
    combined.tracks.append(mido.MidiTrack())
    combined.tracks.append(merged)
    combined.tracks.extend(track.copy() for index, track in enumerate(score.tracks) if index not in voices)
    stream = io.BytesIO()
    combined.save(file=stream)
    stream.seek(0)
    data = arranged_midi_bytes(stream, recipe)
    sha = hashlib.sha256(data).hexdigest()
    if sha != profile['sha256']:
        raise ValueError('完整编排派生 MIDI 与已核对的原谱结构不一致')
    destination = cache / 'midi' / profile['file']
    if destination.resolve() == path.resolve():
        raise ValueError('分析副本不能覆盖原始 MIDI')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    analysis = mido.MidiFile(file=io.BytesIO(data))
    tick, lead_in = 0, None
    for event in analysis.tracks[1]:
        tick += event.time
        if event.type == 'note_on' and event.velocity and event.channel != 9:
            lead_in = tick / analysis.ticks_per_beat
            break
    return destination, {'version': 1, 'sourceSha256': origin['sha256'], 'analysisSha256': sha,
        'voiceTracks': voices, 'voiceTrackNames': names, 'recipe': recipe,
        'leadingVocalRestQuarters': lead_in,
        'policy': 'merge original vocal events; preserve source pitches, ticks, sounding velocities and remaining parts'}


def checked(match, duration, margin):
    best = match['best']
    if (not all(math.isfinite(best[key]) for key in ['cost', 'span']) or
            not math.isfinite(match['runnerUp']['cost']) or
            best['cost'] > .35 or match['runnerUp']['cost'] - best['cost'] < margin or
            not .75 * duration <= best['span'] <= 1.25 * duration or
            not best['starts'] or any(not math.isfinite(at) or at < 0 for at in best['starts']) or
            any(a + .5 >= b for a, b in zip(best['starts'], best['starts'][1:]))):
        raise ValueError('完整编排的成本、调性区分、跨度或起点顺序未通过原有标准')
    return best


def reference_times(cli, period, harmony, rows, offsets, target, detail):
    duration = float(target[0][-1])
    complete = detail['completePerformance'] = {
        'harmony': cli.same_tune.match_cycle(harmony, target, 0, period, duration, 0, cue_offsets=offsets),
        'melody': cli.match_template(rows, target[1], target[0], duration, 0, 'spatialWeights')}
    a = checked(complete['harmony'], duration, .08)
    b = checked(complete['melody'], duration, .06)
    if (len(a['starts']) != len(offsets) or len(b['starts']) != len(offsets) or
            a['key'] != b['key'] or not onsets_agree(a['starts'], b['starts']) or
            not onsets_agree([a['starts'][0] + a['span']], [b['starts'][0] + b['span']])):
        raise ValueError('完整编排的两条整曲路径未共同确认全部分界及结尾')
    starts = [(x + y) / 2 for x, y in zip(a['starts'], b['starts'])]
    ends = starts[1:] + [(a['starts'][0] + a['span'] + b['starts'][0] + b['span']) / 2]
    lengths = [end - at for at, end in zip(starts, ends)]
    if (min(lengths) <= .5 or min(a['starts'][0], b['starts'][0]) >= .75 * min(lengths) or
            duration - min(a['starts'][0] + a['span'], b['starts'][0] + b['span']) >= .75 * min(lengths)):
        raise ValueError('完整编排的首尾或阅读区域跨度仍有未覆盖内容')
    local = detail['localReadings'] = []
    paths = []
    for index, (at, length) in enumerate(zip(starts, lengths)):
        lo, hi = offsets[index], offsets[index + 1] if index + 1 < len(offsets) else period
        selected = (harmony[0] >= lo) & (harmony[0] < hi)
        template = harmony[0][selected] - lo, harmony[1][:, selected]
        pair = {'harmony': cli.same_tune.match_cycle(template, target, 0, hi - lo, length, at, cue_offsets=[0]),
                'melody': cli.match_template([rows[index]], target[1], target[0], length, at, 'spatialWeights')}
        local.append(pair)
        one, two = checked(pair['harmony'], length, .08), checked(pair['melody'], length, .06)
        if (one['key'] != a['key'] or two['key'] != a['key'] or
                len(one['starts']) != 1 or len(two['starts']) != 1):
            raise ValueError('逐阅读区域调性或分界数量与完整编排不一致')
        four = [one['starts'][0], two['starts'][0], a['starts'][index], b['starts'][index]]
        if any(not onsets_agree([x], [y]) for i, x in enumerate(four) for y in four[i + 1:]):
            raise ValueError('完整编排的四条路径未在0.5秒内共同确认阅读位置')
        paths.append(four)
    times = [round(sum(four) / 4, 2) for four in paths]
    if any(a + .5 >= b for a, b in zip(times, times[1:])):
        raise ValueError('完整编排最终阅读时间轴未递增')
    detail.update(referencePeriod=period, readingOffsets=offsets, fourPathOnsets=paths, times=times)
    return times, a['key']


def recording_times(cli, reference, target_path, times, rows, reference_key, cache, detail):
    # Populate both legacy caches first so the existing recording mapper also
    # sees the same representation during cold and warm derivations.
    recording_features(reference, cache)
    target = recording_features(target_path, cache)
    mapped, metrics = map_recordings(reference, target_path, times, cache)
    detail['recordingMap'] = metrics
    if len(mapped) != len(rows) or len(mapped) != len(times):
        raise ValueError('录音音乐对应没有覆盖全部原文阅读区域')
    duration = float(target[0][-1])
    ends = mapped[1:] + [duration]
    matches = detail['localMelody'] = []
    for row, at, end in zip(rows, mapped, ends):
        match = cli.match_template([row], target[1], target[0], end - at, at, 'spatialWeights')
        matches.append(match)
        best = checked(match, end - at, .06)
        if (len(best['starts']) != 1 or best['key'] != (reference_key - metrics['semitoneShift']) % 12 or
                not onsets_agree([at], best['starts'])):
            raise ValueError('其他录音的音乐对应与自身原谱旋律起点不一致')
    detail['times'] = mapped
    return mapped


def generate_timelines(cli, song, profile, source, cache):
    definitions, reference = validate_source(song, profile, source, cli.batch.ROOT, cache)
    path, proof = reference_midi(profile, cache)
    period, harmony, melody = cli.midi_features(path)
    offsets, rows = midi_reading_rows(melody, profile['readingBoundaries'])
    if len(rows) != len(definitions) or offsets[0] > .1:
        raise ValueError('完整编排必须从首个主旋律音依次覆盖全部原文阅读区域')
    detail = {'mode': 'text-complete-arranged-midi', 'referenceDerivation': proof,
              'referenceTrack': reference, 'maximumCosts': {'harmony': .35, 'melody': .35}, 'recordings': {}}
    audio = cli.batch.ROOT / 'data' / reference
    measured = detail['recordings'][reference] = {'method': 'local and complete harmony/melody; all four paths within 0.5 seconds'}
    times, key = reference_times(cli, period, harmony, rows, offsets, recording_features(audio, cache), measured)
    timelines = {reference: {'definitions': definitions, 'times': times, 'returnIndices': []}}
    for item in song['tracks']:
        if item['file'] == reference:
            continue
        measured = detail['recordings'][item['file']] = {'method': 'complete chroma-DTW correspondence and independent per-reading MIDI melody'}
        mapped = recording_times(cli, audio, cli.batch.ROOT / 'data' / item['file'], times, rows, key, cache, measured)
        timelines[item['file']] = {'definitions': definitions, 'times': mapped, 'returnIndices': []}
    return definitions, timelines, detail
