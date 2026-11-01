'use strict';
// Audit every existing JPG return using current JSON and, for legacy blocks, local pixel rows.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.PHP_BASE_URL || 'http://127.0.0.1:8080';
const scores = fs.readdirSync(path.join(root, 'data/sync')).filter(file => file.endsWith('.json'))
    .map(file => JSON.parse(fs.readFileSync(path.join(root, 'data/sync', file))))
    .filter(data => data.source.type === 'image');

(async () => {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
    try {
        const page = await browser.newPage();
        await page.route('https://www.googletagmanager.com/**', route => route.abort());
        await page.goto(base + '/index.php');
        await page.waitForFunction(() => window.HymnSync && window.HymnSyncCore);
        const report = await page.evaluate(async scores => {
            const result = { images: scores.length, tracks: 0, returns: 0, directAnchors: 0, pixelAnchors: 0,
                pixelScanMilliseconds: 0, scannedImages: [], legacyPositions: [], missing: [] };
            for (const source of scores) {
                let image;
                const bands = new Map();
                for (const track of source.tracks.filter(track => track.reviewed)) {
                    result.tracks++;
                    const data = { ...source, cues: track.cues || source.cues };
                    for (const cue of data.cues.filter(cue => cue.transition === 'return')) {
                        result.returns++;
                        let anchor = HymnSyncCore.imageReturnAnchor(data, cue);
                        if (anchor) result.directAnchors++;
                        else {
                            if (!image) {
                                image = new Image();
                                image.src = '/data/' + encodeURIComponent(source.source.file);
                                await image.decode();
                                result.scannedImages.push(data.songId);
                            }
                            if (!bands.has(cue.region)) {
                                const start = performance.now();
                                bands.set(cue.region, HymnSync.imageReturnBands(image, data.regions[cue.region]));
                                result.pixelScanMilliseconds += performance.now() - start;
                            }
                            anchor = HymnSyncCore.imageReturnAnchor(data, cue, bands.get(cue.region));
                            if (anchor) {
                                result.pixelAnchors++;
                                result.legacyPositions.push({ song: data.songId, label: cue.label, y: anchor.y });
                            }
                        }
                        const region = data.regions[cue.region];
                        if (!anchor || !Number.isFinite(anchor.x) || !Number.isFinite(anchor.y) ||
                            anchor.x < 0 || anchor.x > 1 || anchor.y < region.top || anchor.y > region.bottom) {
                            result.missing.push({ song: data.songId, track: track.file, cue });
                        }
                    }
                }
            }
            return result;
        }, scores);
        assert.deepEqual(report.missing, []);
        assert.equal(report.directAnchors + report.pixelAnchors, report.returns);
        // Independent visual checks of the original legacy images, with tolerance for raster scaling.
        const expected = {
            '03-037': { 2: 0.2179, 3: 0.2395, 4: 0.2612 },
            '04-695': { 2: 0.1573, 3: 0.1732, 4: 0.1902, 5: 0.2061 },
            '04-749': { 2: 0.2135, 3: 0.2368 },
            '05-066': { 2: 0.2946, 3: 0.3323 }
        };
        for (const position of report.legacyPositions) {
            const verse = /^第(\d+)段/.exec(position.label || '')?.[1];
            const wanted = position.song === '06-002' ? 0.2606 : expected[position.song]?.[verse];
            assert.ok(wanted !== undefined && Math.abs(position.y - wanted) < 0.004,
                'legacy hint must point to the correct printed stanza: ' + JSON.stringify(position));
        }
        report.actualLegacySeeks = 0;
        for (const songId of report.scannedImages) {
            const source = scores.find(data => data.songId === songId);
            const track = source.tracks.find(track => track.file.includes('-领唱')) || source.tracks[0];
            const cue = (track.cues || source.cues).find(cue => cue.transition === 'return');
            const expected = report.legacyPositions.find(item => item.song === songId && item.label === cue.label);
            await page.context().addCookies([
                { name: 'visited', value: 'true', url: base },
                { name: 'checkbox-sets', value: track.file.includes('-领唱') ? 'set4' : '', url: base },
                { name: 'hymn-auto-scroll', value: '1', url: base }
            ]);
            await page.setViewportSize({ width: 1280, height: 800 });
            await page.goto(base + '/index.php?n=' + songId, { waitUntil: 'commit' });
            await page.bringToFront();
            await page.waitForFunction(() => !document.querySelector('#auto-scroll').disabled &&
                Number.isFinite(document.querySelector('audio').duration));
            await page.screenshot();
            await page.locator('audio').evaluate((audio, time) => {
                audio.pause();
                audio.currentTime = time;
            }, cue.at + track.offset + 0.2);
            await page.waitForFunction(() => {
                const dot = document.querySelector('.sync-return-dot:not([hidden])');
                return dot && Number(getComputedStyle(dot).opacity) > 0.9;
            });
            const y = await page.evaluate(() => {
                const dot = document.querySelector('.sync-return-dot:not([hidden])').getBoundingClientRect();
                const img = document.querySelector('.text-inner img').getBoundingClientRect();
                return (dot.top + dot.height / 2 - img.top) / img.height;
            });
            assert.ok(Math.abs(y - expected.y) < 0.001, 'real page must use the correct stanza: ' + songId);
            if (process.env.SYNC_SCREENSHOT_DIR) {
                fs.mkdirSync(process.env.SYNC_SCREENSHOT_DIR, { recursive: true });
                await page.screenshot({ path: path.join(process.env.SYNC_SCREENSHOT_DIR, songId + '-return.png') });
            }
            report.actualLegacySeeks++;
        }
        if (process.env.SYNC_AUDIT_FILE) fs.writeFileSync(process.env.SYNC_AUDIT_FILE, JSON.stringify(report, null, 2) + '\n');
        console.log(JSON.stringify(report));
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
