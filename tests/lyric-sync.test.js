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
    assert.equal(Core.regionTarget(1000, 1700, 600, 100, 2000), 940);
    assert.equal(Core.regionTarget(3000, 3100, 600, 100, 2200), 2200);
    assert.equal(Core.regionTarget(0, 100, 600, 100, 2200), 0);
});

test('bilingual preview keeps both neighbouring pairs visible at both stops', () => {
    const regions = [{ top: 100, bottom: 180 }, { top: 200, bottom: 280 }, { top: 300, bottom: 380 }];
    const edges = [{ index: 1, top: 140, bottom: 240 }, { index: 2, top: 240, bottom: 340 }];
    const targets = Core.previewTargets([0, 180, 360], regions, edges, 320, 1000);
    assert.deepEqual(targets, [0, 140, 240]);
    for (const edge of edges) for (const index of [edge.index - 1, edge.index]) {
        assert.ok(edge.top - targets[index] >= 0);
        assert.ok(edge.bottom - targets[index] <= 320);
    }
});

test('bilingual preview rejects missing, repeated or invalid line boundaries', () => {
    const data = fixture();
    data.source.type = 'text';
    data.regions = { top: { kind: 'text', startLine: 0, endLine: 3 }, bottom: { kind: 'text', startLine: 4, endLine: 7 } };
    data.provenance = { previousLinePreview: { version: 4, indices: [1] } };
    assert.deepEqual(Core.validate(data), []);
    for (const indices of [[0], [1, 1], [3], [1.5]]) {
        data.provenance.previousLinePreview.indices = indices;
        assert.ok(Core.validate(data).length);
    }
    data.provenance.previousLinePreview.indices = [1];
    data.regions.top.endLine = 0;
    assert.ok(Core.validate(data).length);
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

test('text centers four-line passages and stays still until the next cue', () => {
    assert.equal(Core.textTarget(600, 800, 700, 3000), 350);
    const cues = [{ at: 10, region: 'verse1' }, { at: 34, region: 'verse2' }];
    for (const time of [10, 15, 22, 33.99]) {
        assert.deepEqual(Core.locate(cues, time), { from: 0, to: 0, progress: 1, moving: false });
    }
    assert.equal(Core.locate(cues, 34).from, 1);
    assert.equal(Core.textTarget(0, 100, 700, 3000), 0);
    assert.equal(Core.textTarget(3000, 3100, 700, 2200), 2200);
    assert.equal(Core.textTarget(600, 1600, 700, 3000), 460);
});

test('image systems move only enough to enter the viewport and then hold', () => {
    const target = Core.regionTarget(500, 750, 600, 100, 2000);
    assert.equal(target, 210);
    assert.equal(Core.regionTarget(500, 750, 600, target, 2000), target);
    assert.equal(Core.regionTarget(200, 400, 600, 500, 2000), 140);
});

test('image preview centers the current system and reveals the following lyrics when space permits', () => {
    // Previously this system sat at the bottom (target 210), hiding the following lyrics.
    assert.equal(Core.imageTarget(500, 750, 900, 600, 2000), 360);
    assert.equal(Core.imageTarget(500, 750, undefined, 600, 2000), 325);
    // Small screens protect the current system even when the preview cannot fit.
    assert.equal(Core.imageTarget(500, 750, 1000, 390, 2000), 461);
    assert.equal(Core.imageTarget(500, 1200, 1400, 390, 2000), 461);
    assert.equal(Core.imageTarget(0, 100, 200, 600, 2000), 0);
    assert.equal(Core.imageTarget(3000, 3100, undefined, 600, 2200), 2200);
});

test('image preview settings are optional and rejected for text or invalid fractions', () => {
    const data = fixture();
    data.imageLayout = { mode: 'center-preview', previewFraction: 0.65 };
    assert.deepEqual(Core.validate(data), []);
    for (const value of [-1, 1.1, NaN, '0.65']) {
        data.imageLayout.previewFraction = value;
        assert.ok(Core.validate(data).length);
    }
    data.imageLayout = null;
    assert.ok(Core.validate(data).length);
    data.imageLayout = { mode: 'center-preview', previewFraction: 0.65 };
    data.source.type = 'text';
    assert.ok(Core.validate(data).length);
});
