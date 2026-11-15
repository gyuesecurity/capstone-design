const path = require('node:path');
const os = require('node:os');
const { readFile } = require('node:fs/promises');
let chromium;
try { ({ chromium } = require('playwright')); }
catch (_) {
  ({ chromium } = require(process.env.EVIDENCE_PLAYWRIGHT_MODULE || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')));
}
const assert = require('node:assert/strict');

const base = process.env.EVIDENCE_TEST_URL || 'http://127.0.0.1:8177/';

async function runSmoke(browser) {
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 }, timezoneId: 'America/Los_Angeles' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const email = `browser-${Date.now()}@example.com`;
  const originalName = '소연';
  const updatedName = '수정한 이름';
  const reportId = page.locator('[data-report-value="id"]');

  async function checkAuthor(expected) {
    await page.waitForFunction(name => document.querySelector('[data-report-value="author"]')?.textContent === name, expected);
  }

  async function checkSampleAuthor(expected) {
    await page.goto(base + 'report.html');
    await page.getByRole('link', { name: '예시 보고서 보기' }).click();
    await checkAuthor(expected);
  }

  async function generateFromSelection(id, author, { demo = false } = {}) {
    await page.locator(`[data-report-evidence="${id}"]`).check();
    await page.locator('[data-report-next]').click();
    await checkAuthor(author);
    const note = demo ? '비로그인 시연에서는 user로 기록됩니다.' : '보고서를 생성할 때의 프로필 이름으로 기록됩니다.';
    await page.locator('[data-report-author-note]').filter({ hasText: note }).waitFor();
    await page.locator('[data-report-next]').click();
    await page.waitForURL('**/report_contents.html');
    await page.locator('[data-report-next]').click();
    await page.locator('[data-report-value="author-date"]').filter({ hasText: author }).waitFor();
    await page.locator('[data-pdf-preview][src^="blob:"]').waitFor();
    await page.locator('[data-report-generate]').click();
    await page.waitForURL(url => url.pathname.endsWith('/report_complete.html'));
    await reportId.filter({ hasText: 'RPT-' }).waitFor();
    return await reportId.innerText();
  }

  async function uploadFile(name) {
    await page.goto(base + 'evidence_file.html');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: '파일 선택', exact: true }).click();
    await (await chooser).setFiles({ name, mimeType: 'text/plain', buffer: Buffer.from('actual bytes') });
    await page.getByRole('button', { name: '증거 등록', exact: true }).last().click();
    await page.waitForURL('**/evidence_file_complete.html?id=*');
    return new URL(page.url()).searchParams.get('id');
  }

  await page.goto(base + 'signup.html');
  await page.locator('#name').fill(originalName);
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
  await page.locator('[data-profile-greeting]').filter({ hasText: originalName }).waitFor();
  // The backend handoff may predate the report routes. A 404 must stay visible.
  await page.route('**/api/reports', route => route.fulfill({ status: 404, json: { detail: '보고서 API 준비 중입니다.' } }));
  await page.goto(base + 'report.html');
  await page.getByRole('alert').filter({ hasText: '보고서 API 준비 중입니다.' }).waitFor();
  assert.equal(await page.locator('[data-report-row]').count(), 0);
  await page.unroute('**/api/reports');

  const evidenceId = await uploadFile('actual.txt');
  assert.match(evidenceId, /^EV-/);
  await page.locator('.completion-card').getByText('서버', { exact: true }).waitFor();

  await page.goto(base + 'evidence.html');
  await page.waitForFunction(() => document.querySelector('[data-evidence-count="total"]').textContent === '1개');
  const evidenceRow = page.locator(`[data-evidence-list-row="${evidenceId}"]`);
  assert.match(await evidenceRow.locator('.evidence-id-value').innerText(), /…/);
  await evidenceRow.getByRole('button', { name: '전체 ID 보기' }).click();
  assert.equal(await evidenceRow.locator('.evidence-id-value').innerText(), evidenceId);
  assert.equal(await evidenceRow.getByRole('checkbox').isChecked(), false);
  await evidenceRow.locator('.evidence-local-detail').click();
  assert.equal(await page.locator('.evidence-dialog__details > div').filter({ hasText: '파일 선택 일시' }).locator('strong').innerText(), '기록 안 함');
  const downloadUrl = `**/api/evidence/${evidenceId}/download`;
  for (const [status, detail, message] of [
    [403, '원본 다운로드 권한이 없습니다.', '원본 다운로드 권한이 없습니다.'],
    [422, [{ msg: 'Field required' }], '입력값을 확인해 주세요.']
  ]) {
    await page.route(downloadUrl, route => route.fulfill({ status, json: { detail } }));
    await page.locator('[data-dialog-action="download"]').click();
    await page.getByRole('dialog').getByRole('alert').filter({ hasText: message }).waitFor();
    await page.unroute(downloadUrl);
  }
  await page.goto(base + 'verification.html');
  await page.waitForFunction(() => document.querySelector('#verification-count').textContent.includes('1 / 1개'));
  assert.match(await page.locator('.verification-notice').innerText(), /서버/);
  await page.getByRole('checkbox', { name: 'actual.txt 선택' }).check();
  await page.locator('[data-run-verification]').first().click();
  await page.waitForFunction(() => document.querySelector('#verification-message').textContent.includes('결과 저장'));
  assert.equal(await page.locator('#verification-results [data-verification-result="MATCH"]').count(), 1);

  await page.goto(base + 'Dashboard.html');
  await page.waitForFunction(() => document.querySelector('[data-dashboard-count="match"]').textContent === '1');
  await page.goto(base + 'report_select.html');
  await page.waitForFunction(() => document.querySelectorAll('.report-local-row').length === 1);
  assert.ok(await page.locator('.report-local-row').innerText().then(text => text.includes('actual.txt')));
  const record = await page.evaluate(id => window.EvidenceApi.get(id), evidenceId);
  const kstMinute = new Date(Date.parse(record.registeredAt) + 9 * 60 * 60 * 1000)
    .toISOString().slice(0, 16).replace(/-/g, '/').replace('T', ' ');
  assert.equal(await page.locator('.report-local-date').innerText(), kstMinute.replace(/\//g, '-'));
  // Supply only the report endpoints as fixtures. Authentication, uploads, hashes and sharing use the real local API.
  const reports = [];
  const pdfBytes = Buffer.from('%PDF-1.7\nserver-fixture\n%%EOF');
  await page.route('**/api/reports**', async route => {
    const req = route.request();
    const headers = { Authorization: req.headers().authorization };
    const me = await context.request.get(base + 'api/me', { headers });
    assert.equal(me.status(), 200);
    const user = await me.json();
    const suffix = new URL(req.url()).pathname.slice('/api/reports'.length);
    if (!suffix && req.method() === 'POST') {
      const body = req.postDataJSON();
      assert.deepEqual(Object.keys(body).sort(), ['description', 'options', 'project', 'selected', 'title']);
      assert.deepEqual(body.selected, [evidenceId]);
      const result = {
        ...body, id: `RPT-smoke-${reports.length + 1}`, authorId: user.id, author: user.name,
        date: '2026/11/03', created: '2026/11/03 08:45:31', file: { filename: '서버 보고서.pdf' },
        evidenceSnapshot: [{ ...record, date: kstMinute, statusCode: 'MATCH' }],
        summary: { MATCH: 1, MISMATCH: 0, UNAVAILABLE: 0, NOT_VERIFIED: 0 }
      };
      reports.unshift(result);
      return route.fulfill({ status: 201, json: result });
    }
    const owned = reports.filter(report => report.authorId === user.id);
    if (!suffix) return route.fulfill({ json: owned.map(report => ({
      ...report, evidenceCount: 1, listStatus: { code: 'MATCH', label: '모두 일치' }
    })) });
    const report = owned.find(item => item.id === suffix.split('/')[1]);
    if (!report) return route.fulfill({ status: 404, json: { detail: '보고서를 찾을 수 없습니다.' } });
    if (suffix.endsWith('/download')) return route.fulfill({ contentType: 'application/pdf', body: pdfBytes });
    return route.fulfill({ json: report });
  });
  const firstId = await generateFromSelection(evidenceId, originalName);
  await page.reload();
  await reportId.filter({ hasText: firstId }).waitFor();
  assert.equal(reports.length, 1);
  await page.goto(base + 'mypage.html');
  await page.waitForFunction(name => document.querySelector('[data-profile-name]').textContent === name, originalName);
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
  assert.equal(await page.locator('[name="name"]').inputValue(), originalName);
  await page.locator('[name="name"]').fill(updatedName);
  await page.getByRole('button', { name: '저장', exact: true }).click();
  await page.waitForURL('**/mypage.html');
  await page.waitForFunction(name => document.querySelector('[data-profile-name]').textContent === name, updatedName);
  assert.ok((await page.locator('[data-mypage-activities]').innerText()).includes('프로필 이름 수정'));
  await page.goto(base + 'report_detail.html?report=' + encodeURIComponent(firstId));
  await checkAuthor(originalName);
  assert.equal(await reportId.innerText(), firstId);
  const created = await page.locator('[data-report-value="created"]').innerText();
  assert.match(created, /^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}:\d{2}$/);
  const downloadEvent = page.waitForEvent('download');
  await page.locator('[data-report-download]').click();
  const pdf = await downloadEvent;
  assert.deepEqual(await readFile(await pdf.path()), pdfBytes);
  assert.equal(pdf.suggestedFilename(), '서버 보고서.pdf');
  assert.equal(await page.locator('[data-report-delete]').count(), 0);
  await page.goto(base + 'report.html');
  await page.locator('[data-report-row]').waitFor();
  await page.locator('[data-report-new]').click();
  const secondId = await generateFromSelection(evidenceId, updatedName);
  assert.notEqual(secondId, firstId);
  await page.getByRole('link', { name: '보고서 보기', exact: true }).click();
  await checkAuthor(updatedName);
  assert.equal(await reportId.innerText(), secondId);
  assert.ok(!reports.find(report => report.id === secondId).options.includes('custody'));
  assert.equal(await page.locator('[data-report-custody-events] li:visible').count(), 0);
  const otherEmail = `other-${email}`;
  const registered = await context.request.post(base + 'api/auth/register', {
    data: { name: '다른 작성자', email: otherEmail, password: 'Strong!123' }
  });
  assert.equal(registered.status(), 201);
  await page.goto(base + 'evidence.html?id=' + evidenceId);
  await page.getByRole('button', { name: '공유 관리' }).click();
  await page.getByLabel('공유받을 계정 이메일').fill(otherEmail);
  await page.getByRole('button', { name: '권한 저장' }).click();
  await page.locator('.share-list').getByText(otherEmail, { exact: false }).waitFor();

  await page.goto(base + 'logout_confirm.html');
  await page.locator('#logout-confirm').click();
  await page.waitForURL('**/index.html');
  assert.equal(await page.evaluate(() => sessionStorage.getItem('evidence-auth-token')), null);
  await page.goto(base + 'login.html');
  await page.locator('#email').fill(otherEmail);
  await page.locator('#password').fill('Strong!123');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.waitForURL('**/Dashboard.html');
  await page.waitForFunction(() => document.body.dataset.dashboardReady === 'true');
  assert.equal(await page.locator('[data-dashboard-count="match"]').innerText(), '0');
  await page.goto(base + 'evidence.html?id=' + evidenceId);
  await page.getByRole('dialog').waitFor();
  assert.equal(await page.locator('[data-dialog-action="download"], [data-dialog-action="share"]').count(), 0);
  await page.goto(base + 'verification.html');
  const sharedBox = page.getByRole('checkbox', { name: 'actual.txt 선택' });
  await sharedBox.waitFor();
  assert.equal(await sharedBox.isDisabled(), true);
  assert.equal(await page.locator('#verification-select-all').isDisabled(), true);
  await page.goto(base + 'report_detail.html?report=' + encodeURIComponent(firstId));
  await page.getByRole('alert').filter({ hasText: '보고서를 찾을 수 없습니다.' }).waitFor();
  await page.goto(base + 'report.html');
  await page.locator('[data-report-list-empty]').filter({ hasText: '없습니다' }).waitFor();
  // Enter the demo while this account is still signed in.
  assert.ok(await page.evaluate(() => sessionStorage.getItem('evidence-auth-token')));
  await page.goto(base + 'login.html');
  const demoLink = page.getByRole('link', { name: '화면 시연 계속하기' });
  await demoLink.click();
  await page.waitForURL('**/index_home.html?demo=1');
  assert.equal(await page.evaluate(() => sessionStorage.getItem('evidence-auth-token')), null);
  await page.goto(base + 'mypage.html');
  await page.waitForFunction(() => document.querySelector('[data-profile-name]').textContent === 'user');
  await page.goto(base + 'report_detail.html?report=current');
  await page.waitForURL('**/report.html');
  await checkSampleAuthor('user');
  const localId = await uploadFile('offline.txt');
  await page.locator('.completion-card').getByText('현재 브라우저', { exact: true }).waitFor();
  await page.goto(base + 'evidence.html?id=' + encodeURIComponent(localId));
  const selectedTime = page.locator('.evidence-dialog__details > div').filter({ hasText: '파일 선택 일시' }).locator('strong');
  assert.match(await selectedTime.innerText(), /\d{4}/);
  await page.evaluate(id => {
    localStorage.setItem('evidence-profile-name', '이전 로그인 이름');
    sessionStorage.setItem('evidence-report:draft:v2:demo', JSON.stringify({ author: '이전 초안 작성자', selected: [id] }));
  }, localId);
  await page.goto(base + 'report_select.html');
  const demoReportId = await generateFromSelection(localId, 'user', { demo: true });
  await page.getByRole('link', { name: '보고서 보기', exact: true }).click();
  await checkAuthor('user');
  assert.equal(await reportId.innerText(), demoReportId);
  await page.goto(base + 'login.html');
  await page.locator('#email').fill(email);
  await page.locator('#password').fill('Strong!123');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.waitForURL('**/Dashboard.html');
  await page.goto(base + 'report.html');
  await page.waitForFunction(() => document.querySelectorAll('[data-report-row]').length === 2);
  await page.goto(base + 'report_detail.html?report=' + encodeURIComponent(secondId));
  await checkAuthor(updatedName);
  assert.equal(await reportId.innerText(), secondId);
  assert.deepEqual(errors, []);
  console.log('PASS: signup, upload, verification, dashboard, report, profile, activity, logout, static demo entry');
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try { await runSmoke(browser); }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
