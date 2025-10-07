// 目录列表展示模块
import $ from 'jquery';
import 'datatables.net';
import 'datatables.net-dt/css/dataTables.dataTables.min.css';
import { initSidebarCheckboxes, initMenuButton } from './sidebar.js';

/**
 * 生成目录表格HTML
 */
export function generateTableHtml(data) {
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

/**
 * 初始化DataTable
 */
export function initDataTable() {
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

/**
 * 渲染目录页面
 */
export function renderTableView(data) {
    const tableHtml = generateTableHtml(data);
    document.getElementById('main').innerHTML = tableHtml;
    initDataTable();

    // 初始化侧边栏checkbox状态管理
    // 使用setTimeout确保DataTable完全初始化后再绑定事件
    setTimeout(() => {
        initSidebarCheckboxes();
        initMenuButton();
    }, 100);
}
