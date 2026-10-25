"""Reproduce an explicitly reviewed numbered-score melody for independent image checks."""
import hashlib
import io
import math
from pathlib import Path


def printed_reference_bytes(profile, source_hash):
    import mido
    reference = profile['printedRhythmReference']
    if (profile.get('mode') != 'symbolic-melody-and-optical' or
            reference.get('sourceSha256') != source_hash or profile.get('targetSha256') != source_hash):
        raise ValueError('人工谱面参考必须绑定当前原图，并使用独立光学旋律复核')
    tonic, rows = reference.get('tonicMidi'), reference.get('numberedRowsAndQuarterBeats')
    if type(tonic) is not int or not 12 <= tonic <= 108 or not isinstance(rows, list) or not rows:
        raise ValueError('人工谱面参考的调性或谱行无效')
    accidentals = reference.get('accidentals')
    if accidentals is not None and (
            not isinstance(accidentals, dict) or set(accidentals) != {'version', 'offsets', 'reason'} or
            type(accidentals.get('version')) is not int or accidentals['version'] != 1 or
            not isinstance(accidentals.get('reason'), str) or not accidentals['reason'].strip() or
            not isinstance(accidentals.get('offsets'), list) or len(accidentals['offsets']) != len(rows)):
        raise ValueError('原谱临时升降号须逐行绑定音头、明确版本及核对说明')
    score = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    score.tracks.append(track)
    track.append(mido.MetaMessage('set_tempo', tempo=500000))
    track.append(mido.MetaMessage('track_name', name='Manual transcription of a printed numbered melody'))
    scale, pending = [0, 0, 2, 4, 5, 7, 9, 11], 0
    changed = False
    for index, row in enumerate(rows):
        if not isinstance(row, list) or not row:
            raise ValueError('人工谱面参考包含空谱行')
        offsets = [0] * len(row) if accidentals is None else accidentals['offsets'][index]
        if (not isinstance(offsets, list) or len(offsets) != len(row) or
                any(type(offset) is not int or offset not in (-1, 0, 1) for offset in offsets)):
            raise ValueError('原谱临时升降号必须对应每个音头的整数半音')
        sounding = 0
        for item, offset in zip(row, offsets):
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError('人工谱面音符必须同时提供音级与拍数')
            note, beats = item
            if (type(note) is not int or not 0 <= note <= 7 or type(beats) not in (int, float) or
                    not math.isfinite(beats) or not 0 < beats <= 16 or abs(beats * 480 - round(beats * 480)) > 1e-8):
                raise ValueError('人工谱面音级或拍数无效')
            ticks = round(beats * 480)
            if note == 0:
                if offset:
                    raise ValueError('休止符不能附加升降号')
                pending += ticks
                continue
            sounding += 1
            changed = changed or offset != 0
            pitch = tonic + scale[note] + offset
            track.append(mido.Message('note_on', note=pitch, velocity=90, time=pending))
            track.append(mido.Message('note_off', note=pitch, velocity=0, time=ticks))
            pending = 0
        if sounding < 5:
            raise ValueError('人工谱面参考每行必须有至少五个可复核起音')
    if accidentals is not None and not changed:
        raise ValueError('原谱临时升降号配置没有声明实际升降音')
    track.append(mido.MetaMessage('end_of_track', time=pending))
    stream = io.BytesIO()
    score.save(file=stream)
    return stream.getvalue()


def materialize_printed_reference(profile, source_hash, path):
    data = printed_reference_bytes(profile, source_hash)
    if hashlib.sha256(data).hexdigest() != profile['sha256']:
        raise ValueError('人工谱面参考与已核对的 MIDI 哈希不匹配')
    path = Path(path)
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)
