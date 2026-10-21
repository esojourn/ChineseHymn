(function () {
    'use strict';
    const Core = window.HymnSyncCore;

    window.addEventListener('keydown', event => {
        if (event.defaultPrevented || event.isComposing || !event.ctrlKey || event.altKey || event.shiftKey || event.metaKey ||
            !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        const target = event.target;
        if (target instanceof Element && (target.closest('input, textarea, select') || target.isContentEditable)) return;
        const audio = document.querySelector('audio');
        if (!audio || !Number.isFinite(audio.duration)) return;
        event.preventDefault();
        audio.currentTime = Math.max(0, Math.min(audio.duration,
            audio.currentTime + (event.key === 'ArrowRight' ? 10 : -10)));
    });

    function renderText(pre) {
        const lines = Core.normalizeText(pre.textContent).split('\n');
        pre.replaceChildren(...lines.map((line, index) => {
            const span = document.createElement('span');
            span.className = 'sync-line';
            span.dataset.line = index;
            span.textContent = line || '\u00a0';
            return span;
        }));
        return lines.length;
    }

    class Controller {
        constructor(audio, content, data, track, onManual = () => {}) {
            this.audio = audio;
            this.content = content;
            this.data = track.cues ? { ...data, cues: track.cues } : data;
            this.track = track;
            this.onManual = onManual;
            this.enabled = false;
            this.playing = !audio.paused && audio.readyState >= 3;
            this.positions = [];
            this.abort = new AbortController();
            const listen = (target, event, handler, options = {}) => target.addEventListener(event, handler, { ...options, signal: this.abort.signal });
            listen(audio, 'playing', () => { this.playing = true; this.lastClock = null; this.start(); });
            for (const event of ['pause', 'waiting', 'ended', 'seeking']) {
                listen(audio, event, () => { this.playing = false; this.stop(); });
            }
            listen(audio, 'seeked', () => { this.playing = !audio.paused && audio.readyState >= 3; this.rejoin(); });
            listen(document, 'visibilitychange', () => { if (document.hidden) this.stop(); else this.rejoin(); });
            listen(window, 'resize', () => this.rejoin());
            listen(content, 'load', () => this.rejoin(), { capture: true });
            listen(audio, 'loadedmetadata', () => this.rejoin());
            const ignored = target => target instanceof Element && target.closest('#mySidenav, audio, button, input, select, textarea, [contenteditable], .sync-editor-controls');
            const manual = event => { if (!ignored(event.target)) this.manual(); };
            listen(window, 'wheel', manual, { passive: true });
            listen(window, 'touchmove', manual, { passive: true });
            listen(window, 'keydown', event => {
                if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) manual(event);
            });
            // Native scrollbar drags do not emit wheel/touch events.
            listen(window, 'pointerdown', event => {
                if (event.clientX >= document.documentElement.clientWidth) this.manual();
            });
            listen(window, 'scroll', () => {
                const actual = window.scrollY;
                if (this.enabled && this.expected !== undefined && Math.abs(actual - this.expected) > 3) this.manual();
                this.expected = actual;
            }, { passive: true });
            this.measure();
        }

        measure() {
            const height = window.innerHeight;
            const maximum = Math.max(0, document.documentElement.scrollHeight - height);
            let previous = window.scrollY;
            this.origin = previous;
            this.positions = this.data.cues.map(cue => {
                const region = this.data.regions[cue.region];
                let top, bottom;
                if (region.kind === 'text') {
                    const start = this.content.querySelector('[data-line="' + region.startLine + '"]');
                    const end = this.content.querySelector('[data-line="' + region.endLine + '"]');
                    if (!start || !end) throw new Error('歌词行不存在');
                    top = start.getBoundingClientRect().top + window.scrollY;
                    bottom = end.getBoundingClientRect().bottom + window.scrollY;
                } else {
                    const img = this.content.querySelector('img');
                    const rect = img.getBoundingClientRect();
                    top = rect.top + window.scrollY + region.top * rect.height;
                    bottom = rect.top + window.scrollY + region.bottom * rect.height;
                }
                previous = region.kind === 'text'
                    ? Core.textTarget(top, bottom, height, maximum)
                    : Core.regionTarget(top, bottom, height, previous, maximum);
                return previous;
            });
        }

        setEnabled(enabled) {
            this.enabled = enabled;
            if (enabled) this.rejoin();
            else this.stop();
        }

        manual() {
            if (!this.enabled) return;
            this.setEnabled(false);
            this.onManual();
        }

        rejoin() {
            this.stop();
            this.measure();
            this.expected = window.scrollY;
            this.join = { from: window.scrollY, elapsed: 0 };
            this.lastClock = null;
            this.lastTime = this.audio.currentTime;
            this.lastKey = null;
            this.start();
        }

        stop() {
            cancelAnimationFrame(this.frame);
            this.frame = null;
            this.lastClock = null;
        }

        start() {
            if (this.enabled && !document.hidden && !this.frame) this.frame = requestAnimationFrame(now => this.tick(now));
        }

        tick(now) {
            this.frame = null;
            if (!this.enabled || document.hidden) return;
            const time = this.audio.currentTime;
            const active = this.playing && !this.audio.paused && !this.audio.seeking && this.audio.readyState >= 3;
            const elapsed = this.lastClock === null ? 0 : Math.min(0.1, (now - this.lastClock) / 1000);
            this.lastClock = now;
            // Native looping may rewind without a seek event.
            if (this.lastTime !== undefined && time < this.lastTime - 0.1) {
                this.join = { from: window.scrollY, elapsed: 0 };
                this.lastKey = null;
            }
            this.lastTime = time;
            const state = Core.locate(this.data.cues, time - this.track.offset);
            if (!state) return;
            const key = state.from + ':' + state.to;
            if (!state.moving && this.lastKey !== null && key !== this.lastKey && !this.wasMoving) {
                this.join = { from: window.scrollY, elapsed: 0 };
            }
            this.lastKey = key;
            this.wasMoving = state.moving;
            let target = this.positions[state.from] + (this.positions[state.to] - this.positions[state.from]) * state.progress;
            const first = this.data.cues[0];
            const songTime = time - this.track.offset;
            if (songTime < first.at && first.moveStart !== undefined && first.moveStart < first.at) {
                target = this.origin + (this.positions[0] - this.origin) * Core.ease((songTime - first.moveStart) / (first.at - first.moveStart));
            }
            if (this.join) {
                // A user-requested rejoin can finish while paused; media-driven transitions cannot.
                if (active || this.audio.paused) this.join.elapsed += elapsed;
                const duration = this.data.source.type === 'image' ? 1.2 : 0.6;
                target = this.join.from + (target - this.join.from) * Core.ease(this.join.elapsed / duration);
                if (this.join.elapsed >= duration) this.join = null;
            }
            if (active || this.join || this.audio.paused) {
                this.expected = Math.max(0, Math.min(document.documentElement.scrollHeight - window.innerHeight, target));
                window.scrollTo({ top: this.expected, behavior: 'instant' });
                this.expected = window.scrollY;
            }
            if (active || this.join) this.start();
        }

        destroy() { this.stop(); this.abort.abort(); }
    }

    async function sha256(buffer) {
        if (!window.crypto?.subtle) throw new Error('当前环境无法校验同步素材');
        const hash = await crypto.subtle.digest('SHA-256', buffer);
        return Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, '0')).join('');
    }

    async function fetchHash(url) {
        const response = await fetch(url);
        if (!response.ok) throw new Error('素材读取失败');
        return sha256(await response.arrayBuffer());
    }

    async function mount(config) {
        const audio = document.querySelector('audio');
        const content = document.querySelector('.text-inner');
        const toggle = document.getElementById('auto-scroll');
        const status = document.getElementById('auto-scroll-status');
        if (!audio || !content || !toggle || !status || !config) return;
        document.body.classList.add('sync-player');
        const lineCount = content.querySelector('pre') ? renderText(content.querySelector('pre')) : undefined;
        let controller;
        const resume = document.createElement('button');
        resume.type = 'button';
        resume.id = 'resume-auto-scroll';
        resume.className = 'sync-resume';
        resume.hidden = true;
        resume.title = '继续自动滚动';
        resume.setAttribute('aria-label', '继续自动滚动');
        resume.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor"/></svg>';
        document.body.appendChild(resume);
        resume.addEventListener('click', () => {
            if (!controller || toggle.disabled) return;
            toggle.checked = true;
            toggle.dispatchEvent(new Event('change'));
        });
        toggle.checked = window.Cookies?.get('hymn-auto-scroll') !== '0';
        toggle.disabled = true;
        status.textContent = '正在检查自动滚动数据…';
        toggle.addEventListener('change', () => {
            resume.hidden = true;
            window.Cookies?.set('hymn-auto-scroll', toggle.checked ? '1' : '0', { expires: 60, path: '/' });
            controller?.setEnabled(toggle.checked);
            status.textContent = toggle.checked ? '自动跟随中；手动滚动可接管' : '已关闭，可随时重新开启';
        });
        try {
            const response = await fetch(config.manifest);
            if (!response.ok) throw new Error('本曲暂无自动滚动数据');
            const data = await response.json();
            if (Core.validate(data, { reviewed: true, lineCount }).length) throw new Error('本曲暂无已启用的自动滚动数据');
            if (data.source.type !== (lineCount === undefined ? 'image' : 'text')) throw new Error('同步数据与歌词格式不符');
            const track = data.tracks.find(item => item.file === config.audioFile && item.reviewed);
            if (!track || data.source.file !== config.sourceFile || data.songId !== config.songId) throw new Error('当前版本暂无自动滚动数据');
            const hashes = await Promise.all([
                config.sourceHash || fetchHash(config.sourceUrl),
                config.audioHash || fetchHash(config.audioUrl)
            ]);
            if (hashes[0] !== data.source.sha256 || hashes[1] !== track.sha256) throw new Error('素材已更新，自动滚动待重新校准');
            const img = content.querySelector('img');
            if (img) await img.decode();
            if (document.fonts) await document.fonts.ready;
            controller = new Controller(audio, content, data, track, () => {
                toggle.checked = false;
                window.Cookies?.set('hymn-auto-scroll', '0', { expires: 60, path: '/' });
                resume.hidden = toggle.disabled;
                status.textContent = '已手动接管，点击右下角继续按钮或打开开关恢复跟随';
            });
            const checkDuration = () => {
                if (Number.isFinite(audio.duration) && Math.abs(audio.duration - track.duration) > 1) {
                    controller.setEnabled(false);
                    toggle.disabled = true;
                    resume.hidden = true;
                    status.textContent = '音频时长与同步数据不符，待重新校准';
                }
            };
            toggle.disabled = false;
            status.textContent = toggle.checked ? '自动跟随中；手动滚动可接管' : '打开后随歌曲平滑移动';
            checkDuration();
            audio.addEventListener('loadedmetadata', checkDuration);
            if (!toggle.disabled) controller.setEnabled(toggle.checked);
        } catch (error) {
            resume.hidden = true;
            status.textContent = /[\u3400-\u9fff]/.test(error.message) ? error.message : '自动滚动数据无法读取，仍可手动阅读';
        }
    }

    window.HymnSync = { Controller, renderText, sha256, mount };
    const init = () => {
        if (window.hymnSyncConfig) mount(window.hymnSyncConfig);
        else {
            const toggle = document.getElementById('auto-scroll');
            if (toggle && !document.querySelector('audio')) {
                toggle.checked = window.Cookies?.get('hymn-auto-scroll') !== '0';
                toggle.addEventListener('change', () => window.Cookies?.set('hymn-auto-scroll', toggle.checked ? '1' : '0', { expires: 60, path: '/' }));
            }
        }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
