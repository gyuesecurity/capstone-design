(function () {
  'use strict';

  const page = document.body.dataset.reportPage;
  if (!page) return;
  const DRAFT_KEY = 'evidence-report:draft:v1';
  const RESULT_KEY = 'evidence-report:result:v1';
  const GENERATION_KEY = 'evidence-report:generating:v1';
  const options = ['basic', 'preview', 'metadata', 'exif', 'hash', 'results', 'custody', 'url'];
  const optionNames = ['증거 기본 정보', '증거 미리보기', '메타데이터', 'EXIF 정보', 'SHA-256', '무결성 검증 결과', 'Chain of Custody', 'URL 수집 정보'];
  let evidence = [];
  const statusNames = { NOT_VERIFIED: '미검증', MATCH: '일치', MISMATCH: '불일치', UNAVAILABLE: '검증 불가' };
  const select = (query) => document.querySelector(query);
  const all = (query) => Array.from(document.querySelectorAll(query));
  const date = (value) => new Date(value).toLocaleDateString('sv-SE').replace(/-/g, '/');
  const defaults = () => ({ title: '디지털 증거 검증 보고서', author: '소연', project: '', description: '', selected: [], options: options.slice(), date: date(Date.now()) });

  function formatDate(value) {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? '기록 없음' : `${date(parsed)} ${parsed.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`;
  }

  function typeName(record) {
    if (record.mime?.startsWith('image/')) return '이미지';
    if (record.mime?.startsWith('video/')) return '영상';
    const type = String(record.type || '').toUpperCase();
    if (['PNG', 'JPG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'SVG'].includes(type)) return '이미지';
    if (['MP4', 'MOV', 'WEBM', 'AVI'].includes(type)) return '영상';
    return type || '기타';
  }

  function storedEvidence(record) {
    const statusCode = statusNames[record.verificationStatus] ? record.verificationStatus : 'NOT_VERIFIED';
    return {
      id: record.id, name: record.name || record.filename || record.id, filename: record.filename || '',
      type: typeName(record), date: formatDate(record.registeredAt), registeredAt: record.registeredAt, statusCode,
      status: statusNames[statusCode], size: record.size, mime: record.mime || '',
      sha256: record.sha256 || '', source: record.source || '',
      lastVerification: record.lastVerification || null
    };
  }

  async function loadStoredEvidence() {
    if (window.EvidenceApi && await window.EvidenceApi.enabled()) {
      return (await window.EvidenceApi.list()).map(storedEvidence).sort((a, b) => b.registeredAt.localeCompare(a.registeredAt));
    }
    return new Promise((resolve, reject) => {
      const opening = indexedDB.open('evidence-local-files', 1);
      opening.onupgradeneeded = () => {
        const db = opening.result;
        if (!db.objectStoreNames.contains('records')) db.createObjectStore('records', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('files')) db.createObjectStore('files');
      };
      opening.onerror = () => reject(opening.error);
      opening.onblocked = () => reject(new Error('브라우저 저장소를 열 수 없습니다.'));
      opening.onsuccess = () => {
        const db = opening.result;
        db.onversionchange = () => db.close();
        const transaction = db.transaction('records', 'readonly');
        const request = transaction.objectStore('records').getAll();
        transaction.oncomplete = () => { db.close(); resolve(request.result.map(storedEvidence).sort((a, b) => b.registeredAt.localeCompare(a.registeredAt))); };
        transaction.onabort = () => { db.close(); reject(transaction.error || new Error('증거 목록을 읽을 수 없습니다.')); };
      };
    });
  }

  async function boot() {
    try { evidence = await loadStoredEvidence(); }
    catch (error) { message(`등록된 증거를 읽지 못했습니다: ${error.message}`); return; }

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
      selected: Array.isArray(value.selected) ? [...new Set(value.selected.filter((id) => typeof id === 'string' && (evidence.some((item) => item.id === id) || value.evidenceSnapshot?.some((item) => item.id === id))))] : fallback.selected,
      options: Array.isArray(value.options) ? options.filter((key) => value.options.includes(key)) : fallback.options,
      date: typeof value.date === 'string' ? value.date : fallback.date,
      id: typeof value.id === 'string' ? value.id : '',
      created: typeof value.created === 'string' ? value.created : '',
      evidenceSnapshot: Array.isArray(value.evidenceSnapshot) ? value.evidenceSnapshot.filter((item) => item && typeof item.id === 'string') : []
    };
  }

  let draft = clean(read(DRAFT_KEY));
  const result = read(RESULT_KEY);
  const isCurrent = page === 'complete' || new URLSearchParams(location.search).get('report') === 'current';
  if (isCurrent && (!result || !result.id)) { location.replace('report.html'); return; }
  const sampleEvidence = [
    { id: 'EV-20261006-001', name: 'evidence_001.html', filename: 'evidence_001.html', type: 'HTML', date: '2026/10/06 10:32', statusCode: 'MATCH', status: '일치' },
    { id: 'EV-20261006-002', name: 'screenshot_001.png', filename: 'screenshot_001.png', type: '이미지', date: '2026/10/06 10:21', statusCode: 'MATCH', status: '일치' }
  ];
  const sample = { ...defaults(), title: '디지털 증거 검증 보고서_001', id: 'RPT-20261008-001', date: '2026/10/08', created: '2026/10/08 16:02:31', selected: sampleEvidence.map((item) => item.id), evidenceSnapshot: sampleEvidence };
  const report = isCurrent ? clean(result) : (page === 'detail' || page === 'list' ? sample : draft);
  if (isCurrent && page === 'detail') {
    const heading = select('[role="heading"][aria-level="1"]');
    if (heading) heading.textContent = '보고서 상세 보기';
  }

  function itemsFor(data) {
    const source = data.evidenceSnapshot?.length ? data.evidenceSnapshot : evidence;
    return data.selected.map((id) => source.find((item) => item.id === id)).filter(Boolean);
  }

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
    const counts = itemsFor(data).reduce((result, item) => { result[item.statusCode] = (result[item.statusCode] || 0) + 1; return result; }, {});
    return `일치 ${counts.MATCH || 0}건 · 미검증 ${counts.NOT_VERIFIED || 0}건 · 불일치 ${counts.MISMATCH || 0}건 · 검증 불가 ${counts.UNAVAILABLE || 0}건`;
  }

  function renderValues(data) {
    const count = data.selected.length;
    const values = {
      title: data.title, author: data.author, date: data.date,
      id: data.id || '생성 시 발급', 'id-prefixed': `보고서 ID　${data.id || '생성 시 발급'}`,
      filename: `${data.title}.pdf`, created: data.created || '', count: `${count}건`,
      selection: `선택된 증거: ${count}건`, 'selected-count': `선택 증거 ${count}건`,
      summary: summary(data), status: itemsFor(data).every((item) => item.statusCode === 'MATCH') && count ? '✓ 모두 일치' : '검증 상태 확인 필요',
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
    const container = select('[data-report-detail-evidence-list]') || select('[data-report-document]');
    if (!container) return;
    const preview = container.hasAttribute('data-report-document');
    container.querySelectorAll('[data-report-preview-evidence], [data-report-detail-evidence]').forEach((card) => card.remove());
    if (preview) {
      Array.from(container.children).filter((child) => ['hash', 'custody'].includes(child.dataset.reportOptionSection)).forEach((child) => child.remove());
    }
    const items = itemsFor(data);
    const resultSection = preview ? Array.from(container.children).find((child) => child.dataset.reportOptionSection === 'results') : null;
    items.forEach((item, index) => {
      const card = document.createElement('section');
      card.className = 'report-extra-evidence';
      card.dataset.status = item.statusCode;
      card.dataset[preview ? 'reportPreviewEvidence' : 'reportDetailEvidence'] = String(index);
      const heading = document.createElement('strong');
      heading.textContent = item.name;
      card.append(heading);
      const addLine = (text, option) => {
        const line = document.createElement('p');
        line.textContent = text;
        if (option) line.dataset.reportOptionSection = option;
        card.append(line);
      };
      addLine(`증거 ID: ${item.id} · 원본 파일: ${item.filename || item.name}`, 'basic');
      addLine(`${item.type} · ${formatSize(item.size)} · 수집 일시 ${item.date}`, 'basic');
      addLine(`검증 상태: ${item.status}${item.lastVerification?.completedAt ? ` · 마지막 검증 ${formatDate(item.lastVerification.completedAt)}` : ''}`, 'results');
      addLine(`수집 출처: ${item.source || '기록 없음'} · MIME: ${item.mime || '기록 없음'}`, 'metadata');
      addLine(`등록 SHA-256: ${item.sha256 || '기록 없음'}`, 'hash');
      addLine(`검증 SHA-256: ${item.lastVerification?.currentHash || '검증 기록 없음'}`, 'hash');
      if (item.lastVerification?.reason) addLine(`검증 결과 설명: ${item.lastVerification.reason}`, 'results');
      if (preview) container.insertBefore(card, resultSection);
      else container.append(card);
    });
    if (!items.length) {
      const empty = document.createElement('p');
      empty.className = 'report-empty';
      empty.textContent = '선택한 증거가 없습니다.';
      if (preview) container.insertBefore(empty, resultSection);
      else container.append(empty);
    }
    all('[data-report-option-section]').forEach((section) => {
      section.hidden = !data.options.includes(section.dataset.reportOptionSection);
    });
    if (data.project || data.description || data.options.length !== options.length) {
      const note = document.createElement('div');
      note.className = 'report-draft-note';
      note.textContent = [data.project && `사건 또는 프로젝트명: ${data.project}`, data.description, `포함 항목: ${data.options.map((key) => optionNames[options.indexOf(key)]).join(', ')}`].filter(Boolean).join('\n');
      if (container.hasAttribute('data-report-detail-evidence-list')) container.before(note);
      else container.querySelector('[data-report-value="title"]').parentElement.append(note);
    }
  }

  function formatSize(bytes) {
    if (!Number.isFinite(bytes)) return '크기 기록 없음';
    return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
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

  function renderSelectionRows() {
    if (page !== 'select') return;
    const marker = select('[data-report-evidence-placeholder]');
    const parent = marker?.parentElement;
    if (!parent) return;
    const typeFilter = select('[data-report-filter="type"]');
    const statusFilter = select('[data-report-filter="status"]');
    typeFilter.replaceChildren(new Option('전체 유형', ''), ...[...new Set(evidence.map((item) => item.type))].sort().map((type) => new Option(type, type)));
    statusFilter.replaceChildren(new Option('전체 상태', ''), ...Object.entries(statusNames).map(([code, label]) => new Option(label, code)));
    evidence.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'report-local-row';
      row.dataset.reportEvidenceRow = item.id;
      const selection = document.createElement('div');
      selection.className = 'report-local-select';
      const label = document.createElement('label');
      label.className = 'report-checkbox';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.reportEvidence = item.id;
      checkbox.setAttribute('aria-label', item.name);
      checkbox.checked = draft.selected.includes(item.id);
      const mark = document.createElement('span');
      mark.className = 'report-checkbox-mark';
      const image = document.createElement('img');
      image.alt = '';
      image.src = 'assets/report/b1e8d.svg';
      mark.append(image); label.append(checkbox, mark); selection.append(label);
      const name = document.createElement('div');
      name.className = 'report-local-name';
      const title = document.createElement('strong'); title.textContent = item.name;
      name.append(title);
      if (item.filename && item.filename !== item.name) { const filename = document.createElement('small'); filename.textContent = item.filename; name.append(filename); }
      const addCell = (value, className) => { const cell = document.createElement('div'); cell.className = className; cell.textContent = value; row.append(cell); };
      row.append(selection, name);
      addCell(item.type, 'report-local-type');
      addCell(item.date, 'report-local-date');
      addCell(item.id, 'report-local-id');
      const badge = document.createElement('span');
      badge.className = 'report-local-badge';
      badge.dataset.status = item.statusCode;
      badge.textContent = item.status;
      row.append(badge);
      parent.insertBefore(row, marker);
    });
    if (!evidence.length) {
      marker.textContent = '등록된 증거가 없습니다. 증거 관리에서 파일을 등록한 뒤 다시 확인해 주세요.';
    } else {
      marker.remove();
    }
  }

  renderSelectionRows();

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
    input.checked = draft.selected.includes(input.dataset.reportEvidence);
    input.addEventListener('change', () => {
      draft.selected = all('[data-report-evidence]:checked').map((item) => item.dataset.reportEvidence);
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
      const item = evidence.find((record) => record.id === row.dataset.reportEvidenceRow);
      row.hidden = !`${item.name} ${item.filename} ${item.id}`.toLowerCase().includes(query) || !!(type && item.type !== type) || !!(status && item.statusCode !== status);
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
        const completed = { ...draft, evidenceSnapshot: itemsFor(draft), id: `RPT-${date(now).replace(/\//g, '')}-${now.getTime().toString().slice(-6)}`, created: `${date(now)} ${now.toLocaleTimeString('en-GB', { hour12: false })}` };
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
    const lines = ['EVIDENCE - LOCAL REPORT', 'Digital Evidence Verification Report', `Report ID: ${data.id || 'preview'}`, `Included evidence: ${data.selected.length}`, ...itemsFor(data).flatMap((item) => [`Evidence: ${item.id}`, `File: ${item.filename || item.name}`, `SHA-256: ${item.sha256 || 'unavailable'}`, `Verification: ${item.statusCode}`])];
    const escapePdf = (value) => value.replace(/[^\x20-\x7E]/g, '?').replace(/([\\()])/g, '\\$1');
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
    cells[1].textContent = `${itemsFor(data).length}건`;
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
  }
  boot().catch((error) => message(`보고서를 표시할 수 없습니다: ${error.message}`));
})();
