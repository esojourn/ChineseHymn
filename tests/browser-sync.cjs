'use strict';
// Browser integration checks use a controllable media clock, not fabricated song timings.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const fixture = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/css/styles.css">
<style>body{margin:0}.text-inner{width:600px;margin:200px auto 800px}.sync-line{height:100px}#mySidenav{position:fixed;top:0;left:0;width:150px;background:white;z-index:10}</style></head>
<body><aside id="mySidenav"><button id="ordinary">菜单</button><input type="checkbox" id="auto-scroll"><p id="auto-scroll-status"></p></aside>
<audio></audio><div class="text-inner"><pre>${Array.from({length:40}, (_, i) => '歌词第' + i + '行').join('\n')}</pre></div>
<script src="/css/js.cookie-2.2.1.min.js"></script><script src="/css/lyric-sync-core.js"></script><script src="/css/lyric-sync.js"></script></body></html>`;
const server = http.createServer((req, res) => {
    if (req.url === '/fixture') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(fixture); return; }
    let file;
    try { file = path.resolve(root, '.' + decodeURIComponent(req.url.split('?')[0])); } catch (_) { res.writeHead(400).end(); return; }
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    fs.readFile(file, (error, data) => {
        if (error) { res.writeHead(404).end(); return; }
        const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.jpg': 'image/jpeg', '.mp3': 'audio/mpeg' };
        res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
        res.end(data);
    });
});

(async () => {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = 'http://127.0.0.1:' + server.address().port;
    let browser;
    try {
        browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
        const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/fixture');
        await page.bringToFront();
        // Complete the first paint before measuring animation progress in headless Chromium.
        await page.screenshot();
        await page.evaluate(() => {
            const content = document.querySelector('.text-inner');
            HymnSync.renderText(content.querySelector('pre'));
            window.media = { time: 10, paused: false, ready: 4 };
            const audio = document.querySelector('audio');
            for (const [key, get] of Object.entries({ currentTime: () => media.time, paused: () => media.paused,
                readyState: () => media.ready, seeking: () => false, duration: () => 120 })) Object.defineProperty(audio, key, { configurable: true, get });
            window.syncData = { version: 1, status: 'reviewed', songId: '01-001',
                source: { file: '01-001测试.txt', sha256: 'a'.repeat(64), type: 'text' },
                tracks: [{ file: '01-001测试.mp3', sha256: 'b'.repeat(64), duration: 120, offset: 0, reviewed: true }],
                regions: { a: { kind: 'text', startLine: 0, endLine: 2 }, b: { kind: 'text', startLine: 20, endLine: 22 } },
                cues: [{ at: 0, region: 'a' }, { at: 20, region: 'b', moveStart: 10 }, { at: 60, region: 'a', moveStart: 40 }] };
            window.manualCount = 0;
            window.controller = new HymnSync.Controller(audio, content, syncData, syncData.tracks[0], () => manualCount++);
            controller.setEnabled(true);
        });
        await page.waitForTimeout(700);
        const y0 = await page.evaluate(() => scrollY);
        await page.evaluate(() => { media.time = 15; });
        await page.waitForTimeout(100);
        const halfway = await page.evaluate(() => scrollY);
        await page.evaluate(() => { media.time = 20; });
        await page.waitForTimeout(100);
        const bottom = await page.evaluate(() => scrollY);
        assert.ok(y0 < halfway && halfway < bottom, 'interlude should move gradually towards target: ' + JSON.stringify({y0, halfway, bottom, errors, state: await page.evaluate(() => ({enabled:controller.enabled, playing:controller.playing, positions:controller.positions, manualCount, frame:controller.frame, lastTime:controller.lastTime, hidden:document.hidden}))}));
        assert.equal(await page.evaluate(() => controller.enabled), true, 'programmatic scrolling must not look manual');
        await page.evaluate(() => { media.time = 50; });
        await page.waitForTimeout(100);
        const returning = await page.evaluate(() => scrollY);
        assert.ok(returning < bottom && returning > y0, 'later verse smoothly revisits upper region');
        await page.evaluate(() => { media.paused = true; document.querySelector('audio').dispatchEvent(new Event('pause')); });
        await page.waitForTimeout(150);
        assert.equal(await page.evaluate(() => scrollY), returning, 'pause holds position');
        await page.evaluate(() => {
            media.paused = false; media.ready = 2;
            document.querySelector('audio').dispatchEvent(new Event('waiting'));
        });
        await page.waitForTimeout(100);
        assert.equal(await page.evaluate(() => scrollY), returning, 'buffering holds position');
        await page.evaluate(() => { media.ready = 4; document.querySelector('audio').dispatchEvent(new Event('playing')); });
        await page.locator('#ordinary').click();
        assert.equal(await page.evaluate(() => controller.enabled), true, 'menu click does not take over');
        await page.mouse.move(800, 500);
        await page.mouse.wheel(0, 150);
        await page.waitForTimeout(150);
        assert.equal(await page.evaluate(() => controller.enabled), false);
        assert.equal(await page.evaluate(() => manualCount), 1);
        const manualY = await page.evaluate(() => scrollY);
        await page.evaluate(() => { media.time = 60; });
        await page.waitForTimeout(100);
        assert.equal(await page.evaluate(() => scrollY), manualY, 'manual mode remains in control');
        await page.evaluate(() => controller.setEnabled(true));
        await page.waitForTimeout(200);
        const joining = await page.evaluate(() => scrollY);
        assert.ok(joining < manualY && joining > y0, 'rejoin begins from current position without snapping');
        await page.waitForTimeout(500);
        assert.ok(await page.evaluate(() => scrollY) < joining);
        await page.evaluate(() => {
            media.time = 20; document.querySelector('audio').dispatchEvent(new Event('seeked'));
        });
        await page.waitForTimeout(700);
        assert.ok(await page.evaluate(() => scrollY) > 1000, 'seeking moves to current verse');
        await page.evaluate(() => { media.time = 0; });
        await page.waitForTimeout(700);
        assert.ok(await page.evaluate(() => scrollY) < 200, 'native loop returns smoothly');
        await page.evaluate(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageDown', bubbles: true })); });
        assert.equal(await page.evaluate(() => controller.enabled), false);
        await page.evaluate(() => { controller.setEnabled(true); window.dispatchEvent(new Event('touchmove')); });
        assert.equal(await page.evaluate(() => controller.enabled), false);
        console.log('PASS: text transitions, interlude return, pause/buffering, manual takeover, seek and loop');

        await page.evaluate(() => {
            controller.destroy();
            media.time = 0; media.paused = false;
            const textData = { ...syncData,
                regions: { a: { kind: 'text', startLine: 4, endLine: 7 }, b: { kind: 'text', startLine: 8, endLine: 11 } },
                cues: [{ at: 0, region: 'a' }, { at: 20, region: 'b' }] };
            controller = new HymnSync.Controller(document.querySelector('audio'), document.querySelector('.text-inner'), textData, textData.tracks[0]);
            controller.setEnabled(true);
        });
        await page.waitForTimeout(700);
        const centered = await page.evaluate(() => {
            const first = document.querySelector('[data-line="4"]').getBoundingClientRect();
            const last = document.querySelector('[data-line="7"]').getBoundingClientRect();
            return (first.top + last.bottom) / 2;
        });
        assert.ok(Math.abs(centered - 350) < 2, 'current text centers even when it was already visible');
        const readingStart = await page.evaluate(() => scrollY);
        await page.evaluate(() => { media.time = 10; });
        await page.waitForTimeout(100);
        const readingMiddle = await page.evaluate(() => scrollY);
        assert.equal(readingMiddle, readingStart, 'four-line passage stays still during playback');
        await page.evaluate(() => { media.time = 19.99; });
        await page.waitForTimeout(100);
        const readingEnd = await page.evaluate(() => scrollY);
        assert.equal(readingEnd, readingStart);
        await page.evaluate(() => { media.time = 20; });
        await page.waitForTimeout(200);
        const transitionY = await page.evaluate(() => scrollY);
        assert.ok(transitionY > readingEnd && transitionY < readingEnd + 400, 'next four lines enter smoothly');
        await page.waitForTimeout(500);
        assert.ok(Math.abs(await page.evaluate(() => scrollY) - readingEnd - 400) < 2);
        await page.evaluate(() => { media.time = 25; });
        await page.waitForTimeout(100);
        assert.ok(Math.abs(await page.evaluate(() => scrollY) - readingEnd - 400) < 2, 'next passage stays still too');
        console.log('PASS: centered four-line passages, stable reading and smooth boundary');

        // Image geometry must follow the displayed image, not source pixel dimensions.
        await page.evaluate(async () => {
            controller.destroy(); scrollTo(0, 0);
            const content = document.querySelector('.text-inner');
            content.innerHTML = '<img alt="fixture" style="width:100%" src="data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="2400"><rect width="600" height="2400" fill="white"/></svg>') + '">';
            await content.querySelector('img').decode();
            syncData.source.type = 'image';
            syncData.regions = { a: { kind: 'image', top: 0.1, bottom: 0.2 }, b: { kind: 'image', top: 0.8, bottom: 0.9 } };
            media.time = 20; media.paused = true;
            controller = new HymnSync.Controller(document.querySelector('audio'), content, syncData, syncData.tracks[0]);
            controller.setEnabled(true);
        });
        await page.waitForTimeout(1300);
        const largeImageY = await page.evaluate(() => scrollY);
        await page.evaluate(() => { document.querySelector('.text-inner').style.width = '300px'; window.dispatchEvent(new Event('resize')); });
        await page.waitForTimeout(1300);
        const smallImageY = await page.evaluate(() => scrollY);
        assert.ok(smallImageY < largeImageY && smallImageY > 0);
        assert.equal(await page.evaluate(() => controller.enabled), true);
        console.log('PASS: normalized image coordinates and resize');

        await page.goto(base + '/fixture');
        await page.evaluate(() => {
            Cookies.set('hymn-auto-scroll', '1');
            return HymnSync.mount({ manifest: '/not-found.json' });
        });
        assert.equal(await page.locator('#auto-scroll').isDisabled(), true);
        assert.equal(await page.evaluate(() => Cookies.get('hymn-auto-scroll')), '1');
        assert.match(await page.locator('#auto-scroll-status').textContent(), /暂无/);
        console.log('PASS: missing timeline does not clear preference or break playback');

        const mountedData = {
            version: 1, status: 'reviewed', songId: '01-001',
            source: { file: '01-001测试.txt', sha256: 'a'.repeat(64), type: 'text' },
            tracks: [{ file: '01-001测试.mp3', sha256: 'b'.repeat(64), duration: 120, offset: 0, reviewed: true }],
            regions: { first: { kind: 'text', startLine: 0, endLine: 2 }, last: { kind: 'text', startLine: 20, endLine: 22 } },
            cues: [{ at: 0, region: 'first' }, { at: 20, region: 'last' }]
        };
        await page.route('**/timeline.json', route => route.fulfill({ json: mountedData }));
        await page.goto(base + '/fixture');
        await page.screenshot();
        await page.evaluate(async () => {
            Cookies.remove('hymn-auto-scroll', { path: '/' });
            const audio = document.querySelector('audio');
            Object.defineProperty(audio, 'currentTime', { get: () => 25 });
            Object.defineProperty(audio, 'duration', { get: () => 120 });
            window.mountConfig = { songId: '01-001', manifest: '/timeline.json', sourceFile: '01-001测试.txt',
                audioFile: '01-001测试.mp3', sourceHash: 'a'.repeat(64), audioHash: 'b'.repeat(64) };
            await HymnSync.mount(mountConfig);
        });
        await page.waitForTimeout(700);
        assert.equal(await page.locator('#auto-scroll').isDisabled(), false);
        assert.equal(await page.locator('#auto-scroll').isChecked(), true, 'auto scrolling defaults on without a saved preference');
        assert.equal(await page.locator('#resume-auto-scroll').isVisible(), false);
        assert.ok(await page.evaluate(() => scrollY) > 1000, 'reviewed timeline restores enabled preference');
        await page.mouse.move(800, 500);
        await page.mouse.wheel(0, 100);
        await page.waitForTimeout(100);
        assert.equal(await page.locator('#auto-scroll').isChecked(), false);
        assert.equal(await page.evaluate(() => Cookies.get('hymn-auto-scroll')), '0');
        const resume = page.getByRole('button', { name: '继续自动滚动' });
        assert.equal(await resume.isVisible(), true, 'manual scrolling reveals resume control');
        await page.setViewportSize({ width: 390, height: 844 });
        const resumeBox = await resume.boundingBox();
        assert.ok(resumeBox.x > 300 && resumeBox.y > 750 && resumeBox.x + resumeBox.width <= 390 && resumeBox.y + resumeBox.height <= 844);
        const manualPosition = await page.evaluate(() => scrollY);
        await resume.focus();
        await page.keyboard.press('Enter');
        assert.equal(await page.locator('#auto-scroll').isChecked(), true);
        assert.equal(await resume.isVisible(), false, 'resume hides once following is restored');
        assert.equal(await page.evaluate(() => Cookies.get('hymn-auto-scroll')), '1');
        assert.equal(await page.evaluate(() => document.querySelector('audio').paused), true, 'resume affects scrolling, not audio playback');
        assert.ok(Math.abs(await page.evaluate(() => scrollY) - manualPosition) < 100, 'resume does not immediately jump');
        await page.waitForTimeout(700);
        await page.locator('#auto-scroll').uncheck();
        assert.equal(await resume.isVisible(), false, 'explicit menu disable does not offer an interruption resume');
        await page.locator('#auto-scroll').check();
        assert.equal(await page.evaluate(() => Cookies.get('hymn-auto-scroll')), '1');
        await page.setViewportSize({ width: 1000, height: 700 });
        await page.goto(base + '/fixture');
        await page.evaluate(() => {
            Cookies.set('hymn-auto-scroll', '0', { path: '/' });
            return HymnSync.mount({ songId: '01-001', manifest: '/timeline.json', sourceFile: '01-001测试.txt',
                audioFile: '01-001测试.mp3', sourceHash: 'f'.repeat(64), audioHash: 'b'.repeat(64) });
        });
        assert.equal(await page.locator('#auto-scroll').isChecked(), false, 'saved manual preference overrides the default');
        assert.equal(await page.locator('#auto-scroll').isDisabled(), true);
        assert.equal(await page.locator('#resume-auto-scroll').isVisible(), false);
        assert.match(await page.locator('#auto-scroll-status').textContent(), /素材已更新/);
        console.log('PASS: reviewed data gating, cookie persistence, manual switch state and stale hashes');

        // Real browser decoding, file hashing, editor selection and draft export.
        await page.goto(base + '/tools/sync-editor.html');
        const song = '01-040父啊儿女称颂你名';
        await page.locator('#source-file').setInputFiles(path.join(root, 'data', song + '.txt'));
        await page.locator('#audio-files').setInputFiles([path.join(root, 'data', song + '.mp3'), path.join(root, 'data', song + '-领唱.mp3')]);
        await page.locator('#new-draft').click();
        await page.waitForFunction(() => document.querySelector('#track').options.length === 2);
        await page.locator('#region-id').fill('verse1');
        await page.locator('#region-start').fill('2');
        await page.locator('#region-end').fill('4');
        await page.locator('#save-region').click();
        await page.locator('#cue-at').fill('10');
        await page.locator('#cue-start').fill('0');
        await page.locator('#save-cue').click();
        assert.equal(await page.locator('#cue-list option').count(), 1);
        const enabledDownload = page.waitForEvent('download');
        await page.locator('#export-reviewed').click();
        const enabled = JSON.parse(fs.readFileSync(await (await enabledDownload).path(), 'utf8'));
        assert.equal(enabled.status, 'reviewed');
        assert.ok(enabled.tracks.every(track => track.reviewed), 'export enables both tracks without manual approval');
        const downloadPromise = page.waitForEvent('download');
        await page.locator('#export-draft').click();
        const download = await downloadPromise;
        const draft = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
        assert.equal(draft.status, 'draft');
        assert.equal(draft.tracks.length, 2);
        assert.ok(draft.tracks.every(track => track.duration > 0 && track.sha256.length === 64 && !track.reviewed));
        assert.equal(draft.cues[0].moveStart, 0);
        await page.locator('#review-track').check();
        await page.locator('#cue-at').fill('11');
        await page.locator('#update-cue').click();
        assert.equal(await page.locator('#review-track').isChecked(), false, 'edits invalidate review');
        await page.locator('#independent').check();
        await page.locator('#cue-at').fill('12');
        await page.locator('#update-cue').click();
        const overrideDownload = page.waitForEvent('download');
        await page.locator('#export-draft').click();
        const override = JSON.parse(fs.readFileSync(await (await overrideDownload).path(), 'utf8'));
        assert.equal(override.cues[0].at, 11);
        assert.equal(override.tracks[0].cues[0].at, 12);
        assert.deepEqual(errors, []);
        console.log('PASS: real MP3 metadata, two-version editor, draft export and review invalidation');

        if (process.env.PHP_BASE_URL) {
            for (const query of ['', '?n=01-040', '?n=05-001', '?n=invalid']) {
                await page.goto(process.env.PHP_BASE_URL + '/index.php' + query);
                await page.waitForTimeout(150);
                assert.doesNotMatch(await page.locator('body').innerText(), /Warning:|Fatal error:|Deprecated:/);
                if (query === '?n=01-040' || query === '?n=05-001') {
                    await page.locator('.menu-button').click();
                    await page.waitForFunction(() => document.querySelector('#auto-scroll').disabled);
                    assert.match(await page.locator('#auto-scroll-status').textContent(), /暂无/);
                    const kind = await page.evaluate(() => window.hymnSyncConfig.sourceFile);
                    assert.ok(kind.endsWith(query === '?n=01-040' ? '.txt' : '.jpg'));
                }
            }
            await page.setViewportSize({ width: 390, height: 844 });
            await page.goto(process.env.PHP_BASE_URL + '/index.php?n=05-001');
            await page.locator('.text-inner img').waitFor();
            await page.evaluate(() => document.querySelector('.text-inner img').decode());
            assert.ok(await page.evaluate(() => document.querySelector('.text-inner img').getBoundingClientRect().width) <= 390);
            await page.evaluate(() => scrollTo(0, 1000));
            await page.waitForTimeout(100);
            assert.equal(await page.locator('.menu-button').isVisible(), true);
            assert.deepEqual(errors, []);
            console.log('PASS: actual PHP catalog, text/image player, invalid ID and mobile menu visibility');
        }
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
