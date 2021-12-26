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
        $("input:checkbox[id=set3]").prop("checked", true); //无cookie时，设置默认值，显示“无伴奏”诗歌
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
    
});

