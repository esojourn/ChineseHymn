#!/usr/bin/env python3
"""Propose MIDI score references using cyclic harmonic fingerprints; never publish data."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from sync_batch_audio import features, repeated_verses
from sync_batch_sources import text_source, image_source

spec = importlib.util.spec_from_file_location('midi_retrieval', ROOT / 'tools/midi-sync.py')
midi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(midi)
FRAMES = 512


def rank_candidates(spectra, names, query_chroma):
    query = np.fft.rfft(query_chroma, axis=1)
    best = np.full(len(names), -np.inf)
    keys = np.zeros(len(names), dtype=int)
    phases = np.zeros(len(names), dtype=int)
    for key in range(12):
        products = spectra.conj() * np.roll(query, -key, axis=0)[None, :, :]
        correlation = np.fft.irfft(products.sum(axis=1), n=FRAMES, axis=1) / FRAMES
        phase = correlation.argmax(axis=1)
        scores = correlation[np.arange(len(names)), phase]
        better = scores > best
        best[better], keys[better], phases[better] = scores[better], key, phase[better]
    ranked = np.argsort(best)[::-1][:5]
    return [{'file': names[index], 'similarity': round(float(best[index]), 5),
             'key': int(keys[index]), 'cyclicPhase': int(phases[index])} for index in ranked]


def reference_templates(cache):
    paths = sorted((cache / 'midi').glob('hymnary[0-9][0-9][0-9].mid'))
    if not paths:
        raise ValueError('公开 MIDI 参考尚未缓存到指定目录')
    files = [{'file': path.name, 'sha256': midi.batch.checksum(path)} for path in paths]
    signature = hashlib.sha256(json.dumps({'files': files, 'renderer': midi.batch.checksum(ROOT / 'tools/midi-sync.py'),
        'version': 1, 'frames': FRAMES}, sort_keys=True).encode()).hexdigest()
    store = cache / 'reference/cyclic-midi-templates.npz'
    if store.exists():
        with np.load(store) as saved:
            if str(saved['signature']) == signature:
                return saved['spectra'], json.loads(str(saved['names'])), signature
    fingerprints, names = [], []
    for index, path in enumerate(paths):
        try:
            period, (times, chroma), melody = midi.midi_features(path)
            if period < 5 or len(melody['onsetTimes']) < 8:
                continue
            samples = np.arange(FRAMES) * period / FRAMES
            one = np.stack([np.interp(samples, times, row) for row in chroma])
            one /= np.maximum(np.linalg.norm(one, axis=0), 1e-8)
            fingerprints.append(np.fft.rfft(one, axis=1).astype(np.complex64))
            names.append(path.name)
        except Exception as error:
            print('skip', path.name, type(error).__name__, str(error)[:90], flush=True)
        if (index + 1) % 100 == 0:
            print('MIDI templates', index + 1, len(paths), flush=True)
    if not fingerprints:
        raise ValueError('没有可读取的完整 MIDI 参考')
    spectra = np.stack(fingerprints)
    store.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(store, spectra=spectra, names=json.dumps(names), signature=signature)
    return spectra, names, signature


def main(args):
    cache = args.cache.resolve()
    spectra, names, signature = reference_templates(cache)
    print('TEMPLATES', len(names), flush=True)
    controls = {'01-133', '01-298', '01-493'}
    jobs = [song for song in midi.batch.inventory() if song['tracks'] and song['lyricsAvailable'] and
            not song['songId'].startswith('06-') and (not song['existing'] or song['songId'] in controls)]
    if args.songs:
        jobs = [song for song in jobs if song['songId'] in args.songs]
    # Previously verified recordings provide controls for interpreting new proposals.
    jobs.sort(key=lambda song: (song['songId'] not in controls, song['songId']))
    results = []
    for index, song in enumerate(jobs):
        try:
            source = text_source(ROOT / 'data' / song['source']) if song['type'] == 'text' else image_source(
                json.loads((cache / 'ocr' / (song['source'] + '.json')).read_text()))
            count = len(source['verses'])
            if count < 2:
                continue
            path = ROOT / 'data' / song['reference']
            music = repeated_verses(path, count, cache)
            times, chroma = features(path, cache)
            samples = music['initialOnsetEstimate'] + np.arange(FRAMES) * music['period'] / FRAMES
            query = np.stack([np.interp(samples, times, row) for row in chroma])
            query /= np.maximum(np.linalg.norm(query, axis=0), 1e-8)
            row = {'songId': song['songId'], 'source': song['source'], 'sourceHash': song['sha256'],
                   'audio': song['reference'], 'audioHash': midi.batch.checksum(path), 'diagnosticOnly': True,
                   'templateSignature': signature, 'music': music, 'matches': rank_candidates(spectra, names, query)}
            results.append(row)
            print(index + 1, len(jobs), song['songId'], row['matches'][:3], flush=True)
        except Exception as error:
            results.append({'songId': song['songId'], 'source': song['source'], 'diagnosticOnly': True, 'reason': str(error)})
            print('skip', song['songId'], str(error), flush=True)
        midi.batch.save(cache / 'reference/audio-midi-suggestions.json', results)
    print('COMPLETE', len(results), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--songs', nargs='+')
    main(parser.parse_args())
