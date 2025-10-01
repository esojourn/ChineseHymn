// 赞美诗歌管理应用 - 纯JS版本
(function() {
    'use strict';

    // 全局数据存储
    let hymnData = [];
    let fileListCache = null;

    // 从URL获取参数
    function getUrlParam(name) {
        const urlParams = new URLSearchParams(window.location.search);
        return urlParams.get(name);
    }

    // 获取文件列表 - 需要生成静态文件列表
    async function loadFileList() {
        if (fileListCache) return fileListCache;

        try {
            const response = await fetch('data/files.json');
            fileListCache = await response.json();
            return fileListCache;
        } catch (error) {
            console.error('无法加载文件列表:', error);
            return [];
        }
    }

    // 解析文件名，提取信息
    function parseFileName(filename) {
        const match = filename.match(/^(\d{2})-(\d{1,3})(.+)$/);
        if (!match) return null;

        const folder = match[1];
        const num = match[2];
        const rest = match[3];

        // 提取扩展名和名称
        const extMatch = rest.match(/^(.+)\.(txt|mp3|jpg)$/i);
        if (!extMatch) return null;

        let name = extMatch[1];
        const format = extMatch[2].toLowerCase();
        const isLead = name.endsWith('-领唱');

        if (isLead) {
            name = name.replace(/-领唱$/, '');
        }

        return {
            id: `${folder}-${num}`,
            folder: folder,
            num: num,
            name: name,
            path: `data/${filename}`,
            format: format,
            isLead: isLead,
            fullFilename: filename
        };
    }

    // 组织数据：将txt/jpg文件和对应的mp3文件关联
    function organizeData(files) {
        const dataMap = new Map();

        // 第一遍：处理txt和jpg文件
        files.forEach(filename => {
            const parsed = parseFileName(filename);
            if (!parsed) return;

            if (parsed.format === 'txt' || parsed.format === 'jpg') {
                const key = `${parsed.id}-${parsed.name}`;
                if (!dataMap.has(key)) {
                    dataMap.set(key, {
                        id: parsed.id,
                        folder: parsed.folder,
                        num: parsed.num,
                        name: parsed.name,
                        format: parsed.format,
                        path: parsed.path,
                        matched: false,
                        lead: false
                    });
                }
            }
        });

        // 第二遍：处理mp3文件
        files.forEach(filename => {
            const parsed = parseFileName(filename);
            if (!parsed || parsed.format !== 'mp3') return;

            const key = `${parsed.id}-${parsed.name}`;
            const item = dataMap.get(key);

            if (item) {
                if (parsed.isLead) {
                    item['mp3-lead'] = parsed.path;
                    item.lead = true;
                } else {
                    item.mp3 = parsed.path;
                }
                item.matched = true;
            }
        });

        return Array.from(dataMap.values()).sort((a, b) => {
            return a.id.localeCompare(b.id, 'zh-CN', { numeric: true });
        });
    }

    // 生成目录表格HTML
    function generateTableHtml(data) {
        let html = '<form class="pure-form"><table id="mytable"><thead><tr>';
        html += '<th class="hidden">ID</th><th>目录</th><th>编号</th><th>诗歌</th>';
        html += '</tr></thead><tbody>';

        data.forEach(item => {
            let musicStatus = '';
            let leadStatus = '';
            let titleStatus = '';

            if (!item.matched) {
                musicStatus = '无伴奏';
            }
            if (item.lead) {
                leadStatus = '领唱';
            }
            if (musicStatus || leadStatus) {
                titleStatus = ' - ' + leadStatus + ' ' + musicStatus;
            }

            html += '<tr>';
            html += `<td class='hidden'>${item.folder}${item.num}</td>`;
            html += `<td class='folder'>${item.folder}</td>`;
            html += `<td class='num'>${item.num}</td>`;
            html += `<td><a href="?n=${item.id}">${item.name}</a>${titleStatus}</td>`;
            html += '</tr>\n';
        });

        html += '</tbody></table></form>';
        return html;
    }

    // 初始化DataTable
    function initDataTable() {
        $('#mytable').DataTable({
            "paging": false,
            "aLengthMenu": [[15, 25, 50, 100, -1], [15, 25, 50, 100, "All"]],
            "responsive": true,
            "iDisplayLength": -1,
            "language": {
                "search": "",
                "infoFiltered": "",
                "lengthMenu": "每页显示 _MENU_ 首",
                "info": "显示第 _START_ 至 _END_ 首。共 _TOTAL_ 首",
                "paginate": {
                    "first": "首页",
                    "last": "末页",
                    "next": "向后",
                    "previous": "向前"
                }
            },
            "columnDefs": [{
                "targets": 0,
                "visible": false,
                "searchable": true
            }]
        });
    }

    // 加载歌词文本
    async function loadLyrics(path) {
        try {
            const response = await fetch(path);
            const text = await response.text();
            return text.trim().replace(/\n\n\n+/g, '\n\n');
        } catch (error) {
            console.error('加载歌词失败:', error);
            return '';
        }
    }

    // 获取歌曲标题
    async function getHymnTitle(item) {
        if (item.format !== 'txt') return '';

        const text = await loadLyrics(item.path);
        if (!text) return '';

        const firstLine = text.split('\n')[0];
        let title = firstLine
            .replace(/（/g, '(')
            .replace(/）/g, ')')
            .replace(/\d+/g, '')
            .replace(/\t|\n|\r/g, '')
            .replace(/\(([^\d\)]+)\d+([^\d\)]+)\)/g, '');

        return title.trim();
    }

    // 渲染播放器页面
    async function renderPlayer(item) {
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
            html += '<div class="return"><a href="index.html">回目录</a></div>';
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
            html += '<div class="return"><a href="index.html">回目录</a></div>';
            html += '<form class="pure-form">';
            html += `<input type="text" value="${item.name} - ${pageURL}" id="copyText" class="smallfont">`;
            html += `<input type="text" value="${pageURL}" id="copyLink" class="hidden2">`;
            html += '<br>';
            html += '<a onclick="copyTextFunc(\'link\')" class="smallfont pure-button">拷贝链接</a>';
            html += '<a onclick="copyTextFunc()" class="smallfont pure-button">歌名+链接</a>';
            html += '</form></div></div></div>';
        }

        document.getElementById('main').innerHTML = html;
    }

    // 查找指定ID的项目
    function findItemById(data, id) {
        return data.find(item => item.id === id);
    }

    // 主初始化函数
    async function init() {
        const n = getUrlParam('n');

        // 加载文件列表
        const files = await loadFileList();
        if (!files || files.length === 0) {
            document.getElementById('main').innerHTML = '<h1>无法加载数据</h1>';
            return;
        }

        // 组织数据
        hymnData = organizeData(files);

        if (!n) {
            // 显示目录
            const tableHtml = generateTableHtml(hymnData);
            document.getElementById('main').innerHTML = tableHtml;
            initDataTable();
        } else if (n === 'check') {
            // 检查重复（开发用）
            document.getElementById('main').innerHTML = '<pre>检查功能需要在开发工具中查看</pre>';
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
})();
