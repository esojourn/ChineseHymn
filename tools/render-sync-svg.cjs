// Rasterize a pinned public score without changing its SVG or fetching resources.
const fs = require('node:fs');
const { chromium } = require('playwright');

(async () => {
    const input = JSON.parse(fs.readFileSync(0, 'utf8'));
    if (!Number.isInteger(input.scale) || input.scale < 1 || input.scale > 4) {
        throw new Error('SVG renderer scale must be an integer from 1 to 4.');
    }
    if (require('playwright/package.json').version !== input.playwrightVersion) {
        throw new Error('SVG renderer Playwright version changed.');
    }
    const browser = await chromium.launch({ headless: true });
    try {
        if (browser.version() !== input.chromiumVersion) {
            throw new Error('SVG renderer Chromium version changed.');
        }
        const page = await browser.newPage({ deviceScaleFactor: input.scale });
        await page.route('**/*', route => route.abort());
        await page.setContent('<style>body{margin:0;background:white}img{display:block}</style><img alt="">');
        const bytes = fs.readFileSync(input.source);
        const size = await page.locator('img').evaluate(async (element, data) => {
            element.src = 'data:image/svg+xml;base64,' + data;
            await element.decode();
            return { width: element.naturalWidth, height: element.naturalHeight };
        }, bytes.toString('base64'));
        if (!(size.width > 0 && size.height > 0 && size.width <= 4096 && size.height <= 4096)) {
            throw new Error('SVG score dimensions are invalid or too large.');
        }
        await page.setViewportSize(size);
        await page.locator('img').screenshot({ path: input.destination, timeout: 30000 });
    } finally {
        await browser.close();
    }
})().catch(error => {
    console.error(error.message);
    process.exitCode = 1;
});
