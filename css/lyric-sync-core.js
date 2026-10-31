/* Shared by the player, local editor and Node validation tools. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.HymnSyncCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    function normalizeText(text) {
        return text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').trim().replace(/\n{3,}/g, '\n\n');
    }

    function validate(data, options = {}) {
        const errors = [];
        const finite = Number.isFinite;
        const filename = value => typeof value === 'string' && value.length > 0 && !/[\\/\u0000-\u001f]/.test(value) && value !== '..';
        const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
        if (!data || typeof data !== 'object') return ['同步文件不是对象'];
        if (data.version !== 1) errors.push('不支持的同步格式');
        if (!['draft', 'reviewed'].includes(data.status)) errors.push('审核状态无效');
        if (options.reviewed && data.status !== 'reviewed') errors.push('尚未审核');
        if (!/^\d{2}-\d{1,3}$/.test(data.songId || '')) errors.push('歌曲编号无效');
        if (!data.source || !filename(data.source.file) || !hash(data.source.sha256) ||
            !['text', 'image'].includes(data.source.type)) errors.push('歌词素材信息无效');
        if (data.imageLayout !== undefined && (data.source?.type !== 'image' ||
            data.imageLayout?.mode !== 'center-preview' || !finite(data.imageLayout.previewFraction) ||
            data.imageLayout.previewFraction < 0 || data.imageLayout.previewFraction > 1)) errors.push('图片预读设置无效');
        const tracks = Array.isArray(data.tracks) ? data.tracks : [];
        if (!tracks.length) errors.push('没有音频版本');
        const files = new Set();
        for (const track of tracks) {
            if (!track || !filename(track.file) || !hash(track.sha256) || !finite(track.duration) || track.duration <= 0 ||
                !finite(track.offset) || typeof track.reviewed !== 'boolean' || files.has(track.file)) {
                errors.push('音频版本信息无效或重复');
            }
            if (track) files.add(track.file);
        }
        if (data.status === 'reviewed' && !tracks.some(track => track && track.reviewed)) errors.push('没有审核通过的音频版本');
        const regions = data.regions;
        if (!regions || typeof regions !== 'object' || Array.isArray(regions)) errors.push('缺少阅读区域');
        for (const [id, region] of Object.entries(regions || {})) {
            if (!/^[a-zA-Z0-9_-]+$/.test(id) || !region || region.kind !== data.source?.type) {
                errors.push('阅读区域类型或编号无效');
                continue;
            }
            if (region.kind === 'text' && (!Number.isInteger(region.startLine) || !Number.isInteger(region.endLine) ||
                region.startLine < 0 || region.endLine < region.startLine ||
                (options.lineCount !== undefined && region.endLine >= options.lineCount))) errors.push('文本行范围无效：' + id);
            if (region.kind === 'image' && (!finite(region.top) || !finite(region.bottom) ||
                region.top < 0 || region.bottom > 1 || region.bottom <= region.top)) errors.push('图片范围无效：' + id);
        }
        const cues = Array.isArray(data.cues) ? data.cues : [];
        const preview = data.provenance?.previousLinePreview;
        if (preview?.version === 4 && (data.source?.type !== 'text' || !Array.isArray(preview.indices) ||
            !preview.indices.length || preview.indices.some((index, position) => {
                if (!Number.isInteger(index) || index < 1 || index >= cues.length ||
                    (position > 0 && index <= preview.indices[position - 1])) return true;
                const invalid = [cues[index - 1], cues[index]].some(cue => {
                    const region = regions?.[cue?.region];
                    return !region || region.kind !== 'text' || region.endLine - region.startLine < 1;
                });
                return invalid || regions[cues[index - 1].region].endLine >= regions[cues[index].region].startLine;
            }))) errors.push('双语预读设置无效');
        if (!cues.length && (options.reviewed || data.status === 'reviewed') && tracks.some(track => track && track.reviewed && !track.cues)) errors.push('没有时间点');
        let previous = -1;
        for (const cue of cues) {
            if (!cue || !finite(cue.at) || cue.at < 0 || cue.at <= previous || !Object.hasOwn(regions || {}, cue.region)) {
                errors.push('时间点必须递增且引用有效区域');
                continue;
            }
            if (cue.moveStart !== undefined && (!finite(cue.moveStart) || cue.moveStart < 0 ||
                cue.moveStart < previous || cue.moveStart > cue.at)) errors.push('过渡区间无效');
            if (cue.transition !== undefined && !['intro', 'interlude', 'reading', 'return'].includes(cue.transition)) errors.push('过渡类型无效');
            if (cue.label !== undefined && typeof cue.label !== 'string') errors.push('段落名称无效');
            previous = cue.at;
        }
        for (const track of tracks) {
            if (track && track.cues !== undefined) {
                if (!Array.isArray(track.cues)) errors.push('版本独立时间轴必须为数组');
                else {
                    const { cues: override, ...plainTrack } = track;
                    const issues = validate({ ...data, status: track.reviewed ? data.status : 'draft', tracks: [plainTrack], cues: override }, { ...options, reviewed: options.reviewed && track.reviewed });
                    errors.push(...issues.map(issue => track.file + ': ' + issue));
                }
            } else if (track && cues.some(cue => cue && (cue.at + track.offset < 0 || cue.at + track.offset > track.duration))) {
                errors.push('时间点超出音频时长：' + track.file);
            }
        }
        return errors;
    }

    function ease(value) {
        const x = Math.max(0, Math.min(1, value));
        return x * x * (3 - 2 * x);
    }

    // Cues are arrival times. moveStart explicitly authorizes movement during an interlude.
    function locate(cues, time) {
        if (!cues.length) return null;
        let current = 0;
        for (let i = 1; i < cues.length; i++) {
            const next = cues[i];
            if (time < next.at) {
                if (next.moveStart !== undefined && time >= next.moveStart && next.moveStart < next.at) {
                    return { from: current, to: i, progress: ease((time - next.moveStart) / (next.at - next.moveStart)), moving: true };
                }
                return { from: current, to: current, progress: 1, moving: false };
            }
            current = i;
        }
        return { from: current, to: current, progress: 1, moving: false };
    }

    // Keep a fully visible region still. Oversized regions align their top; authors add subregions.
    function regionTarget(top, bottom, viewport, current, maximum) {
        const margin = Math.min(60, viewport * 0.1);
        if (top >= current + margin && bottom <= current + viewport - margin) return current;
        // Move only far enough to reveal the next system, preserving visual context.
        const target = bottom - top > viewport - 2 * margin ? top - margin
            : Math.max(bottom - viewport + margin, Math.min(current, top - margin));
        return Math.max(0, Math.min(maximum, target));
    }

    function textTarget(top, bottom, viewport, maximum) {
        // Oversized passages begin near the top rather than hiding their opening lines.
        const target = bottom - top > viewport * 0.6 ? top - viewport * 0.2 : (top + bottom - viewport) / 2;
        return Math.max(0, Math.min(maximum, target));
    }

    // Keep both sides of a bilingual preview visible throughout a movement.
    function previewTargets(targets, regions, edges, viewport, maximum) {
        const bounds = targets.map((_, index) => ({
            min: regions[index].bottom - regions[index].top <= viewport * 0.8
                ? Math.max(0, regions[index].bottom - viewport) : 0,
            max: Math.min(maximum, regions[index].top)
        }));
        for (const edge of edges) {
            const min = Math.max(0, edge.bottom - viewport);
            const max = Math.min(maximum, edge.top);
            for (const index of [edge.index - 1, edge.index]) {
                bounds[index].min = Math.max(bounds[index].min, min);
                bounds[index].max = Math.min(bounds[index].max, max);
            }
        }
        return targets.map((target, index) => bounds[index].min <= bounds[index].max
            ? Math.max(bounds[index].min, Math.min(bounds[index].max, target)) : target);
    }

    function imageTarget(top, bottom, previewBottom, viewport, maximum) {
        const margin = Math.min(60, viewport * 0.1);
        // Center the current system. Reveal more of the following system if space permits,
        // but never push the current system above the reading margin to do so.
        let target = (top + bottom - viewport) / 2;
        if (previewBottom !== undefined) target = Math.max(target, previewBottom - viewport + margin);
        target = Math.min(target, top - margin);
        return Math.max(0, Math.min(maximum, target));
    }

    return { normalizeText, validate, ease, locate, regionTarget, textTarget, previewTargets, imageTarget };
});
