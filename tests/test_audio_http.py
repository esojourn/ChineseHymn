"""Run against PHP_BASE_URL to exercise real HTTP byte ranges."""
import os
from pathlib import Path
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import quote


@unittest.skipUnless(os.environ.get('PHP_BASE_URL'), 'PHP_BASE_URL is required')
class AudioHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.asset = Path(__file__).parents[1] / 'data/01-131听哪千万声音雷鸣.mp3'
        cls.raw = cls.asset.read_bytes()
        cls.url = os.environ['PHP_BASE_URL'] + '/audio.php?file=' + quote(cls.asset.name)

    def request(self, headers=None, method='GET', url=None):
        try:
            return urlopen(Request(url or self.url, headers=headers or {}, method=method), timeout=10)
        except HTTPError as error:
            return error

    def test_full_and_head(self):
        with self.request() as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), self.raw)
        with self.request(method='HEAD') as response:
            self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
            self.assertEqual(int(response.headers['Content-Length']), len(self.raw))
            self.assertEqual(response.read(), b'')

    def test_closed_open_and_suffix_ranges(self):
        for value, start, end in [('1000-1999', 1000, 1999), ('600000-', 600000, len(self.raw)-1), ('-100', len(self.raw)-100, len(self.raw)-1), ('600000-9999999', 600000, len(self.raw)-1)]:
            with self.request({'Range': 'bytes=' + value}) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers['Content-Range'], f'bytes {start}-{end}/{len(self.raw)}')
                self.assertEqual(int(response.headers['Content-Length']), end-start+1)
                self.assertEqual(response.read(), self.raw[start:end+1])

    def test_invalid_ranges_and_paths(self):
        for value in ['99999999-', '200-100', '-0']:
            with self.request({'Range': 'bytes=' + value}) as response:
                self.assertEqual(response.status, 416)
                self.assertEqual(response.headers['Content-Range'], f'bytes */{len(self.raw)}')
        for name in ['../index.php', 'missing.mp3', '01-131听哪千万声音雷鸣.txt', '../data/' + self.asset.name]:
            with self.request(url=os.environ['PHP_BASE_URL'] + '/audio.php?file=' + quote(name)) as response:
                self.assertEqual(response.status, 404)
        with self.request(method='POST') as response:
            self.assertEqual(response.status, 405)

    def test_if_range_falls_back_to_complete_representation(self):
        with self.request({'Range': 'bytes=0-99', 'If-Range': '"old"'}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), self.raw)


if __name__ == '__main__':
    unittest.main()
