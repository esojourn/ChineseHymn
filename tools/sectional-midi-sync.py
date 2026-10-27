#!/usr/bin/env python3
"""Rebuild reviewed musical sections and verify every reading boundary in real audio."""
import argparse
import copy
import hashlib
import importlib.util
import json
import io
import math
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from sync_batch_align import export_result
from sync_batch_audio import features, repeated_verses
from sync_batch_reference_score import score_rows_hash
from sync_batch_score import note_rows
from sync_batch_sectional import arranged_midi_bytes, midi_reading_rows, optical_performance_rows, performance_times, stable_outro_times, reading_section_groups, source_verse_groups, verify_verse_music
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
    render = item.get('renderPdf')
    stack = item.get('stackImages')
    svg = item.get('renderSvg')
    if svg is not None:
        source_review = item.get('svgSource')
        if (render is not None or stack is not None or not isinstance(svg, dict) or
                set(svg) != {'version', 'scale', 'playwrightVersion', 'chromiumVersion'} or
                type(svg.get('version')) is not int or svg['version'] != 1 or
                type(svg.get('scale')) is not int or not 1 <= svg['scale'] <= 4 or
                any(not isinstance(svg.get(key), str) or not svg[key].strip()
                    for key in ['playwrightVersion', 'chromiumVersion']) or
                not isinstance(source_review, dict) or set(source_review) != {'file', 'url', 'sha256'} or
                any(not isinstance(source_review[key], str) or not source_review[key] for key in source_review) or
                source_review['file'] == item['file']):
            raise ValueError('SVG 原谱须固定原文、倍率及浏览器版本，不能叠加其他变换')
        source = pinned_file(cache, source_review, prepare)
        from xml.etree import ElementTree
        try:
            if ElementTree.fromstring(source.read_bytes()).tag != '{http://www.w3.org/2000/svg}svg':
                raise ValueError('原始文件不是 SVG 谱面')
        except ElementTree.ParseError as error:
            raise ValueError('原始文件不是有效 SVG 谱面') from error
        if prepare:
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / 'reference.png'
                request = {**svg, 'source': str(source.resolve()), 'destination': str(destination)}
                try:
                    subprocess.run(['node', str(Path(__file__).with_name('render-sync-svg.cjs'))],
                                   input=json.dumps(request), text=True, check=True, capture_output=True, timeout=60)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    raise ValueError('SVG 原谱渲染器或固定版本检查失败') from error
                data = destination.read_bytes()
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('SVG 原谱渲染图片与复核哈希不一致')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    elif stack is not None:
        from PIL import Image, __version__ as pillow_version
        if (render is not None or not isinstance(stack, dict) or
                set(stack) != {'version', 'pillowVersion', 'sources'} or
                type(stack.get('version')) is not int or stack['version'] != 1 or
                stack.get('pillowVersion') != pillow_version or
                not isinstance(stack.get('sources'), list) or not 2 <= len(stack['sources']) <= 12 or
                any(not isinstance(source, dict) or set(source) != {'file', 'url', 'sha256'} or
                    any(not isinstance(source[key], str) or not source[key] for key in ['file', 'url', 'sha256']) or
                    source['file'] == item['file'] for source in stack['sources']) or
                len({source['file'] for source in stack['sources']}) != len(stack['sources'])):
            raise ValueError('多页原谱须固定每页文件、原始哈希、顺序及图片渲染器版本')
        sources = [pinned_file(cache, source, prepare) for source in stack['sources']]
        if prepare:
            pages = []
            for source in sources:
                with Image.open(source) as page:
                    pages.append(page.convert('RGB'))
            raster = Image.new('RGB', (max(page.width for page in pages), sum(page.height for page in pages)), 'white')
            top = 0
            for page in pages:
                raster.paste(page, (0, top))
                top += page.height
            stream = io.BytesIO()
            raster.save(stream, format='PNG')
            data = stream.getvalue()
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('多页原谱合并图片与复核哈希不一致')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    elif render is not None:
        if (not isinstance(render, dict) or type(render.get('version')) is not int or render['version'] != 1 or
                type(render.get('page')) is not int or render['page'] < 0 or
                type(render.get('scale')) is not int or not 1 <= render['scale'] <= 8 or
                not isinstance(item.get('pdfSource'), dict) or 'renderPdf' in item['pdfSource']):
            raise ValueError('PDF 原谱渲染须明确固定原文、页码及倍率')
        source = pinned_file(cache, item['pdfSource'], prepare)
        import pypdfium2 as pdfium
        from pypdfium2.version import PYPDFIUM_INFO, PDFIUM_INFO
        if (render.get('pypdfium2Version') != PYPDFIUM_INFO.version or
                render.get('pdfiumVersion') != PDFIUM_INFO.version):
            raise ValueError('PDF 原谱渲染器版本与已核对的图片不符')
        if prepare:
            with pdfium.PdfDocument(source) as document:
                if render['page'] >= len(document):
                    raise ValueError('PDF 原谱页码超出固定文件')
                stream = io.BytesIO()
                document[render['page']].render(scale=render['scale']).to_pil().save(stream, format='PNG')
            data = stream.getvalue()
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('PDF 原谱渲染图片与复核哈希不一致')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    elif not path.exists() and prepare:
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
    if reference['origin'].get('kind') == 'printed-numbered-score':
        from sync_batch_printed_midi import materialize_printed_reference
        image_review = profile['opticalReference']
        pinned_file(cache, image_review['image'], prepare)
        origin = cache / reference['origin']['file']
        printed = {'mode': 'symbolic-melody-and-optical', 'targetSha256': image_review['image']['sha256'],
                   'printedRhythmReference': reference['origin']['printedRhythmReference'],
                   'sha256': reference['origin']['sha256']}
        materialize_printed_reference(printed, image_review['image']['sha256'], origin)
    else:
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


def local_matcher(profile):
    review = profile.get('localOnsetWindow')
    if review is None:
        return None
    if (not isinstance(review, dict) or type(review.get('version')) is not int or review['version'] != 1 or
            type(review.get('fraction')) not in (int, float) or review['fraction'] != .4):
        raise ValueError('逐段起点上下文需要显式版本及固定40%范围')
    def match(rows, chroma, times, duration, onset, weight):
        return midi.match_template(rows, chroma, times, duration, onset, weight,
            onset_bounds=[onset - .4 * duration, onset + .4 * duration])
    return match


def previous_reading_context(profile):
    review = profile.get('previousReadingContext')
    if review is None:
        return False
    if (not isinstance(review, dict) or set(review) != {'version'} or
            type(review['version']) is not int or review['version'] != 1):
        raise ValueError('前一区域上下文需要版本1，且只能使用一个完整前一区域')
    return True


def source_verse_context(source, profile):
    review = profile.get('sourceVerseContext')
    if review is None:
        return None
    if (not isinstance(review, dict) or set(review) != {'version', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            'previousReadingContext' in profile):
        raise ValueError('原文歌段上下文须说明完整反复结构，且不能叠加前一区域策略')
    verses, at = [], 0
    for verse in source['verses']:
        verses.append(list(range(at, at + len(verse))))
        at += len(verse)
    return source_verse_groups(at, verses)


def verse_music_section(profile, verses, optical):
    review = profile.get('sourceVerseMusicRange')
    if review is None:
        return None
    if (not isinstance(review, dict) or set(review) != {'version', 'endBeforeSection', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            type(review.get('endBeforeSection')) is not int or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            verses is None or len(verses) < 3 or
            any(profile.get(key) for key in ['instrumentalOutroAlternatives', 'referenceEndingSequence']) or
            any(item.get('endingReadingRepeat') for item in profile.get('independentRecordings', []))):
        raise ValueError('正文反复比较范围须绑定至少三段原文及明确的同副歌结尾')
    groups = reading_section_groups(len(optical), profile.get('readingSectionReview'))
    last, end = groups[-1], review['endBeforeSection']
    if len(last) != 2 or end != last[1] or end != len(optical) - 1:
        raise ValueError('正文音乐范围只能排除末阅读区域追加的一次同副歌')
    def sounded(row):
        return [(note, pitch, weight) for note, pitch, weight in
                zip(row['notes'], row['pitchOffsets'], row['rhythmWeights']) if note]
    if not sounded(optical[last[0]]) or sounded(optical[last[0]]) != sounded(optical[end]):
        raise ValueError('被排除的结尾必须是同一原谱副歌，仅可附加声乐休止')
    return end


def verse_music_bounds(section, performance):
    one, two = performance['fullMelody']['best']['starts'], performance['fullOptical']['best']['starts']
    if (len(one) != len(two) or not 0 < section < len(one) or
            any(not math.isfinite(at) for at in [one[0], two[0], one[section], two[section]]) or
            any(round(abs(one[index] - two[index]), 3) > .5 for index in [0, section])):
        raise ValueError('正文音乐范围的两条整曲边界不一致')
    bounds = [(one[index] + two[index]) / 2 for index in [0, section]]
    if not 0 <= bounds[0] < bounds[1]:
        raise ValueError('正文音乐范围顺序无效')
    return bounds


def outro_profiles(profile):
    variants, names = [], set()
    alternatives = profile.get('instrumentalOutroAlternatives', [])
    if alternatives:
        review = profile.get('instrumentalOutroReview')
        if (not isinstance(review, dict) or type(review.get('version')) is not int or review['version'] != 1 or
                review.get('nonLyric') is not True or review.get('region') != profile['regions'][-1] or
                not isinstance(review.get('reason'), str) or not review['reason'].strip() or
                any(section == len(profile['opticalReference']['order']) - 1
                    for section, note in profile['opticalReference']['readingStarts'])):
            raise ValueError('尾奏变体须明确绑定末个阅读区域后的器乐部分')
    for item in alternatives:
        name = item['name']
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('器乐尾奏参考须逐项命名且不重复')
        names.add(name)
        if item['opticalPart'] not in profile['opticalReference']['parts']:
            raise ValueError('器乐尾奏引用了未核对的原谱乐段')
        variant = copy.deepcopy(profile)
        variant['midiReference'].update(file=item['file'], sha256=item['sha256'])
        variant['midiReference']['arrangement']['sections'][-1] = item['quarterRange']
        variant['opticalReference']['order'][-1] = item['opticalPart']
        variants.append((name, variant))
    return variants


def independent_recordings(song, profile):
    recordings = profile.get('independentRecordings', [])
    if not isinstance(recordings, list):
        raise ValueError('独立录音配置必须逐项绑定当前音轨')
    seen = {song['reference']}
    for item in recordings:
        if (not isinstance(item, dict) or set(item) not in [
                {'file', 'sha256', 'sampling'}, {'file', 'sha256', 'sampling', 'endingReadingRepeat'}] or
                not any(item['file'] == track['file'] and item['sha256'] == track['sha256']
                        for track in song['tracks']) or item['file'] in seen):
            raise ValueError('独立录音配置重复或与当前全部音轨哈希不符')
        seen.add(item['file'])
        window = item['sampling']
        if (not isinstance(window, dict) or set(window) != {'durationEstimate', 'onsetEstimate'} or
                any(type(window[key]) not in (int, float) or not math.isfinite(window[key]) for key in window) or
                window['durationEstimate'] <= 0 or window['onsetEstimate'] < 0):
            raise ValueError('独立录音的整曲取样范围无效')
    return recordings


def ending_recording_profile(profile, item):
    repeat = item.get('endingReadingRepeat')
    if 'endingReadingRepeat' not in item:
        return profile
    reference, optical = profile['midiReference'], profile['opticalReference']
    if (not isinstance(repeat, dict) or set(repeat) != {
            'version', 'region', 'opticalPart', 'quarterRange', 'file', 'sha256', 'reason'} or
            type(repeat.get('version')) is not int or repeat['version'] != 1 or
            not isinstance(repeat.get('reason'), str) or not repeat['reason'].strip() or
            repeat.get('region') != profile['regions'][-1] or repeat.get('opticalPart') != optical['order'][-1] or
            optical['readingStarts'][-1] != [len(optical['order']) - 1, 0] or
            'readingSectionReview' in profile or profile.get('instrumentalOutroAlternatives') or
            'transposeSections' in reference['arrangement'] or 'transposeParts' in optical or
            not isinstance(repeat.get('file'), str) or not repeat['file'] or
            repeat['file'] in [reference['file'], reference['origin']['file']] or
            not isinstance(repeat.get('sha256'), str) or len(repeat['sha256']) != 64 or
            any(char not in '0123456789abcdef' for char in repeat['sha256'])):
        raise ValueError('录音尾部再奏只能明确绑定末个阅读区域的同一原谱乐段')
    section = repeat['quarterRange']
    if (not isinstance(section, list) or len(section) != 2 or
            any(type(at) not in (int, float) or not math.isfinite(at) for at in section) or
            not 0 <= section[0] < section[1] <= reference['arrangement']['quarterLength']):
        raise ValueError('末段再奏须引用原 MIDI 中明确的完整拍数范围')
    result = copy.deepcopy(profile)
    result['midiReference'].update(file=repeat['file'], sha256=repeat['sha256'])
    result['midiReference']['arrangement']['sections'].append(section[:])
    result['opticalReference']['order'].append(repeat['opticalPart'])
    return result


def reference_ending_profile(profile):
    if 'referenceEndingSequence' not in profile:
        return profile
    ending = profile['referenceEndingSequence']
    reference, optical = profile['midiReference'], profile['opticalReference']
    if (not isinstance(ending, dict) or set(ending) != {'version', 'region', 'sections', 'file', 'sha256', 'reason'} or
            type(ending.get('version')) is not int or ending['version'] != 1 or
            ending.get('region') != profile['regions'][-1] or
            not isinstance(ending.get('reason'), str) or not ending['reason'].strip() or
            not isinstance(ending.get('sections'), list) or not 1 <= len(ending['sections']) <= 8 or
            optical['readingStarts'][-1] != [len(optical['order']) - 1, 0] or
            'readingSectionReview' in profile or profile.get('instrumentalOutroAlternatives') or
            'transposeSections' in reference['arrangement'] or 'transposeParts' in optical or
            not isinstance(ending.get('file'), str) or not ending['file'] or
            ending['file'] in [reference['file'], reference['origin']['file']] or
            not isinstance(ending.get('sha256'), str) or len(ending['sha256']) != 64 or
            any(char not in '0123456789abcdef' for char in ending['sha256'])):
        raise ValueError('参考录音结尾只能追加末个阅读区域中已经核对的原谱乐句')
    def coordinates(ranges):
        if (not isinstance(ranges, list) or not ranges or any(
                not isinstance(item, list) or len(item) != 3 or
                any(type(value) is not int for value in item) or
                not 0 <= item[1] < item[2] or item[0] < 0 for item in ranges)):
            raise ValueError('结尾原谱乐句须逐音绑定已核对的谱行范围')
        return [(system, index) for system, lo, hi in ranges for index in range(lo, hi)]
    last = coordinates(optical['parts'][optical['order'][-1]])
    result = copy.deepcopy(profile)
    for section in ending['sections']:
        if (not isinstance(section, dict) or set(section) != {'opticalPart', 'quarterRange'} or
                not isinstance(section.get('opticalPart'), str) or section['opticalPart'] not in optical['parts']):
            raise ValueError('结尾只能引用末个阅读区域内的原谱完整乐句')
        chosen = coordinates(optical['parts'][section['opticalPart']])
        if len(chosen) < 5 or sum(last[i:i + len(chosen)] == chosen for i in range(len(last))) != 1:
            raise ValueError('结尾原谱乐句必须是末个阅读区域中唯一、连续的至少五个音头')
        bounds = section['quarterRange']
        if (not isinstance(bounds, list) or len(bounds) != 2 or
                any(type(at) not in (int, float) or not math.isfinite(at) for at in bounds) or
                not 0 <= bounds[0] < bounds[1] <= reference['arrangement']['quarterLength']):
            raise ValueError('结尾乐句须引用原 MIDI 中明确的完整拍数范围')
        result['midiReference']['arrangement']['sections'].append(bounds[:])
        result['opticalReference']['order'].append(section['opticalPart'])
    result['midiReference'].update(file=ending['file'], sha256=ending['sha256'])
    return result


def source_verse_edges(source, profile):
    review = profile.get('sourceVerseEdgeReview')
    if review is None:
        return None
    if (not isinstance(review, dict) or set(review) != {'version', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            'sourceVerseContext' in profile or len(source['verses']) < 2 or
            any(not verse for verse in source['verses'])):
        raise ValueError('首尾完整歌段检查须绑定原文分段及明确说明，不能指定分组或时间')
    groups, at = [], 0
    for verse in source['verses']:
        groups.append(list(range(at, at + len(verse))))
        at += len(verse)
    return groups


def vocal_interlude_material(source, profile, material):
    """Describe unpitched interludes between complete source verses in both queries."""
    review = profile.get('vocalInterludes')
    if review is None:
        return material
    if (not isinstance(review, dict) or set(review) != {'version', 'afterVerses', 'reason'} or
            type(review.get('version')) is not int or review['version'] != 1 or
            not isinstance(review.get('reason'), str) or not review['reason'].strip() or
            not isinstance(review.get('afterVerses'), list) or not review['afterVerses'] or
            any(key in profile for key in ['readingSectionReview', 'sourceVerseContext',
                'sourceVerseMusicRange', 'instrumentalOutroReview', 'instrumentalOutroAlternatives',
                'referenceEndingSequence']) or
            any('endingReadingRepeat' in item for item in profile.get('independentRecordings', []))):
        raise ValueError('段间声乐休止须明确版本、原文完整歌段及拍数，不能与其他结构策略叠加')
    verses = source['verses']
    melody, optical, reference = material
    if (len(verses) < 2 or any(not verse for verse in verses) or
            sum(map(len, verses)) != len(melody) or len(melody) != len(optical)):
        raise ValueError('段间声乐休止不能遗漏原文或改变阅读区域数量')
    groups, at = [], 0
    for verse in verses:
        groups.append(list(range(at, at + len(verse))))
        at += len(verse)
    previous, gaps = 0, []
    for gap in review['afterVerses']:
        if (not isinstance(gap, dict) or set(gap) != {'verse', 'quarterBeats'} or
                type(gap.get('verse')) is not int or not previous < gap['verse'] < len(verses) or
                type(gap.get('quarterBeats')) is not int or not 1 <= gap['quarterBeats'] <= 64):
            raise ValueError('声乐休止只能按原文顺序位于完整歌段之间，使用1至64个四分音符拍数')
        previous = gap['verse']
        gaps.append({**gap, 'afterReadingIndex': groups[gap['verse'] - 1][-1]})
    quarters = sum(hi - lo for lo, hi in profile['midiReference']['arrangement']['sections'])
    weights = sum(sum(row['spatialWeights']) for row in melody)
    if not math.isfinite(quarters) or quarters <= 0 or not math.isfinite(weights) or weights <= 0:
        raise ValueError('段间声乐休止缺少完整原 MIDI 的拍数与采样权重')
    result = copy.deepcopy(material)
    for gap in gaps:
        # MIDI query samples have 0.1-second reference units; no recorded-audio
        # seconds or new pitched notes enter either query.
        weight = gap['quarterBeats'] * weights / quarters
        for rows, key, value in [(result[0], 'spatialWeights', weight),
                                 (result[1], 'rhythmWeights', gap['quarterBeats'])]:
            row = rows[gap['afterReadingIndex']]
            row['notes'].append(0)
            row['pitchOffsets'].append(0)
            row[key].append(value)
        gap['midiReferenceWeight'] = weight
    result[2]['vocalInterludes'] = {'version': 1, 'sourceVerseGroups': groups, 'gaps': gaps,
        'templateOnly': True, 'reason': review['reason']}
    result[2]['opticalReadingRows'] = result[1]
    return result


def measured_performance(profile, window, material, target, cache, prepare, detail, source_verses=None,
                         edge_verses=None):
    melody, optical, reference = material
    verify_local = local_matcher(profile)
    previous_context = previous_reading_context(profile)
    proof = detail['performance'] = {}
    times = performance_times(melody, optical, target, window['durationEstimate'], window['onsetEstimate'],
        reference['symbolicRows']['semitoneShift'], midi.match_template, proof, local_match=verify_local,
        reading_sections=profile.get('readingSectionReview'), previous_context=previous_context,
        source_verses=source_verses, edge_verses=edge_verses)
    variants = outro_profiles(profile)
    if variants:
        checks = detail['instrumentalOutroChecks'] = []
        timelines = [times]
        for name, variant in variants:
            melody_variant, optical_variant, reference_variant = reference_material(variant, cache, prepare)
            check = {'name': name, 'midiReference': variant['midiReference'], 'reference': reference_variant,
                     'performance': {}}
            checks.append(check)
            measured = performance_times(melody_variant, optical_variant, target,
                window['durationEstimate'], window['onsetEstimate'], reference_variant['symbolicRows']['semitoneShift'],
                midi.match_template, check['performance'], local_match=verify_local,
                reading_sections=variant.get('readingSectionReview'), previous_context=previous_context,
                source_verses=source_verses, edge_verses=edge_verses)
            check['times'] = measured
            timelines.append(measured)
            stable_outro_times(timelines)
    return times


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
        verses = source_verse_context(source, profile)
        edge_verses = source_verse_edges(source, profile)
        recordings = independent_recordings(song, profile)
        recording_profiles = [(item, ending_recording_profile(profile, item)) for item in recordings]
        reference_profile = reference_ending_profile(profile)
        body_material = vocal_interlude_material(source, profile, reference_material(profile, cache, prepare))
        material = body_material if reference_profile is profile else reference_material(reference_profile, cache, prepare)
        melody, optical, reference = material
        verse_music_section(reference_profile, verses, optical)
        if len(reading_section_groups(len(melody), profile.get('readingSectionReview'))) != len(definitions):
            raise ValueError('完整乐曲结构未覆盖全部正文阅读区域')
        evidence['midiMatching'].update(reference)
        if reference_profile is not profile:
            evidence['midiMatching']['recordingReference'] = {
                'midiReference': reference_profile['midiReference'], 'opticalReference': reference_profile['opticalReference'],
                'readingBoundaries': reference_profile['readingBoundaries']}
        evidence['performanceOrder'] = definitions
        def measure_recording(file, window, detail, track_profile=reference_profile, track_material=material):
            audio = batch.ROOT / 'data' / file
            music_section = verse_music_section(track_profile, verses, track_material[1])
            measured = measured_performance(track_profile, window, track_material, features(audio, cache), cache, prepare,
                                           detail, source_verses=verses, edge_verses=edge_verses)
            if verses is not None:
                music = (repeated_verses(audio, len(verses), cache) if music_section is None else
                         repeated_verses(audio, len(verses), cache,
                                         audio_range=verse_music_bounds(music_section, detail['performance'])))
                detail['sourceVerseMusic'] = verify_verse_music(measured, verses, music)
                for check in detail.get('instrumentalOutroChecks', []):
                    check['sourceVerseMusic'] = verify_verse_music(check['times'], verses, music)
            return measured
        times = measure_recording(song['reference'], profile['sampling'], evidence['midiMatching'])
        for item, track_profile in recording_profiles:
            detail = dict(item)
            evidence['midiMatching'].setdefault('independentRecordings', []).append(detail)
            track_material = body_material
            if track_profile is not profile:
                track_material = reference_material(track_profile, cache, prepare)
            if track_profile is not profile or reference_profile is not profile:
                detail.update(track_material[2])
                detail['recordingReference'] = {'midiReference': track_profile['midiReference'],
                    'opticalReference': track_profile['opticalReference'],
                    'readingBoundaries': track_profile['readingBoundaries']}
            measured = measure_recording(item['file'], item['sampling'], detail, track_profile, track_material)
            detail['times'] = measured
            evidence.setdefault('independentTimelines', {})[item['file']] = {
                'definitions': definitions, 'times': measured, 'returnIndices': []}
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
