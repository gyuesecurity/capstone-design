// Start a static server or the app and point EVIDENCE_TEST_URL at its root.
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
let chromium;
try { ({ chromium } = require('playwright')); }
catch (_) {
  ({ chromium } = require(process.env.EVIDENCE_PLAYWRIGHT_MODULE || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')));
}

const base = process.env.EVIDENCE_TEST_URL || 'http://127.0.0.1:8177/';
const files = ['index.html', 'index_home.html?demo=1', 'Dashboard.html', 'signup.html', 'evidence_file.html', 'evidence_video.html', 'evidence_url.html', 'evidence.html', 'verification.html', 'report_select.html', 'report_preview.html'];
const zoomLevels = [0.5, 0.75, 1.25, 2, 3, 4, 5, 1];

function inspectLayout() {
  const width = document.documentElement.clientWidth;
  const issues = [];
  if (Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) > width + 2) issues.push('horizontal page overflow');

  for (const shell of document.querySelectorAll('body > .layout-shell, body > .layout-section, .layout-header')) {
    if (Math.abs(shell.getBoundingClientRect().width - width) > 2) issues.push('page section does not fill the window');
  }

  for (const element of document.querySelectorAll('a, button, [role="link"], [role="button"], input, textarea, select')) {
    const box = element.getBoundingClientRect();
    const css = getComputedStyle(element);
    if (!box.width || !box.height || css.visibility === 'hidden' || css.opacity === '0') continue;
    // Data rows intentionally scroll sideways inside their panel. Its filters and footer still need to fit.
    if (element.closest('.layout-table-row, .evidence-local-row, .report-local-row, .dashboard-recent-row, [data-report-row], .verification-table')) continue;
    const label = element.getAttribute('aria-label') || element.id || element.textContent.trim().slice(0, 30);
    if (box.left < -2 || box.right > width + 2) issues.push('off-screen control ' + label);
    for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      if (!['clip', 'hidden'].includes(getComputedStyle(parent).overflowX)) continue;
      const bounds = parent.getBoundingClientRect();
      if (box.left < bounds.left - 2 || box.right > bounds.right + 2) {
        issues.push('clipped control ' + label);
        break;
      }
    }
  }

  for (const table of document.querySelectorAll('.layout-table, .verification-table-scroll')) {
    if (table.scrollWidth <= table.clientWidth + 2) continue;
    const bounds = table.getBoundingClientRect();
    table.scrollLeft = table.scrollWidth;
    if (bounds.left < -2 || bounds.right > width + 2 || table.scrollLeft + table.clientWidth < table.scrollWidth - 2 || !['auto', 'scroll'].includes(getComputedStyle(table).overflowX)) {
      issues.push('table columns are unreachable');
    }
    table.scrollLeft = 0;
  }
  return [...new Set(issues)];
}

function textFits(element) {
  const range = document.createRange();
  range.selectNodeContents(element);
  const lines = [...range.getClientRects()];
  if (!lines.length) return false;
  for (let parent = element; parent; parent = parent.parentElement) {
    if (!['hidden', 'clip'].includes(getComputedStyle(parent).overflowX)) continue;
    const box = parent.getBoundingClientRect();
    if (lines.some(line => line.left < box.left - 1 || line.right > box.right + 1)) return false;
  }
  return true;
}

(async () => {
  const channel = process.env.EVIDENCE_BROWSER_CHANNEL || 'msedge';
  // The temporary profile lets the test use the browser's own page zoom setting.
  const context = await chromium.launchPersistentContext('', { channel, headless: true, viewport: null, args: ['--window-size=1280,900'] });
  try {
    const settings = await context.newPage();
    await settings.goto(channel === 'msedge' ? 'edge://settings/' : 'chrome://settings/');
    const page = await context.newPage();
    await page.addInitScript(() => sessionStorage.setItem('evidence-demo-mode', '1'));
    await page.route('**/api/**', route => route.fulfill({ status: 404, body: '' }));
    const failures = [];
    for (const file of files) {
      const response = await page.goto(new URL(file, base).href, { waitUntil: 'domcontentloaded' });
      assert.ok(response?.ok(), `Could not load ${file}`);
      await page.locator('body.layout-page').waitFor();
      if (file === 'signup.html') await page.locator('#name').fill('확대 축소 확인');
      const baseline = await page.evaluate(() => ({ ratio: devicePixelRatio, outer: outerWidth, inner: innerWidth }));
      for (const zoom of zoomLevels) {
        await settings.evaluate(value => new Promise((resolve, reject) => {
          chrome.settingsPrivate.setDefaultZoom(value, () => chrome.runtime.lastError ? reject(Error(chrome.runtime.lastError.message)) : resolve());
        }), zoom);
        await page.waitForFunction(expected => Math.abs(devicePixelRatio - expected) < 0.01, baseline.ratio * zoom);
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const label = `${file} at ${zoom * 100}%`;
        const actual = await page.evaluate(() => ({ outer: outerWidth, inner: innerWidth }));
        assert.equal(actual.outer, baseline.outer, label + ' changed the physical window');
        assert.ok(Math.abs(actual.inner * zoom - baseline.inner) <= 5, label + ' did not change the layout viewport');
        const issues = await page.evaluate(inspectLayout);
        if (file === 'index_home.html?demo=1') {
          const shell = page.locator('.layout-shell');
          const header = await shell.locator(':scope > header').boundingBox();
          const main = await shell.locator(':scope > main').boundingBox();
          assert.ok(header && main, label + ' header and cards must share the page container');
          assert.ok(Math.abs(header.x - main.x) < 2 && Math.abs(header.width - main.width) < 2, label + ' header and body edges differ');
          if (actual.inner > 1100) {
            const account = await shell.getByRole('link', { name: 'My page' }).boundingBox();
            const cards = await shell.locator('main .layout-grid--4').boundingBox();
            assert.ok(Math.abs(account.x + account.width - cards.x - cards.width) < 2, label + ' menu must align with the rightmost card');
          }
        }
        if (file === 'index.html') {
          const filename = page.getByText('거래 내역_2026.pdf', { exact: true }).first();
          if (!await filename.evaluate(textFits)) issues.push('sample filename is clipped');
        }
        if (file === 'evidence_video.html') {
          const chooseFile = page.getByRole('button', { name: '파일 선택', exact: true });
          const box = await chooseFile.boundingBox();
          if (!box || box.height < 1) issues.push('video file chooser has no visible height');
          else if (zoom === 5) await chooseFile.click({ trial: true });
        }
        if (issues.length) failures.push(label + ' — ' + issues.join(', '));
        if (file === 'signup.html' && zoom === 5) {
          await page.locator('#name').click();
          assert.equal(await page.evaluate(() => document.activeElement.id), 'name');
        }
      }
      assert.equal(new URL(page.url()).pathname, new URL(file, base).pathname, 'Unexpected redirect from ' + file);
      if (file === 'signup.html') assert.equal(await page.locator('#name').inputValue(), '확대 축소 확인');
    }
    assert.deepEqual(failures, []);
    console.log(`PASS: ${files.length * zoomLevels.length} native browser zoom checks (50–500% and back to 100%)`);
  } finally { await context.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
