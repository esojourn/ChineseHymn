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

    function renderText(pre, layout, originalText = pre.textContent) {
        const lines = Core.normalizeText(originalText).split('\n');
        const order = layout?.lineOrder || lines.map((_, index) => index);
        pre.replaceChildren(...order.map((sourceLine, index) => {
            const span = document.createElement('span');
            span.className = 'sync-line';
            // Preserve line breaks even if an older stylesheet is still cached.
            span.style.display = 'block';
            span.dataset.line = index;
            span.dataset.sourceLine = sourceLine;
            span.textContent = lines[sourceLine] || '\u00a0';
            return span;
        }));
        return lines.length;
    }

    function imageReturnBands(img, region) {
        const width = Math.min(1024, img.naturalWidth);
        if (!width || !img.naturalHeight) return [];
        const height = Math.round(img.naturalHeight * width / img.naturalWidth);
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        if (!context) return [];
        context.drawImage(img, 0, 0, width, height);
        const first = Math.max(0, Math.round(region.top * height));
        const last = Math.min(height, Math.round(region.bottom * height));
        if (last <= first) return [];
        let pixels;
        try { pixels = context.getImageData(0, first, width, last - first).data; }
        catch (_) { return []; } // A cross-origin image must still remain readable without a hint.
        const bands = [];
        const sums = [0];
        let start = null, end = 0;
        const dark = (x, y) => {
            if (x < 0 || x >= width || y < 0 || y >= last - first) return false;
            const i = (y * width + x) * 4;
            return pixels[i + 3] > 128 && pixels[i] + pixels[i + 1] + pixels[i + 2] < 435;
        };
        const finish = () => {
            if (start !== null && end - start + 1 >= 5) {
                let left = 0;
                for (let x = 0; x < width; x++) {
                    let ink = 0, above = false, below = false;
                    for (let y = start; y <= end; y++) if (dark(x, y)) ink++;
                    for (let dx = -2; dx <= 2; dx++) {
                        above ||= dark(x + dx, start - 3);
                        below ||= dark(x + dx, end + 3);
                    }
                    // Ignore long staff braces, which continue past both sides of the lyric row.
                    if (ink >= Math.max(2, (end - start + 1) * 0.12) && !(above && below)) {
                        left = x / width;
                        break;
                    }
                }
                bands.push({ top: (first + start) / height, bottom: (first + end + 1) / height,
                    left, density: (sums[end + 1] - sums[start]) / ((end - start + 1) * width) });
            }
            start = null;
        };
        for (let y = 0; y < last - first; y++) {
            let ink = 0;
            for (let x = Math.round(width * 0.08); x < Math.round(width * 0.95); x++) {
                const i = (y * width + x) * 4;
                if (pixels[i + 3] > 128 && pixels[i] + pixels[i + 1] + pixels[i + 2] < 435) ink++;
            }
            sums.push(sums[sums.length - 1] + ink);
            if (ink > width * 0.025) {
                if (start === null) start = y;
                end = y;
            } else if (start !== null && y - end > 2) finish();
        }
        finish();
        return bands;
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
            const regionBounds = [];
            this.positions = this.data.cues.map((cue, index) => {
                const region = this.data.regions[cue.region];
                let top, bottom, previewBottom;
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
                    const next = this.data.regions[this.data.cues[index + 1]?.region];
                    if (this.data.imageLayout && next && next.top >= region.bottom) {
                        previewBottom = rect.top + window.scrollY + rect.height *
                            (next.top + (next.bottom - next.top) * this.data.imageLayout.previewFraction);
                    }
                }
                regionBounds.push({ top, bottom });
                previous = region.kind === 'text'
                    ? Core.textTarget(top, bottom, height, maximum)
                    : this.data.imageLayout ? Core.imageTarget(top, bottom, previewBottom, height, maximum)
                    : Core.regionTarget(top, bottom, height, previous, maximum);
                return previous;
            });
            const legacyBilingual = this.data.provenance?.previousLinePreview?.version === 4;
            const scorePreview = this.data.provenance?.scoreReadingPreviews;
            const bilingualPreview = legacyBilingual || scorePreview?.version === 2;
            if (bilingualPreview || scorePreview?.version === 1) {
                const indices = legacyBilingual ? this.data.provenance.previousLinePreview.indices : scorePreview.indices;
                const edges = indices.map(index => {
                    const before = this.data.regions[this.data.cues[index - 1].region];
                    const after = this.data.regions[this.data.cues[index].region];
                    const last = this.content.querySelector('[data-line="' + (before.endLine - (bilingualPreview ? 1 : 0)) + '"]').getBoundingClientRect();
                    const first = this.content.querySelector('[data-line="' + (after.startLine + (bilingualPreview ? 1 : 0)) + '"]').getBoundingClientRect();
                    return { index, top: last.top + window.scrollY, bottom: first.bottom + window.scrollY };
                });
                this.positions = Core.previewTargets(this.positions, regionBounds, edges, height, maximum);
            }
            this.prepareReturnHints();
        }

        prepareReturnHints() {
            if (this.data.source.type !== 'image') return;
            const img = this.content.querySelector('img');
            if (!img?.naturalWidth) return;
            const key = `${img.currentSrc}:${img.naturalWidth}:${img.naturalHeight}`;
            if (this.returnImage === img && this.returnImageKey === key) return;
            const bands = new Map();
            this.returnAnchors = this.data.cues.map(cue => {
                if (cue.transition !== 'return') return null;
                const anchor = Core.imageReturnAnchor(this.data, cue);
                if (anchor) return anchor;
                if (!bands.has(cue.region)) bands.set(cue.region, imageReturnBands(img, this.data.regions[cue.region]));
                return Core.imageReturnAnchor(this.data, cue, bands.get(cue.region));
            });
            this.returnImage = img;
            this.returnImageKey = key;
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
            this.hideReturnDot();
        }

        hideReturnDot() {
            if (!this.returnDot) return;
            this.returnDot.hidden = true;
            this.returnDot.classList.remove('is-blinking');
        }

        flashReturnDot(index) {
            const anchor = this.returnAnchors?.[index];
            const region = this.data.cues[index].region;
            const img = this.content.querySelector('img');
            if (!anchor || !img) return;
            if (!this.returnDot) {
                this.returnDot = document.createElement('span');
                this.returnDot.className = 'sync-return-dot';
                this.returnDot.setAttribute('aria-hidden', 'true');
                this.returnDot.addEventListener('animationend', event => {
                    if (event.animationName === 'sync-return-blink') this.hideReturnDot();
                }, { signal: this.abort.signal });
                document.body.appendChild(this.returnDot);
            }
            this.hideReturnDot();
            const rect = img.getBoundingClientRect();
            this.returnDot.style.left = `${rect.left + window.scrollX + rect.width * anchor.x}px`;
            this.returnDot.style.top = `${rect.top + window.scrollY + rect.height * anchor.y}px`;
            this.returnDot.dataset.region = region;
            this.returnDot.hidden = false;
            // Restart all three flashes when revisiting the same verse on a later pass.
            void this.returnDot.offsetWidth;
            this.returnDot.classList.add('is-blinking');
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
            if (this.data.source.type === 'image' && state.to !== this.indicatorCue) {
                this.hideReturnDot();
                this.indicatorCue = state.to;
                const cue = this.data.cues[state.to];
                this.pendingReturn = cue.transition === 'return' && this.returnAnchors?.[state.to] ? state.to : null;
            }
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
            // Wait until both an interlude movement and the final smooth scroll have arrived.
            if (!state.moving && !this.join && this.pendingReturn === state.to &&
                Math.abs(window.scrollY - this.positions[state.to]) <= 3) {
                this.flashReturnDot(state.to);
                this.pendingReturn = null;
            }
            if (active || this.join) this.start();
        }

        destroy() { this.stop(); this.abort.abort(); this.returnDot?.remove(); }
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
        const pre = content.querySelector('pre');
        const originalText = pre?.textContent;
        const lineCount = pre ? renderText(pre) : undefined;
        let controller;
        const resume = document.createElement('button');
        resume.type = 'button';
        resume.id = 'resume-auto-scroll';
        resume.className = 'sync-resume';
        resume.hidden = true;
        resume.title = '启用自动滚动';
        resume.setAttribute('aria-label', '启用自动滚动');
        resume.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor"/></svg>';
        document.body.appendChild(resume);
        resume.addEventListener('click', () => {
            if (!controller || toggle.disabled) return;
            setFollowing(true);
        });
        const setFollowing = (enabled, message) => {
            toggle.checked = enabled;
            controller?.setEnabled(enabled);
            resume.hidden = toggle.disabled || enabled;
            status.textContent = message || (enabled ? '自动跟随中；手动滚动可接管' : '已关闭，点击右下角按钮或打开开关启用');
        };
        toggle.checked = window.Cookies?.get('hymn-auto-scroll') !== '0';
        toggle.disabled = true;
        status.textContent = '正在检查自动滚动数据…';
        toggle.addEventListener('change', () => {
            window.Cookies?.set('hymn-auto-scroll', toggle.checked ? '1' : '0', { expires: 60, path: '/' });
            setFollowing(toggle.checked);
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
            if (pre && data.textLayout) renderText(pre, data.textLayout, originalText);
            const img = content.querySelector('img');
            if (img) await img.decode();
            if (document.fonts) await document.fonts.ready;
            controller = new Controller(audio, content, data, track, () => {
                setFollowing(false, '已手动接管，点击右下角按钮或打开开关恢复跟随');
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
            checkDuration();
            audio.addEventListener('loadedmetadata', checkDuration);
            if (!toggle.disabled) setFollowing(toggle.checked);
        } catch (error) {
            resume.hidden = true;
            status.textContent = /[\u3400-\u9fff]/.test(error.message) ? error.message : '自动滚动数据无法读取，仍可手动阅读';
        }
    }

    window.HymnSync = { Controller, renderText, sha256, mount, imageReturnBands };
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
