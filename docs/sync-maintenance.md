# 新增诗歌、更新伴奏后如何维护自动滚动

本文是后续维护入口。适用于新增诗歌、更换伴奏或领唱、修改 TXT 歌词、替换 JPG 歌谱，以及只调整现有滚动时间。所有命令从仓库根目录执行。

播放器只读取 JSON，不在网站上运行识别。制作时，普通歌曲可由程序自动生成候选；特殊编排需要维护者或 AI 核对配置和实际录音。并非所有歌曲都能用一个命令自动通过。没有歌词的歌曲继续跳过；没有配套音频、正文缺段或歌词与录音不符的歌曲列为待补，不编造时间点。

## 1. 下次可以直接给 AI 的指令

复制下面这段，只需替换曲号和更新内容：

```text
请按照 docs/sync-maintenance.md 维护以下歌曲的自动滚动数据：

曲号：05-100、……
变更：只更新伴奏 / 更新领唱 / 新增诗歌 / 修改歌词或歌谱。
新素材：已放入 data/；若尚未替换，先备份旧素材与同步文件。

要求：
1. 先检查实际文件、Git 差异、原同步文件及其 provenance.evidence，
   确认哪些素材变化，找到该曲原来的生成器和专用配置。
2. 只处理这些歌曲。未改动的歌词图片不重新 OCR；
   未改动音轨的最终时间轴和手工修订尽量保留。
   只有确实需要重新定位的录音才重新对齐。
3. 使用新的候选目录，先备份已有数据；
   不以修改 SHA-256、改 reviewed 为 true 或按总时长等比例缩放代替校准。
4. 保留全部实际演唱段落、首段、副歌和反复；
   图片当前谱行居中，空间允许时能看到下一句，回滚不得漏段。
5. 延续用户试听确认的额外提前 1.5 秒策略，最低为 0；
   已前移的时间点不得再次前移，特别注意保留音轨与新生成音轨混用的情况。
   05-099 的现有手工数据不要自动修改；如明确要求重建该曲，单独核对。
6. 检查所有配套伴奏和领唱的哈希、时长、阅读顺序和独立时间轴。
   没有歌词则跳过；素材不完整或校准未通过则报告原因，不强行启用。
7. 完成目标曲校验、桌面/手机实际播放抽查，更新相关证据与目录，
   再 commit + push，并列出处理结果及尚待补充的素材。
```

这段指令允许维护者根据实际歌曲选择方法，而不是重新做全库识别。若旧文件已被覆盖，Git 中上一版素材通常仍可用于比较。

## 2. 先判断需要做什么

| 变更 | 处理方法 |
| --- | --- |
| 只想再提前或延后显示 | 调整现有 JSON；不识别音频或图片。见第 6 节。 |
| 只改 MP3 编码、音量或音质 | 哈希仍会变化。先确认整段演奏、起点、停顿及结尾完全对应，再决定能否复用时间轴；仅时长相同不足以证明。 |
| 只换伴奏，领唱和歌词未变 | 保留原阅读区域与领唱最终时间轴；用旧/新音频对应、同曲原谱或人工校准定位新伴奏。不要对纯伴奏运行语音识别。 |
| 换领唱或改了前奏、速度、间奏、反复 | 为变更的录音重建时间轴，不能只更新哈希或统一缩放。若生成器以领唱为参考，还需重新核对伴奏对应。 |
| 增加另一版录音 | 为新音轨增加独立校准；原来审核通过的音轨不能因复用共享 `cues` 而被改动。 |
| 修改 TXT，包括空行、顺序或双语对照 | 检查源文件哈希与行号。重新建立或核对区域及演唱顺序；不要直接复用旧行号。 |
| 替换、裁切、旋转 JPG，或改了歌词 | 重新核对图片相对坐标、谱行、段落和实际演唱顺序。旧图的区域比例不一定适用。 |
| 新增歌曲 | 命名、目录更新、生成候选、应用前移、校验、发布，按下文完整流程执行。 |

同步文件位于 `data/sync/<原歌词文件名>.json`，例如 `05-100万古磐石.jpg.json`。同名伴奏为 `05-100万古磐石.mp3`，领唱为 `05-100万古磐石-领唱.mp3`。

## 3. 环境与备份

已有制作环境可以直接复用 `.venv`、`node_modules` 和 `sync-drafts/models`。新环境的基础准备：

```sh
npm ci
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements-sync.txt
php -S localhost:8080
```

PHP 需要 `mbstring`。浏览 `http://localhost:8080/index.php` 和 `http://localhost:8080/tools/sync-editor.html`。手工校准不需要 Python 模型。

自动制作还需要离线环境：

```sh
.venv/bin/pip install -r tools/requirements-sync-batch.txt
npx playwright install chromium
```

模型、原始外部 MIDI/PDF/SVG 参考及工作缓存不在 Git 中。优先保留或复制原制作环境的 `sync-drafts/`；脚本的 `--model-dir` 默认为 `sync-drafts/models`。通用语音对齐使用 medium/int8 并要求本地模型可用，不能仅因为某次自由转写使用了 small 或 large-v3，就认为通用对齐所需模型已经齐备。专用配置固定了模型、解析器或原谱哈希的，仍须满足对应要求。环境缺模型时可以先走手工制作，不需要在网站服务器上安装识别模型。

为每轮操作设置独立目录，以下以更新 05-100 为例；新增歌曲时替换为自己的真实曲号与文件名：

```sh
SONG_ID='05-100'
SOURCE_FILE='05-100万古磐石.jpg'
RUN_DIR="sync-drafts/maintenance/${SONG_ID}/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR/before" "$RUN_DIR/cache"
git status --short
```

更换素材前，备份旧 TXT/JPG、全部配套 MP3、同步 JSON 及 JSON 的 `provenance.evidence` 指向的证据文件到 `"$RUN_DIR/before"`。也记录原来的 `timingAdjustment`、`textLayout`、图片阅读区域和各音轨时间轴。不要把模型或这些工作备份加入 Git。

若素材已经换过，可用 `git show 'HEAD:data/具体文件名' > "$RUN_DIR/before/具体文件名"` 从 Git 取出上一版，输出到备份目录，不覆盖当前新文件。确认取出的版本确实是替换前版本；需要时改用更早提交。

素材仍使用同名文件更新；新增、删除或重命名素材后执行：

```sh
npm run update-files
```

PHP 实时扫描目录，`data/files.json` 则供备用静态实现使用，发生目录变化时一并提交。

## 4. 生成候选：选择一条合适的路径

### A. 不安装模型，人工校准或小幅修订

素材未变、只修改滚动位置时，在校准页选择原歌词和全部配套 MP3，导入原 JSON，修改后导出。保留现有 `timingAdjustment`，不要再运行一遍统一前移。

素材哈希已变时，编辑器会拒绝导入旧 JSON，这是正常的。使用新素材建立草稿，参考备份重建区域并校准变更音轨。不能把旧 JSON 的哈希改成新值就当作重新校准。复杂双语布局、原谱专用区域或需保留未改音轨时，优先由维护者根据备份和原生成器制作新候选。

也可先生成仅含元数据的草稿；它没有时间点，不会自动启用：

```sh
.venv/bin/python tools/prepare-sync.py "data/$SOURCE_FILE" \
    --output "$RUN_DIR/manual"
```

TXT 会提供候选行区域；JPG 在不加 OCR 参数时需要手工选区域。`prepare-sync.py` 轻量 OCR 参数使用的是另装的 PaddleOCR，和下文批处理使用的 RapidOCR 不是同一个环境。

记录新时间点时有两种选择：按实际下一句位置记录后，对这份全新候选统一提前 1.5 秒；或者直接按最终提前阅读位置记录。第二种情况不可再减 1.5 秒。每版音轨都要检查，编排不同使用独立时间轴。详细按键见[校准工具说明](lyric-sync.md#不安装模型的制作流程)。

### B. 通用自动候选

适用于普通文字歌词，以及能正确识别谱行、段落的 JPG 简谱。先读旧 JSON 的 `provenance.evidence`：若该曲原先使用专用配置，优先走 C，不要用通用流程覆盖已解决的特殊结构。

**更新已有歌曲时，先把旧同步 JSON 移到备份目录。** `batch-sync.py` 的生成阶段会跳过已经存在同步 JSON 的歌曲，`--retry` 也不会绕过这个条件。新增歌曲没有旧文件则省略这一步：

```sh
mv -- "data/sync/$SOURCE_FILE.json" "$RUN_DIR/before/$SOURCE_FILE.json"
.venv/bin/python tools/batch-sync.py --stage inventory \
    --cache "$RUN_DIR/cache" --songs "$SONG_ID"
```

此后本地该曲暂时没有自动滚动；在候选完成前不要部署这份中间状态。`inventory` 会保存全库素材清单，`--songs` 用于后续制作阶段选择目标，不能把清单数量当作本轮处理数量。

普通 TXT 的上下文对齐：

```sh
.venv/bin/python tools/batch-sync.py --stage align \
    --cache "$RUN_DIR/cache" --songs "$SONG_ID" --kind text \
    --workers 1 --threads 2
```

如果文字曲已生成候选，但伴奏或另一版领唱仍为 `reviewed: false`，可对尚未通过的音轨尝试独立对齐，然后再次检查结果，不能把未通过音轨改为已审核：

```sh
.venv/bin/python tools/batch-sync.py --stage align-tracks \
    --cache "$RUN_DIR/cache" --songs "$SONG_ID" --kind text \
    --workers 1 --threads 2 --retry
```

如需整曲自由转写辅助核对，另运行以下命令；它本身不生成滚动 JSON，也不是上面 `align` 的必需前置步骤：

```sh
.venv/bin/python tools/batch-sync.py --stage transcribe \
    --cache "$RUN_DIR/cache" --songs "$SONG_ID" \
    --model medium --workers 1 --threads 2
```

JPG 简谱的通用路径为先识别原图，再匹配音乐：

```sh
.venv/bin/python tools/batch-sync.py --stage ocr \
    --cache "$RUN_DIR/cache" --songs "$SONG_ID" --kind image --workers 1
.venv/bin/python tools/batch-sync.py --stage score \
    --cache "$RUN_DIR/cache" --songs "$SONG_ID" --kind image \
    --workers 1 --threads 2
```

缓存中 `ready/<原歌词文件名>.json` 才是候选；`alignment/`、`score-alignment/` 等目录保存诊断。**进程退出码为 0 不代表每首歌成功**，批处理可能已经记录 `ERROR` 或 `needs-calibration` 后继续处理。必须检查候选的 `status: reviewed`、对应证据的 `status: ready`、全部音轨的 `reviewed: true`，以及源文件和音轨哈希均为本轮素材。

`needs-calibration` 时先看证据中的 `reason`，核对缺段、反复次数和模型环境；不要降低门槛或直接启用。相同素材的失败候选可用相同命令加 `--retry` 重试。图片只有歌词而没有简谱、非规则编排、器乐变奏等，改用适合的专用方法或人工校准。

### C. 原曲专用方法与配置

先从原 JSON 的 `provenance.evidence` 打开证据，查看其中的 `profile`、`mode` 和生成方式，再查 `tools/` 中对应配置。配置绑定的是旧源文件及音轨哈希；素材更新后必须重新核对配置对应的新录音、段落、参考谱和范围，不能只替换哈希令检查通过。

| 情况或示例 | 生成器 | 配置或补充说明 |
| --- | --- | --- |
| 05-100 完整三段及伴奏器乐反复 | `image-complete-repeat-sync.py` | `sync-image-complete-profiles.json` |
| 05-103、169、249、290 完整谱行修复 | `image-coverage-repair-sync.py` | `sync-image-coverage-profiles.json` |
| 同名原始多声部 MIDI | `repository-midi-sync.py` | `sync-repository-midi-profiles.json` |
| 明确的分段 MIDI/原谱结构 | `sectional-midi-sync.py` | `sync-sectional-midi-profiles.json` |
| 已核对的演唱词块、图片歌词及部分文字曲目 | `align-image-transcript-sync.py` | `sync-image-transcript-profiles.json`、`sync-text-transcript-profiles.json` |
| 06-004 中英逐句阅读 | `paired-reading-sync.py` | [双语说明](sync-paired-readings.md) |
| 06-016 大提琴变调、变奏 | `instrumental-section-sync.py` | `sync-instrumental-section-profiles.json` |
| 01-270 Cambridge 完整曲调 | `staff-cycle-sync.py` | [原谱循环说明](sync-staff-cycles.md) |

这些方法不互相通用，表格也不是所有配置的完整目录。生成器通常支持 `--cache`、`--songs`，是否有 `--prepare`、`--measure` 或模型参数，以各程序 `--help` 为准。例如下面的命令只在对应配置已重新核对且参考资料齐备时执行：

```sh
.venv/bin/python tools/image-complete-repeat-sync.py \
    --cache "$RUN_DIR/cache" --songs 05-100 --prepare
```

`--prepare` 的含义因程序而异；可能重新 OCR，也可能下载固定原谱。重建时需要的外部原谱和模型若未保存在新缓存，先按该配置恢复。沿用缓存只限哈希一致的原始输入，不将旧 `ready/` 成品当新候选。使用专用生成器也应先移出旧同步文件，以免某些生成器直接跳过。

## 5. 只更新一条音轨时的原则

歌词和图片未变，可以保留原来的 `regions`、`imageLayout`、`textLayout`。未变录音可复用已验证的时间轴及原始识别缓存；新录音单独进行音乐对应或校准。音轨哈希变化后，播放器会停用不匹配的时间轴，因此要把新音频及新 JSON 一同部署。

重点区分两种候选：

- **整份重新生成**：全部音轨时间都是生成器的基础时间，按第 6 节对整份候选额外提前一次。
- **保留旧轨，仅替换新轨**：旧轨已经提前，新轨可能还没提前。先把新轨处理成最终阅读时间，再与旧轨合并；必要时把旧的共享 `cues` 固定为旧轨的独立 `cues`。不能删除旧文件的调整标记后，对混合文件全体减 1.5 秒。当前前移工具按整份文件处理，不提供“仅处理某个音轨”的选项。

第二种情况由维护者按本页顶部的 AI 指令处理，发布前逐音轨与备份比较。若新录音改变了反复次数或段落顺序，还需重新检查共享预读元数据是否适用于新的独立时间轴。

## 6. 延续额外提前 1.5 秒，避免重复调整

`1.5.1` 已将现有数据在各自生成器输出基础上**额外**提前 1.5 秒，05-099 的既有手工调整除外。即使生成器本身已有阅读预览，也要区分其基础预览与本次额外调整，不能把旧的最终时间当作原始起唱证据。

对**全部音轨都是新基础时间**的候选，先预览待调整数量，再写入，范围只选本轮候选目录及曲号：

```sh
.venv/bin/python tools/advance-scroll-times.py \
    --directory "$RUN_DIR/cache/ready" --songs "$SONG_ID"
.venv/bin/python tools/advance-scroll-times.py \
    --directory "$RUN_DIR/cache/ready" --songs "$SONG_ID" --write
```

对人工导出的全新基础候选，将 `--directory` 改为人工导出文件所在目录。不要把目录指向诊断或 inventory JSON；工具处理的是同步文件。

工具同步调整共享 `cues[].at`、各轨独立 `cues[].at` 和已有 `moveStart`，最低为 0；不修改 `tracks[].offset`。`provenance.timingAdjustment` 记录本次处理，重复执行会跳过，找不到明确指定的曲号会报错。不要复制旧标记到尚未前移的新基础候选，否则工具会误认为已处理。若手工已记录最终提前位置，直接保留最终时间，不再运行这个命令。

05-099 始终被此工具排除。保留它的现有手工文件；如果某轮明确重做了该曲，需要单独试听确认新数据的最终阅读时间，不能因脚本跳过就认为新候选已提前。

## 7. 校验、替换和实际播放

先对候选执行与发布相同的结构、歌词行号和素材哈希检查：

```sh
node tools/validate-sync.js "$RUN_DIR/cache/ready/$SOURCE_FILE.json"
```

自动候选还要检查其 `provenance.evidence` 对应的新证据。首次发布可直接运行：

```sh
.venv/bin/python tools/batch-sync.py --stage publish \
    --cache "$RUN_DIR/cache" --songs "$SONG_ID"
```

更新时，旧同步 JSON 已在第 4 节移出；对应旧证据文件也需先备份并移出发布路径，否则发布器会拒绝覆盖。`--retry` 不能改变这个保护。先完成新候选和证据检查，再移动旧证据并发布；不删除原始素材。若专用 JPG 配置采用人工复核的谱行布局，确认发布器没有按普通 OCR 重建成错误区域；保留专用生成器核对过的坐标，并执行实际页面位置检查。

阅读候选中的 `provenance.evidence` 可找到应发布的证据路径；批处理发布器会根据扩展名到本轮缓存的 `alignment/`、`score-alignment/`、`transcript-alignment/`、`midi-alignment/` 或 `reference-alignment/` 取对应记录，文件名仍为 `<原歌词文件名>.json`。更新时将旧证据移到本轮备份，不要把旧诊断当新证据复制回来。

人工导出的 JSON 没有批处理证据时，不走批处理发布器；候选验证通过后复制到 `data/sync/`，工作说明记录本轮校准方法和调整策略。更新自动生成的曲目应随修改补充对应核对记录。

发布到本地工作区后：

```sh
npm run sync:validate
npm test
```

另一个终端保持 PHP 服务运行。当前批处理歌曲可执行：

```sh
PHP_BASE_URL=http://localhost:8080 SYNC_SONGS="$SONG_ID" \
    SYNC_AUDIT_FILE="$RUN_DIR/browser.json" npm run test:batch-sync
```

此测试默认只遍历 `provenance.batch` 为 `bulk-2026-10` 的数据，人工新曲和早期试点可能被跳过。务必核对报告的 `songs` 和 `tracks` 是否包含目标曲全部音轨；没有覆盖时用实际页面或补充定向测试验证，不能把“测试运行了”当作“目标曲通过了”。首批三首 JPG 可另用 `PHP_BASE_URL=http://localhost:8080 npm run test:image-preview`。

实际试听必须覆盖第一段、每次换行、反复/副歌返回、长间奏、最后一段与尾奏；分别检查伴奏和领唱、桌面和手机横竖屏。图片应保留当前歌词并露出下一句，提前移动应在下一句开始前完成；不要为了露出后句而隐藏仍在唱的前句。

失败时保留新候选和诊断，恢复备份的**配套素材及同步文件**，或保持无自动滚动继续正常播放。只恢复旧 JSON、继续使用新 MP3，会因哈希不符而保持停用，不是完整回退。

## 8. 提交与后续部署

核对 `git diff --stat`、目标曲时间轴和未改音轨的差异，仅提交本轮修改的 TXT/JPG/MP3、同步 JSON、配置、证据，以及发生目录变化时的 `data/files.json`。素材与同步数据配套部署；目录增加或删除要同步反映。不要提交 `.venv/`、模型、缓存或工作备份。

发布器生成的记录可用以下命令刷新，但它的清单不能替代哈希校验：

```sh
.venv/bin/python tools/batch-sync.py --stage report --cache "$RUN_DIR/cache"
git diff --check
git status --short
```

`report` 会更新全库清单，新的隔离缓存可能没有以前未完成曲目的诊断；提交前核对是否产生无关状态变化。`published` 只说明文件存在，并不证明当前素材哈希匹配，仍以 `sync:validate` 为准。完成后按本轮授权 commit + push；合并 master 或创建新 release 另按发布安排执行。

## 9. 常见现象

| 现象 | 原因与处理 |
| --- | --- |
| 命令结束但没处理目标曲 | 检查旧同步 JSON 是否仍在 `data/sync/`，曲号是否正确，是否被素材核对清单拦住。 |
| 导入失败或播放器停用滚动 | 检查歌词/图片、全部 MP3 哈希、文件名、版本及音频时长；更新素材后需新候选。 |
| `needs-source-review` | 参见 `tools/sync-source-reviews.json` 和[待补记录](sync-bulk-evidence/remaining-source-audit.json)；先解决真实缺段或错配。 |
| 新歌没有专用配置 | 尝试适合的通用路径或人工校准；专用配置不是按标题猜测就能复用。 |
| 第一段漏掉或编号正确但时间偏后一整段 | 复查完整反复次数和开头搜索；05-100 曾发生此问题，不能只看 JSON 是否有第一段区域。 |
| 提前后又早了 1.5 秒 | 检查是否重复修改、是否把已前移旧轨与原始新轨混合处理；从备份恢复并仅处理未前移部分。 |
| 新缓存缺原谱、模型或文件哈希不符 | 恢复配置所需的原始参考资料或重新核对新参考；不跳过绑定检查。 |
| 只需要改善图片定位 | 素材未变且时间正确，可参考 `prepare-sync.py --draft ... --reframe`；它保留时间，不应再前移。 |
