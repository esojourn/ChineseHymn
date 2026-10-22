"""Source-coordinate reading regions and explicit stanza order for offline alignment."""
import importlib.util
from functools import lru_cache
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location('sync_prepare', Path(__file__).with_name('prepare-sync.py'))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
VERSE = re.compile(r'^\s*(\d{1,2})(?:\s*[.．、)]\s*|\s+(?=\D))')
CHINESE_VERSE = re.compile(r'^\s*[（(]([一二三四五六七八九十])[）)]\s*')
CHORUS = re.compile(r'^\s*[（(\[]?(?:副(?:歌)?|chorus)[）)\]]?\s*[:：.．]?\s*$', re.I)
CREDITS = re.compile(r'^(?:歌手|演唱|作词|作詞|作曲|词曲|詞曲|编曲|編曲|lyrics\s+by|music\s+by|performed\s+by)\s*[:：]', re.I)


@lru_cache(maxsize=1)
def converter():
    from opencc import OpenCC
    return OpenCC('t2s')


def clean(text, language='zh'):
    text = converter().convert(text).lower().translate(str.maketrans('祢祂惟', '你他唯'))
    pattern = r'[a-z]' if language == 'en' else r'[\u3400-\u9fff]'
    return ''.join(re.findall(pattern, text))


def verse_number(text):
    match = VERSE.match(text)
    if match:
        return int(match[1])
    match = CHINESE_VERSE.match(text)
    return '一二三四五六七八九十'.index(match[1]) + 1 if match else None


def strip_verse(text):
    return CHINESE_VERSE.sub('', VERSE.sub('', text))


def text_source(path, language='zh'):
    lines = prepare.read_text(path).split('\n')
    numbered = [i for i, line in enumerate(lines) if verse_number(line) == 1]
    first = numbered[0] if numbered else next((i + 1 for i, line in enumerate(lines) if not line.strip()), 1)
    regions, verses = {}, {}
    verse, block, pending = 1, 1, []
    has_numbers = bool(numbered)

    def flush():
        if not pending:
            return
        region_id = f'r{len(regions) + 1}'
        text = ' '.join(strip_verse(lines[i]) for i in pending if clean(strip_verse(lines[i]), language))
        if clean(text, language):
            regions[region_id] = {'kind': 'text', 'startLine': pending[0], 'endLine': pending[-1]}
            verses.setdefault(verse if has_numbers else block, []).append(
                {'region': region_id, 'text': text, 'label': f'第{verse if has_numbers else block}段'})
        pending.clear()

    for i in range(first, len(lines)):
        line = lines[i].strip()
        if CREDITS.match(line):
            flush()
            continue
        number = verse_number(line)
        if number and number != verse:
            flush()
            verse = number
        if not line or CHORUS.match(line) or (number and not clean(strip_verse(line), language)):
            flush()
            if not has_numbers and not line:
                block += 1
            continue
        if not re.search(r'[\u3400-\u9fffA-Za-z]', line):
            continue
        if pending and i - pending[0] >= 4:
            flush()
        pending.append(i)
        if len(pending) == 4:
            flush()
    flush()
    return {'regions': regions, 'verses': list(verses.values()), 'language': language}


def image_source(ocr):
    rows = ocr['rows']
    systems, current = [], None
    chorus = False
    for row in rows:
        text = row['text']
        if re.match(r'^(?:通用调|同用调|注[:：]|词[:：]|曲[:：]|译[:：])', text.strip()):
            continue
        if CHORUS.match(text):
            chorus = True
            continue
        chinese = len(re.findall(r'[\u3400-\u9fff]', text))
        digits = len(re.findall('[1-7]', text))
        if chinese <= 1 and digits >= 4 and row['top'] > .07:
            if current and current['lyrics']:
                systems.append(current)
            current = {'notes': row, 'lyrics': [], 'chorus': chorus}
        elif current and chinese >= 4 and not CHORUS.match(text) and row['confidence'] >= .45:
            current['lyrics'].append(row)
    if current and current['lyrics']:
        systems.append(current)
    if not systems:
        raise ValueError('未识别出谱行及对应横排歌词')
    verse_count = max([int(match[1]) for system in systems for row in system['lyrics']
                       if (match := VERSE.match(row['text'])) and int(match[1]) < 15] or [1])
    regions, verses = {}, [[] for _ in range(verse_count)]
    for index, system in enumerate(systems):
        lyrics = system['lyrics']
        numbered = {int(match[1]): row for row in lyrics if (match := VERSE.match(row['text']))}
        if len(lyrics) == verse_count and verse_count > 1:
            numbered = {i + 1: row for i, row in enumerate(lyrics)}
        if not numbered and len(lyrics) > 1:
            # Wrapped chorus belongs to one musical system, not to separate stanzas.
            lyrics = [{**lyrics[0], 'text': ''.join(row['text'] for row in lyrics), 'bottom': lyrics[-1]['bottom']}]
        for verse in range(verse_count):
            lyric = numbered.get(verse + 1) if numbered else lyrics[0]
            if lyric is None:
                raise ValueError(f'第 {index + 1} 谱行缺少第 {verse + 1} 段歌词，需核对 OCR')
            region_id = f's{index + 1}_v{verse + 1}' if numbered else f's{index + 1}_chorus'
            # Protect the sung lyric row even when many printed stanzas make a system taller than a screen.
            top = max(system['notes']['top'] - .008, lyric['top'] - .04)
            bottom = min(1, lyric['bottom'] + .012)
            if system['chorus'] and numbered:
                # The continuation of a common chorus may be printed only beside verse 1.
                # Retain the entire chorus system instead of hiding that shared text.
                top = max(0, system['notes']['top'] - .008)
                bottom = min(1, max(row['bottom'] for row in system['lyrics']) + .012)
            regions[region_id] = {'kind': 'image', 'top': round(top, 6), 'bottom': round(bottom, 6)}
            verses[verse].append({'region': region_id, 'text': VERSE.sub('', lyric['text']),
                                  'label': f'第{verse + 1}段：第{index + 1}谱行'})
    return {'regions': regions, 'verses': verses, 'language': 'zh', 'systems': systems}
