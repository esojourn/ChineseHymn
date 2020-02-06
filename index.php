<?php
ini_set('display_errors', 1); 
ini_set('display_startup_errors', 1); 
error_reporting(E_ALL);
?>
<!DOCTYPE html>  

<html lang="zh-CN" class="no-js">
<head>  
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, maximum-scale=1">
    <title>敬拜赞美</title>  

    <link rel="stylesheet" type="text/css" href="styles.css"/>     
    <link rel="stylesheet" type="text/css" href="DataTables-1.10.20/css/jquery.dataTables.min.css"/>
     
    <script type="text/javascript" src="jQuery-3.3.1/jquery-3.3.1.min.js"></script>
    <script type="text/javascript" src="DataTables-1.10.20/js/jquery.dataTables.min.js"></script>

</head>  
<body>  

<?php

if (isset($_GET["n"])){
    $n=htmlspecialchars($_GET["n"]);
}
//$n=(isset($_GET["n"])) ? htmlspecialchars($_GET["n"]) : NULL;

include 'lib.php';

$path    = 'data';
$data = scandir($path);
//Following code will remove . and .. from the returned array from scandir:
$data = array_diff(scandir($path), array('.', '..'));


$name = array();
$audio = array();
//['folder']['num']['name'] 

foreach ($data as $key => $value) {
    $filesAll[] = $value;
    if(substr(mb_strtolower($value), -4) == '.txt'){
        $name[] = get_name_array($value, $path);
    }
}

foreach ($data as $key => $value) {  
//上面第一次处理 .txt 文件。据此得出文件列表。
//下面得出 .mp3 文件列表。用于比对是否有对应音频文件。
    if (substr(mb_strtolower($value), -4) == '.mp3') {
        $audio[] = get_name_array($value, $path);
    }
}

$output = '<table id="mytable"><thead><tr><th>诗歌</th></tr></thead><tbody>';

foreach ($name as $k1 => $v1) {
    foreach ($audio as $k2 => $v2) {
        if($name[$k1]['id']==$audio[$k2]['id']){
            $name[$k1]['matched'] =TRUE;
        }
    }
    $output .= "<tr><td>\n";
    $output .= '<a href="?n='.$v1['id'].'">' .$v1['name']."</a>\n";
    $output .= "</td></tr>\n";
}
$output .= '</tbody></table>';



/*
foreach ($filesAll as $key => $value) {
    $namestr = mb_substr($value, 0, mb_strlen($value-4));
    $namestr = mb_strlen($value);
    echo $namestr . '<br>';
}
*/

//echo (substr($filesAll[0],-4) == '.mp3') ?  'yes' : 'no';
    
echo '<pre>';

//var_dump($name);
//var_dump($audio);
echo '</pre>';



if($n=='index' || $n=='i' || $n==''){
    echo '<div class="main">';
    echo $output;
    echo '</div>';

}else{

?>
<div class='main'>
    <div class='audio clearfix'>
    <audio controls autoplay>  
        <source src="<?php echo $n.'.mp3';?>" type="audio/mpeg">  
    </audio>  
</div>

<div class='img'>
    <img src="<?php echo $n.'.png';?>" />
    <h1 class='content'><a href='?n=i'>回目录</a></h1>
</div>
</div>

<?php
}
?>
<script type="text/javascript">
$(document).ready( function () {
    $('#mytable').DataTable({
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
        }
    });
} );
</script>
</body>  
</html> 