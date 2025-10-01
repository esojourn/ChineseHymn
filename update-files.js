#!/usr/bin/env node
// 更新文件列表脚本 - 在添加新的音频/歌词文件后运行此脚本

const fs = require('fs');
const path = require('path');

const dataDir = path.join(__dirname, 'data');
const outputFile = path.join(dataDir, 'files.json');

try {
    // 读取data目录中的所有文件
    const files = fs.readdirSync(dataDir)
        .filter(file => {
            // 只包含 .txt, .mp3, .jpg 文件
            return /\.(txt|mp3|jpg)$/i.test(file);
        })
        .sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true }));

    // 写入JSON文件
    fs.writeFileSync(outputFile, JSON.stringify(files, null, 2), 'utf8');

    console.log(`✓ 成功更新文件列表: ${files.length} 个文件`);
    console.log(`✓ 输出文件: ${outputFile}`);
} catch (error) {
    console.error('错误:', error.message);
    process.exit(1);
}
