(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const Core = window.HymnSyncCore;
    const audio = $('audio');
    const content = $('content');
    let data, source, files = [], urls = [], controller, lineCount;
    const message = text => { $('message').textContent = text; };
    const track = () => data?.tracks.find(item => item.file === $('track').value);
    const activeCues = () => track()?.cues || data.cues;
    const attempt = fn => async () => { try { await fn(); } catch (error) { message(error.message); } };
    const required = () => { if (!data) throw new Error('请先建立或导入草稿。'); };

    function stopPreview() {
        controller?.destroy();
        controller = null;
        $('preview').checked = false;
        document.body.classList.remove('sync-editor-preview');
    }

    function changed() {
        stopPreview();
        data.status = 'draft';
        data.tracks.forEach(item => { item.reviewed = false; });
        $('review-track').checked = false;
    }

    function options(element, entries, selected) {
        element.replaceChildren(...entries.map(([value, label]) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            return option;
        }));
        if (selected !== undefined) element.value = selected;
    }

    function lists() {
        options($('region-list'), Object.keys(data.regions).map(id => [id, id]), $('region-list').value);
        options($('cue-list'), activeCues().map((cue, index) => [String(index), `${cue.at.toFixed(2)}s · ${cue.label || cue.region}`]));
    }

    async function duration(url) {
        return new Promise((resolve, reject) => {
            const probe = document.createElement('audio');
            probe.preload = 'metadata';
            const timer = setTimeout(() => { probe.removeAttribute('src'); reject(new Error('音频时长读取超时')); }, 15000);
            const finish = fn => { clearTimeout(timer); probe.removeAttribute('src'); probe.load(); fn(); };
            probe.onloadedmetadata = () => {
                const value = probe.duration;
                finish(() => Number.isFinite(value) && value > 0 ? resolve(value) : reject(new Error('音频时长无效')));
            };
            probe.onerror = () => finish(() => reject(new Error('音频无法解码')));
            probe.src = url;
        });
    }

    async function readMaterials() {
        source = $('source-file').files[0];
        files = Array.from($('audio-files').files);
        if (!source || !files.length) throw new Error('请选择歌词和至少一个 MP3 文件。');
        stopPreview();
        data = undefined;
        audio.pause();
        urls.forEach(URL.revokeObjectURL);
        urls = files.map(file => URL.createObjectURL(file));
        const type = /\.txt$/i.test(source.name) ? 'text' : 'image';
        content.replaceChildren();
        lineCount = undefined;
        if (type === 'text') {
            const bytes = await source.arrayBuffer();
            let text;
            try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
            catch (_) { text = new TextDecoder('gb18030').decode(bytes); }
            const pre = document.createElement('pre');
            pre.textContent = text;
            content.append(pre);
            lineCount = window.HymnSync.renderText(pre);
        } else {
            const wrap = document.createElement('div');
            wrap.className = 'image-wrap';
            const img = document.createElement('img');
            img.alt = source.name;
            img.draggable = false;
            const url = URL.createObjectURL(source);
            urls.push(url);
            img.src = url;
            const selection = document.createElement('div');
            selection.id = 'selection';
            selection.hidden = true;
            wrap.append(img, selection);
            content.append(wrap);
            await img.decode();
        }
        $('start-label').textContent = type === 'text' ? '首行（从 0 开始）' : '上边界（0–1）';
        $('end-label').textContent = type === 'text' ? '末行' : '下边界（0–1）';
        for (const id of ['region-start', 'region-end']) $(id).step = type === 'text' ? '1' : '0.001';
        message('正在计算素材校验值…');
        const tracks = await Promise.all(files.map(async (file, index) => ({
            file: file.name, sha256: await window.HymnSync.sha256(await file.arrayBuffer()),
            duration: await duration(urls[index]), offset: 0, reviewed: false
        })));
        const songId = source.name.match(/^\d{2}-\d{1,3}/)?.[0];
        if (!songId) throw new Error('歌词文件名需要以歌曲编号开头。');
        return { version: 1, songId, status: 'draft', source: {
            file: source.name, type, sha256: await window.HymnSync.sha256(await source.arrayBuffer())
        }, tracks, regions: {}, cues: [] };
    }

    function show() {
        options($('track'), data.tracks.map(item => [item.file, item.file]));
        lists();
        switchTrack();
        message('素材已载入。选择阅读区域后记录时间点；间奏开始时间可用于缓慢回滚。');
    }

    function switchTrack() {
        stopPreview();
        const current = track();
        const time = audio.currentTime || 0;
        audio.pause();
        audio.src = urls[files.findIndex(file => file.name === current.file)];
        audio.onloadedmetadata = () => { audio.currentTime = Math.min(time, audio.duration); };
        $('offset').value = current.offset;
        $('review-track').checked = current.reviewed;
        $('independent').checked = !!current.cues;
        lists();
    }

    $('new-draft').onclick = attempt(async () => { data = await readMaterials(); show(); });
    $('import-file').onchange = attempt(async () => {
        const input = $('import-file').files[0];
        if (!input) return;
        const imported = JSON.parse(await input.text());
        const materials = await readMaterials();
        const errors = Core.validate(imported, { lineCount });
        if (errors.length) throw new Error(errors.join('\n'));
        if (imported.source.file !== materials.source.file || imported.source.sha256 !== materials.source.sha256) throw new Error('歌词文件与导入数据不匹配，请重新制作草稿。');
        if (imported.tracks.length !== materials.tracks.length || imported.tracks.some(item => !materials.tracks.some(actual =>
            actual.file === item.file && actual.sha256 === item.sha256 && Math.abs(actual.duration - item.duration) < 1))) {
            throw new Error('请同时选择同步文件关联的所有音频；文件更换后需重新制作草稿。');
        }
        data = imported;
        show();
    });
    $('track').onchange = switchTrack;
    $('independent').onchange = attempt(() => {
        required();
        changed();
        if ($('independent').checked) track().cues = structuredClone(data.cues);
        else delete track().cues;
        lists();
        message($('independent').checked ? '已复制共享时间轴，接下来的时间修改仅用于当前版本。' : '当前版本已恢复共享时间轴。');
    });
    $('offset').onchange = attempt(() => { required(); const value = Number($('offset').value); if (!Number.isFinite(value)) throw new Error('偏移无效'); changed(); track().offset = value; });
    audio.ontimeupdate = () => { $('clock').textContent = `${audio.currentTime.toFixed(2)} 秒`; };

    function selection() {
        if (!data) return;
        const start = Number($('region-start').value), end = Number($('region-end').value);
        if (data.source.type === 'text') {
            content.querySelectorAll('.sync-line').forEach(line => line.classList.toggle('selected', Number(line.dataset.line) >= start && Number(line.dataset.line) <= end));
        } else {
            const band = $('selection');
            band.hidden = false;
            band.style.top = `${start * 100}%`;
            band.style.height = `${(end - start) * 100}%`;
        }
    }
    content.onclick = event => {
        if (!data || $('preview').checked) return;
        let value;
        if (data.source.type === 'text') {
            const line = event.target.closest('[data-line]');
            if (!line) return;
            value = Number(line.dataset.line);
        } else {
            const img = content.querySelector('img');
            const rect = img.getBoundingClientRect();
            value = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
            value = Math.round(value * 1000) / 1000;
        }
        if (event.shiftKey) $('region-end').value = value;
        else {
            $('region-start').value = value;
            $('region-end').value = data.source.type === 'text' ? value : Math.min(1, value + 0.15);
        }
        selection();
    };
    $('region-start').oninput = selection;
    $('region-end').oninput = selection;
    $('region-list').onchange = () => {
        const id = $('region-list').value, region = data.regions[id];
        if (!region) return;
        $('region-id').value = id;
        $('region-start').value = region.kind === 'text' ? region.startLine : region.top;
        $('region-end').value = region.kind === 'text' ? region.endLine : region.bottom;
        selection();
    };
    $('save-region').onclick = attempt(() => {
        required();
        const id = $('region-id').value.trim();
        if (!/^[a-zA-Z0-9_-]+$/.test(id)) throw new Error('区域编号使用英文字母、数字、横线或下划线。');
        const start = Number($('region-start').value), end = Number($('region-end').value);
        const region = data.source.type === 'text' ? { kind: 'text', startLine: start, endLine: end } : { kind: 'image', top: start, bottom: end };
        const candidate = { ...data, status: 'draft', regions: { ...data.regions, [id]: region } };
        const errors = Core.validate(candidate, { lineCount });
        if (errors.length) throw new Error(errors.join('\n'));
        changed(); data.regions[id] = region; lists(); $('region-list').value = id;
        message('区域已保存。');
    });
    $('delete-region').onclick = attempt(() => {
        required(); const id = $('region-list').value;
        if ([data.cues, ...data.tracks.map(item => item.cues || [])].some(cues => cues.some(cue => cue.region === id))) throw new Error('请先删除引用该区域的时间点。');
        changed(); delete data.regions[id]; lists();
    });

    function mark(id) { required(); $(id).value = Math.max(0, audio.currentTime - track().offset).toFixed(2); }
    $('mark-arrival').onclick = attempt(() => mark('cue-at'));
    $('mark-start').onclick = attempt(() => { mark('cue-start'); $('cue-kind').value = 'interlude'; });
    document.addEventListener('keydown', event => {
        if (event.target.closest('input, select, textarea, button') || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key.toLowerCase() === 'a') $('mark-arrival').click();
        if (event.key.toLowerCase() === 'i') $('mark-start').click();
    });
    function saveCue(update) {
        required();
        const cue = { at: Number($('cue-at').value), region: $('region-list').value, label: $('cue-label').value, transition: $('cue-kind').value };
        if ($('cue-start').value !== '') cue.moveStart = Number($('cue-start').value);
        const cues = activeCues().slice();
        if (update) {
            if ($('cue-list').value === '') throw new Error('请选择时间点。');
            cues[Number($('cue-list').value)] = cue;
        } else cues.push(cue);
        cues.sort((a, b) => a.at - b.at);
        const candidate = track().cues ? { ...data, status: 'draft', tracks: data.tracks.map(item => item === track() ? { ...item, cues } : item) } : { ...data, status: 'draft', cues };
        const errors = Core.validate(candidate, { lineCount });
        if (errors.length) throw new Error(errors.join('\n'));
        changed();
        if (track().cues) track().cues = cues;
        else data.cues = cues;
        lists(); $('cue-list').value = String(cues.indexOf(cue));
        message('时间点已保存。');
    }
    $('save-cue').onclick = attempt(() => saveCue(false));
    $('update-cue').onclick = attempt(() => saveCue(true));
    $('delete-cue').onclick = attempt(() => { required(); if ($('cue-list').value === '') return; changed(); activeCues().splice(Number($('cue-list').value), 1); lists(); });
    $('cue-list').onchange = () => {
        const cue = activeCues()[Number($('cue-list').value)];
        if (!cue) return;
        $('cue-at').value = cue.at; $('cue-start').value = cue.moveStart ?? '';
        $('cue-label').value = cue.label || ''; $('cue-kind').value = cue.transition || 'reading';
        $('region-list').value = cue.region; $('region-list').onchange();
    };
    $('seek-cue').onclick = attempt(() => {
        required(); const cue = activeCues()[Number($('cue-list').value)];
        if (!cue) throw new Error('请选择时间点。');
        audio.currentTime = Math.max(0, (cue.moveStart ?? cue.at) + track().offset - 3);
        return audio.play();
    });
    $('preview').onchange = attempt(() => {
        required();
        if (!$('preview').checked) { stopPreview(); return; }
        const errors = Core.validate(data, { lineCount });
        if (errors.length || !activeCues().length) { $('preview').checked = false; throw new Error(errors.join('\n') || '请先添加时间点。'); }
        content.querySelectorAll('.selected').forEach(line => line.classList.remove('selected'));
        if ($('selection')) $('selection').hidden = true;
        controller?.destroy();
        document.body.classList.add('sync-editor-preview');
        controller = new window.HymnSync.Controller(audio, content, data, track(), () => { $('preview').checked = false; document.body.classList.remove('sync-editor-preview'); message('已手动接管，可重新开启预览。'); });
        controller.setEnabled(true);
    });
    $('review-track').onchange = attempt(() => { required(); track().reviewed = $('review-track').checked; data.status = 'draft'; });

    function exportFile(reviewed) {
        required();
        const output = { ...data, status: reviewed ? 'reviewed' : 'draft',
            tracks: data.tracks.map(item => ({ ...item, reviewed: reviewed && (item.cues || data.cues).length > 0 })),
            activation: { policy: reviewed ? 'automatic' : 'draft', listeningReviewRequired: false } };
        const errors = Core.validate(output, { reviewed, lineCount });
        if (errors.length) throw new Error(errors.join('\n'));
        const url = URL.createObjectURL(new Blob([JSON.stringify(output, null, 2) + '\n'], { type: 'application/json' }));
        const link = document.createElement('a');
        link.href = url; link.download = data.source.file + '.json'; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        message(reviewed ? '已导出。放入 data/sync/ 后运行 npm run sync:validate，再提交发布。' : '草稿已导出，不会对听众启用。');
    }
    $('export-draft').onclick = attempt(() => exportFile(false));
    $('export-reviewed').onclick = attempt(() => exportFile(true));
})();
