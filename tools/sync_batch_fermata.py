"""Build a score-time analysis copy for a documented terminal MIDI fermata."""
from fractions import Fraction
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def terminal_score_fermata(path):
    try:
        root = ET.parse(path).getroot()
        part = root.find('part')
        if root.tag != 'score-partwise' or part is None:
            raise ValueError('需要完整 MusicXML 第一声部')
        divisions, total, pitched = 1, Fraction(0), []
        measures = part.findall('measure')
        for measure in measures:
            position, extent, previous = Fraction(0), Fraction(0), None
            voice = '1'
            for item in measure:
                if item.tag == 'attributes':
                    divisions = int(item.findtext('divisions', str(divisions)))
                    if divisions <= 0:
                        raise ValueError('无效谱面时值')
                elif item.tag in ('backup', 'forward'):
                    duration = Fraction(int(item.findtext('duration')), divisions)
                    if duration <= 0:
                        raise ValueError('无效声部时值')
                    position += (-1 if item.tag == 'backup' else 1) * duration
                    if position < 0:
                        raise ValueError('谱面声部超出小节')
                    extent = max(extent, position)
                elif item.tag == 'note':
                    duration = Fraction(int(item.findtext('duration', '0')), divisions)
                    chord = item.find('chord') is not None
                    voice = item.findtext('voice', voice if chord else '1')
                    if duration <= 0 or chord and previous is None:
                        raise ValueError('无效音符时值或和弦')
                    start = previous if chord else position
                    if not chord:
                        previous = start
                        position += duration
                    extent = max(extent, start + duration)
                    if voice == '1' and item.find('pitch') is not None:
                        pitched.append((total + start, total + start + duration,
                            item.find('notations/fermata') is not None, measure.get('number')))
            total += extent
        if not pitched:
            raise ValueError('谱面没有主旋律')
        first = min(note[0] for note in pitched)
        last = max(note[0] for note in pitched)
        fermatas = [note for note in pitched if note[0] == last and note[2] and
                    note[1] == total and note[3] == measures[-1].get('number')]
        if first != 0 or not fermatas:
            raise ValueError('只有完整谱面最后主旋律音的延长记号可以处理')
        return fermatas[0]
    except (ET.ParseError, TypeError, IndexError) as error:
        raise ValueError('无法核对原始 MusicXML 延长音') from error


def score_time_reference(path, profile, cache):
    if 'terminalFermataTempo' not in profile:
        return path, None
    review, complete = profile['terminalFermataTempo'], profile.get('fullPerformanceCheck')
    if (not isinstance(review, dict) or set(review) != {'version', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            not isinstance(complete, dict) or set(complete) != {'version'} or
            type(complete.get('version')) is not int or complete['version'] != 1 or
            any(key in profile for key in ['referenceDiscovery', 'referenceCycle', 'opticalReference'])):
        raise ValueError('末尾延长音处理须核对原始谱面并启用完整四路径检查，不能指定时间或速度')
    if checksum(path) != profile['sha256']:
        raise ValueError('原始 MIDI 哈希不匹配')
    xml = cache / profile['scoreReferenceFile']
    if checksum(xml) != profile['scoreReferenceSha256']:
        raise ValueError('原始 MusicXML 哈希不匹配')
    start, end, _, measure = terminal_score_fermata(xml)
    import mido
    score = mido.MidiFile(path)
    if score.type != 1 or score.ticks_per_beat <= 0:
        raise ValueError('需要同步多声部 MIDI')
    tempos, notes = [], []
    for index, track in enumerate(score.tracks):
        tick, active = 0, {}
        for message in track:
            tick += message.time
            if message.type == 'set_tempo':
                tempos.append((tick, index, message))
            if message.type not in ('note_on', 'note_off') or message.channel == 9:
                continue
            key = message.channel, message.note
            if message.type == 'note_on' and message.velocity:
                active.setdefault(key, []).append(tick)
            elif key in active:
                onset = active[key].pop()
                if not active[key]:
                    del active[key]
                if tick <= onset:
                    raise ValueError('无效 MIDI 音符时值')
                notes.append((onset, tick))
            else:
                raise ValueError('MIDI 存在未配对音符')
        if active:
            raise ValueError('MIDI 存在未结束音符')
    tempos.sort(key=lambda item: (item[0], item[1]))
    if not tempos or tempos[0][0] != 0 or not notes:
        raise ValueError('MIDI 缺少完整初始速度或音符')
    base = tempos[0][2].tempo
    foreign = [item for item in tempos if item[2].tempo != base]
    first = min(a for a, b in notes)
    last_on = max(a for a, b in notes)
    last_off = max(b for a, b in notes)
    if (not foreign or len({(tick, message.tempo) for tick, _, message in foreign}) != 1 or
            foreign[0][2].tempo <= base or not last_on < foreign[0][0] < last_off or
            Fraction(last_on - first, score.ticks_per_beat) != start or
            Fraction(last_off - first, score.ticks_per_beat) != end or
            any(b != last_off for a, b in notes if a <= foreign[0][0] < b) or
            any(tick > foreign[0][0] and tick < last_off for tick, _, _ in tempos) or
            not any(tick == last_off and message.tempo == base for tick, _, message in tempos)):
        raise ValueError('减速必须完全位于谱面末尾延长音内，其他速度和音符不能调整')
    slow, at = foreign[0][2].tempo, foreign[0][0]
    for _, _, message in foreign:
        message.tempo = base
    relative = Path('midi-score-time-v1') / (profile['sha256'] + '.mid')
    derived = cache / relative
    derived.parent.mkdir(parents=True, exist_ok=True)
    score.save(derived)
    return derived, {'version': 1, 'sourceSha256': profile['sha256'],
        'scoreSha256': profile['scoreReferenceSha256'], 'analysisFile': relative.as_posix(),
        'analysisSha256': checksum(derived), 'baseMicrosecondsPerQuarter': base,
        'terminalMicrosecondsPerQuarter': slow, 'replacedTempoEvents': len(foreign),
        'midiLeadInQuarters': str(Fraction(first, score.ticks_per_beat)),
        'terminalTempoQuarter': str(Fraction(at - first, score.ticks_per_beat)),
        'fermata': {'measure': measure, 'startQuarter': str(start), 'endQuarter': str(end)}}
