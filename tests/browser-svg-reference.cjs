const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');

(async () => {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'hymn-svg-test-'));
    let requests = 0;
    const server = http.createServer((request, response) => {
        requests += 1;
        response.end('unexpected external resource');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    try {
        const browser = await chromium.launch({ headless: true });
        const chromiumVersion = browser.version();
        await browser.close();
        const input = {
            source: path.join(directory, '原谱 "1 2 3".svg'),
            destination: path.join(directory, 'reference.png'),
            scale: 2,
            playwrightVersion: require('playwright/package.json').version,
            chromiumVersion
        };
        const render = options => new Promise((resolve, reject) => {
            const child = require('node:child_process').execFile(process.execPath,
                [path.resolve(__dirname, '../tools/render-sync-svg.cjs')],
                { timeout: 60000 }, error => error ? reject(error) : resolve());
            child.stdin.end(JSON.stringify({ ...input, ...options }));
        });
        const opening = '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60">';
        const body = '<rect id="note" width="120" height="60" fill="#336699"/>';
        const original = opening + body + '</svg>';
        await fs.writeFile(input.source, original);
        await render();
        const expected = await fs.readFile(input.destination);
        assert.equal(expected.readUInt32BE(16), 240);
        assert.equal(expected.readUInt32BE(20), 120);
        await render();
        assert.deepEqual(await fs.readFile(input.destination), expected);
        assert.equal(await fs.readFile(input.source, 'utf8'), original);

        const url = `http://127.0.0.1:${server.address().port}/external`;
        await fs.writeFile(input.source, opening + body +
            `<script>document.getElementById('note').setAttribute('fill','red');fetch('${url}');</script></svg>`);
        await render();
        assert.deepEqual(await fs.readFile(input.destination), expected, 'SVG scripts must stay inactive');
        await fs.writeFile(input.source, opening + body +
            `<image href="${url}" x="100" y="50" width="1" height="1"/></svg>`);
        await render();
        assert.equal(requests, 0, 'rasterization must not fetch external resources');

        await fs.writeFile(input.destination, expected);
        for (const options of [{ playwrightVersion: 'changed' }, { chromiumVersion: 'changed' },
            { scale: 1.5 }, { scale: 5 }]) {
            await assert.rejects(render(options));
            assert.deepEqual(await fs.readFile(input.destination), expected);
        }
        await fs.writeFile(input.source, '<svg xmlns="http://www.w3.org/2000/svg" width="5000" height="10"/>');
        await assert.rejects(render());
        assert.deepEqual(await fs.readFile(input.destination), expected);
        console.log('PASS SVG pixels, dimensions, source preservation, inactive scripts, offline resources and renderer versions');
    } finally {
        await new Promise(resolve => server.close(resolve));
        await fs.rm(directory, { recursive: true, force: true });
    }
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
