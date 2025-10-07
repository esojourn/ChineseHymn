// 播放器模块
import Cookies from 'js-cookie';
import { loadLyrics, getHymnTitle } from './dataLoader.js';
import { initSidebarCheckboxes, initMenuButton } from './sidebar.js';

/**
 * 渲染播放器页面
 */
export async function renderPlayer(item) {
    const settings = Cookies.get('checkbox-sets');
    const visited = Cookies.get('visited');

    let autoplay = '';
    let loop = '';
    let preferLead = true;

    if (settings || visited) {
        if (settings) {
            const settingsArr = settings.split(',');
            autoplay = settingsArr.includes('set1') ? 'autoplay' : '';
            loop = settingsArr.includes('set2') ? 'loop' : '';
            preferLead = settingsArr.includes('set4');
        }
    }

    // 确定使用哪个音频文件
    let mp3Path = '';
    let playMark = '';

    if (item.matched) {
        if (preferLead && item['mp3-lead']) {
            mp3Path = item['mp3-lead'];
            playMark = '领';
        } else if (preferLead && item.mp3) {
            mp3Path = item.mp3;
        } else if (!preferLead && item.mp3) {
            mp3Path = item.mp3;
        } else if (!preferLead && item['mp3-lead']) {
            mp3Path = item['mp3-lead'];
            playMark = '领';
        }
    }

    const hymnTitle = await getHymnTitle(item);
    document.title = hymnTitle ? `${hymnTitle} - 敬拜赞美` : '敬拜赞美';

    let html = '<div class="main"><div class="audio clearfix">';
    html += `<audio controls ${autoplay} ${loop}>`;
    html += `<source src="${mp3Path}" type="audio/mpeg">`;
    html += '</audio></div>';

    const pageURL = window.location.href.split('&')[0];

    if (item.format === 'txt') {
        const lyrics = await loadLyrics(item.path);
        const displayText = playMark ? `(${playMark}) - ${lyrics}` : lyrics;

        html += '<div class="text"><div class="text-inner">';
        html += `<pre>${displayText}</pre>`;
        html += '</div><div class="footer">';
        html += '<div class="return"><a href="/">回目录</a></div>';
        html += '<form class="pure-form">';
        html += `<input type="text" value="${hymnTitle} - ${pageURL}" id="copyText" class="smallfont">`;
        html += `<input type="text" value="${pageURL}" id="copyLink" class="hidden2">`;
        html += '<br>';
        html += '<a onclick="copyTextFunc(\'link\')" class="smallfont pure-button">拷贝链接</a>';
        html += '<a onclick="copyTextFunc()" class="smallfont pure-button">歌名+链接</a>';
        html += '</form></div></div>';

    } else if (item.format === 'jpg') {
        const displayMark = playMark ? '领唱版<br />' : '';
        html += '<div class="text"><div class="text-inner">';
        html += `${displayMark}<img src="${item.path}" />`;
        html += '<div class="footer">';
        html += '<div class="return"><a href="/">回目录</a></div>';
        html += '<form class="pure-form">';
        html += `<input type="text" value="${item.name} - ${pageURL}" id="copyText" class="smallfont">`;
        html += `<input type="text" value="${pageURL}" id="copyLink" class="hidden2">`;
        html += '<br>';
        html += '<a onclick="copyTextFunc(\'link\')" class="smallfont pure-button">拷贝链接</a>';
        html += '<a onclick="copyTextFunc()" class="smallfont pure-button">歌名+链接</a>';
        html += '</form></div></div></div>';
    }

    document.getElementById('main').innerHTML = html;

    // 初始化侧边栏checkbox状态管理和菜单按钮
    // 使用setTimeout确保DOM完全加载后再绑定事件
    setTimeout(() => {
        initSidebarCheckboxes();
        initMenuButton();
    }, 100);
}
