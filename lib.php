<?php
function get_name_array($value, $path){

    $filesTxt[] = $value;
    preg_match("/^[0-9]{2}-[0-9]{2,3}/", $value, $arr);
    $id = $arr[0];
    $subid = explode('-', $id);

    $name = [
        'id' => $id,
        'folder' => $subid[0],
        'num' => $subid[1],
        'name' => preg_replace('/\.[a-z|A-Z|0-9]{3}$/', '', ltrim($value, $id)),  // ltrim 去掉$id， preg_replace去掉扩展名
        'path' => $path . '/' . $value,
        'matched' => FALSE,
    ];
    unset($arr, $subid, $id);
    return $name;
}