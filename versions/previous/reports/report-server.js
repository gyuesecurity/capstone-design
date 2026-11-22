// Authenticated report API. Browser-only reports use the existing demo workflow.
export const draftOptions = ['basic', 'metadata', 'hash', 'results'];

export function reportUrl(page, id) {
  return `report_${page}.html?report=${encodeURIComponent(id)}`;
}

function reportPath(id) { return '/api/reports/' + encodeURIComponent(id); }

function checkedReport(report, expectedId) {
  if (typeof report?.id !== 'string' || !report.id || (expectedId && report.id !== expectedId) ||
      typeof report.title !== 'string' || typeof report.author !== 'string' ||
      !Array.isArray(report.selected) || !Array.isArray(report.evidenceSnapshot) || !Array.isArray(report.options)) {
    throw new Error('서버의 보고서 정보를 확인할 수 없습니다.');
  }
  return report;
}

export async function loadDetail(id) {
  if (!id || id === 'current') throw new Error('보고서 목록에서 확인할 보고서를 선택해 주세요.');
  const response = await window.EvidenceApi.request(reportPath(id));
  return checkedReport(await response.json(), id);
}

export async function loadList() {
  const response = await window.EvidenceApi.request('/api/reports');
  const list = await response.json();
  if (!Array.isArray(list) || list.some(row => typeof row?.id !== 'string' || !row.id || !row.listStatus)) {
    throw new Error('서버의 보고서 목록을 확인할 수 없습니다.');
  }
  return list;
}

export async function downloadPdf(id) {
  const response = await window.EvidenceApi.request(reportPath(id) + '/download');
  if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/pdf') {
    throw new Error('서버에서 PDF 파일을 받지 못했습니다.');
  }
  const blob = await response.blob();
  if (!blob.size) throw new Error('서버에서 빈 PDF 파일을 받았습니다.');
  return blob;
}

export async function generate(draft, userId, originalTicket, saveTicket, saveResult) {
  const link = document.querySelector('[data-report-cancel]');
  const status = document.querySelector('.report-progress-status');
  const meter = document.querySelector('progress');
  const percent = document.querySelector('[data-report-percent]');
  const heading = document.querySelector('.report-progress-heading h2');
  const panel = document.querySelector('.report-progress-card');
  let ticket = originalTicket;
  let busy = false;
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'report-retry';
  retry.dataset.reportRetry = 'generation';
  retry.hidden = true;
  link.parentElement.prepend(retry);
  const candidates = document.createElement('ul');
  candidates.className = 'report-recovery-list';
  candidates.dataset.reportGenerationResults = '';
  status.after(candidates);
  link.textContent = '보고서 목록';
  link.href = 'report.html';
  percent.hidden = true;

  function describe(text, error = false) {
    status.textContent = text;
    status.setAttribute('role', error ? 'alert' : 'status');
    status.classList.toggle('report-operation-error', error);
    status.dataset.reportOperationState = error ? 'error' : busy ? 'busy' : 'ready';
  }
  function setBusy(value) {
    busy = value;
    retry.disabled = value;
    panel.setAttribute('aria-busy', String(value));
  }
  function offer(label, action) {
    retry.textContent = label;
    retry.hidden = false;
    retry.onclick = () => { if (!busy) action(); };
  }

  async function checkResults() {
    if (busy) return;
    setBusy(true);
    heading.textContent = '생성 결과 확인';
    describe('보고서 목록에서 생성 결과를 다시 확인하고 있습니다.');
    try {
      const list = await loadList();
      candidates.replaceChildren();
      for (const item of list) {
        const row = document.createElement('li');
        const anchor = document.createElement('a');
        anchor.href = reportUrl('detail', item.id);
        anchor.textContent = [item.title || item.id, item.created || item.createdAt].filter(Boolean).join(' · ');
        row.append(anchor);
        candidates.append(row);
      }
      describe(list.length
        ? '아래 보고서에서 생성 여부를 확인해 주세요. 중복 생성을 막기 위해 새 생성 요청은 보내지 않았습니다.'
        : '현재 목록에서 생성 결과를 찾지 못했습니다. 아직 처리 중일 수 있으므로 잠시 후 다시 확인해 주세요.', true);
    } catch (error) {
      describe('생성 결과를 조회하지 못했습니다. ' + error.message + ' 새 생성 요청은 보내지 않았습니다.', true);
    } finally { setBusy(false); offer('생성 결과 다시 확인', checkResults); }
  }

  async function run() {
    if (busy) return;
    if (ticket.reportId) { location.replace(reportUrl('complete', ticket.reportId)); return; }
    if (ticket.sent) {
      meter.hidden = true;
      heading.textContent = '생성 결과 확인 필요';
      describe('이미 생성 요청을 보냈지만 결과를 확인하지 못했습니다. 중복 생성을 막기 위해 보고서 목록을 먼저 확인해 주세요.', true);
      offer('생성 결과 다시 확인', checkResults);
      return;
    }
    setBusy(true);
    retry.hidden = true;
    candidates.replaceChildren();
    meter.hidden = false;
    meter.removeAttribute('value');
    heading.textContent = 'PDF 보고서 생성 중…';
    describe('로그인 상태와 보고서 정보를 확인하고 있습니다.');
    let posted = false;
    try {
      const current = await window.EvidenceApi.profile(true);
      if (current?.id !== userId) throw new Error('로그인 계정이 변경되었습니다. 원래 계정으로 로그인하거나 보고서를 다시 작성해 주세요.');
      const nextTicket = { ...ticket, sent: true };
      if (!saveTicket(nextTicket)) throw new Error('생성 요청 정보를 저장하지 못했습니다. 브라우저 저장소 설정을 확인한 뒤 다시 시도해 주세요.');
      ticket = nextTicket;
      describe('서버에서 PDF를 생성하고 있습니다.');
      const { title, project, description, selected, options } = draft;
      // Once sent, a missing response does not prove that creation failed.
      posted = true;
      const response = await window.EvidenceApi.request('/api/reports', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, project, description, selected, options })
      });
      const report = checkedReport(await response.json());
      ticket = { ...ticket, reportId: report.id, sent: true };
      saveTicket(ticket);
      saveResult(report);
      location.replace(reportUrl('complete', report.id));
    } catch (error) {
      meter.hidden = true;
      heading.textContent = '보고서 생성 확인';
      const definiteFailure = !posted || (error.status >= 400 && error.status < 500 && error.status !== 408);
      if (definiteFailure) {
        ticket = { ...ticket, sent: false };
        saveTicket(ticket);
        describe(error.message + ' 작성 내용은 유지됩니다. 다시 시도하거나 미리보기에서 내용을 확인해 주세요.', true);
        offer('다시 시도', run);
        link.href = 'report_preview.html';
        link.textContent = '미리보기로 돌아가기';
      } else {
        describe(error.message + ' 생성 여부를 확인하지 못했습니다. 새 요청을 보내기 전에 결과를 확인해 주세요.', true);
        offer('생성 결과 다시 확인', checkResults);
        link.href = 'report.html';
        link.textContent = '보고서 목록';
      }
    } finally { setBusy(false); }
  }
  await run();
}
