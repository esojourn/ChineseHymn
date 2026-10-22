'use strict';
// Actual PHP pages and MP3 seeks, plus every cue's geometry in three viewport sizes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.PHP_BASE_URL || 'http://127.0.0.1:8080';
const selected = (process.env.SYNC_SONGS || '').split(',').filter(Boolean);

(async () => {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
    const report = { songs: [], tracks: 0, geometryChecks: 0, seekChecks: 0, errors: [] };
    try {
        const page = await browser.newPage();
        page.on('pageerror', error => report.errors.push(error.message));
        await page.route('https://www.googletagmanager.com/**', route => route.abort());
        for (const file of fs.readdirSync(path.join(root, 'data/sync'))) {
            if (!file.endsWith('.json')) continue;
            const data = JSON.parse(fs.readFileSync(path.join(root, 'data/sync', file), 'utf8'));
            if (data.provenance?.batch !== 'bulk-2026-10' || (selected.length && !selected.includes(data.songId))) continue;
            report.songs.push(data.songId);
            for (const track of data.tracks.filter(track => track.reviewed)) {
                const cues = track.cues || data.cues;
                await page.context().addCookies([
                    { name: 'checkbox-sets', value: track.file.includes('-领唱') ? 'set4' : '', url: base },
                    { name: 'hymn-auto-scroll', value: '1', url: base }
                ]);
                await page.goto(base + '/index.php?n=' + data.songId);
                await page.waitForFunction(() => !document.querySelector('#auto-scroll').disabled &&
                    Number.isFinite(document.querySelector('audio').duration));
                assert.equal(await page.evaluate(() => hymnSyncConfig.audioFile), track.file);
                await page.locator('audio').evaluate(audio => audio.pause());
                await page.evaluate(({ data, track }) => {
                    // Disabled probe reads the exact controller geometry without driving scrolling.
                    window.geometryProbe = new HymnSync.Controller(document.querySelector('audio'),
                        document.querySelector('.text-inner'), data, track);
                }, { data, track });
                for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
                    await page.setViewportSize(viewport);
                    const geometry = await page.evaluate(() => {
                        geometryProbe.measure();
                        return geometryProbe.data.cues.map((cue, index) => {
                            const region = geometryProbe.data.regions[cue.region];
                            let top, bottom;
                            if (region.kind === 'image') {
                                const rect = document.querySelector('.text-inner img').getBoundingClientRect();
                                top = scrollY + rect.top + region.top * rect.height;
                                bottom = scrollY + rect.top + region.bottom * rect.height;
                            } else {
                                top = scrollY + document.querySelector('[data-line="' + region.startLine + '"]').getBoundingClientRect().top;
                                bottom = scrollY + document.querySelector('[data-line="' + region.endLine + '"]').getBoundingClientRect().bottom;
                            }
                            const target = geometryProbe.positions[index];
                            return { target, top: top - target, bottom: bottom - target, maximum: document.documentElement.scrollHeight - innerHeight };
                        });
                    });
                    for (const bounds of geometry) {
                        assert.ok(Number.isFinite(bounds.target) && bounds.target >= 0 && bounds.target <= bounds.maximum + 1);
                        assert.ok(bounds.top >= -1, 'opening lyrics must remain visible: ' + data.songId + JSON.stringify(bounds));
                        if (bounds.bottom - bounds.top <= viewport.height * .8) {
                            assert.ok(bounds.bottom <= viewport.height + 1, 'current passage fits on screen: ' + data.songId + JSON.stringify(bounds));
                        }
                        report.geometryChecks++;
                    }
                    const indices = [...new Set([0, 1, cues.findIndex(cue => cue.transition === 'return'), cues.length - 1])].filter(index => index >= 0);
                    for (const index of indices) {
                        await page.locator('audio').evaluate((audio, time) => { audio.currentTime = time; }, cues[index].at + track.offset + .5);
                        await page.waitForFunction(() => !document.querySelector('audio').seeking);
                        await page.waitForTimeout(data.source.type === 'image' ? 1400 : 800);
                        assert.equal(await page.locator('#auto-scroll').isChecked(), true);
                        const actual = await page.evaluate(() => scrollY);
                        assert.ok(Math.abs(actual - geometry[index].target) <= 3,
                            `${track.file} cue ${index}, ${viewport.width}: actual ${actual}, expected ${geometry[index].target}`);
                        report.seekChecks++;
                    }
                    if (process.env.SYNC_SCREENSHOT_DIR) {
                        fs.mkdirSync(process.env.SYNC_SCREENSHOT_DIR, { recursive: true });
                        await page.screenshot({ path: path.join(process.env.SYNC_SCREENSHOT_DIR,
                            `${data.songId}-${track.file.includes('-领唱') ? 'lead' : 'base'}-${viewport.width}.png`) });
                    }
                }
                report.tracks++;
                console.log('PASS batch layout and actual seeks:', track.file);
            }
        }
        assert.ok(report.songs.length, 'at least one selected bulk sidecar must be exercised');
        assert.deepEqual(report.errors, []);
        if (process.env.SYNC_AUDIT_FILE) fs.writeFileSync(process.env.SYNC_AUDIT_FILE, JSON.stringify(report, null, 2) + '\n');
        console.log(JSON.stringify(report));
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
