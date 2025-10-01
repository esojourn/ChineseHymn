// 数据加载和处理模块

let fileListCache = null;
let hymnDataCache = null;

/**
 * 从URL获取参数
 */
export function getUrlParam(name) {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get(name);
}

/**
 * 获取文件列表 - 从静态JSON文件加载
 */
export async function loadFileList() {
    if (fileListCache) return fileListCache;

    try {
        const response = await fetch('/data/files.json');
        fileListCache = await response.json();
        return fileListCache;
    } catch (error) {
        console.error('无法加载文件列表:', error);
        return [];
    }
}

/**
 * 解析文件名，提取信息
 */
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
        path: `/data/${filename}`,
        format: format,
        isLead: isLead,
        fullFilename: filename
    };
}

/**
 * 组织数据：将txt/jpg文件和对应的mp3文件关联
 */
export function organizeData(files) {
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

/**
 * 加载并缓存赞美诗数据
 */
export async function loadHymnData() {
    if (hymnDataCache) return hymnDataCache;

    const files = await loadFileList();
    if (!files || files.length === 0) {
        return [];
    }

    hymnDataCache = organizeData(files);
    return hymnDataCache;
}

/**
 * 查找指定ID的项目
 */
export function findItemById(data, id) {
    return data.find(item => item.id === id);
}

/**
 * 加载歌词文本
 */
export async function loadLyrics(path) {
    try {
        const response = await fetch(path);
        const text = await response.text();
        return text.trim().replace(/\n\n\n+/g, '\n\n');
    } catch (error) {
        console.error('加载歌词失败:', error);
        return '';
    }
}

/**
 * 获取歌曲标题
 */
export async function getHymnTitle(item) {
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
