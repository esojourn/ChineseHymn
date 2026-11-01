'use strict';
// Real PHP pages and original MP3 playback: a hint follows arrival, flashes three times, and clears.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.PHP_BASE_URL || 'http://127.0.0.1:8080';
const data = JSON.parse(fs.readFileSync(path.join(root, 'data/sync/05-302恩友歌.jpg.json')));
const Core = require('../css/lyric-sync-core');

(async () => {
    const browser = await chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'],
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
    const errors = [];
    let returns = 0;
    try {
        const page = await browser.newPage();
        page.on('pageerror', error => errors.push(error.message));
        await page.route('https://www.googletagmanager.com/**', route => route.abort());
        for (const track of data.tracks) {
            await page.context().addCookies([
                { name: 'visited', value: 'true', url: base },
                { name: 'checkbox-sets', value: track.file.includes('-领唱') ? 'set4' : '', url: base },
                { name: 'hymn-auto-scroll', value: '1', url: base }
            ]);
            await page.goto(base + '/index.php?n=05-302', { waitUntil: 'commit' });
            await page.bringToFront();
            await page.waitForFunction(() => !document.querySelector('#auto-scroll').disabled &&
                Number.isFinite(document.querySelector('audio').duration));
            await page.locator('audio').evaluate(audio => audio.pause());
            assert.equal(await page.evaluate(() => hymnSyncConfig.audioFile), track.file);
            await page.screenshot();
            await page.evaluate(() => {
                window.returnAnimations = [];
                for (const type of ['animationstart', 'animationiteration', 'animationend']) {
                    document.addEventListener(type, event => {
                        if (event.animationName === 'sync-return-blink') returnAnimations.push(event.type);
                    });
                }
            });
            const cues = track.cues || data.cues;
            for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
                await page.setViewportSize(viewport);
                for (const cue of cues.filter(item => item.transition === 'return')) {
                    await page.locator('audio').evaluate((audio, time) => {
                        audio.pause();
                        audio.currentTime = time;
                    }, cue.at + track.offset - 0.7);
                    await page.waitForFunction(() => !document.querySelector('audio').seeking);
                    await page.waitForTimeout(1350);
                    assert.equal(await page.locator('.sync-return-dot:not([hidden])').count(), 0);
                    await page.evaluate(() => { returnAnimations.length = 0; });
                    await page.locator('audio').evaluate(audio => audio.play());
                    // The cue has changed, but the 1.2-second scroll has not arrived yet.
                    await page.waitForFunction(time => document.querySelector('audio').currentTime >= time,
                        cue.at + track.offset + 0.15);
                    assert.equal(await page.locator('.sync-return-dot:not([hidden])').count(), 0,
                        'hint must not flash while returning');
                    await page.waitForFunction(region => {
                        const dot = document.querySelector('.sync-return-dot:not([hidden])');
                        return dot?.dataset.region === region && Number(getComputedStyle(dot).opacity) > 0.9;
                    }, cue.region);
                    const anchor = Core.imageReturnAnchor(data, cue);
                    const geometry = await page.evaluate(anchor => {
                        const dot = document.querySelector('.sync-return-dot:not([hidden])');
                        const marker = dot.getBoundingClientRect();
                        const img = document.querySelector('.text-inner img').getBoundingClientRect();
                        return { x: marker.left + marker.width / 2, y: marker.top + marker.height / 2,
                            expectedX: img.left + img.width * anchor.x, expectedY: img.top + img.height * anchor.y,
                            iterations: getComputedStyle(dot).animationIterationCount };
                    }, anchor);
                    assert.ok(Math.abs(geometry.x - geometry.expectedX) < 1 && Math.abs(geometry.y - geometry.expectedY) < 1);
                    assert.ok(geometry.x >= 5 && geometry.x < viewport.width - 5 && geometry.y >= 5 && geometry.y < viewport.height - 5);
                    assert.equal(geometry.iterations, '3');
                    if (process.env.SYNC_SCREENSHOT_DIR) {
                        fs.mkdirSync(process.env.SYNC_SCREENSHOT_DIR, { recursive: true });
                        await page.screenshot({ path: path.join(process.env.SYNC_SCREENSHOT_DIR,
                            `05-302-${track.file.includes('-领唱') ? 'lead' : 'backing'}-${cue.region}-${viewport.width}.png`) });
                    }
                    await page.waitForFunction(() => returnAnimations.includes('animationend'));
                    assert.deepEqual(await page.evaluate(() => returnAnimations),
                        ['animationstart', 'animationiteration', 'animationiteration', 'animationend']);
                    assert.equal(await page.locator('.sync-return-dot:not([hidden])').count(), 0);
                    await page.waitForTimeout(150);
                    assert.equal(await page.locator('.sync-return-dot:not([hidden])').count(), 0,
                        'the same passage must not retrigger its hint');
                    returns++;
                }
            }
            // Seeking back to a previously visited return should show the hint again, even while paused.
            const revisit = cues.find(cue => cue.transition === 'return');
            await page.locator('audio').evaluate((audio, time) => { audio.pause(); audio.currentTime = time; }, revisit.at + 0.2);
            await page.waitForFunction(() => !!document.querySelector('.sync-return-dot:not([hidden])'));
            await page.mouse.wheel(0, 50);
            await page.waitForFunction(() => !document.querySelector('#auto-scroll').checked);
            assert.equal(await page.locator('.sync-return-dot:not([hidden])').count(), 0,
                'manual takeover must clear the hint');
            console.log('PASS three flashes after each return, responsive position and manual cancellation:', track.file);
        }
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({ song: '05-302', tracks: data.tracks.length, naturalReturns: returns, flashesPerReturn: 3, errors }));
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
