# 全库滚动数据：分批处理

2026-10-22 用户确认小批量效果后，授权处理全部歌曲，每完成一小批校验即 commit + push，用户可随时试听反馈。共 420 份歌词素材，418 份有对应音频；原有 8 首同步数据保留。进度以 [全库清单](sync-bulk-progress.json) 和已经提交的 `data/sync/` 为准。

## 批次 01

| 曲目 | 形式 | 启用音轨 | 每版时间点 |
| --- | --- | ---: | ---: |
| [01-040 父啊儿女称颂你名](http://localhost:8080/index.php?n=01-040) | TXT | 领唱、伴奏 | 7 |
| [05-001 圣哉三一歌](http://localhost:8080/index.php?n=05-001) | JPG | 伴奏 | 4 |
| [05-002 三一来临歌](http://localhost:8080/index.php?n=05-002) | JPG | 伴奏 | 16 |
| [05-004 赞我天父歌](http://localhost:8080/index.php?n=05-004) | JPG | 伴奏 | 20 |
| [05-011 荣归天父歌](http://localhost:8080/index.php?n=05-011) | JPG | 伴奏 | 18 |
| [05-016 慈父上帝歌](http://localhost:8080/index.php?n=05-016) | JPG | 伴奏 | 12 |

共 6 首、7 个音轨、84 个时间点。《圣哉三一歌》原谱印有四段，但录音仅演奏一段，因此只跟随实际演奏的一段。《三一来临歌》等按实际音乐反复次数回到下一段。文字保持最多四行居中；图片按本次演唱的歌词行建立阅读区域，当前行居中，并尽量完整预读下一个区域。图片的多段歌词仍保留在原图上，无重新排版。

文字采用已知歌词的 medium/int8 两组上下文音频窗口对齐，结合实际音乐反复核对段落起点；伴奏使用独立的音乐对应路径。图片通过横排 OCR、简谱旋律与实际音频的匹配建立时间轴，分别使用音符横向位置和节奏符号两组模板核对谱行边界，并检查实际反复次数。普通语音转写错误较多的候选不直接启用；模板冲突、区域缺失或音乐对应不足的曲目留待修订。

图片 OCR 的倾斜校正只服务于识别，阅读区域已映射回原图坐标。简谱行内同一组音符下方可能印有多段歌词，区域引用按各段及副歌的顺序建立。为完整预读下一歌词行，这批聚焦歌词行的 JPG 区域使用 `previewFraction: 1`；原先聚焦完整谱行的试点数据保持原设置。

机器复核记录位于 [sync-bulk-evidence](sync-bulk-evidence/)。`reviewed: true` 是播放器启用标记，不表示已完成逐曲人工试听，也不把两种算法相近当作实测歌词误差。各曲的完整原始素材、时间点和检查依据均可追溯。

批次 01 已通过素材哈希、时间范围、同步结构与 JS/Python 单元测试。实际 PHP 播放页完成 7 个音轨、三种视口下的 252 个区域位置检查与 75 次关键跳转，等待动画完成后核对，无页面脚本错误；结果见 [浏览器记录](sync-bulk-evidence/batch-01-browser.json)。原有 8 份同步文件字节不变。

## 批次 02

| 曲目 | 形式 | 启用音轨 | 每版时间点 |
| --- | --- | ---: | ---: |
| [01-122 每逢思念奇妙十架](http://localhost:8080/index.php?n=01-122) | TXT | 领唱、伴奏 | 5 |
| [05-020 荣耀大君王歌](http://localhost:8080/index.php?n=05-020) | JPG | 伴奏 | 12 |
| [05-023 赞美歌](http://localhost:8080/index.php?n=05-023) | JPG | 伴奏 | 9 |
| [05-027 天父世界](http://localhost:8080/index.php?n=05-027) | JPG | 伴奏 | 9 |
| [05-028 万福泉源歌](http://localhost:8080/index.php?n=05-028) | JPG | 领唱 | 12 |
| [05-029 愿仍高唱歌](http://localhost:8080/index.php?n=05-029) | JPG | 伴奏 | 24 |

共 6 首、7 个音轨、76 个时间点。对《愿仍高唱歌》中只印在第一段旁边的共同副歌续句，后续段落保留整块副歌可见，避免隐藏公共歌词。已通过同步校验及 228 项浏览器区域位置、78 次实际跳转检查，无页面脚本错误；见 [浏览器记录](sync-bulk-evidence/batch-02-browser.json)。

## 批次 03

| 曲目 | 启用音轨 | 每版时间点 |
| --- | ---: | ---: |
| [05-033 赞主权能](http://localhost:8080/index.php?n=05-033) | 伴奏 | 16 |
| [05-036 贺他为王歌](http://localhost:8080/index.php?n=05-036) | 领唱、伴奏 | 15 |
| [05-038 赞美耶稣歌](http://localhost:8080/index.php?n=05-038) | 伴奏 | 15 |
| [05-039 拥戴歌](http://localhost:8080/index.php?n=05-039) | 伴奏 | 16 |
| [05-040 荣耀归主名歌](http://localhost:8080/index.php?n=05-040) | 伴奏 | 12 |
| [05-041 颂赞主圣名歌](http://localhost:8080/index.php?n=05-041) | 伴奏 | 16 |

共 6 首、7 个音轨、105 个时间点。已通过同步校验及 315 项浏览器区域位置、84 次实际跳转检查，无页面脚本错误；见 [浏览器记录](sync-bulk-evidence/batch-03-browser.json)。

## 批次 04

| 曲目 | 启用音轨 | 每版时间点 |
| --- | ---: | ---: |
| [01-119 哎呀！救主真曾流血](http://localhost:8080/index.php?n=01-119) | 领唱、伴奏 | 5 |
| [05-042 尊贵名歌](http://localhost:8080/index.php?n=05-042) | 伴奏 | 16 |
| [05-044 主配受赞美](http://localhost:8080/index.php?n=05-044) | 领唱、伴奏 | 10 |
| [05-046 赞美不尽歌](http://localhost:8080/index.php?n=05-046) | 领唱、伴奏 | 12 |
| [05-047 颂主歌](http://localhost:8080/index.php?n=05-047) | 领唱、伴奏 | 16 |
| [05-048 道路真理生命歌](http://localhost:8080/index.php?n=05-048) | 领唱、伴奏 | 8 |

共 6 首、11 个音轨、118 个时间点。已通过同步校验及 354 项浏览器区域位置、126 次实际跳转检查，无页面脚本错误；见 [浏览器记录](sync-bulk-evidence/batch-04-browser.json)。批处理已补充相邻印刷歌词行粘连的拆分识别、曲调参考页脚排除，以及谱行数量变化时禁止截断时间轴的检查。

## 批次 05

| 曲目 | 启用音轨 | 每版时间点 |
| --- | ---: | ---: |
| [05-015 称谢歌](http://localhost:8080/index.php?n=05-015) | 伴奏 | 12 |
| [05-049 主名甜美歌](http://localhost:8080/index.php?n=05-049) | 伴奏 | 10 |
| [05-053 奇妙救主歌](http://localhost:8080/index.php?n=05-053) | 领唱、伴奏 | 16 |
| [05-054 心向主宝座歌](http://localhost:8080/index.php?n=05-054) | 伴奏 | 20 |
| [05-055 赞美主恩歌](http://localhost:8080/index.php?n=05-055) | 伴奏 | 12 |
| [05-058 生命活水歌](http://localhost:8080/index.php?n=05-058) | 伴奏 | 20 |

共 6 首、7 个音轨、106 个时间点。已通过同步校验及 318 项浏览器区域位置、84 次实际跳转检查，无页面脚本错误；见 [浏览器记录](sync-bulk-evidence/batch-05-browser.json)。另对前五批全部 39 个音轨完成 1467 项区域位置检查，并确认 830 处空间足够的图片阅读位置完整露出下一句，见 [预读复核](sync-bulk-evidence/batches-01-05-geometry.json)。文字音频窗口新增逐块断点保存，中断后可复用已完成窗口。

清单另标记 `missing-lyrics`：目前有 23 份 TXT 只有歌名，或含“歌词整理中”和链接，不能制作正文对应的阅读区域；原文件保留，不把标题冒充歌词正文。`missing-audio` 为原素材没有配套 MP3 的 2 首：`01-330 我舍一切而要耶稣`、`05-379 诗篇二十三`。

## 断点续跑

基础依赖及模型安装到独立环境；浏览器播放不需要这些依赖。

```sh
python -m pip install -r tools/requirements-sync-batch.txt
python tools/batch-sync.py --stage inventory --cache /tmp/chinese-hymn-bulk
python tools/batch-sync.py --stage ocr --cache /tmp/chinese-hymn-bulk --workers 2 --threads 2
python tools/batch-sync.py --stage align --cache /tmp/chinese-hymn-bulk \
    --model-dir /tmp/chinese-hymn-models --songs 01-040 --workers 1 --threads 2
python tools/batch-sync.py --stage align --kind text --cache /tmp/chinese-hymn-bulk \
    --model-dir /tmp/chinese-hymn-models --workers 2 --threads 2
python tools/batch-sync.py --stage score --cache /tmp/chinese-hymn-bulk \
    --songs 05-001 05-002 --workers 1 --threads 1
python tools/batch-sync.py --stage publish --cache /tmp/chinese-hymn-bulk \
    --songs 05-020 05-023
python tools/batch-sync.py --stage report --cache /tmp/chinese-hymn-bulk
```

模型必须已缓存，程序不自动下载模型。`transcribe` 阶段可额外生成普通语音转写供诊断，不是发布条件。原图、MP3 和 TXT 不修改；已有同步文件自动跳过。缓存保存原素材 SHA-256，素材变化后重新计算。候选写入缓存的 `ready/`；`publish` 仅接收显式指定的本批曲目，核对当前素材及全部音轨，再将候选和复核记录放入仓库，拒绝覆盖已有文件。随后运行同步校验和浏览器位置检查，通过后提交推送。`report` 更新全库进度，包括待校准及待音轨匹配的曲目。

`--retry` 可重试失败候选，优先复用已保存的音频窗口。只有至少两个独立窗口组已确认相同音乐相位、重复音乐足够相似，且冲突候选中有一个得到相位佐证时，才丢弃冲突窗口；无声学依据的边界不靠音乐相位补造。

音频窗口按原素材哈希、演唱顺序、语言及窗口参数保存断点；每完成一组即写入缓存。任务中断后重启同一命令，已完成的匹配窗口不会重复计算。`--kind text` 或 `--kind image` 可限定处理形式。

```sh
npm run sync:validate
npm test
python3 -m unittest discover -s tests -p 'test_*.py'
PHP_BASE_URL=http://127.0.0.1:8080 \
    SYNC_SONGS=01-040,05-001,05-002,05-004,05-011,05-016 npm run test:batch-sync
```

浏览器检查各已启用音轨的真实加载、每个时间点在桌面及手机横竖屏下的位置，并实际跳转到首屏、第二屏、回滚及末屏，等待动画完成后核对。`SYNC_SCREENSHOT_DIR` 可保存截图，`SYNC_AUDIT_FILE` 可保存检查汇总。如需指定已有 Chromium，使用 `CHROMIUM_PATH`。
