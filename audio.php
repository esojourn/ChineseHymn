<?php
// Serve repository audio with byte ranges, including on PHP's development server.
$method = $_SERVER['REQUEST_METHOD'];
if ($method !== 'GET' && $method !== 'HEAD') {
    header('Allow: GET, HEAD');
    http_response_code(405);
    exit;
}
$name = $_GET['file'] ?? '';
$root = realpath(__DIR__ . '/data');
if (!is_string($name) || preg_match('/[\\\\\/\x00-\x1f]/', $name) || strtolower(pathinfo($name, PATHINFO_EXTENSION)) !== 'mp3') {
    http_response_code(404);
    exit;
}
$path = realpath($root . '/' . $name);
if ($path === false || dirname($path) !== $root || !is_file($path) || !is_readable($path)) {
    http_response_code(404);
    exit;
}
$stream = fopen($path, 'rb');
if ($stream === false) {
    http_response_code(404);
    exit;
}
$size = fstat($stream)['size'];
$start = 0;
$end = $size - 1;
header('Content-Type: audio/mpeg');
header('Accept-Ranges: bytes');
header('Cache-Control: no-cache, no-transform');
header('X-Content-Type-Options: nosniff');
// If-Range cannot be validated here; respond with the complete representation.
$range = $method === 'GET' && !isset($_SERVER['HTTP_IF_RANGE']) ? ($_SERVER['HTTP_RANGE'] ?? '') : '';
if (preg_match('/^bytes=(\d*)-(\d*)$/', $range, $parts)) {
    if ($parts[1] === '') {
        $length = (int) $parts[2];
        $start = max(0, $size - $length);
    } else {
        $start = (int) $parts[1];
        if ($parts[2] !== '') $end = min($end, (int) $parts[2]);
    }
    if ($start >= $size || $start > $end || ($parts[1] === '' && (int) $parts[2] === 0)) {
        header('Content-Range: bytes */' . $size);
        header('Content-Length: 0');
        http_response_code(416);
        fclose($stream);
        exit;
    }
    http_response_code(206);
    header("Content-Range: bytes $start-$end/$size");
}
// Do not compress a byte-range response or buffer the entire recording in memory.
ini_set('zlib.output_compression', '0');
while (ob_get_level() > 0) ob_end_clean();
header('Content-Length: ' . max(0, $end - $start + 1));
if ($method === 'HEAD') {
    fclose($stream);
    exit;
}
fseek($stream, $start);
$remaining = $end - $start + 1;
while ($remaining > 0 && !feof($stream) && !connection_aborted()) {
    $chunk = fread($stream, min(65536, $remaining));
    if ($chunk === false || $chunk === '') break;
    echo $chunk;
    $remaining -= strlen($chunk);
}
fclose($stream);
