(function () {
  'use strict';

  const page = document.body.dataset.reportPage;
  if (!page) return;
  const DRAFT_KEY = 'evidence-report:draft:v1';
  const RESULT_KEY = 'evidence-report:result:v1';
  const GENERATION_KEY = 'evidence-report:generating:v1';
  const options = ['basic', 'preview', 'metadata', 'exif', 'hash', 'results', 'custody', 'url'];
  const optionNames = ['증거 기본 정보', '증거 미리보기', '메타데이터', 'EXIF 정보', 'SHA-256', '무결성 검증 결과', 'Chain of Custody', 'URL 수집 정보'];
  const evidence = [
    { name: 'evidence_001.html', id: 'EV-20261006-001', type: 'HTML', date: '2026/10/06 10:32', status: '정상' },
    { name: 'screenshot_001.png', id: 'EV-20261006-002', type: '이미지', date: '2026/10/06 10:21', status: '정상' },
    { name: 'evidence_video_001.mp4', id: 'EV-20261008-008', type: '영상', date: '2026/10/08 15:32', status: '정상' },
    { name: 'report_001.pdf', id: 'EV-20261005-003', type: 'PDF', date: '2026/10/05 18:40', status: '검증 필요' }
  ];
  const select = (query) => document.querySelector(query);
  const all = (query) => Array.from(document.querySelectorAll(query));
  const date = (value) => new Date(value).toLocaleDateString('sv-SE').replace(/-/g, '/');
  const defaults = () => ({ title: '디지털 증거 검증 보고서', author: '소연', project: '', description: '', selected: [0, 1], options: options.slice(), date: date(Date.now()) });

  function read(key) {
    try { return JSON.parse(sessionStorage.getItem(key)); } catch (_) { return null; }
  }

  function message(text) {
    let element = select('.report-message');
    if (!element) {
      element = document.createElement('div');
      element.className = 'report-message';
      element.setAttribute('role', 'alert');
      select('main').prepend(element);
    }
    element.textContent = text;
  }

  function write(key, value) {
    try { sessionStorage.setItem(key, JSON.stringify(value)); return true; }
    catch (_) { message('작성 내용을 저장할 수 없습니다. 브라우저의 사이트 저장소 설정을 확인해 주세요.'); return false; }
  }

  function remove(key) {
    try { sessionStorage.removeItem(key); } catch (_) { /* Storage may be disabled. */ }
  }

  function clean(value) {
    const fallback = defaults();
    if (!value || typeof value !== 'object') return fallback;
    return {
      ...fallback,
      title: typeof value.title === 'string' ? value.title.slice(0, 150) : fallback.title,
      project: typeof value.project === 'string' ? value.project.slice(0, 200) : '',
      description: typeof value.description === 'string' ? value.description.slice(0, 4000) : '',
      selected: Array.isArray(value.selected) ? [...new Set(value.selected.filter((i) => Number.isInteger(i) && evidence[i]))] : fallback.selected,
      options: Array.isArray(value.options) ? options.filter((key) => value.options.includes(key)) : fallback.options,
      date: typeof value.date === 'string' ? value.date : fallback.date,
      id: typeof value.id === 'string' ? value.id : '',
      created: typeof value.created === 'string' ? value.created : ''
    };
  }

  let draft = clean(read(DRAFT_KEY));
  const result = read(RESULT_KEY);
  const isCurrent = page === 'complete' || new URLSearchParams(location.search).get('report') === 'current';
  if (isCurrent && (!result || !result.id)) { location.replace('report.html'); return; }
  const sample = { ...defaults(), title: '디지털 증거 검증 보고서_001', id: 'RPT-20261008-001', date: '2026/10/08', created: '2026/10/08 16:02:31' };
  const report = isCurrent ? clean(result) : (page === 'detail' || page === 'list' ? sample : draft);

  function saveDraft() { return write(DRAFT_KEY, draft); }
  function valid() {
    if (!draft.selected.length) { message('보고서에 포함할 증거를 한 개 이상 선택해 주세요.'); return false; }
    if (!draft.title.trim()) {
      message('보고서 제목을 입력해 주세요.');
      select('[data-report-field="title"]')?.focus();
      return false;
    }
    if (!draft.options.length) { message('보고서에 포함할 내용을 한 개 이상 선택해 주세요.'); return false; }
    return true;
  }

  function summary(data) {
    const normal = data.selected.filter((i) => evidence[i].status === '정상').length;
    const pending = data.selected.length - normal;
    return `정상 ${normal}건 · 검증 필요 ${pending}건`;
  }

  function renderValues(data) {
    const count = data.selected.length;
    const values = {
      title: data.title, author: data.author, date: data.date,
      id: data.id || '생성 시 발급', 'id-prefixed': `보고서 ID　${data.id || '생성 시 발급'}`,
      filename: `${data.title}.pdf`, created: data.created || '', count: `${count}건`,
      selection: `선택된 증거: ${count}건`, 'selected-count': `선택 증거 ${count}건`,
      summary: summary(data), status: data.selected.some((i) => evidence[i].status !== '정상') ? '! 검증 필요 항목 포함' : '✓ 무결성 확인',
      'preview-description': `선택한 ${count}건의 증거와 보고서에 포함되는 정보를 최종 확인하세요.`,
      'document-description': `등록된 디지털 증거 ${count}건의 수집 기록과 선택한 보고서 항목을 정리합니다.`,
      'complete-description': `선택한 증거 ${count}건으로 보고서를 생성했습니다.`,
      'progress-description': `선택한 ${count}건의 증거와 무결성 검증 정보를 문서에 포함하고 있습니다.`,
      'custody-description': `증거 ${count}건의 수집 및 처리 이력을 보고서와 함께 보존합니다.`,
      'author-date': `작성자　${data.author}　　　　　　　 작성일　${data.date}`,
      footer: `EVIDENCE · ${data.id || '미리보기'}`
    };
    all('[data-report-value]').forEach((element) => { element.textContent = values[element.dataset.reportValue] ?? ''; });
  }

  function renderEvidence(data) {
    all('[data-report-preview-evidence], [data-report-detail-evidence]').forEach((card) => {
      const index = Number(card.dataset.reportPreviewEvidence ?? card.dataset.reportDetailEvidence);
      card.hidden = !data.selected.includes(index);
    });
    all('[data-report-option-section]').forEach((section) => {
      section.hidden = !data.options.includes(section.dataset.reportOptionSection);
    });
    const container = select('[data-report-detail-evidence-list]') || select('[data-report-document]');
    if (!container) return;
    data.selected.filter((i) => i > 1).forEach((i) => {
      const item = evidence[i];
      const card = document.createElement('section');
      card.className = 'report-extra-evidence';
      const title = document.createElement('strong');
      title.textContent = item.name;
      card.append(title);
      [item.id, `${item.type} · ${item.date}`, `검증 상태: ${item.status}`, '상세 메타데이터 및 전체 해시값은 증거 상세에서 확인하세요.'].forEach((text) => {
        const line = document.createElement('p'); line.textContent = text; card.append(line);
      });
      const lastPreview = container.querySelector('[data-report-preview-evidence="1"]');
      if (lastPreview) lastPreview.after(card);
      else container.append(card);
    });
    if (data.project || data.description || data.options.length !== options.length) {
      const note = document.createElement('div');
      note.className = 'report-draft-note';
      note.textContent = [data.project && `사건 또는 프로젝트명: ${data.project}`, data.description, `포함 항목: ${data.options.map((key) => optionNames[options.indexOf(key)]).join(', ')}`].filter(Boolean).join('\n');
      if (container.hasAttribute('data-report-detail-evidence-list')) container.before(note);
      else container.querySelector('[data-report-value="title"]').parentElement.append(note);
    }
  }

  renderValues(report);
  if (page === 'preview' || page === 'detail') renderEvidence(report);

  function setupPreviewNavigation() {
    if (page !== 'preview') return;
    const documentPreview = select('[data-report-document]');
    const tocHeading = all('div').find((element) => element.textContent.trim() === '목차');
    const toc = tocHeading?.parentElement;
    if (!documentPreview || !toc) return;

    const overview = select('[data-report-value="title"]')?.parentElement;
    const evidenceCard = select('[data-report-preview-evidence="0"]');
    const sections = [
      { label: '1. 보고서 개요', target: overview },
      { label: '2. 증거 기본 정보', target: evidenceCard, option: 'basic' },
      { label: '3. 증거 수집 정보', target: evidenceCard, option: 'basic' },
      { label: '4. 메타데이터', target: documentPreview.querySelector('[data-report-option-section="metadata"]'), option: 'metadata' },
      { label: '5. SHA-256 무결성 검증', target: documentPreview.querySelector('[data-report-option-section="hash"]'), option: 'hash' },
      { label: '6. Chain of Custody', target: documentPreview.querySelector('[data-report-option-section="custody"]'), option: 'custody' },
      { label: '7. 검증 결과', target: documentPreview.querySelector('[data-report-option-section="results"]'), option: 'results' },
      { label: '8. 결론', target: select('[data-report-value="footer"]')?.parentElement }
    ];
    const buttons = [];
    sections.forEach((section, index) => {
      const existing = Array.from(toc.children).find((element) => element.textContent.trim() === section.label);
      if (!existing) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'report-preview-toc';
      button.textContent = section.label;
      button.dataset.reportPreviewSection = String(index);
      button.setAttribute('aria-controls', 'report-preview-document');
      if (section.option && !report.options.includes(section.option)) button.hidden = true;
      existing.replaceWith(button);
      buttons[index] = button;
    });
    documentPreview.id = 'report-preview-document';

    const previewHeader = documentPreview.previousElementSibling;
    const counter = previewHeader && Array.from(previewHeader.children).find((element) => /1\s*\/\s*8/.test(element.textContent));
    let pageCounter = counter;
    let previous;
    let next;
    if (counter) {
      const controls = document.createElement('div');
      controls.className = 'report-preview-controls';
      previous = document.createElement('button');
      previous.type = 'button';
      previous.className = 'report-preview-control';
      previous.textContent = '이전';
      pageCounter = document.createElement('span');
      pageCounter.className = 'report-preview-page';
      next = document.createElement('button');
      next.type = 'button';
      next.className = 'report-preview-control';
      next.textContent = '다음';
      controls.append(previous, pageCounter, next);
      counter.replaceWith(controls);
    }

    let activeIndex = 0;
    function availableIndexes() {
      return sections.map((section, index) => index).filter((index) => sections[index].target && !sections[index].target.hidden);
    }
    function adjacentIndex(from, direction) {
      const available = availableIndexes();
      const position = available.indexOf(from);
      if (position < 0) return available[0] ?? from;
      return available[Math.min(Math.max(position + direction, 0), available.length - 1)];
    }
    function selectSection(index, shouldScroll) {
      const nextIndex = (index + sections.length) % sections.length;
      const section = sections[nextIndex];
      if (!section.target || section.target.hidden) return;
      sections.forEach((item) => item.target?.classList.remove('report-preview-section-focus'));
      section.target.classList.add('report-preview-section-focus');
      buttons.forEach((button, buttonIndex) => {
        if (!button) return;
        const active = buttonIndex === nextIndex;
        button.classList.toggle('report-preview-toc--active', active);
        if (active) button.setAttribute('aria-current', 'page');
        else button.removeAttribute('aria-current');
      });
      activeIndex = nextIndex;
      if (pageCounter) pageCounter.textContent = `${nextIndex + 1} / ${sections.length}`;
      const available = availableIndexes();
      if (previous) previous.disabled = nextIndex === available[0];
      if (next) next.disabled = nextIndex === available[available.length - 1];
      if (shouldScroll) section.target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    buttons.forEach((button, index) => button?.addEventListener('click', () => selectSection(index, true)));
    previous?.addEventListener('click', () => selectSection(adjacentIndex(activeIndex, -1), true));
    next?.addEventListener('click', () => selectSection(adjacentIndex(activeIndex, 1), true));
    selectSection(0, false);
  }

  setupPreviewNavigation();

  all('[data-report-new]').forEach((link) => link.addEventListener('click', (event) => {
    draft = defaults();
    remove(GENERATION_KEY);
    if (!saveDraft()) event.preventDefault();
  }));

  all('[data-report-field]').forEach((input) => {
    const key = input.dataset.reportField;
    if (key === 'search') { input.addEventListener('input', filterRows); return; }
    input.value = draft[key] || '';
    input.addEventListener('input', () => { draft[key] = input.value; saveDraft(); });
  });

  all('[data-report-evidence]').forEach((input) => {
    input.checked = draft.selected.includes(Number(input.dataset.reportEvidence));
    input.addEventListener('change', () => {
      draft.selected = all('[data-report-evidence]:checked').map((item) => Number(item.dataset.reportEvidence));
      saveDraft(); renderValues(draft);
    });
  });

  all('[data-report-option]').forEach((input) => {
    input.checked = draft.options.includes(input.dataset.reportOption);
    input.addEventListener('change', () => {
      draft.options = all('[data-report-option]:checked').map((item) => item.dataset.reportOption);
      saveDraft();
    });
    input.closest('.report-option').addEventListener('click', (event) => {
      if (!event.target.closest('.report-checkbox')) input.click();
    });
  });

  function filterRows() {
    const query = (select('[data-report-field="search"]')?.value || '').trim().toLowerCase();
    const type = select('[data-report-filter="type"]')?.value;
    const status = select('[data-report-filter="status"]')?.value;
    all('[data-report-evidence-row]').forEach((row) => {
      const item = evidence[Number(row.dataset.reportEvidenceRow)];
      row.hidden = !`${item.name} ${item.id}`.toLowerCase().includes(query) || !!(type && item.type !== type) || !!(status && item.status !== status);
    });
  }
  all('[data-report-filter]').forEach((input) => input.addEventListener('change', filterRows));
  all('[data-report-next]').forEach((link) => link.addEventListener('click', (event) => {
    if (!valid() || !saveDraft()) event.preventDefault();
  }));
  all('[data-report-generate]').forEach((link) => link.addEventListener('click', (event) => {
    if (!valid() || !saveDraft() || !write(GENERATION_KEY, { started: Date.now() })) event.preventDefault();
  }));

  if (page === 'progress') {
    if (!read(GENERATION_KEY) || !valid()) { location.replace('report_preview.html'); return; }
    let percent = 68;
    let cancelled = false;
    // Preserve the existing frontend demo generation while giving it its own route.
    const timer = window.setInterval(() => {
      if (cancelled) return;
      percent = Math.min(100, percent + 4);
      select('progress').value = percent;
      select('[data-report-percent]').textContent = `${percent}%`;
      if (percent === 100) {
        clearInterval(timer);
        const now = new Date();
        const completed = { ...draft, id: `RPT-${date(now).replace(/\//g, '')}-${now.getTime().toString().slice(-6)}`, created: `${date(now)} ${now.toLocaleTimeString('en-GB', { hour12: false })}` };
        if (write(RESULT_KEY, completed)) {
          remove(GENERATION_KEY);
          location.replace('report_complete.html');
        }
      }
    }, 400);
    select('[data-report-cancel]').addEventListener('click', () => { cancelled = true; clearInterval(timer); remove(GENERATION_KEY); });
    window.addEventListener('pagehide', () => { cancelled = true; clearInterval(timer); });
    window.addEventListener('pageshow', (event) => { if (event.persisted) location.replace('report_preview.html'); });
  }

  function downloadPdf(data) {
    // This is the existing small demonstration PDF, not a server-generated evidence report.
    const lines = ['EVIDENCE - DEMO REPORT', 'Digital Evidence Verification Report', `Report ID: ${data.id || 'preview'}`, `Included evidence: ${data.selected.length}`, ...data.selected.map((i) => evidence[i].id), `Verified samples: ${data.selected.filter((i) => evidence[i].status === '정상').length}`];
    const escapePdf = (value) => value.replace(/([\\()])/g, '\\$1');
    const stream = `BT /F1 12 Tf 72 760 Td ${lines.map((line, i) => `${i ? '0 -24 Td ' : ''}(${escapePdf(line)}) Tj`).join(' ')} ET`;
    const objects = [
      '<< /Type /Catalog /Pages 2 0 R >>',
      '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
      `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
      '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'
    ];
    let pdf = '%PDF-1.4\n';
    const offsets = [];
    objects.forEach((object, index) => { offsets.push(pdf.length); pdf += `${index + 1} 0 obj\n${object}\nendobj\n`; });
    const xref = pdf.length;
    pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    offsets.forEach((offset) => { pdf += `${String(offset).padStart(10, '0')} 00000 n \n`; });
    pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
    const url = URL.createObjectURL(new Blob([pdf], { type: 'application/pdf' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${data.title.replace(/[\\/:*?"<>|]/g, '_')}.pdf`;
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  all('[data-report-download]').forEach((button) => button.addEventListener('click', () => downloadPdf(report)));
  if (page === 'list' && result && result.id) {
    const data = clean(result);
    const row = select('[data-report-row]').cloneNode(true);
    const cells = Array.from(row.children);
    cells[0].lastElementChild.textContent = data.title;
    cells[1].textContent = `${data.selected.length}건`;
    cells[2].textContent = data.created;
    cells[3].textContent = summary(data);
    row.querySelector('a').href = 'report_detail.html?report=current';
    row.querySelector('[data-report-download]').addEventListener('click', () => downloadPdf(data));
    select('[data-report-row]').before(row);
  }
  all('[data-report-delete]').forEach((button) => button.addEventListener('click', () => {
    if (!isCurrent) { message('이 화면은 기존 보고서의 상세 보기 예시입니다.'); return; }
    if (window.confirm('이 보고서를 삭제하시겠습니까? 원본 증거는 유지됩니다.')) {
      remove(RESULT_KEY); location.href = 'report.html';
    }
  }));
})();
