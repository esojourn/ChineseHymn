#!/usr/bin/env python3
"""Rebuild reviewed musical sections and verify every reading boundary in real audio."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time
import urllib.request
from sync_batch_align import export_result
from sync_batch_audio import features
from sync_batch_reference_score import score_rows_hash
from sync_batch_score import note_rows
from sync_batch_sectional import arranged_midi_bytes, midi_reading_rows, optical_performance_rows, performance_times
from sync_batch_sources import clean, text_source
from sync_batch_symbolic import score_row_offsets


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


batch = load_module('sectional_batch_io', 'batch-sync.py')
midi = load_module('sectional_midi_checks', 'midi-sync.py')


def pinned_file(cache, item, prepare):
    path = cache / item['file']
    if not path.exists() and prepare:
        request = urllib.request.Request(item['url'], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=45) as stream:
            data = stream.read()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('下载的原始曲调或谱面与复核哈希不一致')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if batch.checksum(path) != item['sha256']:
        raise ValueError('原始曲调或谱面已变化，需重新复核')
    return path


def reference_material(profile, cache, prepare=False):
    reference = profile['midiReference']
    origin = pinned_file(cache, reference['origin'], prepare)
    data = arranged_midi_bytes(origin, reference['arrangement'])
    if hashlib.sha256(data).hexdigest() != reference['sha256']:
        raise ValueError('重建的乐曲结构与已核对 MIDI 不一致')
    path = cache / reference['file']
    if not path.exists() or path.read_bytes() != data:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    image_review = profile['opticalReference']
    if image_review.get('noteExtractorVersion') != 5:
        raise ValueError('分段原谱需要明确的数字规范化提取版本5')
    image = pinned_file(cache, image_review['image'], prepare)
    recognition_path = cache / image_review['recognitionFile']
    if prepare:
        ocr = load_module('sectional_reference_ocr', 'batch-sync.py')
        ocr.ROOT = image.parent.parent
        ocr.recognize_image(({'songId': 'reference', 'source': image.name,
                              'sha256': image_review['image']['sha256']}, str(recognition_path.parent.parent), 1))
    recognition = json.loads(recognition_path.read_text())
    if (recognition['source'] != image.name or recognition['sha256'] != image_review['image']['sha256'] or
            recognition['recognizerVersion'] != image_review['recognizerVersion']):
        raise ValueError('原谱识别文件不匹配当前固定图片')
    rows = note_rows(image, recognition, recognition_path.parent.parent,
                     reviewed_systems=image_review['reviewedSystems'], normalize_digits=True)
    if score_rows_hash(rows) != image_review['rowsSha256']:
        raise ValueError('原谱光学音符或节奏已变化')
    period, harmony, melody = midi.midi_features(path)
    offsets, melody_rows = midi_reading_rows(melody, profile['readingBoundaries'])
    optical_rows = optical_performance_rows(rows, image_review)
    matched, proof = score_row_offsets(optical_rows, melody['onsetTimes'], melody['onsetPitches'])
    if len(offsets) != len(matched) or any(round(abs(a - b), 3) > .1 for a, b in zip(offsets, matched)):
        raise ValueError('整曲 MIDI 与原谱的唯一阅读分界不一致')
    return melody_rows, optical_rows, {'referencePeriod': period, 'readingOffsets': offsets,
        'symbolicRows': proof, 'sourceRows': rows, 'opticalReadingRows': optical_rows}


def validate_source(song, profile):
    if (type(profile.get('version')) is not int or profile['version'] != 1 or song['type'] != 'text' or
            profile.get('sourceHash') != song['sha256'] or profile.get('tracks') != song['tracks'] or
            profile.get('audioFile') != song['reference']):
        raise ValueError('乐曲结构配置与当前歌词、全部录音或参考音轨不符')
    source = text_source(batch.ROOT / 'data' / song['source'])
    definitions = [item for verse in source['verses'] for item in verse]
    if (len(definitions) < 3 or profile.get('regions') != [item['region'] for item in definitions] or
            len(profile.get('regionOpenings', [])) != len(definitions) or
            any(len(clean(opening)) < 4 or not clean(item['text']).startswith(clean(opening))
                for item, opening in zip(definitions, profile['regionOpenings']))):
        raise ValueError('实际结构未完整绑定原文阅读区域及起句')
    window = profile['sampling']
    if (any(type(window.get(key)) not in (int, float) or not math.isfinite(window[key])
            for key in ['durationEstimate', 'onsetEstimate']) or
            window['durationEstimate'] <= 0 or window['onsetEstimate'] < 0):
        raise ValueError('整曲取样范围无效')
    return source, definitions


def generate(song, profile, cache, prepare=False):
    started = time.monotonic()
    destination = cache / 'midi-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {'algorithmVersion': 1,
                'mode': 'text-sectional-score', 'profile': profile,
                'maximumCosts': {'melody': .35, 'optical': .35}, 'keyMargin': .06,
                'maximumOnsetDifference': .5, 'spanRatio': [.75, 1.25]}}
    try:
        source, definitions = validate_source(song, profile)
        melody, optical, reference = reference_material(profile, cache, prepare)
        if len(melody) != len(definitions):
            raise ValueError('完整乐曲结构未覆盖全部正文阅读区域')
        evidence['midiMatching'].update(reference)
        evidence['performanceOrder'] = definitions
        target = features(batch.ROOT / 'data' / song['reference'], cache)
        proof = {}
        evidence['midiMatching']['performance'] = proof
        times = performance_times(melody, optical, target, profile['sampling']['durationEstimate'],
            profile['sampling']['onsetEstimate'], reference['symbolicRows']['semitoneShift'], midi.match_template, proof)
        return export_result(song, batch.ROOT, cache, destination, evidence, started, source,
                             definitions, times, [0])
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - started, 3))
        batch.save(destination, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    parser.add_argument('--prepare', action='store_true', help='Download pinned missing references and rebuild their OCR')
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name('sync-sectional-midi-profiles.json').read_text())
    if set(args.songs) - profiles.keys():
        parser.error('曲目缺少已复核的完整乐曲结构配置')
    for song in batch.inventory():
        if song['songId'] in args.songs and not song['existing']:
            print(generate(song, profiles[song['songId']], args.cache.resolve(), args.prepare), flush=True)
