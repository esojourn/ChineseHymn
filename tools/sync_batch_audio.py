"""Music-derived correspondence between recordings. No duration-scaled cue placement."""
import hashlib
import json
from pathlib import Path
import numpy as np
from numba import njit
from scipy import signal


def onsets_agree(first, second, tolerance=.5):
    """Compare sampled audio times at millisecond precision, including the tolerance edge."""
    return (bool(first) and len(first) == len(second) and
            all(np.isfinite(a) and np.isfinite(b) and round(abs(float(a) - float(b)), 3) <= tolerance
                for a, b in zip(first, second)))


def features(path, cache):
    from faster_whisper.audio import decode_audio
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    destination = cache / 'chroma' / (digest + '.npz')
    if destination.exists():
        data = np.load(destination)
        return data['times'], data['chroma']
    audio = decode_audio(str(path), sampling_rate=16000)
    frequencies, times, spectrum = signal.stft(audio, 16000, nperseg=4096, noverlap=2496, boundary='zeros')
    chosen = (frequencies >= 70) & (frequencies <= 2400)
    midi = np.round(69 + 12 * np.log2(frequencies[chosen] / 440)).astype(int)
    magnitude = np.abs(spectrum[chosen])
    chroma = np.sqrt(np.stack([magnitude[midi % 12 == pitch].sum(axis=0) for pitch in range(12)]))
    chroma -= chroma.mean(axis=0)
    chroma /= np.maximum(np.linalg.norm(chroma, axis=0), 1e-8)
    destination.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(destination, times=times.astype(np.float32), chroma=chroma.astype(np.float32))
    return times, chroma


def repeated_verses(path, count, cache, audio_range=None):
    if count < 2:
        raise ValueError('单段或无法识别段数，需要语音或谱面独立定位')
    times, chroma = features(path, cache)
    if audio_range is not None:
        if (not isinstance(audio_range, (list, tuple)) or len(audio_range) != 2 or
                any(type(at) not in (int, float) or not np.isfinite(at) for at in audio_range) or
                not 0 <= audio_range[0] < audio_range[1] <= float(times[-1])):
            raise ValueError('音乐反复比较范围必须位于当前完整录音内')
        selected = (times >= audio_range[0]) & (times < audio_range[1])
        times, chroma = times[selected], chroma[:, selected]
        if len(times) < 2:
            raise ValueError('音乐反复比较范围过短')
        times = times - times[0]
    step = float(times[1] - times[0])
    duration = float(times[-1])
    low = max(6, (duration - 50) / count * .75)
    high = min(duration * .7, duration / count * 1.3)
    scores = []
    for lag in range(round(low / step), round(high / step) + 1):
        left = chroma[:, 40:-lag - 40]
        right = chroma[:, 40 + lag:-40]
        if left.shape[1] > 60:
            scores.append((float(np.mean(np.sum(left * right, axis=0))), lag))
    similarity, lag = max(scores)
    if similarity < .60:
        raise ValueError(f'未找到可靠的音乐重复：{similarity:.3f}')
    period = lag * step
    intro = max(0, duration - count * period - min(4, period * .08))
    intro = min(intro, period * .75)
    shifts = [0.0]
    for verse in range(1, count):
        expected = round(verse * period / step)
        start, end = round((intro + 2) / step), round((intro + period - 2) / step)
        if audio_range is not None:
            # The final source verse need not have the earlier instrumental gap.
            # Compare a shared window in every verse, retaining at least 75% of
            # the independently detected cycle and the existing similarity gates.
            end = min(end, chroma.shape[1] - (count - 1) * round(period / step) - 13)
            if end - start < .75 * period / step:
                raise ValueError('末段共有音乐跨度不足75%')
        matches = []
        for offset in range(max(1, expected - 12), min(chroma.shape[1] - end, expected + 13)):
            matches.append((float(np.mean(np.sum(chroma[:, start:end] * chroma[:, start + offset:end + offset], axis=0))), offset))
        if not matches:
            raise ValueError('实际录音段数与歌词不匹配')
        score, offset = max(matches)
        if score < .55:
            raise ValueError(f'第{verse + 1}段音乐对应不足：{score:.3f}')
        shifts.append(round(offset * step, 4))
    result = {'period': round(period, 4), 'similarity': similarity, 'initialOnsetEstimate': round(intro, 3), 'shifts': shifts}
    if audio_range is not None:
        result['comparisonRange'] = {'start': audio_range[0], 'end': audio_range[1],
                                     'minimumSharedCycleRatio': .75}
    return result


def alignment_band(n, m, lag):
    return max(150, abs(lag) + 20, abs(m - n - lag) + 20)


@njit(cache=True)
def constrained_path(cost, lag, band):
    n, m = cost.shape
    total = np.full((n + 1, m + 1), np.inf, dtype=np.float32)
    back = np.zeros((n, m), dtype=np.uint8)
    total[0, 0] = 0
    for i in range(n):
        for j in range(max(0, i + lag - band), min(m, i + lag + band + 1)):
            diagonal, vertical, horizontal = total[i, j], total[i, j + 1] + .08, total[i + 1, j] + .08
            direction = 0 if diagonal <= vertical and diagonal <= horizontal else 1 if vertical <= horizontal else 2
            total[i + 1, j + 1] = cost[i, j] + (diagonal if direction == 0 else vertical if direction == 1 else horizontal)
            back[i, j] = direction
    if not np.isfinite(total[n, m]):
        return np.empty((0, 2), dtype=np.int32)
    i, j = n - 1, m - 1
    path = []
    while i >= 0 and j >= 0:
        path.append((i, j))
        direction = back[i, j]
        if direction == 0:
            i -= 1
            j -= 1
        elif direction == 1:
            i -= 1
        else:
            j -= 1
    return np.array(path[::-1], dtype=np.int32)


def map_recordings(reference, target, cue_times, cache):
    ta, a = features(reference, cache)
    tb, b = features(target, cache)
    n, m = a.shape[1], b.shape[1]
    candidates = []
    for shift in range(12):
        shifted = np.roll(b, shift, axis=0)
        for lag in range(-213, 214, 2):
            lo, hi = max(100, -lag), min(n - 100, m - 100 - lag)
            if hi > lo:
                candidates.append((float(np.mean(np.sum(a[:, lo:hi:3] * shifted[:, lo + lag:hi + lag:3], axis=0))), shift, lag))
    if not candidates:
        raise ValueError('录音过短，不能可靠匹配')
    similarity, shift, lag = max(candidates)
    cost = np.ascontiguousarray(1 - a.T @ np.roll(b, shift, axis=0), dtype=np.float32)
    # Include both endpoints even if the lag search favors a later musical repeat.
    path = constrained_path(cost, lag, alignment_band(n, m, lag))
    if not len(path):
        raise ValueError('未找到连续的音乐对应路径')
    mapping = np.full(n, np.nan)
    for i in range(n):
        js = path[path[:, 0] == i, 1]
        if len(js):
            mapping[i] = np.median(tb[js])
    valid = np.isfinite(mapping)
    result = np.interp(cue_times, ta[valid], mapping[valid])
    mean_cost = float(np.mean(cost[path[:, 0], path[:, 1]]))
    local_costs = []
    for at in cue_times:
        nearby = np.abs(ta[path[:, 0]] - at) < 2
        local_costs.append(float(np.mean(cost[path[nearby, 0], path[nearby, 1]])))
    metrics = {'method': 'chroma-DTW', 'semitoneShift': shift, 'initialSimilarity': similarity,
               'meanPathCost': mean_cost, 'cueWindowCosts': local_costs}
    if mean_cost > .42 or any(value > .60 for value in local_costs) or np.any(np.diff(result) <= .5):
        raise ValueError('录音编排或音乐对应不可靠：' + json.dumps(metrics, ensure_ascii=False))
    return np.round(result, 2).tolist(), metrics
