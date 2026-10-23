"""Verify numbered-score row starts against all optimal symbolic MIDI alignments."""
import numpy as np
from numba import njit


@njit(cache=True)
def edit_costs(left, right):
    costs = np.empty((len(left) + 1, len(right) + 1), dtype=np.int32)
    costs[:, 0] = np.arange(len(left) + 1)
    costs[0, :] = np.arange(len(right) + 1)
    for i in range(len(left)):
        for j in range(len(right)):
            costs[i + 1, j + 1] = min(costs[i, j] + int(left[i] != right[j]),
                                      costs[i + 1, j] + 1, costs[i, j + 1] + 1)
    return costs


def documented_row_offsets(rows, onset_times, onset_pitches, anchors, key):
    """Verify explicit score/MIDI row witnesses for a documented tune variant."""
    if (len(anchors) != len(rows) or not 0 <= key <= 11 or len(onset_times) != len(onset_pitches) or
            any(not np.isfinite(at) or at < 0 for at in onset_times)):
        raise ValueError('逐行参考配置数量或调性无效')
    scale = [-1, 0, 2, 4, 5, 7, 9, 11]
    offsets, proofs, previous = [], [], -1
    for row, anchor in zip(rows, anchors):
        source_indices, midi_indices = anchor['sourceNoteIndices'], anchor['referenceNoteIndices']
        if (len(source_indices) < 5 or len(source_indices) != len(midi_indices) or source_indices[0] != 0 or
                source_indices != sorted(set(source_indices)) or midi_indices != sorted(set(midi_indices)) or
                source_indices[-1] >= len(row['notes']) or midi_indices[0] <= previous or
                midi_indices[-1] >= len(onset_pitches) or midi_indices[0] < 0):
            raise ValueError('逐行参考必须从首音开始并提供至少五个有序音符')
        pitches = [(scale[note] + offset) % 12 if note else -1 for note, offset in
                   zip(row['notes'], row.get('pitchOffsets', [0] * len(row['notes'])))]
        omitted = [i for i in range(source_indices[-1]) if i not in source_indices]
        if omitted != anchor.get('tiedSourceNoteIndices', []) or any(
                i == 0 or pitches[i] != pitches[i - 1] for i in omitted):
            raise ValueError('参考首句只能省略已经目视核对的同音连结音头')
        if any(pitches[i] < 0 or (pitches[i] + key) % 12 != onset_pitches[j]
               for i, j in zip(source_indices, midi_indices)):
            raise ValueError('原谱首句音符与指定 MIDI 位置不符')
        offsets.append(round(float(onset_times[midi_indices[0]]), 4))
        proofs.append({**anchor, 'at': offsets[-1], 'sourcePitches': [pitches[i] for i in source_indices],
                       'referencePitches': [onset_pitches[j] for j in midi_indices]})
        previous = midi_indices[-1]
    if not offsets or offsets[0] > .1 or any(a + .1 >= b for a, b in zip(offsets, offsets[1:])):
        raise ValueError('逐行参考的起拍或先后位置无效')
    return offsets, {'method': 'documented row openings; at least five exact pitches per system; printed optical melody independently verified in actual audio',
                     'semitoneShift': key, 'rowStarts': proofs, 'offsets': offsets}


def score_row_offsets(rows, onset_times, onset_pitches):
    expected, boundaries = [], []
    scale = [-1, 0, 2, 4, 5, 7, 9, 11]
    for row in rows:
        if len(row['notes']) != len(row.get('pitchOffsets', row['notes'])):
            raise ValueError('谱面音符与升降号数量不一致')
        pitches = [(scale[note] + offset) % 12 for note, offset in
                   zip(row['notes'], row.get('pitchOffsets', [0] * len(row['notes']))) if note]
        if len(pitches) < 3:
            raise ValueError('每个谱行至少需要三个可核对的音符')
        boundaries.append(len(expected))
        expected.extend(pitches)
    if (len(expected) < 8 or len(onset_times) != len(onset_pitches) or not onset_times or
            any(not np.isfinite(at) or at < 0 for at in onset_times) or
            any(a >= b for a, b in zip(onset_times, onset_times[1:]))):
        raise ValueError('谱面或 MIDI 旋律起点不完整')
    target = np.asarray(onset_pitches, dtype=np.int32)
    candidates = []
    for key in range(12):
        query = (np.asarray(expected, dtype=np.int32) + key) % 12
        forward = edit_costs(query, target)
        candidates.append((int(forward[-1, -1]), key, query, forward))
    candidates.sort(key=lambda item: (item[0], item[1]))
    cost, key, query, forward = candidates[0]
    evidence = {'method': 'all optimal global pitch-sequence alignments; exact row-opening notes',
                'noteCount': len(expected), 'midiNoteCount': len(target), 'editCost': cost,
                'runnerUpCost': candidates[1][0], 'semitoneShift': key}
    if cost / len(query) > .12 or (candidates[1][0] - cost) / len(query) < .08:
        raise ValueError('谱面音符与 MIDI 旋律对应不足或调性有歧义')
    backward = edit_costs(query[::-1].copy(), target[::-1].copy())
    offsets, diagnostics = [], []
    for boundary in boundaries:
        possible = [j for j in range(len(target)) if query[boundary] == target[j] and
                    forward[boundary, j] + backward[len(query) - boundary - 1, len(target) - j - 1] == cost]
        if not possible or max(onset_times[j] for j in possible) - min(onset_times[j] for j in possible) > .1:
            raise ValueError('MIDI 中存在多个同样合理的谱行起点')
        offsets.append(round(float(np.mean([onset_times[j] for j in possible])), 4))
        diagnostics.append({'scoreNoteIndex': boundary, 'possibleMidiNoteIndices': possible, 'at': offsets[-1]})
    if any(a + .1 >= b for a, b in zip(offsets, offsets[1:])):
        raise ValueError('MIDI 谱行起点不递增')
    evidence.update(rowStarts=diagnostics, offsets=offsets)
    return offsets, evidence
