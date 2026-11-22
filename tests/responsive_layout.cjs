// Start the app (or a static server), then set EVIDENCE_TEST_URL if needed.
// Optional: EVIDENCE_BROWSER_CHANNEL=chrome.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
let chromium;
try { ({ chromium } = require('playwright')); }
catch (_) {
  ({ chromium } = require(process.env.EVIDENCE_PLAYWRIGHT_MODULE || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')));
}

const base = process.env.EVIDENCE_TEST_URL || 'http://127.0.0.1:8177/';
const root = path.resolve(__dirname, '..');
const sizes = [
  [1920, 1080], [1366, 768], [1024, 600], [768, 1024],
  [390, 844], [320, 568], [1440, 480]
];

async function checkGeometry(page, label) {
  const issues = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const failures = [];
    if (Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) > width + 2) {
      failures.push('The page overflows horizontally');
    }
    // Tables may scroll inside their own panel; other controls must be reachable.
    for (const el of document.querySelectorAll('a, button, [role="button"], [role="link"], input, select, textarea')) {
      if (el.closest('[hidden], .layout-table, .verification-table-scroll, template')) continue;
      const box = el.getBoundingClientRect();
      const css = getComputedStyle(el);
      if (!box.width || !box.height || css.visibility === 'hidden' || css.opacity === '0') continue;
      if (box.left < -2 || box.right > width + 2) failures.push('Control outside viewport: ' + (el.getAttribute('aria-label') || el.textContent).trim().slice(0, 50));
      if (el.matches('input:not([type="checkbox"]):not([type="radio"]), select, textarea') && box.width < 40) failures.push('Input collapsed: ' + el.getAttribute('aria-label'));
    }
    const header = document.querySelector('.layout-header, .account-header');
    const content = document.querySelector('.report-main, .account-main, .mypage-main, .completion-main');
    if (header && content && content.getBoundingClientRect().top < header.getBoundingClientRect().bottom - 2) {
      failures.push('Content overlaps the header');
    }
    return failures;
  });
  assert.deepEqual(issues, [], label);
}

(async () => {
  const browser = await chromium.launch({ channel: process.env.EVIDENCE_BROWSER_CHANNEL || 'msedge', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await context.addInitScript(() => sessionStorage.setItem('evidence-demo-mode', '1'));
    await context.route(/https:\/\/fonts\./, route => route.abort());
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));

    // Exercise live upload content and long names before checking populated lists.
    await page.goto(base + 'evidence_file.html');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: '파일 선택', exact: true }).click();
    const longName = 'responsive-' + 'long-file-name-'.repeat(8) + '.txt';
    await (await chooser).setFiles({ name: longName, mimeType: 'text/plain', buffer: Buffer.from('responsive layout regression') });
    await page.locator('[data-upload-card]:not([hidden])').waitFor();
    for (const [width, height] of sizes) {
      await page.setViewportSize({ width, height });
      await checkGeometry(page, 'Selected file at ' + width);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '증거 등록', exact: true }).last().click();
    await page.waitForURL('**/evidence_file_complete.html*');
    await page.locator('.completion-card:not([hidden])').waitFor();
    await checkGeometry(page, 'Successful upload');

    let cases = 0;
    const files = fs.readdirSync(root).filter(file => file.endsWith('.html'));
    for (const file of files) {
      await page.goto(base + file, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(150);
      for (const [width, height] of sizes) {
        // Resize the same document to catch failures when returning to desktop.
        await page.setViewportSize({ width, height });
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await checkGeometry(page, file + ' at ' + width + 'x' + height);
        cases++;
      }
    }

    // A short window must still allow scrolling to the first and last form controls.
    await page.goto(base + 'signup.html');
    await page.setViewportSize({ width: 1024, height: 320 });
    await page.locator('#name').scrollIntoViewIfNeeded();
    assert.ok((await page.locator('#name').boundingBox()).y >= 0);
    await page.getByRole('button', { name: '가입하기' }).scrollIntoViewIfNeeded();
    await checkGeometry(page, 'Short signup window');
    assert.deepEqual(errors, []);
    console.log(`PASS: ${cases} live resize cases, populated evidence, long filename, upload, short-window form`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
