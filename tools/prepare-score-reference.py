#!/usr/bin/env python3
"""Rebuild and verify the pinned optical references used by selected text MIDI profiles."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import urllib.request
from sync_batch_reference_score import score_rows_hash
from sync_batch_score import note_rows

ROOT = Path(__file__).resolve().parents[1]


def prepare(profile, cache, song_id):
    reference = profile['opticalReference']
    svg, image, recognition = [cache / reference[key] for key in ['svgFile', 'imageFile', 'recognitionFile']]
    if not svg.exists():
        request = urllib.request.Request(profile['scoreReferenceUrl'], headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': reference['pageUrl']})
        data = urllib.request.urlopen(request, timeout=60).read()
        if hashlib.sha256(data).hexdigest() != profile['scoreReferenceSha256']:
            raise ValueError('下载原谱与已核对的哈希不一致')
        svg.parent.mkdir(parents=True, exist_ok=True)
        svg.write_bytes(data)
    if hashlib.sha256(svg.read_bytes()).hexdigest() != profile['scoreReferenceSha256']:
        raise ValueError('原谱文件已改变')
    image.parent.mkdir(parents=True, exist_ok=True)
    temporary = image.with_suffix('.rendering.png')
    subprocess.run(['node', str(ROOT / 'tools/render-score-reference.cjs')],
        input=json.dumps({'svgFile': str(svg), 'imageFile': str(temporary), **reference['render']}),
        text=True, check=True)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != reference['imageSha256']:
        raise ValueError(f'渲染结果已改变，需重新核对：{temporary}')
    temporary.replace(image)
    spec = importlib.util.spec_from_file_location('reference_ocr', ROOT / 'tools/batch-sync.py')
    batch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(batch)
    # Reuse the image recognizer in a separate module with an isolated reference data directory.
    batch.ROOT = image.parent.parent
    reference_cache = recognition.parent.parent
    batch.recognize_image(({'songId': song_id, 'source': image.name, 'sha256': reference['imageSha256']},
                           str(reference_cache), 1))
    ocr = json.loads(recognition.read_text())
    if ocr['sha256'] != reference['imageSha256'] or ocr['recognizerVersion'] != reference['recognizerVersion']:
        raise ValueError('原谱识别版本或图片哈希不一致')
    rows = note_rows(image, ocr, reference_cache)
    if score_rows_hash(rows) != reference['rowsSha256']:
        raise ValueError('原谱光学音符或节奏已改变，需重新核对')
    print(song_id, 'verified printed reference', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name('sync-midi-references.json').read_text())
    for song_id in args.songs:
        prepare(profiles[song_id], args.cache.resolve(), song_id)
