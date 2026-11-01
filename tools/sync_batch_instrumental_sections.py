"""Fixed harmonic separation and tempo-stable instrumental reading matches."""

import hashlib
import json
from pathlib import Path

import numpy as np

HARMONIC_RECIPE = {
    'version': 1,
    'librosaVersion': '0.11.0',
    'samplingRate': 16000,
    'stftLength': 4096,
    'stftHop': 512,
    'hpssKernel': 31,
    'hpssPower': 2.0,
    'hpssMargin': [1.0, 1.0],
    'cqtHop': 1600,
    'minimumMidi': 36,
    'octaves': 5,
    'binsPerOctave': 36,
    'tuning': 0,
    'normalization': 'square-root, centered, column L2',
}


def harmonic_cqt(path, cache, bounds):
    import librosa
    from faster_whisper.audio import decode_audio
    from sync_batch_wait_score import sha

    if librosa.__version__ != HARMONIC_RECIPE['librosaVersion'] or bounds != [170, 'end']:
        raise ValueError('器乐谐波分析须使用固定版本和复核范围')
    metadata = {**HARMONIC_RECIPE, 'sourceSha256': sha(path), 'bounds': bounds, 'numpyVersion': np.__version__}
    destination = Path(cache) / 'chroma-harmonic-cqt-v1' / (metadata['sourceSha256'] + '.npz')
    if destination.exists():
        with np.load(destination, allow_pickle=False) as stored:
            if json.loads(str(stored['metadata'].item())) != metadata:
                raise ValueError('器乐谐波缓存参数或原录音已变化')
            times, chroma = stored['times'], stored['chroma']
            expected_hash = str(stored['featuresSha256'].item())
    else:
        audio = decode_audio(str(path), sampling_rate=16000)
        clip = audio[170 * 16000 :]
        if len(clip) < 50 * 16000:
            raise ValueError('器乐末段复核范围不足')
        spectrum = librosa.stft(clip, n_fft=4096, hop_length=512)
        harmonic, _ = librosa.decompose.hpss(spectrum, kernel_size=31, power=2.0, margin=(1.0, 1.0))
        samples = librosa.istft(harmonic, hop_length=512, length=len(clip))
        chroma = librosa.feature.chroma_cqt(
            y=samples,
            sr=16000,
            hop_length=1600,
            fmin=librosa.midi_to_hz(36),
            n_octaves=5,
            bins_per_octave=36,
            tuning=0,
            norm=None,
        )
        chroma = np.sqrt(chroma)
        chroma -= chroma.mean(axis=0)
        chroma /= np.maximum(np.linalg.norm(chroma, axis=0), 1e-8)
        chroma = chroma.astype(np.float32)
        times = np.arange(chroma.shape[1], dtype=np.float32) * np.float32(0.1)
        expected_hash = None
    if (
        times.dtype != np.float32
        or chroma.dtype != np.float32
        or chroma.shape != (12, len(times))
        or not np.isfinite(chroma).all()
        or not np.array_equal(times, np.arange(len(times), dtype=np.float32) * np.float32(0.1))
    ):
        raise ValueError('器乐谐波缓存时间轴或音高数据无效')
    digest = hashlib.sha256(times.tobytes() + chroma.tobytes()).hexdigest()
    if expected_hash is not None and expected_hash != digest:
        raise ValueError('器乐谐波缓存内容哈希不符')
    if expected_hash is None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            np.savez_compressed(
                stream, metadata=json.dumps(metadata, sort_keys=True), times=times, chroma=chroma, featuresSha256=digest
            )
        temporary.replace(destination)
    return (times, chroma), {**metadata, 'featuresSha256': digest, 'timeOrigin': 170}


def agreeing_positions(matches):
    from sync_batch_wait_score import agreement

    starts = [match['best']['starts'] for match in matches.values()]
    if not starts or any(len(row) != len(starts[0]) for row in starts):
        raise ValueError('器乐路径的阅读区域数量不一致')
    return [agreement(list(values)) for values in zip(*starts)]


def stable_tempo_band(attempts, minimum):
    if minimum != 3:
        raise ValueError('器乐变奏须由三个连续速度估计确认')
    groups, current = [], []
    previous = None
    for attempt in attempts:
        duration = attempt['durationEstimate']
        if previous is not None and duration != previous + 2:
            raise ValueError('器乐速度复核网格不连续')
        previous = duration
        if attempt['accepted']:
            current.append(attempt)
        else:
            if len(current) >= minimum:
                groups.append(current)
            current = []
    if len(current) >= minimum:
        groups.append(current)
    if len(groups) != 1:
        raise ValueError('器乐变奏未取得唯一连续速度支持')
    selected = groups[0]
    positions = [agreeing_positions(row['matches']) for row in selected]
    from sync_batch_wait_score import agreement

    return [agreement(list(values)) for values in zip(*positions)], [row['durationEstimate'] for row in selected]
