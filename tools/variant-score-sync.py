#!/usr/bin/env python3
"""Align complete reading regions when a printed final verse varies the melody."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


batch = module('variant_batch', 'batch-sync.py')
midi = module('variant_midi', 'midi-sync.py')
from sync_batch_align import export_result
from sync_batch_audio import features, repeated_verses
from sync_batch_cqt import cqt_features
from sync_batch_score import match_template
from sync_batch_sources import text_source


def printed_rows(cache, profile):
    import cv2
    import pypdfium2
    from rapidocr_onnxruntime import RapidOCR
    from PIL import Image
    if str(pypdfium2.PYPDFIUM_INFO) != profile['pdfiumVersion']:
        raise ValueError('PDF 渲染库版本与已复核的原谱不符')
    source = cache / profile['pdf']['file']
    if batch.checksum(source) != profile['pdf']['sha256']:
        raise ValueError('原谱 PDF 哈希不符')
    document = pypdfium2.PdfDocument(str(source))
    recognizer = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    rows, proof = [], []
    for item in profile['scoreRows']:
        image = document[item['page']].render(scale=4).to_pil().convert('RGB')
        import io
        raw = io.BytesIO()
        image.save(raw, format='PNG')
        if hashlib.sha256(raw.getvalue()).hexdigest() != item['imageSha256']:
            raise ValueError('原谱页面渲染结果已改变')
        image = np.asarray(image.convert('L'))
        lo, hi = item['band']
        _, _, components, _ = cv2.connectedComponentsWithStats((image[lo:hi] < 150).astype('uint8'), 8)
        glyphs = sorted((int(x), int(y), int(w), int(h)) for x, y, w, h, n in components[1:]
                        if n > 15 and 4 < w < 25 and 14 <= h <= 22)
        notes, confidence = [], []
        for x, y, w, h in glyphs:
            crop = image[lo + y:lo + y + h, x:x + w]
            canvas = np.full((h + 12, w + 16), 255, np.uint8)
            canvas[6:6 + h, 8:8 + w] = crop
            canvas = cv2.resize(canvas, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            recognized, _ = recognizer.text_rec(cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR))
            char, probability = recognized[0][:2]
            if char not in '1234567' or len(char) != 1 or float(probability) < .94:
                raise ValueError('原谱音符识别结果不明确')
            notes.append(int(char))
            confidence.append(float(probability))
        if notes != item['notes'] or len(notes) != len(item['beats']):
            raise ValueError('逐音识别与已复核的原谱音符不符')
        if any(type(beat) not in (int, float) or beat not in (.5, 1, 1.5, 2, 4) for beat in item['beats']):
            raise ValueError('原谱人工节奏无效')
        rows.append({'notes': notes, 'rhythmWeights': item['beats']})
        proof.append({'page': item['page'], 'imageSha256': item['imageSha256'],
                      'band': item['band'], 'boxes': glyphs, 'confidence': confidence})
    return rows, proof


def checked_match(result, expected_span):
    best, other = result['best'], result['runnerUp']
    if (not all(np.isfinite(value) for value in (best['cost'], other['cost'], best['span'], expected_span)) or
            expected_span <= 0 or best['cost'] > .35 or other['cost'] - best['cost'] < .06 or
            not .75 * expected_span <= best['span'] <= 1.25 * expected_span):
        raise ValueError('谱面与音频的旋律、调性或跨度验证不足')
    return best


def generate(song, profile, cache):
    start = time.monotonic()
    destination = cache / 'midi-alignment' / (song['source'] + '.json')
    evidence = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                'trackHashes': song['tracks'], 'midiMatching': {'algorithmVersion': 1,
                'mode': 'text-variant-score', 'profile': profile}}
    try:
        if (song['sha256'] != profile['targetSha256'] or song['tracks'] != profile['tracks'] or
                len(song['tracks']) != 1 or batch.checksum(ROOT / 'data' / song['reference']) != song['tracks'][0]['sha256']):
            raise ValueError('歌词或录音与已复核的素材不符')
        source = text_source(ROOT / 'data' / song['source'])
        verses = source['verses']
        if len(verses) != len(profile['openings']) or any(len(verse) != 1 for verse in verses):
            raise ValueError('原文必须为逐段独立显示的完整阅读区域')
        definitions = [verse[0] for verse in verses]
        if any(not item['text'].startswith(opening) for item, opening in zip(definitions, profile['openings'])):
            raise ValueError('原文段首与核对的诗节顺序不符')
        score, glyphs = printed_rows(cache, profile)
        midi_path = cache / profile['midi']['file']
        if batch.checksum(midi_path) != profile['midi']['sha256']:
            raise ValueError('原始 MIDI 哈希不符')
        period, _, melody = midi.midi_features(midi_path)
        if (len(melody['onsetTimes']) < 5 or melody['onsetPitches'][:4] != profile['midi']['firstPitches'] or
                [int((pitch - melody['onsetPitches'][0]) % 12) for pitch in melody['onsetPitches'][:4]] != [0, 2, 5, 5] or
                score[0]['notes'][:4] != [5, 6, 1, 1]):
            raise ValueError('原谱与原始 MIDI 的四个共同起音不符')
        end = melody['onsetTimes'][4]
        count = round(end / .1)
        first = {key: melody[key][:count] for key in ['notes', 'pitchOffsets', 'spatialWeights']}
        audio = ROOT / 'data' / song['reference']
        music = repeated_verses(audio, len(verses), cache)
        stft = features(audio, cache)
        cqt, cqt_proof = cqt_features(audio, cache)
        decisions, starts = [], []
        for index, shift in enumerate(music['shifts']):
            estimate = music['initialOnsetEstimate'] + shift
            results, bests = {}, []
            for name, (times, chroma) in [('stft', stft), ('cqt', cqt)]:
                full = match_template(score, chroma, times, music['period'], estimate, 'rhythmWeights')
                local_period = end * music['period'] / period
                local = match_template([first], chroma, times, local_period, estimate, 'spatialWeights',
                                       onset_bounds=[max(0, estimate - 3), estimate + 3])
                full_best, local_best = checked_match(full, music['period']), checked_match(local, local_period)
                if (full_best['key'] + 7) % 12 != (local_best['key'] + melody['onsetPitches'][0]) % 12:
                    raise ValueError('谱面与 MIDI 的起音调性不一致')
                results[name] = {'completePrintedVerse': full, 'fourOriginalMidiOnsets': local}
                bests.extend([full_best['starts'][0], local_best['starts'][0]])
            if round(max(bests) - min(bests), 3) > .5:
                raise ValueError('谱面与 MIDI 四条路径的段首相差超过半秒')
            starts.append(round(sum(bests) / len(bests), 2))
            decisions.append({'verse': index + 1, 'estimate': estimate, 'paths': results, 'measuredStart': starts[-1]})
        if any(a + .5 >= b for a, b in zip(starts, starts[1:])):
            raise ValueError('实测诗节顺序不正确')
        if starts[0] >= .75 * music['period'] or float(stft[0][-1]) - (starts[-1] + decisions[-1]['paths']['stft']['completePrintedVerse']['best']['span']) >= .75 * music['period']:
            raise ValueError('录音首尾仍可能存在未覆盖的完整诗节')
        evidence['midiMatching'].update(music=music, referencePeriod=period, glyphs=glyphs,
                                         cqtFeatures=cqt_proof, decisions=decisions)
        evidence['performanceOrder'] = definitions
        evidence['initialReadingView'] = {'version': 1, 'at': 0, 'region': definitions[0]['region'], 'lyricOnsetMeasured': False}
        export_result(song, ROOT, cache, destination, evidence, start, source, definitions, [0] + starts[1:], [0])
        return song['songId'], 'ready'
    except (ValueError, FileNotFoundError, KeyError) as error:
        evidence.update(status='needs-calibration', reason=str(error), elapsedSeconds=round(time.monotonic() - start, 3))
        batch.save(destination, evidence)
        return song['songId'], 'needs-calibration: ' + str(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+', required=True)
    args = parser.parse_args()
    profiles = json.loads((ROOT / 'tools/sync-variant-score-profiles.json').read_text())
    for song in batch.inventory():
        if song['songId'] in args.songs and song['songId'] in profiles:
            print(*generate(song, profiles[song['songId']], args.cache), flush=True)
