'use strict';
// Requires a running PHP server. Checks real score geometry after the 1.2 s transition.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.PHP_BASE_URL || 'http://127.0.0.1:8080';

(async () => {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
    const errors = [];
    let checks = 0;
    try {
        const page = await browser.newPage();
        page.on('pageerror', error => errors.push(error.message));
        await page.route('https://www.googletagmanager.com/**', route => route.abort());
        for (const song of ['03-037', '04-749', '04-695']) {
            const file = fs.readdirSync(path.join(root, 'data/sync')).find(name => name.startsWith(song));
            const data = JSON.parse(fs.readFileSync(path.join(root, 'data/sync', file), 'utf8'));
            for (const lead of [true, false]) {
                await page.context().addCookies([
                    { name: 'checkbox-sets', value: lead ? 'set4' : '', url: base },
                    { name: 'hymn-auto-scroll', value: '1', url: base }
                ]);
                await page.goto(base + '/index.php?n=' + song);
                await page.waitForFunction(() => !document.querySelector('#auto-scroll').disabled &&
                    Number.isFinite(document.querySelector('audio').duration));
                await page.locator('audio').evaluate(audio => audio.pause());
                const audioFile = await page.evaluate(() => hymnSyncConfig.audioFile);
                assert.equal(audioFile.includes('-领唱'), lead);
                const track = data.tracks.find(item => item.file === audioFile);
                const cues = track.cues || data.cues;
                const indices = [...new Set([0, 1, cues.findIndex(cue => cue.transition === 'return'), cues.length - 1])];
                for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
                    await page.setViewportSize(viewport);
                    for (const index of indices.filter(index => index >= 0)) {
                        const cue = cues[index];
                        const time = song === '03-037' && index === 1 ? 29 : cue.at + track.offset + 1.5;
                        await page.locator('audio').evaluate((audio, at) => { audio.currentTime = at; }, time);
                        await page.waitForFunction(() => !document.querySelector('audio').seeking);
                        await page.waitForTimeout(1400);
                        assert.equal(await page.locator('#auto-scroll').isChecked(), true);
                        const bounds = await page.evaluate(({ region, next, fraction }) => {
                            const rect = document.querySelector('.text-inner img').getBoundingClientRect();
                            const top = rect.top + region.top * rect.height;
                            const bottom = rect.top + region.bottom * rect.height;
                            const preview = next && next.top >= region.bottom
                                ? rect.top + (next.top + (next.bottom - next.top) * fraction) * rect.height : null;
                            return { top, bottom, preview, height: innerHeight, y: scrollY };
                        }, { region: data.regions[cue.region], next: data.regions[cues[index + 1]?.region],
                            fraction: data.imageLayout.previewFraction });
                        const label = `${song} ${audioFile} ${viewport.width} ${time}: ${JSON.stringify(bounds)}`;
                        const margin = Math.min(60, viewport.height * 0.1);
                        assert.ok(bounds.top >= -1, 'current system starts on screen: ' + label);
                        if (bounds.bottom - bounds.top <= viewport.height - margin * 2) {
                            assert.ok(bounds.bottom <= viewport.height + 1, 'current system stays readable: ' + label);
                        }
                        if (bounds.preview !== null && bounds.preview - bounds.top <= viewport.height - margin * 2) {
                            assert.ok(bounds.preview <= viewport.height - margin + 1, 'next lyrics are exposed when space permits: ' + label);
                        }
                        if (song === '03-037' && index === 1 && viewport.width === 1280) {
                            assert.ok(Math.abs((bounds.top + bounds.bottom) / 2 - viewport.height / 2) < 10, label);
                            // Original scan: bottom of “主替我舍身” is at about 72.5% of its height.
                            assert.ok(await page.evaluate(() => {
                                const rect = document.querySelector('.text-inner img').getBoundingClientRect();
                                return rect.top + rect.height * 0.725 < innerHeight;
                            }), '03-037 at 29 s previews 主替我舍身');
                            if (lead && process.env.SYNC_SCREENSHOT_DIR) {
                                fs.mkdirSync(process.env.SYNC_SCREENSHOT_DIR, { recursive: true });
                                await page.screenshot({ path: path.join(process.env.SYNC_SCREENSHOT_DIR, '03-037-29s.png') });
                            }
                        }
                        checks++;
                    }
                }
                console.log('PASS image preview, verse return and last screen:', audioFile);
            }
        }
        assert.deepEqual(errors, []);
        console.log(`PASS ${checks} settled image checks; no page errors`);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
