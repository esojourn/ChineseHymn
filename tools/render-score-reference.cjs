const fs = require('node:fs');
const { chromium } = require('playwright');

process.env.PW_TEST_SCREENSHOT_NO_FONTS_READY = '1';

(async () => {
    const input = JSON.parse(fs.readFileSync(0, 'utf8'));
    if (require('playwright/package.json').version !== input.playwrightVersion) {
        throw new Error('Printed reference renderer version changed; review the raster again.');
    }
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage({ viewport: input.viewport });
        await page.setContent(fs.readFileSync(input.svgFile, 'utf8'));
        await page.locator('svg').screenshot({ path: input.imageFile, timeout: 30000 });
    } finally {
        await browser.close();
    }
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
