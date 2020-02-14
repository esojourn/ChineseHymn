<?php
/*
 * 根据目录中所有文件，整理出格式化的数组。
*/
function get_name_array($value, $path)
{
    $filesTxt[] = $value;
    preg_match("/^[0-9]{2}-[0-9]{1,3}/", $value, $arr);
    $id = $arr[0];
    $subid = explode('-', $id);

    $name = [
        'id' => $id,
        'folder' => $subid[0],
        'num' => $subid[1],
        'name' => preg_replace('/\.[a-z|A-Z|0-9]{3}$/', '', ltrim($value, $id)),  // ltrim 去掉$id， preg_replace去掉扩展名
        'path' => $path . '/' . $value,
        'matched' => FALSE,
        'format' => substr(mb_strtolower($value), -3),
    ];
    unset($arr, $subid, $id);
    return $name;
}

/*
 * 建立表格字符串。同时更新数组内 matched 字段信息。
*/
function get_html_contenttable(&$name, $audio)
{
    $output = '<form class="pure-form"><table id="mytable"><thead><tr><th class="hidden">ID</th><th>目录</th><th>编号</th><th>诗歌</th></tr></thead><tbody>';
    foreach ($name as $k1 => $v1) {
        foreach ($audio as $k2 => $v2) {
            if ($name[$k1]['id'] == $audio[$k2]['id']) {  //如果txt文件有对应的mp3，则更新matched为true
                $name[$k1]['mp3'] = $audio[$k2]['path'];
                $name[$k1]['matched'] = TRUE;
            }
        }
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
        if ($name[$k1]['matched'] == TRUE) {
            $output .= '<a href="?n=' . $v1['id'] . '">' . $v1['name'] . "</a>\n";
        } else {
            $output .= '<a href="?n=' . $v1['id'] . '">' . $v1['name'] . "</a> - 无伴奏\n";
        }
        $output .= "</td></tr>\n";
    }
    $output .= '</tbody></table></form>';
    $output .= <<<EOL
    <script type="text/javascript">
    $(document).ready( function () {
        $('#mytable').DataTable({
            "aLengthMenu": [ [15, 25, 50, 100, -1], [15, 25, 50, 100, "All"] ],
            "iDisplayLength": 15,
            "language": {
                "search": "搜索：",
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
    return FALSE;
}


function print_html_header()
{
    echo <<<EOL
    <!DOCTYPE html>  
    <html lang="zh-CN" class="no-js">
    <head>  
        <meta charset="UTF-8">
	    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1.5, minimum-scale=0.6, user-scalable=yes"/>
        <title>敬拜赞美</title>

        <link rel="stylesheet" type="text/css" href="DataTables-1.10.20/css/jquery.dataTables.min.css"/>
        <script type="text/javascript" src="jQuery-3.3.1/jquery-3.3.1.min.js"></script>
        <script type="text/javascript" src="DataTables-1.10.20/js/jquery.dataTables.min.js"></script>

        <link rel="stylesheet" href="pure-min.css">

        <link rel="stylesheet" type="text/css" href="styles.css"/>     

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
    if ($_SERVER["SERVER_PORT"] != "80") {
        $pageURL .= $_SERVER["SERVER_NAME"] . ":" . $_SERVER["SERVER_PORT"] . $_SERVER["REQUEST_URI"];
    } else {
        $pageURL .= $_SERVER["SERVER_NAME"] . $_SERVER["REQUEST_URI"];
    }
    return $pageURL;
}

function print_html_player($name)
{
    //var_dump($name);
    $mp3 = isset($name["mp3"]) ? $name["mp3"] : '';
    $id = $name["id"];
    $pageURL = get_page_url();
    $loop = '';

    if($name['folder'] == '05'){
        $loop = 'loop';
    }

    echo <<<EOL
    <div class='main'>
    <div class='audio clearfix'>
    <audio controls autoplay $loop>  
        <source src="$mp3" type="audio/mpeg">  
    </audio>  
</div>
EOL;

    if ($name["format"] == 'txt') {
        $file = file_get_contents($name["path"]);

        $encode = mb_detect_encoding($file, array("ASCII", 'UTF-8', "GB2312", "GBK", 'BIG5', 'LATIN1'));
        if ($encode != 'UTF-8') {
            $file = mb_convert_encoding($file, 'UTF-8', $encode);
        }

        echo <<<EOL


    <div class='text'><div class='text-inner'><pre>
        $file
        </pre></div>
        <br>
        
        <div class='footer'>
            <div class='return'><a href='?n=i'>回目录</a></div>
            <div class='link'>本首链接：$pageURL</div>
        </div>
    </div>
    </div>
EOL;
    } elseif ($name["format"] == 'jpg') {
        $file = $name["path"];
        echo <<<EOL
        <div class='text'><div class='text-inner'><img src=$file />
            <div class='footer'>
                <div class='return'><a href='?n=i'>回目录</a></div>
                <div class='link'>本首链接：$pageURL</div>
            </div>
        </div>
        </div>
EOL;
    }
}

function print_html_footer()
{
    echo <<<EOL
    <div class="footer"><a href="https://jinshuju.net/f/bQvJ9p">故障报告</a></div>
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
