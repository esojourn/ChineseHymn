// 侧边栏和checkbox状态管理模块
import $ from 'jquery';
import Cookies from 'js-cookie';

/**
 * 打开侧边栏导航
 */
export function openNav() {
    document.getElementById("mySidenav").style.left = "0px";
    $(".mask").css({
        "height": $(document).height() + "px",
        "width": $(window).width() + "px",
        "opacity": "0.8"
    });
}

/**
 * 关闭侧边栏导航
 */
export function closeNav() {
    document.getElementById("mySidenav").style.left = "-250px";
    $(".mask").css({ "width": "0", "height": "0", "opacity": "0" });
}

/**
 * 处理分类checkbox的逻辑（全选/取消全选）
 */
function changeCats(clickedID, catsClass) {
    const cat0 = $("input:checkbox[id=cat0]");
    const cat1 = $("input:checkbox[id=cat1]");
    const cat2 = $("input:checkbox[id=cat2]");
    const cat3 = $("input:checkbox[id=cat3]");

    if (clickedID === "cat0") {
        const isChecked = cat0.is(":checked");
        cat1.prop("checked", isChecked);
        cat2.prop("checked", isChecked);
        cat3.prop("checked", isChecked);
    }

    if (clickedID !== "cat0" && clickedID !== null) {
        if (cat1.is(":checked") && cat2.is(":checked") && cat3.is(":checked")) {
            cat0.prop("checked", true);
        } else if (!cat1.is(":checked") || !cat2.is(":checked") || !cat3.is(":checked")) {
            cat0.prop("checked", false);
        }
    }
}

/**
 * 保存checkbox状态到cookie
 */
function setCookie(className) {
    const idArray = [];
    $("input:checkbox[class='" + className + "']:checked").each(function () {
        idArray.push(this.id);
    });
    Cookies.set(className, idArray.join(","), { expires: 60 });
}

/**
 * 根据checkbox状态过滤表格
 */
function changeTable(className) {
    // 检查DataTable是否存在（播放器页面没有DataTable）
    if (!$.fn.DataTable.isDataTable('#mytable')) {
        return;
    }

    const table = $('#mytable').DataTable();
    const idArray = [];
    let searchStr = '';

    $("input:checkbox[class='" + className + "']:checked").each(function () {
        idArray.push(this.value);
    });

    searchStr = idArray.join("|");

    table.column(1).search(searchStr, true, false);

    // 处理"显示无伴奏诗歌"选项
    if (!$("input:checkbox[id=set3]").is(":checked")) {
        table.column(3).search("^(?!.*(无伴奏)).*$", true, false);
    } else {
        table.column(3).search("", true, false);
    }

    table.draw();
}

/**
 * 删除所有cookies
 */
function removeCookies() {
    Cookies.remove("checkbox-cats");
    Cookies.remove("checkbox-sets");
    Cookies.remove("visited");
    Cookies.remove("name");
}

/**
 * 某些设置变更需要刷新页面
 */
function refreshPage(clickedID) {
    const urlParams = new URLSearchParams(window.location.search);
    const n = urlParams.get('n');

    if (n && (clickedID === 'set2' || clickedID === 'set4')) {
        location.reload();
    }
}

/**
 * 初始化侧边栏checkbox状态管理
 */
export function initSidebarCheckboxes() {
    const catsClass = "checkbox-cats";
    const setsClass = "checkbox-sets";

    // 从cookie恢复分类checkbox状态
    if (Cookies.get(catsClass)) {
        const cats = Cookies.get(catsClass).split(",");
        cats.forEach((item) => {
            $("input:checkbox[id=" + item + "]").prop("checked", true);
        });
    } else if (Cookies.get('visited') !== "true") {
        // 首次访问，默认全选
        $("input:checkbox[class='" + catsClass + "']").each(function () {
            $(this).prop("checked", true);
        });
    }

    // 从cookie恢复设置checkbox状态
    if (Cookies.get(setsClass)) {
        const sets = Cookies.get(setsClass).split(",");
        sets.forEach((item) => {
            $("input:checkbox[id=" + item + "]").prop("checked", true);
        });
    } else if (Cookies.get('visited') !== "true") {
        // 首次访问，设置默认值
        $("input:checkbox[id=set1]").prop("checked", true);  // 自动播放
        $("input:checkbox[id=set3]").prop("checked", true);  // 显示"无伴奏"诗歌
        $("input:checkbox[id=set4]").prop("checked", true);  // 领唱版优先
    }

    // 标记已访问
    Cookies.set('visited', 'true', { expires: 60 });

    // 初始化状态
    changeCats(null, catsClass);
    changeTable(catsClass);

    // 只在首次访问或没有cookie时才保存
    if (!Cookies.get(catsClass)) {
        setCookie(catsClass);
    }
    if (!Cookies.get(setsClass)) {
        setCookie(setsClass);
    }

    // 绑定checkbox点击事件
    $("input:checkbox").click(function () {
        const clickedID = $(this).attr('id');
        changeCats(clickedID, catsClass);
        setCookie(catsClass);
        setCookie(setsClass);
        changeTable(catsClass);
        refreshPage(clickedID);
    });

    // 绑定删除cookies按钮
    $("a[id=removeCookies]").click(function () {
        removeCookies();
    });

    // 美化表格header（仅在目录页面）
    if ($.fn.DataTable.isDataTable('#mytable')) {
        $("div[id=mytable_length]").addClass("tableHeader");
        $("div[id=mytable_filter]").addClass("tableHeader");
        $(".tableHeader").wrapAll("<div class='tableHeaderWrap'></div>");
        $("table[id=mytable]").css("width", "100%");
        $("table[id=mytable]").addClass("responsive");
    }
}

/**
 * 初始化菜单按钮的显示/隐藏逻辑
 */
export function initMenuButton() {
    const menuButton = document.querySelector("span.menu-button");
    if (!menuButton) return;

    let lastScrollTop = 0;
    let mouseTimer = null;
    const MOUSE_TIMEOUT = 3000; // 3秒
    let isMouseInCorner = false;

    // 监听鼠标移动
    document.addEventListener('mousemove', function(e) {
        const cornerX = 100;
        const cornerY = 100;
        isMouseInCorner = (e.clientX <= cornerX && e.clientY <= cornerY);

        if (isMouseInCorner) {
            menuButton.style.opacity = "1";
            menuButton.style.visibility = "visible";
        }

        if (mouseTimer) {
            clearTimeout(mouseTimer);
        }

        mouseTimer = setTimeout(function() {
            if (!isMouseInCorner) {
                menuButton.style.opacity = "0";
                menuButton.style.visibility = "hidden";
            }
        }, MOUSE_TIMEOUT);
    });

    // 监听滚动事件
    window.addEventListener('scroll', function() {
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;

        if (scrollTop > lastScrollTop && !isMouseInCorner) {
            // 向下滚动且鼠标不在左上角区域时，淡出按钮
            menuButton.style.opacity = "0";
            menuButton.style.visibility = "hidden";
        } else if (scrollTop <= lastScrollTop || isMouseInCorner) {
            // 向上滚动或鼠标在左上角区域时，淡入按钮
            menuButton.style.opacity = "1";
            menuButton.style.visibility = "visible";
        }

        lastScrollTop = scrollTop <= 0 ? 0 : scrollTop;
    });

    // 初始化时，如果页面没有滚动，则显示按钮
    if (window.pageYOffset === 0) {
        menuButton.style.opacity = "1";
        menuButton.style.visibility = "visible";
    }
}

/**
 * 复制文本功能
 */
export function copyTextFunc(mode) {
    const copyText = mode === "link"
        ? document.getElementById("copyLink")
        : document.getElementById("copyText");

    if (copyText) {
        copyText.select();
        copyText.setSelectionRange(0, 9999);
        document.execCommand("copy");
    }
}
