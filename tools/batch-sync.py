#!/usr/bin/env python3
"""Resumable offline batch preparation; caches are keyed by original asset hashes."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Separate process namespaces may share a PID while writing the same cache.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def evidence_directory(candidate):
    name = candidate['provenance']['evidence']
    for suffix, directory in [('.transcript.json', 'transcript-alignment'), ('.midi.json', 'midi-alignment'),
                              ('.reference.json', 'reference-alignment'), ('.score.json', 'score-alignment')]:
        if name.endswith(suffix):
            return directory
    return 'alignment'


def inventory(root=ROOT):
    songs = []
    for source in sorted((root / 'data').iterdir()):
        if source.suffix.lower() not in ('.txt', '.jpg'):
            continue
        tracks = [path for path in [source.with_suffix('.mp3'), source.with_name(source.stem + '-领唱.mp3')] if path.exists()]
        lead = next((path for path in tracks if path.stem.endswith('-领唱')), None)
        lyrics_available = True
        if source.suffix == '.txt':
            from sync_batch_sources import prepare
            lines = prepare.read_text(source).split('\n')
            body = '\n'.join(line for line in lines[1:] if not re.match(r'^\s*(?:https?://|歌词整理中)', line))
            lyrics_available = len(re.findall(r'[\u3400-\u9fffA-Za-z]', body)) >= 12
        songs.append({'songId': re.match(r'\d{2}-\d{1,3}', source.name)[0], 'source': source.name,
                      'sha256': checksum(source), 'type': 'text' if source.suffix == '.txt' else 'image',
                      'tracks': [{'file': path.name, 'sha256': checksum(path)} for path in tracks],
                      'reference': (lead or tracks[0]).name if tracks else None,
                      'lyricsAvailable': lyrics_available,
                      'existing': (root / 'data/sync' / (source.name + '.json')).exists()})
    return songs


def progress(songs, cache):
    from collections import Counter
    items = []
    for song in songs:
        destination = ROOT / 'data/sync' / (song['source'] + '.json')
        data = json.loads(destination.read_text()) if destination.exists() else None
        status = ('published' if data.get('provenance', {}).get('batch') == 'bulk-2026-10' else 'existing') if data else 'missing-audio' if not song['tracks'] else 'missing-lyrics' if not song['lyricsAvailable'] else 'pending'
        item = {'songId': song['songId'], 'source': song['source'], 'type': song['type'], 'status': status,
                'tracks': [track['file'] for track in song['tracks']]}
        if data and any(not track['reviewed'] for track in data['tracks']):
            item['status'] = 'partially-published'
            item['pendingTracks'] = [track['file'] for track in data['tracks'] if not track['reviewed']]
        if not data and song['tracks'] and song['lyricsAvailable']:
            ready = cache / 'ready' / (song['source'] + '.json')
            if ready.exists():
                candidate = json.loads(ready.read_text())
                directory = evidence_directory(candidate)
                evidence_path = cache / directory / ready.name
                evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
                current = (candidate['source']['sha256'] == song['sha256'] and
                           sorted((track['file'], track['sha256']) for track in candidate['tracks']) ==
                           sorted((track['file'], track['sha256']) for track in song['tracks']) and
                           evidence.get('status') == 'ready' and evidence.get('sourceHash') == song['sha256'])
                if current:
                    item['status'] = 'ready-to-verify' if all(track['reviewed'] for track in candidate['tracks']) else 'needs-track-alignment'
                else:
                    item['status'] = 'needs-calibration'
                    item['reason'] = evidence.get('reason', '候选或复核记录已失效，需重新匹配当前素材')[:250]
            else:
                for directory in ['transcript-alignment', 'midi-alignment', 'reference-alignment', 'score-alignment', 'alignment']:
                    evidence = cache / directory / (song['source'] + '.json')
                    if evidence.exists():
                        result = json.loads(evidence.read_text())
                        if result.get('sourceHash') == song['sha256'] and result['status'] == 'needs-calibration':
                            item['status'] = 'needs-calibration'
                            item['reason'] = result['reason'][:250]
                            break
        items.append(item)
    report = {'date': time.strftime('%Y-%m-%d'), 'totalSources': len(items),
              'counts': dict(Counter(item['status'] for item in items)), 'songs': items}
    save(ROOT / 'docs/sync-bulk-progress.json', report)
    return report['counts']


def publish(song, cache):
    candidate = cache / 'ready' / (song['source'] + '.json')
    data = json.loads(candidate.read_text())
    if data['source']['sha256'] != song['sha256'] or any(not track['reviewed'] for track in data['tracks']):
        raise ValueError('素材哈希不匹配，或仍有未校准的音轨')
    if sorted((track['file'], track['sha256']) for track in data['tracks']) != sorted((track['file'], track['sha256']) for track in song['tracks']):
        raise ValueError('音频素材与候选制作时不一致')
    # Recompute geometry from the current OCR rules; timing arrays remain untouched.
    if song['type'] == 'image':
        from sync_batch_sources import image_source
        definition = image_source(json.loads((cache / 'ocr' / candidate.name).read_text()))
        if set(definition['regions']) != set(data['regions']):
            raise ValueError('阅读区域发生结构变化，需重新匹配时间轴')
        data['regions'] = definition['regions']
    script = "const fs=require('node:fs'),c=require('./css/lyric-sync-core');const e=c.validate(JSON.parse(fs.readFileSync(0,'utf8')));if(e.length){console.error(e);process.exit(1)}"
    subprocess.run(['node', '-e', script], cwd=ROOT, input=json.dumps(data), text=True, check=True)
    directory = evidence_directory(data)
    evidence = json.loads((cache / directory / candidate.name).read_text())
    if evidence['status'] != 'ready' or evidence['sourceHash'] != song['sha256']:
        raise ValueError('缺少匹配当前素材的成功复核记录')
    targets = [(ROOT / data['provenance']['evidence'], evidence), (ROOT / 'data/sync' / candidate.name, data)]
    if any(path.exists() for path, value in targets):
        raise ValueError('拒绝覆盖已有同步文件或复核记录')
    created = []
    try:
        for path, value in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                created.append(path)
                stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    except Exception:
        for path in created:
            path.unlink()
        raise


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
    checkpoint = Path(cache) / 'asr-windows' / (audio.name + '.json')
    previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    current = (previous.get('version') == 1 and previous.get('sha256') == fingerprint and previous.get('model') == model_name)
    rows = list(previous.get('segments', [])) if current else []
    seek = int(previous.get('seek', 0)) if current else 0
    elapsed = float(previous.get('elapsedSeconds', 0)) if current else 0
    language = previous.get('language') if current else None if song['songId'].startswith('06-') else 'zh'
    frame_seconds = _model.feature_extractor.time_per_frame
    segments, info = _model.transcribe(str(audio), language=language, beam_size=2, word_timestamps=True,
                                      condition_on_previous_text=False, vad_filter=False, temperature=0,
                                      clip_timestamps=[seek * frame_seconds])
    for row in segments:
        if row.seek < seek:
            raise ValueError('转写窗口发生回退，保留上一个完整断点供复核')
        if row.seek > seek:
            # A new chunk proves all captions from the preceding chunk are done.
            # Never persist a half-decoded chunk as if it were complete.
            seek = row.seek
            save(checkpoint, {'version': 1, 'file': audio.name, 'sha256': fingerprint, 'model': model_name,
                              'seek': seek, 'language': info.language, 'segments': rows,
                              'elapsedSeconds': round(elapsed + time.monotonic() - start, 3)})
            print(f'ASR checkpoint {audio.name}: {seek * frame_seconds:.2f}/{info.duration:.2f} s, {len(rows)} segments', flush=True)
        rows.append({'seek': int(row.seek), 'start': float(row.start), 'end': float(row.end), 'text': row.text,
                     'avgLogprob': float(row.avg_logprob), 'noSpeechProbability': float(row.no_speech_prob),
                     'words': [{'word': word.word, 'start': float(word.start), 'end': float(word.end),
                                'probability': float(word.probability)} for word in row.words or []]})
    save(destination, {'file': audio.name, 'sha256': fingerprint, 'model': model_name,
                       'language': info.language, 'duration': info.duration, 'segments': rows,
                       'elapsedSeconds': round(elapsed + time.monotonic() - start, 3)})
    return song['songId'], f'{len(rows)} segments, {round(time.monotonic() - start, 1)} s'


def recognize_image(job):
    global _ocr
    song, cache, threads, *options = job
    retry = bool(options and options[0])
    destination = Path(cache) / 'ocr' / (song['source'] + '.json')
    if destination.exists():
        previous = json.loads(destination.read_text())
        if not retry and previous.get('sha256') == song['sha256'] and previous.get('recognizerVersion', 0) >= 2:
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
    mask = binary_closing(projection > width * .06, structure=np.ones(max(2, round(height * .004))))
    labels, count = label(mask)
    bands = []
    for index in range(1, count + 1):
        ys = np.flatnonzero(labels == index)
        if len(ys) < height * .004:
            continue
        top, bottom = max(0, int(ys[0]) - 4), min(height, int(ys[-1]) + 5)
        bands.append((top, bottom))
    # Touching glyphs can join two or three printed lyric rows into one projection band.
    ordinary = sorted(bottom - top for top, bottom in bands if height * .012 <= bottom - top <= height * .04)
    line_height = ordinary[round((len(ordinary) - 1) * .75)] if ordinary else height * .025
    split = []
    for top, bottom in bands:
        pieces = max(1, round((bottom - top) / line_height)) if bottom - top > line_height * 1.65 else 1
        edges = np.linspace(top, bottom, pieces + 1).round().astype(int)
        split.extend(zip(edges[:-1].tolist(), edges[1:].tolist()))
    def read_row(top, bottom, trim=False):
        # Recognize horizontal bands explicitly: whole-page OCR often merges verses into vertical columns.
        left, right = 0, width
        if trim:
            columns = np.flatnonzero((gray[top:bottom] < 150).sum(axis=0) > 2)
            if not len(columns):
                return None
            left, right = max(0, int(columns[0]) - 8), min(width, int(columns[-1]) + 9)
        result, _ = _ocr(image[top:bottom, left:right], use_det=False, use_cls=False)
        if result:
            text, confidence = result[0][:2]
            corners = np.array([[0, top, 1], [width, top, 1], [0, bottom, 1], [width, bottom, 1]]) @ inverse.T
            return {'top': round(max(0, float(corners[:, 1].min()) / height), 6),
                    'bottom': round(min(1, float(corners[:, 1].max()) / height), 6),
                    'recognitionBand': [top, bottom], 'text': text, 'confidence': float(confidence)}
        return None

    rows = [row for top, bottom in split if (row := read_row(top, bottom)) is not None]
    recognition = {'source': song['source'], 'sha256': song['sha256'], 'width': width, 'height': height,
                   'recognizerVersion': 5, 'recognitionAngle': angle, 'rows': rows}
    from sync_batch_sources import image_source, IMAGE_VERSE
    try:
        image_source(recognition)
    except ValueError as error:
        missing = re.search(r'第 (\d+) 谱行缺少', str(error))
        if missing:
            notes = [row for row in rows if row['top'] > .07 and
                     len(re.findall(r'[\u3400-\u9fff]', row['text'])) <= 1 and len(re.findall('[1-7]', row['text'])) >= 4]
            index = int(missing[1]) - 1
            if index < len(notes):
                lo, hi = notes[index]['bottom'], notes[index + 1]['top'] if index + 1 < len(notes) else 1
                sparse_labels, count = label(binary_closing(projection > width * .025,
                    structure=np.ones(max(2, round(height * .004)))))
                recovered = []
                for band in range(1, count + 1):
                    ys = np.flatnonzero(sparse_labels == band)
                    if len(ys) < height * .01:
                        continue
                    top, bottom = max(0, int(ys[0]) - 4), min(height, int(ys[-1]) + 5)
                    if not lo <= (top + bottom) / 2 / height <= hi:
                        continue
                    row = read_row(top, bottom, trim=True)
                    if row and row['confidence'] >= .7 and len(re.findall(r'[\u3400-\u9fff]', row['text'])) >= 2:
                        recovered.append(row)
                merged = [row for row in rows if not any(new['top'] <= (row['top'] + row['bottom']) / 2 <= new['bottom']
                                                        for new in recovered)] + recovered
                candidate = {**recognition, 'rows': sorted(merged, key=lambda row: (row['top'] + row['bottom']) / 2)}
                expected = max([int(match[1]) for row in rows if row['top'] > notes[0]['bottom'] and
                                (match := IMAGE_VERSE.match(row['text'])) and int(match[1]) < 15] or [1])
                try:
                    source = image_source(candidate)
                    if len(source['systems']) == len(notes) and len(source['verses']) == expected:
                        recognition = {**candidate, 'sparseRowRecovery': {'system': index + 1, 'rows': recovered}}
                except ValueError:
                    pass
    recognition['elapsedSeconds'] = round(time.monotonic() - start, 3)
    save(destination, recognition)
    return song['songId'], f'{len(recognition["rows"])} rows, {round(time.monotonic() - start, 1)} s'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['inventory', 'ocr', 'transcribe', 'align', 'align-tracks', 'score', 'publish', 'report'], required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--model-dir', default=str(Path(__file__).resolve().parents[1] / 'sync-drafts/models'))
    parser.add_argument('--model', default='small')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--songs', nargs='*')
    parser.add_argument('--kind', choices=['text', 'image'], help='仅处理指定的歌词形式')
    parser.add_argument('--retry', action='store_true', help='重新处理失败候选；先尝试用已有窗口及音乐复核解决冲突')
    args = parser.parse_args()
    songs = inventory()
    save(args.cache / 'inventory.json', songs)
    if args.stage == 'report':
        print(json.dumps(progress(songs, args.cache), ensure_ascii=False))
        return
    if args.stage == 'publish':
        if not args.songs:
            parser.error('分批发布必须用 --songs 显式指定曲号')
        for song in songs:
            if song['songId'] in args.songs:
                publish(song, args.cache)
                print('Prepared for browser verification: ' + song['source'], flush=True)
        print(json.dumps(progress(inventory(), args.cache), ensure_ascii=False))
        return
    if args.stage == 'inventory':
        print(json.dumps({'sources': len(songs), 'existing': sum(song['existing'] for song in songs),
                          'withAudio': sum(bool(song['tracks']) for song in songs)}, ensure_ascii=False))
        return
    selected = [song for song in songs if not song['existing'] and (not args.songs or song['songId'] in args.songs)]
    if args.kind:
        selected = [song for song in selected if song['type'] == args.kind]
    if args.stage == 'align':
        selected = [song for song in selected if song['lyricsAvailable']]
    selected = [song for song in selected if song['type'] == 'image'] if args.stage == 'ocr' else [song for song in selected if song['reference']]
    if args.stage in ('align', 'align-tracks', 'score'):
        from sync_batch_align import generate, review_recordings
        worker = review_recordings if args.stage == 'align-tracks' else generate
        if args.stage == 'align-tracks':
            selected = [song for song in selected if song['type'] == 'text' and song['lyricsAvailable'] and
                        (args.cache / 'ready' / (song['source'] + '.json')).exists()]
        if args.stage == 'score':
            selected = [song for song in selected if song['type'] == 'image']
    else:
        worker = recognize_image if args.stage == 'ocr' else transcribe
    jobs = [(song, str(args.cache), args.threads, args.retry) if args.stage == 'ocr' else
            (song, str(args.cache), args.model_dir, args.threads, args.stage, args.retry) if args.stage in ('align', 'align-tracks', 'score') else
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
