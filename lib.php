<?php
/*
 * 根据目录中所有文件，整理出格式化的数组。
 */
function get_name_array($value, $path)
{
    global $pathroot;
    $filesTxt[] = $value;
    preg_match("/^[0-9]{2}-[0-9]{1,3}/", $value, $arr);
    $id = $arr[0];
    $subid = explode('-', $id);

    $name = [
        'id' => $id,
        'folder' => $subid[0],
        'num' => $subid[1],
        'name' => preg_replace('/\.[a-z|A-Z|0-9]{3}$/', '', ltrim($value, $id)), // ltrim 去掉$id， preg_replace去掉扩展名
        'path' => $pathroot . '/' . $path . '/' . $value,
        'matched' => false,
        'lead' => false,
        'format' => substr(mb_strtolower($value), -3),
    ];
    unset($arr, $subid, $id);
    return $name;
}

/*
 * 操作$name，加入对应的mp3, mp3-lead信息
 */
function get_audio_info(&$name, $audio)
{
    foreach ($name as $k1 => $v1) {
        foreach ($audio as $k2 => $v2) {
            if ($name[$k1]['id'] == $audio[$k2]['id']) { //如果txt文件有对应的mp3，则更新matched为true
                if ($name[$k1]['name'] == $audio[$k2]['name']) { // 有名字一样的
                    $name[$k1]['mp3'] = $audio[$k2]['path'];
                    $name[$k1]['matched'] = true;
                }
                if ($name[$k1]['name'] . '-领唱' == $audio[$k2]['name']) { // 有名字带‘领唱’的
                    $name[$k1]['mp3-lead'] = $audio[$k2]['path'];
                    $name[$k1]['lead'] = true;
                    $name[$k1]['matched'] = true;
                }
            }
        }
    }
}

/*
 * 建立表格字符串。同时更新数组内 matched 字段信息。
 */
function get_html_contenttable($name)
{
    $music_status = '';
    $lead_status = '';
    $title_status = '';
    $output = '<form class="pure-form"><table id="mytable"><thead><tr><th class="hidden">ID</th><th>目录</th><th>编号</th><th>诗歌</th></tr></thead><tbody>';
    foreach ($name as $k1 => $v1) {

        $output .= "<tr>\n";

        // $output .= "<td>";
        // $output .= $v1['id'];
        // $output .= "</td>\n";

        $output .= "<td class='hidden'>";
        $output .= $v1['folder'] . $v1['num'];
        $output .= "</td>\n";

        $output .= "<td class='folder'>";
        $output .= $v1['folder'];
        $output .= "</td>\n";

        $output .= "<td class='num'>";
        $output .= $v1['num'];
        $output .= "</td>\n";

        $output .= "<td>\n";

        if ($name[$k1]['matched'] == false) {
            $music_status = '无伴奏';
        }
        if ($name[$k1]['lead'] == true) {
            $lead_status = '领唱';
        }

        if ($music_status != '' || $lead_status != '') {
            $title_status = ' - ' . $lead_status . ' ' . $music_status;
        }

        $output .= '<a href="?n=' . $v1['id'] . '">' . $v1['name'] . "</a>$title_status\n";
        $output .= "</td></tr>\n";

        $music_status = '';
        $lead_status = '';
        $title_status = ''; //清除状态
    }
    // echo '<pre>';
    // var_dump($name);
    // echo '</pre>';
    $output .= '</tbody></table></form>';
    $output .= <<<EOL
    <script type="text/javascript">
    $(document).ready( function () {
        $('#mytable').DataTable({
            "paging": false,
            "aLengthMenu": [ [15, 25, 50, 100, -1], [15, 25, 50, 100, "All"] ],
            "responsive": true,
            "iDisplayLength": -1,
            "language": {
                "search": "搜索：",
                "infoFiltered": "",
                "lengthMenu":     "每页显示 _MENU_ 首",
                "info": "显示第 _START_ 至 _END_ 首。共 _TOTAL_ 首",
                    "paginate": {
                    "first":      "首页",
                    "last":       "末页",
                    "next":       "向后",
                    "previous":   "向前"
                },
                "columnDefs": [
                    {
                        "targets": 0,
                        "visible": false,
                        "searchable": true
                    }
                ]
            }
        });
    } );
    </script>
EOL;
    return $output;
}

function is_valid_id($name, $n)
{
    foreach ($name as $key => $value) {
        if ($n == $value['id']) {
            return $key;
        }
    }
    return false;
}

function get_hymn_title($name, $is_front)
{
    global $serverroot;

    if ($name["format"] == 'txt' && !$is_front) {
        $file = file_get_contents($serverroot . $name["path"]);

        $encode = mb_detect_encoding($file, array("ASCII", 'UTF-8', "GB2312", "GBK", 'BIG5', 'LATIN1'));
        if ($encode != 'UTF-8') {
            $file = mb_convert_encoding($file, 'UTF-8', $encode);
        }
        $file = trim($file);
        $hymntitle = substr($file, 0, strpos($file, "\n"));
        $hymntitle = str_replace('（', '(', $hymntitle);
        $hymntitle = str_replace('）', ')', $hymntitle);
        //$hymntitle = preg_replace('/[0-9]|\t|\n|\r*/', '', $hymntitle);
        $hymntitle = preg_replace('/((\()([^0-9][^\)]+)(\d+)([^0-9][^\)]+)(\)))|[0-9]|\t|\n|\r*/', '', $hymntitle);
        //1. 替换 (AAA 1 BBB)，2. 替换数字、\t \r等
    } else {
        $hymntitle = '';
    }
    return $hymntitle;
}

function print_html_header($hymntitle)
{
    global $pathroot;

    if ($hymntitle != '') {
        $hymntitle = '<title>' . $hymntitle . ' - 敬拜赞美</title>';
    } else {
        $hymntitle = '<title>敬拜赞美</title>';
    }

    echo <<<EOL
    <!DOCTYPE html>
    <html lang="zh-CN" class="no-js">
    <head>
        <meta charset="UTF-8">
	    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1.5, minimum-scale=0.6, user-scalable=yes"/>
        $hymntitle
        <link rel="stylesheet" type="text/css" href="$pathroot/DataTables-1.10.20/css/jquery.dataTables.min.css"/>
        <script type="text/javascript" src="$pathroot/jQuery-3.3.1/jquery-3.3.1.min.js"></script>
        <script type="text/javascript" src="$pathroot/DataTables-1.10.20/js/jquery.dataTables.min.js"></script>
        <script type="text/javascript" src="$pathroot/css/js.cookie-2.2.1.min.js"></script>
        <link rel="stylesheet" href="$pathroot/css/pure-min.css">
        <link rel="stylesheet" type="text/css" href="$pathroot/css/styles.css"/>

        <!-- Global site tag (gtag.js) - Google Analytics -->
        <script async src="https://www.googletagmanager.com/gtag/js?id=G-EVK44GKC2E"></script>
        <script>
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());

        gtag('config', 'G-EVK44GKC2E');
        </script>

        <script>
        function copyTextFunc(mode) {
            console.log(mode);
            if(mode == "link"){
                var copyText = document.getElementById("copyLink");
            }else{
                var copyText = document.getElementById("copyText");
            }
            copyText.select();
            copyText.setSelectionRange(0, 9999)
            document.execCommand("copy");
            // alert("已经拷贝：" + copyText.value);
        }
        </script>

    </head>
    <body>
EOL;
}

function get_page_url()
{
    $pageURL = 'http';
    if (isset($_SERVER["HTTPS"])) {
        if ($_SERVER["HTTPS"] == "on") {
            $pageURL .= "s";
        }
    }
    $pageURL .= "://";
    if ($_SERVER["SERVER_PORT"] != "80" && $_SERVER["SERVER_PORT"] != "443") {
        $pageURL .= $_SERVER["SERVER_NAME"] . ":" . $_SERVER["SERVER_PORT"] . $_SERVER["REQUEST_URI"];
    } else {
        $pageURL .= $_SERVER["SERVER_NAME"] . $_SERVER["REQUEST_URI"];
    }
    return $pageURL;
}

function print_html_player($name, $hymntitle)
{
    global $serverroot, $pathroot;
    $cookie_name = "checkbox-sets";
    $cookie_visited = "visited";
    $loop = '';
    $lead = true;
    $autoplay = 'autoplay';
    $play = '';

    if (isset($_COOKIE[$cookie_name]) || isset($_COOKIE[$cookie_visited])) { //设置为空，但重复访问时，应重新进入判断。

        if (isset($_COOKIE[$cookie_name])) {
            $settings = explode(',', $_COOKIE[$cookie_name]);
            if (in_array("set1", $settings)) {
                $autoplay = 'autoplay';
            } else {
                $autoplay = '';
            }
            if (in_array("set2", $settings)) {
                $loop = 'loop';
            } else {
                $loop = '';
            }

            if (in_array("set4", $settings)) {
                $lead = true;
            } else {
                $lead = false;
            }
        }
    }
    // echo '<pre>';
    // var_dump($name);
    // echo '</pre>';

    if ($name['matched'] == true) { //判断mp3使用哪个版本。

        //$lead 是cookie开关。$name['mp3-lead'] 或 $name['mp3']是音乐路径。
        if ($lead == true && array_key_exists('mp3-lead', $name)) {
            $mp3 = $name["mp3-lead"];
            $play = '领';
        } elseif ($lead == true && array_key_exists('mp3', $name)) {
            $mp3 = $name["mp3"];
        } elseif ($lead == false && array_key_exists('mp3', $name)) {
            $mp3 = $name["mp3"];
        } elseif ($lead == false && array_key_exists('mp3-lead', $name)) {
            $mp3 = $name["mp3-lead"];
            $play = '领';
        }
    } else { //无伴奏
        $mp3 = '';
    }
    $id = $name["id"];
    $pageURL = preg_replace("/&.*$/U", '', get_page_url());

    echo <<<EOL
    <div class='main'>
    <div class='audio clearfix'>
    <audio controls $autoplay $loop>
        <source src="$mp3" type="audio/mpeg">
    </audio>
    </div>
EOL;

    if ($name["format"] == 'txt') {
        $file = file_get_contents($serverroot . $name["path"]);

        $encode = mb_detect_encoding($file, array("ASCII", 'UTF-8', "GB2312", "GBK", 'BIG5', 'LATIN1'));
        if ($encode != 'UTF-8') {
            $file = mb_convert_encoding($file, 'UTF-8', $encode);
        }
        //$file = preg_replace('/\ |\t*/', '', $file);
        $file = trim($file);
        $file = preg_replace('/\n\n\n|\r\n\r\n\r\n|\r\r\rt*/', "\n\n", $file);
        if ($play != '') {
            $file = "($play) - " . $file;
        }

        echo <<<EOL
        <div class='text'><div class='text-inner'>
        <pre>$file</pre>
        </div>
        <div class='footer'>
            <div class='return'><a href='$pathroot'>回目录</a></div>
            <form class="pure-form">
            <input type="text" value="$hymntitle - $pageURL" id="copyText" class="smallfont">
            <input type="text" value="$pageURL" id="copyLink"  class="hidden2">
            <br>
            <a onclick="copyTextFunc('link')" class="smallfont pure-button">拷贝链接</a>
            <a onclick="copyTextFunc()" class="smallfont pure-button">歌名+链接</a>
            </form>
        </div>
        </div>
EOL;
    } elseif ($name["format"] == 'jpg') {
        $file = $name["path"];
        $hymntitle = $name["name"];
        if ($play != '') {
            $play = '领唱版<br />';
        }
        echo <<<EOL
        <div class='text'><div class='text-inner'>$play<img src=$file />
            <div class='footer'>
                <div class='return'><a href='$pathroot'>回目录</a></div>
                <form class="pure-form">
                <input type="text" value="$hymntitle - $pageURL" id="copyText" class="smallfont">
                <input type="text" value="$pageURL" id="copyLink" class="hidden2">
                <br>
                <a onclick="copyTextFunc('link')" class="smallfont pure-button">拷贝链接</a>
                <a onclick="copyTextFunc()" class="smallfont pure-button">歌名+链接</a>
                </form>
            </div>
        </div>
        </div>
EOL;
    }
}

function print_html_menu()
{
    global $pathroot;
    echo <<<EOL
    <div id="mask" class="mask" onclick="closeNav()"></div>
    <div id="mySidenav" class="sidenav">
    <a href="javascript:void(0)" class="closebtn" onclick="closeNav()">&times;</a>
        <div class="content">
            <div class="checkboxes-and-radios">
                <h2>曲目</h2>
                <input type="checkbox" name="cats[]" class="checkbox-cats" id="cat0" value="all">
                <label for="cat0">全部分类</label>
                <input type="checkbox" name="cats[]" class="checkbox-cats" id="cat1" value="01|02|03|04">
                <label for="cat1">经典圣诗</label>
                <input type="checkbox" name="cats[]" class="checkbox-cats" id="cat2" value="05">
                <label for="cat2">红本400首</label>
                <input type="checkbox" name="cats[]" class="checkbox-cats" id="cat3" value="06">
                <label for="cat3">诗歌欣赏</label>

                <h2>设置</h2>
                <input type="checkbox" name="sets[]" class="checkbox-sets" id="set1" value="1">
                <label for="set1">自动播放</label>
                <input type="checkbox" name="sets[]" class="checkbox-sets" id="set2" value="2">
                <label for="set2">单曲循环</label>
                <input type="checkbox" name="sets[]" class="checkbox-sets" id="set3" value="3">
                <label for="set3">显示无伴奏诗歌</label>
                <input type="checkbox" name="sets[]" class="checkbox-sets" id="set4" value="4">
                <label for="set4">优先使用领唱</label>

            <!--<h2>曲目</h2>
                <input type="radio" name="radio-cats" id="radio-1" value="1" checked>
                <label for="radio-1">Radio Label 1</label>
                <input type="radio" name="radio-cats" id="radio-2" value="2">
                <label for="radio-2">Radio Label 2</label>
                <input type="radio" name="radio-cats" id="radio-3" value="3" checked>
                <label for="radio-3">Radio Label 3</label>
                -->
            </div>
            <div class="sidebarfooter">
                <a href="$pathroot">回目录</a><br><br>
                <!--<a id="removeCookies" style="font-size: 12px;">删除cookies</a>-->
            </div>
        </div>
    </div>
    <span style="font-size:30px;cursor:pointer" onclick="openNav()" class="menu-button">&#9776;</span>


EOL;
}

function print_html_footer()
{
    echo <<<EOL
    <div class="footer">
    <a href="https://github.com/esojourn/ChineseHymn/releases" target="_blank">v1.4.1</a>
    </div>
    <script type="text/javascript" src="css/scripts.js"></script>
    </body>
</html>
EOL;
}

function check_duplication($arrays)
{
    echo "<pre>find completely identical arrays\n";
    foreach ($arrays as $current_key => $current_array) {
        $search_key = array_search($current_array, $arrays);
        //echo "current key: $current_key \n";
        //echo "search key: $search_key \n";
        if ($current_key != $search_key) {
            echo "duplicate found for item $current_key\n";
        }
        //echo "\n";
    }

    echo "\n\nfind arrays with duplicate value for 'name'\n";
    foreach ($arrays as $current_key => $current_array) {

        foreach ($arrays as $search_key => $search_array) {
            if ($search_array['name'] == $current_array['name']) {
                if ($search_key != $current_key) {
                    echo "value: " . $search_array['id'] . ' - ' . $search_array['name'] . "\n";
                    echo "current key: $current_key\n";
                    echo "duplicate found: $search_key\n";
                }
            }
        }
        //echo "\n";
    }

    echo "\n\nfind arrays with duplicate value for 'id'\n";
    foreach ($arrays as $current_key => $current_array) {
        foreach ($arrays as $search_key => $search_array) {
            if ($search_array['id'] == $current_array['id']) {
                if ($search_key != $current_key) {
                    echo "value: " . $search_array['id'] . ' - ' . $search_array['name'] . "\n";
                    echo "current key: $current_key\n";
                    echo "duplicate found: $search_key\n";
                }
            }
        }
        //echo "\n";
    }
    echo "</pre>";
}
