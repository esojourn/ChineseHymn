<?php
ini_set('display_errors', 1); 
ini_set('display_startup_errors', 1); 
error_reporting(E_ALL);

include 'lib.php';
 
print_html_header();

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

$output = get_html_contenttable($name, $audio);

echo '<pre>';
//var_dump($name);
//var_dump($audio);
echo '</pre>';

if (isset($_GET["n"])){
    $n=htmlspecialchars($_GET["n"]);
}else{
    $n='';
}
//$n=(isset($_GET["n"])) ? htmlspecialchars($_GET["n"]) : NULL;

$playkey=is_valid_id($name, $n);
//var_dump($playkey);

preg_match("/^[0-9]{2}-[0-9]{2,3}/", $n);

if($n=='index' || $n=='i' || $n==''){
    echo '<div class="main">';
    echo $output;
    echo '</div>';
}elseif($playkey !== FALSE){ //如果ID符合格式
    print_html_player($name[$playkey]);
}else{
    echo '<h1>无权访问</h1>';
}
?>

</body>  
</html> 