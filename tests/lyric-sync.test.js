'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const Core = require('../css/lyric-sync-core');

function fixture() {
    return {
        version: 1, songId: '05-001', status: 'reviewed',
        source: { file: '05-001圣哉三一歌.jpg', sha256: 'a'.repeat(64), type: 'image' },
        tracks: [{ file: '05-001圣哉三一歌.mp3', sha256: 'b'.repeat(64), duration: 120, offset: 0, reviewed: true }],
        regions: { top: { kind: 'image', top: 0.1, bottom: 0.3 }, bottom: { kind: 'image', top: 0.7, bottom: 0.9 } },
        cues: [{ at: 10, region: 'top', moveStart: 0, transition: 'intro' },
            { at: 30, region: 'bottom' }, { at: 70, region: 'top', moveStart: 50, transition: 'interlude' }]
    };
}

test('a reviewed image can revisit an earlier region after an instrumental interlude', () => {
    const data = fixture();
    assert.deepEqual(Core.validate(data, { reviewed: true }), []);
    assert.deepEqual(Core.locate(data.cues, 45), { from: 1, to: 1, progress: 1, moving: false });
    assert.deepEqual(Core.locate(data.cues, 60), { from: 1, to: 2, progress: 0.5, moving: true });
    assert.deepEqual(Core.locate(data.cues, 70), { from: 2, to: 2, progress: 1, moving: false });
    assert.equal(Core.locate(data.cues, 50).progress, 0);
});

test('seek and loop lookup is absolute, with no accumulated clock or forward-only cursor', () => {
    const cues = fixture().cues;
    assert.equal(Core.locate(cues, 100).to, 2);
    assert.equal(Core.locate(cues, 0).to, 0);
    assert.equal(Core.locate(cues, 65).progress, Core.ease(0.75));
    assert.equal(Core.locate(cues, 35).to, 1);
});

test('a short interlude still arrives exactly at the next verse boundary', () => {
    const cues = fixture().cues;
    cues[2].moveStart = 69.8;
    assert.ok(Core.locate(cues, 69.9).progress > 0.49);
    assert.equal(Core.locate(cues, 70).progress, 1);
});

test('reject corrupt, overlapping or stale-shaped timelines before playback', () => {
    for (const mutate of [
        d => { d.cues[2].moveStart = 20; },
        d => { d.cues[2].at = 30; },
        d => { d.cues[2].region = 'missing'; },
        d => { d.regions.top.bottom = 2; },
        d => { d.tracks[0].offset = 60; },
        d => { d.source.file = '../escape.jpg'; },
        d => { d.source.sha256 = 'old'; },
        d => { d.tracks[0].reviewed = false; },
        d => { d.cues[0].at = NaN; },
        d => { d.cues = []; }
    ]) {
        const data = fixture(); mutate(data);
        assert.ok(Core.validate(data, { reviewed: true }).length);
    }
});

test('drafts are editable but cannot be enabled for listeners', () => {
    const data = fixture(); data.status = 'draft'; data.cues = [];
    assert.deepEqual(Core.validate(data), []);
    assert.ok(Core.validate(data, { reviewed: true }).length);
});

test('a different arrangement may have a separate timeline without changing its pair', () => {
    const data = fixture();
    data.tracks.push({ ...data.tracks[0], file: '05-001圣哉三一歌-领唱.mp3', duration: 200,
        cues: [{ at: 15, region: 'top' }, { at: 140, region: 'bottom', moveStart: 120 }] });
    assert.deepEqual(Core.validate(data, { reviewed: true }), []);
    data.tracks[1].cues[1].at = 210;
    assert.ok(Core.validate(data, { reviewed: true }).length);
});

test('text normalization is stable across BOM, CRLF and extra blank lines', () => {
    assert.equal(Core.normalizeText('\uFEFF 第一段\r\n\r\n\r\n副歌\r\n '), '第一段\n\n副歌');
    const data = fixture();
    data.source.type = 'text'; data.source.file = '05-001圣哉三一歌.txt';
    data.regions = { top: { kind: 'text', startLine: 0, endLine: 2 }, bottom: { kind: 'text', startLine: 3, endLine: 5 } };
    assert.deepEqual(Core.validate(data, { lineCount: 6 }), []);
    assert.ok(Core.validate(data, { lineCount: 5 }).length);
});

test('visible content stays still, oversized regions align at top and bottom is clamped', () => {
    assert.equal(Core.regionTarget(160, 400, 600, 100, 2000), 100);
    assert.equal(Core.regionTarget(1000, 1700, 600, 100, 2000), 880);
    assert.equal(Core.regionTarget(3000, 3100, 600, 100, 2200), 2200);
    assert.equal(Core.regionTarget(0, 100, 600, 100, 2200), 0);
});

test('all transition samples stay in bounds and progress monotonically', () => {
    let previous = 0;
    for (let i = 0; i <= 100; i++) {
        const value = Core.ease(i / 100);
        assert.ok(value >= previous && value >= 0 && value <= 1);
        previous = value;
    }
    assert.equal(Core.ease(-1), 0);
    assert.equal(Core.ease(2), 1);
});
