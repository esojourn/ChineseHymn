function openNav() {
    document.getElementById("mySidenav").style.left = "0px";
    //document.getElementById("main").style.marginLeft = "250px";
    //document.getElementById("main").style.opacity = "0.2";
    //document.body.style.backgroundColor = "rgba(0,0,0,1)";

    $(".mask").css({
        "height": $(document).height() + "px",
        "width": $(window).width() + "px",
        "opacity": "0.8"
    });
}

function closeNav() {
    document.getElementById("mySidenav").style.left = "-250px";
    //document.getElementById("main").style.marginLeft = "0";
    //document.getElementById("main").style.opacity = "1";
    //document.body.style.backgroundColor = "white";
    $(".mask").css({ "width": "0", "height": "0", "opacity": "0" });

}


/*
* 逻辑处理，更新按钮状态，选all时，自动全选等等。。
*/
function changeCats(clickedID, catsClass) {
    var cat0 = $("input:checkbox[id=cat0]"),
        cat1 = $("input:checkbox[id=cat1]"),
        cat2 = $("input:checkbox[id=cat2]"),
        cat3 = $("input:checkbox[id=cat3]")
        ;
    //    console.log('changCats, status0: ' + cat0.is(":checked"));
    //    console.log(clickedID);
    //    console.log(catsClass);
    if (clickedID == "cat0") {
        if (cat0.is(":checked")) {
            cat1.prop("checked", true);
            cat2.prop("checked", true);
            cat3.prop("checked", true);
        }
        if (!cat0.is(":checked")) {
            cat1.prop("checked", false);
            cat2.prop("checked", false);
            cat3.prop("checked", false);
        }
    }
    if (clickedID != "cat0" && clickedID != null) {
        if (cat1.is(":checked") && cat2.is(":checked") && cat3.is(":checked")) {
            cat0.prop("checked", true);
        } else if (!cat1.is(":checked") || !cat2.is(":checked") || !cat3.is(":checked")) {
            cat0.prop("checked", false);
        }
    }
}

function setCookie(className) {
    var idArray = [];
    $("input:checkbox[class='" + className + "']:checked").each(function () {
        idArray.push(this.id);
    });
    Cookies.set(className, idArray.join(","), { expires: 60 });
    //console.log(idArray.join(","));
}

function changeTable(className) {
    var table = $('#mytable').DataTable();
    var idArray = [],
        searchStr = '';

    $("input:checkbox[class='" + className + "']:checked").each(function () {
        idArray.push(this.value);
    });

    searchStr = idArray.join("|");

    //console.log(searchStr);

    table.column(1)
        .search(searchStr, true, false);

    if (!$("input:checkbox[id=set3]").is(":checked")) {
        table.column(3)
            .search("^(?!.*(无伴奏)).*$", true, false);
    } else {
        table.column(3)
            .search("", true, false);
    }
    table.draw();
}

function removeCookies() {
    //console.log(Cookies.get());
    Cookies.remove("checkbox-cats");
    Cookies.remove("checkbox-sets");
    Cookies.remove("visited");
    Cookies.remove("name");
    //console.log(Cookies.get());
}

function refreshPage(clickedID, catsClass) {
    console.log(clickedID);
    var n = getQueryVariable("n");
    console.log(n);
    if (n != false) {
        if (clickedID == 'set2' || clickedID == 'set4') {
            location.reload();
        }
    }
}

function getQueryVariable(variable) {
    var query = window.location.search.substring(1);
    var vars = query.split("&");
    for (var i = 0; i < vars.length; i++) {
        var pair = vars[i].split("=");
        if (pair[0] == variable) { return pair[1]; }
    }
    return (false);
}

$(function () {
    //console.log("ready!");
    //console.log(Cookies.get());

    //console.log(Cookies.get('name'));
    var catsClass = "checkbox-cats",
        setsClass = "checkbox-sets";

    //Cookies.remove(setsClass);
    // update cats / sets check status
    if (Cookies.get(catsClass)) { //according to cookie, update "checked" status of cats 
        var cats = Cookies.get(catsClass).split(",");
        cats.forEach(function (item, index, array) {
            $("input:checkbox[id=" + item + "]").prop("checked", true);
        });
    } else if (Cookies.get('visited') != "true") { // if no cookie, set to all cats
        var cats = [];
        $("input:checkbox[class='" + catsClass + "']").each(function () {
            cats.push(this.id);
            $(this).prop("checked", true);
        });

    }
    if (Cookies.get(setsClass)) { //according to cookie, update "checked" status of sets 
        var sets = Cookies.get(setsClass).split(",");
        sets.forEach(function (item, index, array) {
            $("input:checkbox[id=" + item + "]").prop("checked", true);
        });
    } else if (Cookies.get('visited') != "true") {
        
        $("input:checkbox[id=set1]").prop("checked", true); //无cookie时，设置默认值 autoplay
        $("input:checkbox[id=set3]").prop("checked", true); //无cookie时，设置默认值，显示"无伴奏"诗歌
        $("input:checkbox[id=set4]").prop("checked", true); //无cookie时，设置默认值，领唱版优先
    }
    //  sets = Cookies.get(setsClass).split(",");

    Cookies.set('visited', true, { expires: 60 });

    //$("input:checkbox[id=set3]").prop("checked", true); 

    //console.log(cats);
    //console.log(sets);

    changeCats(null, catsClass);
    changeTable(catsClass);
    setCookie(catsClass);
    setCookie(setsClass);

    $("input:checkbox").click(function () {
        changeCats($(this).attr('id'), catsClass);
        setCookie(catsClass);
        setCookie(setsClass);
        changeTable(catsClass);
        refreshPage($(this).attr('id'), setsClass);
        //TODO 单曲循环 和 领唱 需要 修改DOM

    });
    $("a[id=removeCookies]").click(function () {
        removeCookies();
    });

    //增加table header div，用于固定图层
    $("div[id=mytable_length]").addClass("tableHeader");
    $("div[id=mytable_filter]").addClass("tableHeader");
    //$(".tableHeader").wrapAll("<div class='tableHeaderWrap clearfix'><div class='container'></div></div>");
    $(".tableHeader").wrapAll("<div class='tableHeaderWrap'></div>");
    $("table[id=mytable]").css("width", "100%");
    $("table[id=mytable]").addClass("responsive");
});

// 获取菜单按钮元素
var menuButton = document.querySelector("span.menu-button");
// 上次滚动的位置
var lastScrollTop = 0;

// 鼠标移动检测相关变量
let mouseTimer = null;
const MOUSE_TIMEOUT = 3000; // 3秒
let isMouseInCorner = false;

// 监听鼠标移动
document.addEventListener('mousemove', function(e) {
    // 检查鼠标是否在左上角区域（例如：左上角 100x100 像素的区域）
    const cornerX = 100;
    const cornerY = 100;
    isMouseInCorner = (e.clientX <= cornerX && e.clientY <= cornerY);

    // 如果鼠标在左上角区域，显示按钮
    if (isMouseInCorner && menuButton) {
        menuButton.style.opacity = "1";
        menuButton.style.visibility = "visible";
    }

    // 清除之前的定时器
    if (mouseTimer) {
        clearTimeout(mouseTimer);
    }

    // 设置新的定时器
    mouseTimer = setTimeout(function() {
        // 如果鼠标不在左上角区域，则隐藏按钮
        if (!isMouseInCorner && menuButton) {
            menuButton.style.opacity = "0";
            menuButton.style.visibility = "hidden";
        }
    }, MOUSE_TIMEOUT);
});

// 监听滚动事件
window.onscroll = function() {scrollFunction()};

// 修改原有的滚动函数，增加对鼠标位置的判断
function scrollFunction() {
    var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    if (menuButton) {
        if (scrollTop > lastScrollTop && !isMouseInCorner) {
            // 向下滚动且鼠标不在左上角区域时，淡出按钮
            menuButton.style.opacity = "0";
            menuButton.style.visibility = "hidden";
        } else if (scrollTop <= lastScrollTop || isMouseInCorner) {
            // 向上滚动或鼠标在左上角区域时，淡入按钮
            menuButton.style.opacity = "1";
            menuButton.style.visibility = "visible";
        }
    }
    lastScrollTop = scrollTop <= 0 ? 0 : scrollTop;
}

// 初始化时，如果页面没有滚动，则显示按钮
if (menuButton && window.pageYOffset === 0) {
    menuButton.style.opacity = "1";
    menuButton.style.visibility = "visible";
}

