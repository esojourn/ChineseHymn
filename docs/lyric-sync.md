# 歌词自动滚动与校准

网站继续使用 PHP，浏览器按音频播放位置平滑滚动。Python/OCR/语音模型只用于制作数据，不是网站运行依赖。原有图片不重排、不高亮。

## 听众使用

在菜单中打开“自动滚动”，默认关闭。选择保存 60 天。有已审核且素材校验通过的数据时才启用。手动滚轮、触摸拖动、滚动按键或拖动滚动条会关闭跟随；重新打开开关恢复。菜单始终可达，点击播放器或菜单不会接管页面。

前奏、间奏使用标注的过渡区间缓慢移动到下一段。较短间奏优先按时到位。未指定过渡区间时，段落边界仍需完成的位移使用约 0.6 秒平滑衔接。暂停和缓冲停止音乐驱动的推进；快进、循环及重新启用时从当前屏幕位置衔接。未制作同步数据的歌曲继续正常播放、手动阅读。

文字歌词优先将当前阅读区域放在屏幕中间。在没有指定过渡区间的连续段落之间，随播放进度缓慢向下一段移动，同时将当前区域末行保留在屏幕上方约 30% 以下。相邻段落因此提前衔接；距离过远时限制提前移动，边界处再平滑接续。回到上方的重复段落仍按标注时机切换。页面首尾受可滚动范围限制，过长区域应拆分成更小的阅读区域。

## 本地启动

```sh
php -S localhost:8080
```

PHP 需要 `mbstring`。路径现在根据 `SCRIPT_NAME` 自动计算，可运行在站点根目录或 `/hymn/`。访问 `http://localhost:8080/index.php` 播放歌曲，访问 `http://localhost:8080/tools/sync-editor.html` 校准。

也可用 `npm run serve` 单独运行校准工具；静态服务器不能执行 PHP，首页保留跳转到 PHP 的行为。校准页使用浏览器 File API，不向服务器上传或写入素材。请通过 localhost 或 HTTPS 使用，以便计算 SHA-256。

## 不安装模型的制作流程

1. 在校准页选择原始 TXT/JPG，以及同名伴奏和 `-领唱.mp3`；点击“建立草稿”。
2. 文本点击首行、Shift＋点击末行；图片点击区域上边界、Shift＋点击下边界，也可以输入比例数值。区域要能在手机一屏中阅读，过长段落拆成多个区域。
3. 为区域填写英文编号并保存。播放音频，在下段开唱位置按 A 记录到达时间；在上段唱完、间奏开始时按 I 记录移动开始时间。填写段落说明并新增时间点。
4. 同一图片的第二段可再次引用上方区域；副歌也可重复引用。只有实际演唱结束后，才允许开始移出当前区域。
5. 勾选预览，在正常播放、间奏、快进和手机布局下检查。预览中手动滚动会退出自动模式。
6. 切换音频版本逐个试听。默认共享时间轴；只有整体起点偏差时填写偏移（正值表示这版晚开始）。编排不同时，勾选“当前版本使用独立时间轴”，在副本上修改。
7. 完整试听后勾选当前版本审核。任何区域、时间或偏移修改都会清除审核；没有审核的版本不会启用。
8. 导出已审核 JSON，放入 `data/sync/`，运行 `npm run sync:validate`，与歌曲一起提交。文件名必须为原歌词文件名加 `.json`，例如 `05-001圣哉三一歌.jpg.json`。

导出的草稿也可再次导入。导入时选择相同原歌词及全部关联音频，文件哈希和时长必须匹配。更换素材后创建新草稿并重新审核。校准工具不会把自动生成的结果自行标记为已审核。

## 离线辅助制作

基础 CLI 只依赖轻量的 MP3 元数据读取库，不需要 GPU：

```sh
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements-sync.txt
.venv/bin/python tools/prepare-sync.py 'data/01-042赞美我主君王.txt' --output sync-drafts
```

输出包含原始素材哈希、各版时长、文本阅读区域和草稿状态；没有录音依据时不编造时间点。输出文件已存在时拒绝覆盖。文本统一 CRLF/BOM/连续空行，以相同规则建立行号。浏览器支持 UTF-8 和 GB18030；其他编码先转换并重新制作数据。

可选模型安装在独立环境，按上游安装说明选择匹配的 PyTorch/PaddlePaddle 与 CPU/GPU 版本：

- [PaddleOCR 3.x](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)：另装 Pillow 与 PaddlePaddle。`--ocr` 使用 PP-OCRv5 获取原图坐标；不旋转或拉直图片，避免坐标错位。结果是候选文字区域，需要人工合并成谱行并确认交错歌词顺序。
- [WhisperX](https://github.com/m-bain/whisperX)：另装 FFmpeg。`--align` 对现有领唱录音做中文识别及时间对齐，再把识别字符匹配到已知歌词。默认 CPU/int8；`--device cuda` 使用 GPU。首次使用会下载模型。拖腔、合唱、繁简体差异和重复歌词可能导致漏配。

```sh
# 模型已安装时执行；均只生成草稿。
python tools/prepare-sync.py 'data/01-042赞美我主君王.txt' --align --output sync-drafts
python tools/prepare-sync.py 'data/05-001圣哉三一歌.jpg' --ocr --output sync-drafts
```

`--alignment result.json` 可复用带 `segments[].words[]` 时间点的 WhisperX 结果，不重复推理。`--order order.json` 提供真实演唱顺序，每项为 `{"region":"r1","text":"该区域本次唱的歌词","label":"第二段"}`；可重复引用区域。图片音频对齐必须显式提供顺序，不能把图片从上到下的 OCR 顺序当演唱顺序。`--draft` 可载入校准页导出的区域草稿后再对齐。

人声空隙只是间奏候选，可能实际为漏识别。纯伴奏不运行语音识别：优先参考已审核领唱时间轴，无参考时人工标记。两版时长一致不代表节奏一致，时长不同也可能只是头尾留白；均须听音确认。

## JSON 接口（version 1）

- `songId`、`status`（`draft`/`reviewed`）。
- `source`：`file`（仅文件名）、原始字节 `sha256`、`type`（`text`/`image`）。
- `tracks`：各版 `file`、`sha256`、`duration`、`offset`、`reviewed`；可选 `cues` 覆盖共享时间轴。
- `regions`：区域编号到对象的映射。文本为 `{kind:"text", startLine:2, endLine:5}`，行号从 0 起且包含空行；图片为 `{kind:"image", top:0.2, bottom:0.4}`，比例相对原图高度。
- `cues`：按 `at` 严格递增的到达时间，包含 `region`、可选 `label`、`moveStart` 和 `transition`（`intro`/`interlude`/`reading`/`return`）。移动开始不得早于上一到达时间，且不得晚于本次到达时间。末尾最后一个区域保持到播放结束；尾奏可使用最后区域的保持或回滚点。

统一时间 = 当前音频时间 − 当前版本 offset。JSON 不保存屏幕像素；布局改变后重新计算。PHP 仅在存在同步文件时计算素材哈希，浏览器无需再次下载完整 MP3 校验；备用静态 JS 播放器通过浏览器计算。未审核、缺失、损坏、哈希不符或时长不符的数据不会控制滚动。

## 验证与发布

```sh
npm ci
npm test
python3 -m unittest discover -s tests -p 'test_*.py'
npx playwright install chromium
npm run test:browser
npm run sync:validate
php -l index.php
php -l lib.php
```

浏览器测试使用可控媒体时钟验证回滚、暂停、缓冲、人工接管、重新加入、快进与循环，并读取仓库真实 MP3 验证校准页。可通过 `CHROMIUM_PATH` 指定已有 Chromium。它不代表实际歌曲已试听审核；真机还需检查 iOS Safari、Android Chrome、图片加载与横竖屏，以及歌词最后一屏。

另开本地 PHP 服务后，可用 `PHP_BASE_URL=http://localhost:8080 npm run test:browser` 同时检查真实 PHP 目录页、文本/图片页、无效歌曲编号和手机菜单可见性。

试点素材检查见 [试点记录](sync-pilot.md)。正式启用每首歌前，目标是无段落跳错、长间奏平滑过渡、边界误差不超过 ±1 秒，并分别试听所有启用版本。记录每首自动处理耗时、人工修订耗时和原始边界误差，再决定批量处理范围。
