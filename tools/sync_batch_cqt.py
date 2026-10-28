"""Explicit, reproducible constant-Q audio features for reviewed score matching."""
import hashlib
import json
from pathlib import Path

import numpy as np


RECIPE = {'version': 1, 'kind': 'constant-q', 'librosaVersion': '0.11.0',
          'samplingRate': 16000, 'hopLength': 1600, 'minimumMidi': 36,
          'octaves': 5, 'binsPerOctave': 36, 'tuning': 0,
          'normalization': 'square-root, centered, column L2'}


def audio_feature_review(profile):
    if 'audioFeatures' not in profile:
        return None
    review = profile['audioFeatures']
    if (not isinstance(review, dict) or set(review) != {'version', 'kind', 'librosaVersion', 'reason'} or
            type(review.get('version')) is not int or review['version'] != RECIPE['version'] or
            review.get('kind') != RECIPE['kind'] or review.get('librosaVersion') != RECIPE['librosaVersion'] or
            not isinstance(review.get('reason'), str) or not review['reason'].strip()):
        raise ValueError('常Q音高分析须明确固定版本、算法、依赖版本及复核说明，不能指定音频截段或自由参数')
    return review


def cqt_features(path, cache):
    import librosa
    from faster_whisper.audio import decode_audio
    if librosa.__version__ != RECIPE['librosaVersion']:
        raise ValueError('常Q音高分析库版本与固定配置不符')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    metadata = {**RECIPE, 'sourceSha256': digest, 'numpyVersion': np.__version__}
    destination = Path(cache) / 'chroma-cqt-v1' / (digest + '.npz')
    if destination.exists():
        try:
            with np.load(destination, allow_pickle=False) as data:
                if json.loads(str(data['metadata'].item())) != metadata:
                    raise ValueError('常Q音高缓存与当前录音或固定参数不符')
                times, chroma = data['times'], data['chroma']
                stored_hash = str(data['featuresSha256'].item())
        except (KeyError, OSError, json.JSONDecodeError) as error:
            raise ValueError('常Q音高缓存不完整') from error
    else:
        audio = decode_audio(str(path), sampling_rate=RECIPE['samplingRate'])
        chroma = librosa.feature.chroma_cqt(y=audio, sr=RECIPE['samplingRate'],
            hop_length=RECIPE['hopLength'], fmin=librosa.midi_to_hz(RECIPE['minimumMidi']),
            n_octaves=RECIPE['octaves'], bins_per_octave=RECIPE['binsPerOctave'],
            tuning=RECIPE['tuning'], norm=None)
        chroma = np.sqrt(chroma)
        chroma -= chroma.mean(axis=0)
        chroma /= np.maximum(np.linalg.norm(chroma, axis=0), 1e-8)
        chroma = chroma.astype(np.float32)
        times = np.arange(chroma.shape[1], dtype=np.float32) * np.float32(.1)
        stored_hash = None
    if (times.dtype != np.float32 or chroma.dtype != np.float32 or
            times.ndim != 1 or len(times) < 2 or chroma.shape != (12, len(times)) or
            not np.isfinite(times).all() or not np.isfinite(chroma).all() or
            not np.array_equal(times, np.arange(len(times), dtype=np.float32) * np.float32(.1))):
        raise ValueError('常Q音高缓存的完整时间轴或十二音级数据无效')
    feature_hash = hashlib.sha256(times.tobytes() + chroma.tobytes()).hexdigest()
    if stored_hash is not None and stored_hash != feature_hash:
        raise ValueError('常Q音高缓存内容哈希不符')
    if stored_hash is None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.npz.tmp')
        with temporary.open('wb') as stream:
            np.savez_compressed(stream, metadata=json.dumps(metadata, sort_keys=True),
                                times=times, chroma=chroma, featuresSha256=feature_hash)
        temporary.replace(destination)
    return (times, chroma), {**metadata, 'frames': len(times), 'featuresSha256': feature_hash}
