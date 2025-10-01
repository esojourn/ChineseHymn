// 主入口文件
import './style.css';
import { loadHymnData, getUrlParam, findItemById } from './dataLoader.js';
import { renderTableView } from './tableView.js';
import { renderPlayer } from './player.js';

/**
 * 主初始化函数
 */
async function init() {
    const n = getUrlParam('n');

    // 加载文件列表
    const hymnData = await loadHymnData();
    if (!hymnData || hymnData.length === 0) {
        document.getElementById('main').innerHTML = '<h1>无法加载数据</h1>';
        return;
    }

    if (!n) {
        // 显示目录
        renderTableView(hymnData);
    } else if (n === 'check') {
        // 检查重复（开发用）
        document.getElementById('main').innerHTML = '<pre>检查功能需要在开发工具中查看</pre>';
        console.log('Hymn Data:', hymnData);
    } else {
        // 显示播放器
        const item = findItemById(hymnData, n);
        if (item) {
            await renderPlayer(item);
        } else {
            document.getElementById('main').innerHTML = '<h1>无权访问</h1>';
        }
    }
}

// 页面加载完成后初始化
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
