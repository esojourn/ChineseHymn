#!/usr/bin/env python3
"""Resumable offline batch preparation; caches are keyed by original asset hashes."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def inventory(root=ROOT):
    songs = []
    for source in sorted((root / 'data').iterdir()):
        if source.suffix.lower() not in ('.txt', '.jpg'):
            continue
        tracks = [path for path in [source.with_suffix('.mp3'), source.with_name(source.stem + '-领唱.mp3')] if path.exists()]
        lead = next((path for path in tracks if path.stem.endswith('-领唱')), None)
        songs.append({'songId': re.match(r'\d{2}-\d{1,3}', source.name)[0], 'source': source.name,
                      'sha256': checksum(source), 'type': 'text' if source.suffix == '.txt' else 'image',
                      'tracks': [{'file': path.name, 'sha256': checksum(path)} for path in tracks],
                      'reference': (lead or tracks[0]).name if tracks else None,
                      'existing': (root / 'data/sync' / (source.name + '.json')).exists()})
    return songs


_model = None
_ocr = None


def transcribe(job):
    global _model
    song, cache, model_dir, model_name, threads = job
    audio = ROOT / 'data' / song['reference']
    destination = Path(cache) / 'asr' / (audio.name + '.json')
    fingerprint = checksum(audio)
    if destination.exists():
        previous = json.loads(destination.read_text())
        if previous.get('sha256') == fingerprint and previous.get('model') == model_name:
            return song['songId'], 'cached'
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(model_name, device='cpu', compute_type='int8', cpu_threads=threads,
                              download_root=model_dir, local_files_only=True)
    start = time.monotonic()
    language = None if song['songId'].startswith('06-') else 'zh'
    segments, info = _model.transcribe(str(audio), language=language, beam_size=2, word_timestamps=True,
                                      condition_on_previous_text=False, vad_filter=False, temperature=0)
    rows = [{'start': float(row.start), 'end': float(row.end), 'text': row.text,
             'avgLogprob': float(row.avg_logprob), 'noSpeechProbability': float(row.no_speech_prob),
             'words': [{'word': word.word, 'start': float(word.start), 'end': float(word.end),
                        'probability': float(word.probability)} for word in row.words or []]} for row in segments]
    save(destination, {'file': audio.name, 'sha256': fingerprint, 'model': model_name,
                       'language': info.language, 'duration': info.duration, 'segments': rows,
                       'elapsedSeconds': round(time.monotonic() - start, 3)})
    return song['songId'], f'{len(rows)} segments, {round(time.monotonic() - start, 1)} s'


def recognize_image(job):
    global _ocr
    song, cache, threads = job
    destination = Path(cache) / 'ocr' / (song['source'] + '.json')
    if destination.exists():
        previous = json.loads(destination.read_text())
        if previous.get('sha256') == song['sha256'] and previous.get('recognizerVersion') == 2:
            return song['songId'], 'cached'
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
    import numpy as np
    import cv2
    from PIL import Image
    from scipy.ndimage import binary_closing, label
    start = time.monotonic()
    image = np.array(Image.open(ROOT / 'data' / song['source']).convert('RGB'))
    gray = np.mean(image, axis=2)
    height, width = gray.shape
    # Deskew recognition only; map each band back to original coordinates below.
    sample = cv2.resize((gray < 150).astype(np.uint8), (600, round(height * 600 / width)))
    sh, sw = sample.shape
    angles = []
    for angle in np.arange(-2.5, 2.501, .1):
        matrix = cv2.getRotationMatrix2D((sw / 2, sh / 2), float(angle), 1)
        rotated = cv2.warpAffine(sample, matrix, (sw, sh))
        histogram = rotated[:, 25:-25].sum(axis=1)
        angles.append((float(np.sum(histogram.astype(float) ** 2)), -abs(angle), float(angle)))
    angle = max(angles)[2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
    image = cv2.warpAffine(image, matrix, (width, height), borderValue=(255, 255, 255))
    inverse = cv2.invertAffineTransform(matrix)
    gray = np.mean(image, axis=2)
    projection = (gray[:, int(width * .06):int(width * .97)] < 150).sum(axis=1)
    mask = binary_closing(projection > width * .03, structure=np.ones(max(2, round(height * .004))))
    labels, count = label(mask)
    rows = []
    for index in range(1, count + 1):
        ys = np.flatnonzero(labels == index)
        if len(ys) < height * .004:
            continue
        top, bottom = max(0, int(ys[0]) - 4), min(height, int(ys[-1]) + 5)
        # Recognize horizontal bands explicitly: whole-page OCR often merges verses into vertical columns.
        result, _ = _ocr(image[top:bottom], use_det=False, use_cls=False)
        if result:
            text, confidence = result[0][:2]
            corners = np.array([[0, top, 1], [width, top, 1], [0, bottom, 1], [width, bottom, 1]]) @ inverse.T
            rows.append({'top': round(max(0, float(corners[:, 1].min()) / height), 6),
                         'bottom': round(min(1, float(corners[:, 1].max()) / height), 6),
                         'recognitionBand': [top, bottom],
                         'text': text, 'confidence': float(confidence)})
    save(destination, {'source': song['source'], 'sha256': song['sha256'], 'width': width, 'height': height,
                       'recognizerVersion': 2, 'recognitionAngle': angle,
                       'rows': rows, 'elapsedSeconds': round(time.monotonic() - start, 3)})
    return song['songId'], f'{len(rows)} rows, {round(time.monotonic() - start, 1)} s'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['inventory', 'ocr', 'transcribe', 'align', 'score'], required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--model-dir', default='/tmp/chinese-hymn-models')
    parser.add_argument('--model', default='small')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--songs', nargs='*')
    args = parser.parse_args()
    songs = inventory()
    save(args.cache / 'inventory.json', songs)
    if args.stage == 'inventory':
        print(json.dumps({'sources': len(songs), 'existing': sum(song['existing'] for song in songs),
                          'withAudio': sum(bool(song['tracks']) for song in songs)}, ensure_ascii=False))
        return
    selected = [song for song in songs if not song['existing'] and (not args.songs or song['songId'] in args.songs)]
    selected = [song for song in selected if song['type'] == 'image'] if args.stage == 'ocr' else [song for song in selected if song['reference']]
    if args.stage in ('align', 'score'):
        from sync_batch_align import generate
        worker = generate
        if args.stage == 'score':
            selected = [song for song in selected if song['type'] == 'image']
    else:
        worker = recognize_image if args.stage == 'ocr' else transcribe
    jobs = [(song, str(args.cache), args.threads) if args.stage == 'ocr' else
            (song, str(args.cache), args.model_dir, args.threads, args.stage) if args.stage in ('align', 'score') else
            (song, str(args.cache), args.model_dir, args.model, args.threads) for song in selected]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(worker, job): job[0] for job in jobs}
        for count, future in enumerate(as_completed(tasks), 1):
            song = tasks[future]
            try:
                song_id, message = future.result()
                print(f'{count}/{len(jobs)} {song_id}: {message}', flush=True)
            except Exception as error:
                save(args.cache / 'errors' / (song['source'] + f'.{args.stage}.json'),
                     {'source': song['source'], 'stage': args.stage, 'error': str(error)})
                print(f'{count}/{len(jobs)} ERROR {song["songId"]}: {error}', flush=True)


if __name__ == '__main__':
    main()
