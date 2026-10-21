#!/usr/bin/env node
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const Core = require('../css/lyric-sync-core');
const dataDir = path.resolve(__dirname, '../data');
const syncDir = path.join(dataDir, 'sync');
const inputs = process.argv.slice(2);
const files = inputs.length ? inputs : fs.readdirSync(syncDir).filter(name => name.endsWith('.json')).map(name => path.join(syncDir, name));
let failed = 0;
for (const file of files) {
    try {
        const data = JSON.parse(fs.readFileSync(file, 'utf8'));
        const errors = Core.validate(data);
        if (errors.length) throw new Error(errors.join('; '));
        for (const asset of [data.source, ...data.tracks]) {
            const bytes = fs.readFileSync(path.join(dataDir, asset.file));
            const hash = crypto.createHash('sha256').update(bytes).digest('hex');
            if (hash !== asset.sha256) throw new Error('素材已变更：' + asset.file);
            if (asset === data.source && asset.type === 'text') {
                let text;
                try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
                catch (_) { text = new TextDecoder('gb18030').decode(bytes); }
                const lines = Core.normalizeText(text).split('\n').length;
                const issues = Core.validate(data, { lineCount: lines });
                if (issues.length) throw new Error(issues.join('; '));
            }
        }
        if (path.basename(file) !== data.source.file + '.json') throw new Error('文件名必须为 ' + data.source.file + '.json');
        console.log(`${data.status === 'reviewed' ? '已审核' : '草稿（不会启用）'} ${file}`);
    } catch (error) {
        failed++;
        console.error(`${file}: ${error.message}`);
    }
}
if (!files.length) console.log('尚无歌曲同步数据。使用 tools/sync-editor.html 制作并审核后导出。');
process.exitCode = failed ? 1 : 0;
