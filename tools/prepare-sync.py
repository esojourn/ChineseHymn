#!/usr/bin/env python3
"""Generate enabled sidecars when timing exists; optionally keep drafts. Models are optional."""
import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path


def normalize_text(text):
    return re.sub(r"\n{3,}", "\n\n", text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip())

def read_text(path):
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "big5"):
        try:
            return normalize_text(raw.decode(encoding))
        except UnicodeDecodeError:
            pass
    raise ValueError(f"无法解码歌词：{path}")


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def text_regions(text):
    """Bound long stanzas to four source lines; never invent timing from text length."""
    lines = normalize_text(text).split("\n")
    regions = {}
    start = None
    for index in range(len(lines) + 1):
        nonempty = index < len(lines) and bool(lines[index].strip())
        if nonempty and start is None:
            start = index
        if start is not None and (not nonempty or index - start == 3):
            end = index if nonempty else index - 1
            regions[f"r{len(regions) + 1}"] = {
                "kind": "text", "startLine": start, "endLine": end,
                "candidateText": "".join(lines[start:end + 1]),
            }
            start = None
    return regions


def ocr_regions(path):
    try:
        from PIL import Image
        from paddleocr import PaddleOCR
    except ImportError as error:
        raise RuntimeError("OCR 需要单独安装 Pillow、PaddleOCR 3.x 及匹配的 PaddlePaddle。") from error
    with Image.open(path) as image:
        height = image.height
    # Preserve original coordinates: document unwarping/rotation would invalidate the boxes.
    model = PaddleOCR(ocr_version="PP-OCRv5", lang="ch", use_doc_orientation_classify=False,
                      use_doc_unwarping=False, use_textline_orientation=False)
    result = next(iter(model.predict(str(path))))
    rows = []
    for text, score, polygon in zip(result["rec_texts"], result["rec_scores"], result["rec_polys"]):
        if not re.search(r"[\u3400-\u9fff]", text) or float(score) < 0.5:
            continue
        ys = [float(point[1]) for point in polygon]
        xs = [float(point[0]) for point in polygon]
        rows.append((min(ys), min(xs), max(ys), text, float(score)))
    rows.sort()
    return {f"r{i + 1}": {
        "kind": "image", "top": max(0, top / height - 0.025),
        "bottom": min(1, bottom / height + 0.015),
        "candidateText": text, "ocrConfidence": round(score, 3),
    } for i, (top, _, bottom, text, score) in enumerate(rows)}


def clean(text):
    return "".join(re.findall(r"[\u3400-\u9fffA-Za-z]", text)).lower()


def candidate_cues(order, segments, minimum=0.6):
    """Map aligned ASR characters to known lyrics in performance order."""
    expected = ""
    spans = []
    for item in order:
        lyric = clean(item["text"])
        start = len(expected)
        expected += lyric
        spans.append((start, len(expected), item))
    heard = ""
    times = []
    ends = []
    for segment in segments:
        for word in segment.get("words", []):
            if "start" not in word or "end" not in word:
                continue
            text = clean(word.get("word", ""))
            heard += text
            times.extend([float(word["start"])] * len(text))
            ends.extend([float(word["end"])] * len(text))
    matches = {}
    for block in difflib.SequenceMatcher(None, expected, heard, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            matches[block.a + offset] = block.b + offset
    cues = []
    diagnostics = []
    previous_end = None
    for start, end, item in spans:
        indices = [matches[index] for index in range(start, end) if index in matches]
        ratio = len(indices) / max(1, end - start)
        diagnostics.append({"region": item["region"], "matchedFraction": round(ratio, 3)})
        if not indices or ratio < minimum:
            previous_end = None  # Unmatched words must not become a fictitious interlude.
            continue
        at = round(times[min(indices)], 3)
        if cues and at <= cues[-1]["at"]:
            previous_end = None
            continue
        cue = {"at": at, "region": item["region"], "label": item.get("label", "待校准"), "transition": "reading"}
        # Gap in aligned vocals is a candidate, not proof of an instrumental interlude.
        if previous_end is not None and at - previous_end >= 3:
            cue.update(moveStart=round(previous_end, 3), transition="interlude")
        elif not cues and at > 0:
            cue.update(moveStart=0, transition="intro")
        cues.append(cue)
        previous_end = max(ends[index] for index in indices)
    return cues, diagnostics


def align(path, model_name, device):
    try:
        import whisperx
    except ImportError as error:
        raise RuntimeError("音频对齐需要单独安装 WhisperX 和 FFmpeg。") from error
    waveform = whisperx.load_audio(str(path))
    model = whisperx.load_model(model_name, device, language="zh", compute_type="int8" if device == "cpu" else "float16")
    result = model.transcribe(waveform, batch_size=1 if device == "cpu" else 8)
    del model
    acoustic, metadata = whisperx.load_align_model(language_code="zh", device=device)
    return whisperx.align(result["segments"], acoustic, metadata, waveform, device, return_char_alignments=False)


def set_activation(data, draft_only=False):
    for track in data["tracks"]:
        track["reviewed"] = not draft_only and bool(track.get("cues", data["cues"]))
    data["status"] = "reviewed" if any(track["reviewed"] for track in data["tracks"]) else "draft"
    data["activation"] = {"policy": "draft" if draft_only else "automatic", "listeningReviewRequired": False}


def set_image_layout(data):
    """Preview the next performed system, without changing acoustic arrival times."""
    if data["source"]["type"] == "image":
        data["imageLayout"] = {"mode": "center-preview", "previewFraction": 0.65}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="data/ 内的 TXT 或 JPG")
    parser.add_argument("--output", type=Path, required=True, help="输出目录；永不覆盖已有文件")
    parser.add_argument("--ocr", action="store_true", help="用 PaddleOCR 生成图片区域草稿")
    parser.add_argument("--align", action="store_true", help="用领唱录音运行 WhisperX")
    parser.add_argument("--alignment", type=Path, help="复用已有 WhisperX JSON，免去模型推理")
    parser.add_argument("--order", type=Path, help='演唱顺序 JSON：[{"region":"r1","text":"歌词","label":"第一段"}]')
    parser.add_argument("--draft", type=Path, help="复用校准页面导出的区域草稿")
    parser.add_argument("--reframe", action="store_true", help="更新 --draft 中的 JPG 定位策略，保留全部音轨、时间点和启用状态")
    parser.add_argument("--draft-only", action="store_true", help="仅生成不启用的草稿；默认有时间点即启用")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--model", default="large-v3")
    args = parser.parse_args()
    source = args.source.resolve()
    if source.suffix.lower() not in (".txt", ".jpg"):
        parser.error("只接受 TXT 或 JPG")
    if args.reframe and (source.suffix.lower() != ".jpg" or not args.draft or
                         args.ocr or args.align or args.alignment or args.order or args.draft_only):
        parser.error("--reframe 仅用于 JPG，必须提供 --draft，不能同时重新识别、对齐或更改启用状态")
    match = re.match(r"\d{2}-\d{1,3}", source.name)
    if not match:
        parser.error("文件名缺少歌曲编号")
    output = args.output / (source.name + ".json")
    if output.exists():
        parser.error(f"输出已存在，不覆盖：{output}")
    try:
        from mutagen.mp3 import MP3
    except ImportError as error:
        raise RuntimeError("请先安装 tools/requirements-sync.txt 中的轻量依赖。") from error
    audio = [path for path in sorted(source.parent.iterdir()) if path.suffix.lower() == ".mp3" and path.stem in (source.stem, source.stem + "-领唱")]
    if not audio:
        parser.error("未找到同名伴奏或领唱 MP3")
    tracks = [{"file": path.name, "sha256": checksum(path), "duration": MP3(path).info.length,
               "offset": 0, "reviewed": False} for path in audio]
    kind = "text" if source.suffix.lower() == ".txt" else "image"
    regions = text_regions(read_text(source)) if kind == "text" else ocr_regions(source) if args.ocr else {}
    if args.draft:
        draft = json.loads(args.draft.read_text(encoding="utf-8"))
        if draft["source"]["file"] != source.name or draft["source"]["sha256"] != checksum(source):
            parser.error("区域草稿对应的歌词素材已改变")
        regions = draft["regions"]
    data = {"version": 1, "songId": match[0], "status": "draft",
            "source": {"file": source.name, "type": kind, "sha256": checksum(source)},
            "tracks": tracks, "regions": regions, "cues": [],
            "preparation": {"warnings": ["时间点默认启用；自动生成不代表已试听。", "时长相同不代表段落已同步，可在校准页调整各音轨。"]}}
    if len(tracks) > 1:
        data["preparation"]["durationDifference"] = round(max(t["duration"] for t in tracks) - min(t["duration"] for t in tracks), 3)
    if args.align or args.alignment:
        if kind == "image" and not args.order:
            parser.error("图片歌词需先确认区域和演唱顺序，并通过 --order 提供；不按图片纵向位置猜测顺序。")
        lines = read_text(source).split("\n") if kind == "text" else []
        order = json.loads(args.order.read_text(encoding="utf-8")) if args.order else [
            {"region": key, "text": "".join(lines[value["startLine"]:value["endLine"] + 1])} for key, value in regions.items()]
        if any(item["region"] not in regions for item in order):
            parser.error("演唱顺序引用了不存在的阅读区域")
        if args.alignment:
            result = json.loads(args.alignment.read_text(encoding="utf-8"))
        else:
            lead = next((path for path in audio if path.stem.endswith("-领唱")), None)
            if not lead:
                parser.error("没有领唱录音，请人工校准，不对纯伴奏运行语音识别。")
            result = align(lead, args.model, args.device)
        data["cues"], diagnostics = candidate_cues(order, result["segments"])
        # Keep the reference timeline intact; a shorter arrangement needs its own draft.
        duration_limit = max(track["duration"] for track in tracks)
        data["cues"] = [cue for cue in data["cues"] if cue["at"] <= duration_limit]
        for track in tracks:
            if any(cue["at"] > track["duration"] for cue in data["cues"]):
                track["cues"] = [cue.copy() for cue in data["cues"] if cue["at"] <= track["duration"]]
                data["preparation"]["warnings"].append(track["file"] + " 短于候选时间轴；已生成截断的独立草稿，请核对编排并重新标注。")
        data["preparation"]["alignment"] = diagnostics
        data["preparation"]["warnings"].append("人声空隙仅为间奏候选；检查漏唱、拖腔和重复副歌。")
    if args.reframe:
        if draft.get("source", {}).get("type") != "image" or draft.get("songId") != match[0]:
            parser.error("原同步数据的歌曲或类型不匹配")
        original_tracks = draft.get("tracks", [])
        if len(original_tracks) != len(tracks) or any(not any(
                old["file"] == actual["file"] and old["sha256"] == actual["sha256"] and
                abs(old["duration"] - actual["duration"]) <= 1 for actual in tracks)
                for old in original_tracks):
            parser.error("原同步数据的音频素材已改变，请重新校准")
        data = draft
    else:
        set_activation(data, args.draft_only)
    set_image_layout(data)
    args.output.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(output)


if __name__ == "__main__":
    main()
