
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
    $(".mask").css({ "width": "0", "height": "0", "opacity": "0"});

}
