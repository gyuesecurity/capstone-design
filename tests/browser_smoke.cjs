const path = require('node:path');
const os = require('node:os');
let chromium;
try { ({ chromium } = require('playwright')); }
catch (_) {
  ({ chromium } = require(process.env.EVIDENCE_PLAYWRIGHT_MODULE || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')));
}
const assert = require('node:assert/strict');

const base = process.env.EVIDENCE_TEST_URL || 'http://127.0.0.1:8177/';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const email = `browser-${Date.now()}@example.com`;

  await page.goto(base + 'signup.html');
  await page.locator('#name').fill('브라우저 테스트');
  await page.locator('#email').fill(email);
  await page.locator('#send-verification-code').click();
  const note = await page.locator('#email-verification-status').innerText();
  const code = note.match(/\b\d{6}\b/)?.[0];
  assert.ok(code, note);
  await page.locator('#verification-code').fill(code);
  await page.locator('#verify-email-code').click();
  await page.locator('#password').fill('Strong!123');
  await page.locator('#password-confirm').fill('Strong!123');
  await page.locator('#signup-agreement').check();
  await page.getByRole('button', { name: '가입하기' }).click();
  await page.waitForURL('**/Dashboard.html');
  assert.ok(await page.evaluate(() => !!sessionStorage.getItem('evidence-auth-token')));

  await page.goto(base + 'evidence_file.html');
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: '파일 선택', exact: true }).click();
  await (await chooser).setFiles({ name: 'actual.txt', mimeType: 'text/plain', buffer: Buffer.from('actual bytes') });
  await page.getByRole('button', { name: '증거 등록', exact: true }).last().click();
  await page.waitForURL('**/evidence_file_complete.html?id=*');
  const evidenceId = new URL(page.url()).searchParams.get('id');
  assert.match(evidenceId, /^EV-/);

  await page.goto(base + 'evidence.html');
  await page.waitForFunction(() => document.querySelector('[data-evidence-count="total"]').textContent === '1개');
  assert.equal(await page.locator(`[data-evidence-list-row="${evidenceId}"]`).count(), 1);
  await page.goto(base + 'verification.html');
  await page.waitForFunction(() => document.querySelector('#verification-count').textContent.includes('1 / 1개'));
  await page.getByRole('checkbox', { name: 'actual.txt 선택' }).check();
  await page.locator('[data-run-verification]').first().click();
  await page.waitForFunction(() => document.querySelector('#verification-message').textContent.includes('결과 저장'));
  assert.equal(await page.locator('[data-verification-result="MATCH"]').count(), 1);

  await page.goto(base + 'Dashboard.html');
  await page.waitForFunction(() => document.querySelector('[data-dashboard-count="match"]').textContent === '1');
  await page.goto(base + 'report_select.html');
  await page.waitForFunction(() => document.querySelectorAll('.report-local-row').length === 1);
  assert.ok(await page.locator('.report-local-row').innerText().then(text => text.includes('actual.txt')));
  await page.goto(base + 'mypage.html');
  await page.waitForFunction(() => document.querySelector('[data-profile-name]').textContent === '브라우저 테스트');
  assert.equal(await page.locator('[data-profile-email]').first().innerText(), email);
  assert.equal(await page.locator('[data-mypage-stat="evidence"]').innerText(), '1');
  assert.equal(await page.locator('[data-mypage-stat="matched"]').innerText(), '1');
  assert.equal(await page.locator('[data-mypage-stat="verifications"]').innerText(), '1');
  assert.ok((await page.locator('[data-mypage-activities]').innerText()).includes('actual.txt'));
  assert.notEqual(await page.locator('[data-mypage-last-login]').innerText(), '기록 없음');
  await page.screenshot({ path: '.test-data/mypage-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 375, height: 850 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: '.test-data/mypage-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto(base + 'profile_edit.html');
  await page.waitForFunction(expected => document.querySelector('[name="email"]').value === expected, email);
  assert.equal(await page.locator('[name="name"]').inputValue(), '브라우저 테스트');
  await page.locator('[name="name"]').fill('수정한 이름');
  await page.getByRole('button', { name: '저장', exact: true }).click();
  await page.waitForURL('**/mypage.html');
  await page.waitForFunction(() => document.querySelector('[data-profile-name]').textContent === '수정한 이름');
  assert.ok((await page.locator('[data-mypage-activities]').innerText()).includes('프로필 이름 수정'));
  await page.goto(base + 'logout_confirm.html');
  await page.locator('#logout-confirm').click();
  await page.waitForURL('**/index.html');
  assert.equal(await page.evaluate(() => sessionStorage.getItem('evidence-auth-token')), null);
  await page.route('**/api/health', route => route.fulfill({ status: 404, body: 'Not Found' }));
  await page.goto(base + 'login.html');
  await page.locator('#email').fill('demo@example.com');
  await page.locator('#password').fill('Demo!123');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  const demoLink = page.getByRole('link', { name: '화면 시연 계속하기' });
  assert.equal(await demoLink.count(), 1);
  await demoLink.click();
  await page.waitForURL('**/index_home.html');
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('PASS: signup, upload, verification, dashboard, report, profile, activity, logout, static demo entry');
})().catch(error => { console.error(error); process.exitCode = 1; });
