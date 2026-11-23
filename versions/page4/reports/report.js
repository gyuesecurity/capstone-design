(async function () {
  'use strict';

  const page = document.body.dataset.reportPage;
  if (!page) return;
  const savedPage = page === 'complete' || page === 'detail';
  const options = ['basic', 'preview', 'metadata', 'exif', 'hash', 'results', 'custody', 'url'];
  const pendingOptions = new Set(['exif', 'custody']);
  const defaultOptions = options.filter(key => !pendingOptions.has(key));
  const optionNames = ['증거 기본 정보', '증거 미리보기', '메타데이터', 'EXIF 정보', 'SHA-256', '무결성 검증 결과', 'Chain of Custody', 'URL 수집 정보'];
  const statusNames = { NOT_VERIFIED: '미검증', MATCH: '일치', MISMATCH: '불일치', UNAVAILABLE: '검증 불가' };
  const select = (query) => document.querySelector(query);
  const all = (query) => Array.from(document.querySelectorAll(query));
  const timeZone = 'Asia/Seoul';
  const date = (value) => new Date(value).toLocaleDateString('sv-SE', { timeZone }).replace(/-/g, '/');
  const defaults = () => ({ title: '디지털 증거 검증 보고서', author: '', project: '', description: '', selected: [], options: defaultOptions.slice(), date: date(Date.now()) });

  function formatDate(value, withSeconds = false) {
    if (value == null || value === '') return '기록 없음';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '기록 없음';
    const time = parsed.toLocaleTimeString('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', second: withSeconds ? '2-digit' : undefined });
    return `${date(parsed)} ${time}`;
  }

  function typeName(record) {
    if (record.mime?.startsWith('image/')) return '이미지';
    if (record.mime?.startsWith('video/')) return '영상';
    const type = String(record.type || '').toUpperCase();
    if (['PNG', 'JPG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'SVG'].includes(type)) return '이미지';
    if (['MP4', 'MOV', 'WEBM', 'AVI'].includes(type)) return '영상';
    return type || '기타';
  }

  // Reports saved by earlier browser versions stored the custody reference as text.
  function normalizeCustody(item) {
    if (!Object.hasOwn(item, 'custodyAnchor')) return item;
    const { custodyAnchor, custodySnapshot = custodyAnchor || null, ...record } = item;
    return { ...record, custodySnapshot };
  }

  // Keep display metadata for live rows separate from the saved report evidence snapshot.
  const registrationById = new Map();
  function sortedEvidence(items) {
    return items.map(storedEvidence).sort((a, b) => registrationById.get(b.id).timestamp - registrationById.get(a.id).timestamp);
  }

  function storedEvidence(item) {
    const record = normalizeCustody(item);
    registrationById.set(record.id, window.EvidenceListDisplay.registrationTime(record));
    const statusCode = statusNames[record.verificationStatus] ? record.verificationStatus : 'NOT_VERIFIED';
    return {
      id: record.id, name: record.name || record.filename || record.id, filename: record.filename || '',
      type: typeName(record), date: formatDate(record.registeredAt), registeredAt: record.registeredAt, statusCode,
      status: statusNames[statusCode], size: record.size, mime: record.mime || '',
      sha256: record.sha256 || '', source: record.source || '',
      registeredBy: record.registeredBy || null,
      custodySnapshot: record.custodySnapshot ?? null,
      lastVerification: record.lastVerification || null, isOwner: !record.access || record.access.isOwner === true
    };
  }

  async function loadStoredEvidence(profile) {
    if (profile) {
      return sortedEvidence((await window.EvidenceApi.list('all')).filter(item => item.access?.canView !== false));
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
        transaction.oncomplete = () => { db.close(); resolve(sortedEvidence(request.result)); };
        transaction.onabort = () => { db.close(); reject(transaction.error || new Error('증거 목록을 읽을 수 없습니다.')); };
      };
    });
  }

  function message(text, retry, label = '다시 시도', busy = false) {
    let element = select('.report-message');
    if (!element) {
      element = document.createElement('div');
      element.className = 'report-message';
      select('main').prepend(element);
    }
    element.hidden = !text;
    element.setAttribute('role', busy ? 'status' : 'alert');
    element.setAttribute('aria-busy', String(busy));
    element.classList.toggle('report-message--busy', busy);
    const description = document.createElement('p');
    description.textContent = text;
    element.replaceChildren(description);
    if (retry) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'report-retry';
      button.dataset.reportRetry = 'operation';
      button.textContent = label;
      button.addEventListener('click', async () => {
        if (button.disabled) return;
        button.disabled = true;
        element.setAttribute('aria-busy', 'true');
        try { await retry(); }
        catch (error) { message(error.message, retry, label); }
        finally {
          if (button.isConnected) button.disabled = false;
          element.setAttribute('aria-busy', 'false');
        }
      });
      element.append(button);
    }
  }

  function lockReportControls(nodes) {
    const snapshots = Array.from(new Set(nodes)).map(node => ({ node, disabled: node.disabled, aria: node.getAttribute('aria-disabled'), tab: node.getAttribute('tabindex') }));
    const block = event => {
      if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
      event.preventDefault(); event.stopImmediatePropagation();
    };
    snapshots.forEach(({ node }) => {
      if ('disabled' in node) node.disabled = true;
      node.setAttribute('aria-disabled', 'true');
      node.classList.add('report-control-busy');
      node.addEventListener('click', block, true);
      node.addEventListener('keydown', block, true);
    });
    return () => snapshots.forEach(({ node, disabled, aria, tab }) => {
      if ('disabled' in node) node.disabled = disabled;
      if (aria === null) node.removeAttribute('aria-disabled'); else node.setAttribute('aria-disabled', aria);
      if (tab === null) node.removeAttribute('tabindex'); else node.setAttribute('tabindex', tab);
      node.classList.remove('report-control-busy');
      node.removeEventListener('click', block, true);
      node.removeEventListener('keydown', block, true);
    });
  }

  function write(key, value) {
    try { sessionStorage.setItem(key, JSON.stringify(value)); return true; }
    catch (_) { message('작성 내용을 저장할 수 없습니다. 브라우저의 사이트 저장소 설정을 확인해 주세요.'); return false; }
  }

  function remove(key) {
    try { sessionStorage.removeItem(key); } catch (_) { /* Storage may be disabled. */ }
  }

  function read(key) {
    try { return JSON.parse(sessionStorage.getItem(key)); } catch (_) { return null; }
  }

  let profile;
  let server;
  let evidence = [];
  let draftAuthor;
  const screen = select('[data-report-screen]');
  if (savedPage) screen.hidden = true;
  // Bind to the current user and draft before accepting input or navigation.
  screen.inert = true;
  screen.setAttribute('aria-busy', 'true');
  try {
    profile = await window.EvidenceApi.profile();
    if (profile) server = await import('./report-server.js?v=operation-feedback-1');
    draftAuthor = profile?.name ?? 'user';
    if (page === 'info') {
      select('[data-report-value="author"]').textContent = draftAuthor;
      select('[data-report-author-note]').textContent = profile
        ? '보고서를 생성할 때의 프로필 이름으로 기록됩니다.'
        : '비로그인 시연에서는 user로 기록됩니다.';
    }
    if (!server || (!savedPage && !['list', 'progress'].includes(page))) evidence = await loadStoredEvidence(profile);
  } catch (error) {
    if (screen) screen.hidden = true;
    message(error.message, () => location.reload(), '다시 불러오기');
    return;
  }
  const scope = profile ? profile.id : 'demo';
  const DRAFT_KEY = `evidence-report:draft:v2:${scope}`;
  const RESULT_KEY = `evidence-report:result:v2:${scope}`;
  const GENERATION_KEY = `evidence-report:generating:v2:${scope}`;

  function clean(value) {
    // Preserve the interpretation of historical reports; draft filtering happens separately.
    const fallback = { ...defaults(), options: options.slice() };
    if (!value || typeof value !== 'object') return fallback;
    const snapshot = Array.isArray(value.evidenceSnapshot)
      ? value.evidenceSnapshot.filter((item) => item && typeof item.id === 'string').map(normalizeCustody) : [];
    const availableIds = new Set([...evidence, ...snapshot].map((item) => item.id));
    return {
      ...fallback,
      author: profile ? (typeof value.author === 'string' ? value.author.trim() : '') : 'user',
      legacyTime: value.legacyTime === true,
      title: typeof value.title === 'string' ? value.title.slice(0, 150) : fallback.title,
      project: typeof value.project === 'string' ? value.project.slice(0, 200) : '',
      description: typeof value.description === 'string' ? value.description.slice(0, 4000) : '',
      selected: Array.isArray(value.selected) ? [...new Set(value.selected.filter((id) => availableIds.has(id)))] : fallback.selected,
      options: Array.isArray(value.options) ? options.filter((key) => value.options.includes(key)) : fallback.options,
      date: typeof value.date === 'string' ? value.date : fallback.date,
      id: typeof value.id === 'string' ? value.id : '',
      created: typeof value.created === 'string' ? value.created : '',
      evidenceSnapshot: snapshot
    };
  }

  // Old drafts and results had no owner. Only adopt ones tied entirely to this user's own evidence.
  function migrateLegacy(key, legacyKey, isResult) {
    if (read(key)) return;
    const legacy = read(legacyKey);
    if (!Array.isArray(legacy?.selected) || !legacy.selected.length) return;
    const ownIds = new Set(evidence.filter((item) => item.isOwner).map((item) => item.id));
    if (!legacy.selected.every((id) => ownIds.has(id))) return;
    const data = clean(legacy);
    data.author = profile ? '' : 'user'; // Legacy account reports did not record the author's real name.
    data.evidenceSnapshot = data.evidenceSnapshot.filter((item) => data.selected.includes(item.id));
    data.legacyTime = isResult;
    if (!isResult) data.date = date(Date.now());
    if (isResult && !data.id) return;
    if (write(key, data)) remove(legacyKey);
  }
  migrateLegacy(DRAFT_KEY, 'evidence-report:draft:v1', false);
  if (!server) migrateLegacy(RESULT_KEY, 'evidence-report:result:v1', true);
  function availableDraftOptions(value) {
    const selected = Array.isArray(value) ? value : defaultOptions;
    return options.filter(key => !pendingOptions.has(key) && selected.includes(key) && (!server || server.draftOptions.includes(key)));
  }
  const storedDraft = read(DRAFT_KEY);
  let draft = { ...clean(storedDraft), author: draftAuthor };
  draft.options = availableDraftOptions(draft.options);
  if (server) {
    draft.options = draft.options.filter(option => server.draftOptions.includes(option));
    // On the progress route the server validates the actual selection at creation time.
    if (page === 'progress') draft.selected = read(DRAFT_KEY)?.selected || [];
  }
  // Only repair an in-progress draft. Saved results and server snapshots keep their original options.
  if (!savedPage && page !== 'list' && storedDraft && JSON.stringify(storedDraft.options) !== JSON.stringify(draft.options)) {
    write(DRAFT_KEY, { ...storedDraft, options: draft.options });
  }
  const search = new URLSearchParams(location.search);
  const requestedId = search.get('report') || search.get('reportId') || search.get('id');
  const isCurrent = savedPage && (server || page === 'complete' || requestedId === 'current');
  let result = server ? null : read(RESULT_KEY);
  if (savedPage && server) {
    try {
      result = await server.loadDetail(requestedId);
    } catch (error) {
      message(error.message, () => location.reload(), '보고서 다시 불러오기');
      if (page === 'complete') {
        const back = document.createElement('a');
        back.className = 'report-back';
        back.href = 'report.html';
        back.textContent = '← 보고서 목록';
        select('main').append(back);
      }
      return;
    }
  } else if (savedPage && requestedId && requestedId !== 'current') {
    message('서버 보고서는 로그인 후 확인할 수 있습니다.');
    return;
  }
  if (isCurrent && (!result || !result.id)) { location.replace('report.html'); return; }
  const sampleEvidence = [
    { id: 'EV-20261006-001', name: 'evidence_001.html', filename: 'evidence_001.html', type: 'HTML', date: '2026/10/06 10:32', statusCode: 'MATCH', status: '일치' },
    { id: 'EV-20261006-002', name: 'screenshot_001.png', filename: 'screenshot_001.png', type: '이미지', date: '2026/10/06 10:21', statusCode: 'MATCH', status: '일치' }
  ];
  const sample = { ...defaults(), author: draftAuthor, title: '디지털 증거 검증 보고서_001', id: 'RPT-20261008-001', date: '2026/10/08', created: '2026/10/08 16:02:31', selected: sampleEvidence.map((item) => item.id), evidenceSnapshot: sampleEvidence };
  const report = isCurrent ? clean(result) : (page === 'detail' ? sample : draft);
  if (server && isCurrent) {
    report.filename = result.file?.filename;
    if (page === 'complete') select('[data-report-detail-link]').href = server.reportUrl('detail', report.id);
  }
  if (savedPage) screen.hidden = false;
  if (page === 'detail') {
    select('.rp-heading h1').textContent = isCurrent ? '보고서 상세' : '예시 보고서 상세';
    if (server || !isCurrent) select('[data-report-delete]')?.remove();
  }

  function itemsFor(data) {
    const source = data.id ? data.evidenceSnapshot : evidence;
    return data.selected.map((id) => source.find((item) => item.id === id)).filter(Boolean);
  }

  function saveDraft() {
    draft.options = availableDraftOptions(draft.options);
    return write(DRAFT_KEY, draft);
  }
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
    return countSummary(counts);
  }

  function countSummary(counts) {
    return `일치 ${counts.MATCH || 0}건 · 미검증 ${counts.NOT_VERIFIED || 0}건 · 불일치 ${counts.MISMATCH || 0}건 · 검증 불가 ${counts.UNAVAILABLE || 0}건`;
  }

  function listStatus(data) {
    const counts = itemsFor(data).reduce((result, item) => { result[item.statusCode] = (result[item.statusCode] || 0) + 1; return result; }, {});
    if (counts.MISMATCH) return { code: 'MISMATCH', label: `불일치 ${counts.MISMATCH}건` };
    if (counts.UNAVAILABLE) return { code: 'UNAVAILABLE', label: `검증 불가 ${counts.UNAVAILABLE}건` };
    if (counts.NOT_VERIFIED) return { code: 'NOT_VERIFIED', label: `미검증 ${counts.NOT_VERIFIED}건` };
    if (counts.MATCH) return { code: 'MATCH', label: '✓ 모두 일치' };
    return { code: 'NOT_VERIFIED', label: '미검증' };
  }

  function renderValues(data) {
    const count = data.selected.length;
    const items = itemsFor(data);
    const values = {
      title: data.title, author: data.author || '작성자 미기록', date: data.date,
      'created-label': data.legacyTime ? '생성 일시 (시간대 미기록)' : '생성 일시 (KST)',
      'date-label': data.legacyTime ? '작성일 (시간대 미기록)' : '작성일 (KST)',
      id: data.id || '생성 시 발급', 'id-prefixed': `보고서 ID　${data.id || '생성 시 발급'}`,
      filename: data.filename || `${data.title}.pdf`, created: data.created || '', count: `${count}건`,
      selection: `선택된 증거: ${count}건`, 'selected-count': `선택 증거 ${count}건`,
      summary: summary(data), status: count && items.length === count && items.every(item => item.statusCode === 'MATCH') ? '✓ 모두 일치' : '검증 상태 확인 필요',
      'preview-description': `선택한 ${count}건의 증거와 보고서에 포함되는 정보를 최종 확인하세요.`,
      'document-description': `선택한 증거 ${count}건의 보고서 항목을 표시합니다.`,
      'complete-description': `선택한 증거 ${count}건으로 보고서를 생성했습니다.`,
      'progress-description': `선택한 증거 ${count}건과 검증 결과를 문서로 묶고 있습니다.`,
      'author-date': `작성자 ${data.author || '작성자 미기록'} · 작성일 (${data.legacyTime ? '시간대 미기록' : 'KST'}) ${data.date}`,
      footer: `EVIDENCE · ${data.id || '미리보기'}`
    };
    all('[data-report-value]').forEach((element) => { element.textContent = values[element.dataset.reportValue] ?? ''; });
  }

  function renderEvidence(data) {
    const container = select('[data-report-detail-evidence-list], [data-summary-body]');
    if (!container) return;
    const preview = container.hasAttribute('data-summary-body');
    const items = itemsFor(data);
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
      const registered = item.registeredAt ? `${formatDate(item.registeredAt)} (KST)` : `${item.date || '기록 없음'}${data.legacyTime && item.date ? ' (시간대 미기록)' : ''}`;
      addLine(`${item.type} · ${formatSize(item.size)} · 등록 일시 ${registered}`, 'basic');
      addLine(`검증 상태: ${item.status}${item.lastVerification?.completedAt ? ` · 마지막 검증 ${formatDate(item.lastVerification.completedAt)} (KST)` : ''}`, 'results');
      if (data.options.includes('basic') || data.options.includes('custody')) addLine(`등록자: ${item.registeredBy || '미기록'}`);
      if (item.lastVerification && (data.options.includes('results') || data.options.includes('custody'))) {
        addLine(`마지막 검증자: ${item.lastVerification.performedBy || '미기록'}`);
      }
      addLine(`수집 출처: ${item.source || '기록 없음'} · MIME: ${item.mime || '기록 없음'}`, 'metadata');
      addLine(`등록 SHA-256: ${item.sha256 || '기록 없음'}`, 'hash');
      addLine(`검증 SHA-256: ${item.lastVerification?.currentHash || '검증 기록 없음'}`, 'hash');
      if (item.lastVerification?.reason) addLine(`검증 결과 설명: ${item.lastVerification.reason}`, 'results');
      const snapshot = item.custodySnapshot;
      const reference = typeof snapshot === 'string' ? snapshot
        : [snapshot?.sequenceNo, snapshot?.eventHash].filter(value => value != null && value !== '').join(' · ');
      if (reference) addLine(`관리 이력 기준점: ${reference}`, 'custody');
      container.append(card);
    });
    if (!items.length) {
      const empty = document.createElement('p');
      empty.className = 'report-empty';
      empty.textContent = data.id ? '생성 당시 저장된 증거 상세 정보가 없습니다.' : '선택한 증거가 없습니다.';
      container.append(empty);
    }
    all('[data-report-option-section]').forEach((section) => {
      section.hidden = !data.options.includes(section.dataset.reportOptionSection);
    });
    if (data.project || data.description || data.options.length !== options.length) {
      const note = document.createElement('div');
      note.className = 'report-draft-note';
      note.textContent = [data.project && `사건 또는 프로젝트명: ${data.project}`, data.description, `포함 항목: ${data.options.map((key) => optionNames[options.indexOf(key)]).join(', ')}`].filter(Boolean).join('\n');
      if (preview) container.prepend(note);
      else container.before(note);
    }
  }

  function formatSize(bytes) {
    if (!Number.isFinite(bytes)) return '크기 기록 없음';
    return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
  }

  renderValues(report);
  if (page === 'detail') {
    renderEvidence(report);
    if (isCurrent && report.options.includes('custody')) renderCustody(report);
    window.ReportPackages.show(report.id, Boolean(profile));
  }

  function renderCustody(data) {
    const list = select('[data-report-custody-events]');
    function add(action, subject, time, actor) {
      const line = document.createElement('li');
      line.textContent = [action, subject, time, actor || '이름 미기록'].filter(Boolean).join(' · ');
      list.append(line);
    }
    for (const item of data.evidenceSnapshot) {
      if (!data.selected.includes(item.id)) continue;
      if (item.registeredAt) add('증거 등록', item.name, formatDate(item.registeredAt) + ' (KST)', item.registeredBy);
      const check = item.lastVerification;
      if (check?.completedAt) add('마지막 검증', item.name, formatDate(check.completedAt) + ' (KST)', check.performedBy);
    }
    if (data.created) add('보고서 생성', '', data.created + (data.legacyTime ? ' (시간대 미기록)' : ' (KST)'), data.author);
    select('[data-report-custody-empty]').hidden = list.children.length > 0;
  }

  async function reportBlob(data) {
    const { createReportPdf } = await import('./report-pdf.js');
    return createReportPdf(data, itemsFor(data), formatDate);
  }

  if (page === 'preview') {
    renderEvidence(report);
    const paper = select('[data-report-document]');
    select('[data-summary-author]').textContent = select('[data-report-value="author-date"]').textContent;
    const conclusion = document.createElement('p');
    conclusion.className = 'rp-summary-result';
    conclusion.textContent = report.options.includes('results') ? summary(report) : `선택한 증거 ${report.selected.length}건`;
    select('[data-summary-body]').append(conclusion);
    const sectionTargets = [
      ['보고서 개요', paper.querySelector('h2')],
      ['증거 기본 정보', paper.querySelector('[data-report-option-section="basic"]:not([hidden])')],
      ['증거 수집 정보', paper.querySelector('[data-report-option-section="basic"]:not([hidden]) + p')],
      ['메타데이터', paper.querySelector('[data-report-option-section="metadata"]:not([hidden])')],
      ['SHA-256 무결성 검증', paper.querySelector('[data-report-option-section="hash"]:not([hidden])')],
      ['Chain of Custody', paper.querySelector('[data-report-option-section="custody"]:not([hidden])')],
      ['검증 결과', paper.querySelector('[data-report-option-section="results"]:not([hidden])')],
      ['결론', conclusion]
    ].map(([label, target], index) => ({ label, target, index })).filter(section => section.target);
    const previous = select('[data-summary-prev]');
    const next = select('[data-summary-next]');
    let active = 0;
    function showSection(position, scroll = false) {
      active = position;
      for (const [i, section] of sectionTargets.entries()) {
        section.target.classList.toggle('rp-section-current', active === i);
        if (active === i) section.button.setAttribute('aria-current', 'location');
        else section.button.removeAttribute('aria-current');
      }
      previous.disabled = active === 0;
      next.disabled = active === sectionTargets.length - 1;
      select('[data-summary-position]').textContent = `${active + 1} / ${sectionTargets.length}`;
      if (scroll) sectionTargets[active].target.scrollIntoView({block: 'nearest'});
    }
    sectionTargets.forEach((section, index) => {
      const li = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.reportPreviewSection = String(section.index);
      button.textContent = `${index + 1}. ${section.label}`;
      button.addEventListener('click', () => showSection(index, true));
      section.button = button;
      li.append(button);
      select('[data-summary-toc]').append(li);
    });
    previous.addEventListener('click', () => showSection(active - 1, true));
    next.addEventListener('click', () => showSection(active + 1, true));
    showSection(0);
    all('[data-preview-mode]').forEach(button => {
      button.addEventListener('click', () => {
        all('[data-preview-mode]').forEach(mode => {
          const selected = mode === button;
          mode.setAttribute('aria-pressed', String(selected));
          document.getElementById(mode.getAttribute('aria-controls')).hidden = !selected;
        });
      });
    });
    const status = select('[data-pdf-status]');
    let previewBusy = false;
    let previewUrl = null;
    async function loadPreview() {
      if (previewBusy) return;
      previewBusy = true;
      const unlock = lockReportControls(all('[data-report-generate], [data-report-download]'));
      status.hidden = false;
      status.setAttribute('role', 'status');
      status.textContent = 'PDF 미리보기를 만들고 있습니다.';
      message('', null);
      try {
        const blob = await reportBlob(report);
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(blob);
        const link = select('[data-open-preview]');
        link.href = previewUrl;
        link.hidden = false;
        const frame = select('[data-pdf-preview]');
        frame.src = previewUrl + '#view=FitH';
        frame.hidden = false;
        status.hidden = true;
      } catch (error) {
        status.textContent = 'PDF 미리보기를 열지 못했습니다.';
        status.setAttribute('role', 'alert');
        message('미리보기를 열지 못했습니다. ' + error.message, loadPreview, '미리보기 다시 만들기');
      } finally { previewBusy = false; unlock(); }
    }
    loadPreview();
    window.addEventListener('pagehide', event => { if (!event.persisted && previewUrl) URL.revokeObjectURL(previewUrl); });
  }

  function renderSelectionRows() {
    if (page !== 'select') return;
    const marker = select('[data-report-evidence-placeholder]');
    const parent = marker?.parentElement;
    if (!parent) return;
    const typeFilter = select('[data-report-filter="type"]');
    const statusFilter = select('[data-report-filter="status"]');
    typeFilter.replaceChildren(new Option('전체 유형', ''), ...[...new Set(evidence.map((item) => item.type))].sort().map((type) => new Option(type, type)));
    statusFilter.replaceChildren(new Option('전체 상태', ''), ...Object.entries(statusNames).map(([code, label]) => new Option(label, code)));
    evidence.forEach((item, index) => {
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
      const type = document.createElement('div');
      type.className = 'report-local-type';
      type.textContent = item.type;
      const registration = registrationById.get(item.id);
      const time = document.createElement('time');
      time.className = 'report-local-date';
      time.textContent = registration.text;
      if (registration.dateTime) time.dateTime = registration.dateTime;
      const idCell = document.createElement('div');
      idCell.className = 'report-local-id';
      window.EvidenceListDisplay.renderEvidenceId(idCell, item.id, index);
      row.append(selection, name, type, time, idCell);
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
    draft = { ...defaults(), author: draftAuthor };
    if (server) draft.options = server.draftOptions.slice();
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
      const id = input.dataset.reportEvidence;
      draft.selected = draft.selected.filter(selectedId => selectedId !== id);
      if (input.checked) draft.selected.push(id);
      saveDraft(); renderValues(draft);
    });
  });

  all('[data-report-option]').forEach((input) => {
    if (pendingOptions.has(input.dataset.reportOption)) {
      input.checked = false;
      input.disabled = true;
      // A stale checked property or synthetic event must not re-enable a pending option.
      input.addEventListener('change', () => {
        input.checked = false;
        input.disabled = true;
        saveDraft();
      });
      return;
    }
    input.checked = draft.options.includes(input.dataset.reportOption);
    if (server && !server.draftOptions.includes(input.dataset.reportOption)) {
      input.disabled = true;
      const note = select('[data-report-server-note]');
      note.hidden = false;
      note.id = 'report-server-note';
      input.setAttribute('aria-describedby', `report-option-${input.dataset.reportOption}-note ${note.id}`);
      return;
    }
    input.addEventListener('change', () => {
      draft.options = availableDraftOptions(all('[data-report-option]:checked').map((item) => item.dataset.reportOption));
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
    const rows = all('[data-report-evidence-row]');
    rows.forEach((row) => {
      const item = evidence.find((record) => record.id === row.dataset.reportEvidenceRow);
      row.hidden = !`${item.name} ${item.filename} ${item.id}`.toLowerCase().includes(query) || !!(type && item.type !== type) || !!(status && item.statusCode !== status);
    });
    select('[data-report-no-matches]').hidden = !rows.length || rows.some(row => !row.hidden);
  }
  all('[data-report-filter]').forEach((input) => input.addEventListener('change', filterRows));
  all('[data-report-next]').forEach((link) => link.addEventListener('click', (event) => {
    if (!valid() || !saveDraft()) event.preventDefault();
  }));
  function generationContents(author = draft.author) {
    return JSON.stringify([author, draft.title, draft.project, draft.description, draft.selected, draft.options, draft.date]);
  }

  let generationStarting = false;
  all('[data-report-generate]').forEach((link) => link.addEventListener('click', (event) => {
    if (generationStarting) { event.preventDefault(); return; }
    if (!valid() || !saveDraft()) { event.preventDefault(); return; }
    const contents = generationContents();
    let saved;
    if (server) {
      const previous = read(GENERATION_KEY);
      // Returning to preview must not erase an unresolved request for the same content.
      saved = previous?.contents === contents && previous.sent
        ? true : write(GENERATION_KEY, { contents, author: draft.author });
    } else {
      const previous = read(GENERATION_KEY);
      saved = previous?.contents === contents && previous.key
        ? true : write(GENERATION_KEY, { key: crypto.randomUUID(), contents, author: draft.author });
    }
    if (!saved) { event.preventDefault(); return; }
    generationStarting = true;
    lockReportControls(all('[data-report-generate]'));
  }));

  screen.inert = false;
  screen.removeAttribute('aria-busy');
  if (page === 'progress') {
    const generation = read(GENERATION_KEY);
    if (server) {
      if (!generation || generation.contents !== generationContents(generation.author) || !valid()) { location.replace('report_preview.html'); return; }
      try {
        await server.generate(draft, profile.id, generation, value => write(GENERATION_KEY, value), value => write(RESULT_KEY, value));
      } catch (error) { message(error.message, () => location.reload(), '진행 상태 다시 확인'); }
      return;
    }
    if (!generation?.key || generation.contents !== generationContents(generation.author) || !valid()) { location.replace('report_preview.html'); return; }
    const reportId = 'RPT-' + generation.key.replace(/-/g, '');
    if (result?.id === reportId) { location.replace('report_complete.html'); return; }
    let cancelled = false;
    let busy = false;
    let timer;
    const meter = select('progress');
    const status = select('.report-progress-status');
    const card = select('.report-progress-card');
    function runDemo() {
      if (busy || cancelled) return;
      busy = true;
      card.setAttribute('aria-busy', 'true');
      let percent = 68;
      meter.hidden = false;
      status.setAttribute('role', 'status');
      status.textContent = '';
      message('', null);
      timer = window.setInterval(async () => {
        if (cancelled) return;
        percent = Math.min(100, percent + 4);
        meter.value = percent;
        select('[data-report-percent]').textContent = `${percent}%`;
        if (percent !== 100) return;
        clearInterval(timer);
        try {
          const current = await window.EvidenceApi.profile(true);
          if (current?.id !== profile?.id) throw new Error('로그인 계정이 변경되었습니다. 원래 계정으로 돌아가거나 미리보기에서 다시 확인해 주세요.');
          if (cancelled) return;
          const completed = { ...draft, author: current?.name ?? 'user', evidenceSnapshot: itemsFor(draft), id: reportId, created: formatDate(new Date(), true) };
          if (!write(RESULT_KEY, completed)) throw new Error('보고서 저장에 실패했습니다. 브라우저 저장소 설정을 확인한 뒤 다시 시도해 주세요.');
          location.replace('report_complete.html');
        } catch (error) {
          meter.hidden = true;
          status.setAttribute('role', 'alert');
          status.textContent = '보고서를 생성하지 못했습니다. 작성 내용은 유지됩니다.';
          message(error.message, runDemo, '보고서 생성 다시 시도');
        } finally { busy = false; card.setAttribute('aria-busy', 'false'); }
      }, 400);
    }
    runDemo();
    select('[data-report-cancel]').addEventListener('click', () => { cancelled = true; clearInterval(timer); remove(GENERATION_KEY); });
    window.addEventListener('pagehide', () => { cancelled = true; clearInterval(timer); });
  }

  let downloading = false;
  async function downloadPdf(data, button) {
    if (downloading) return;
    downloading = true;
    const unlock = lockReportControls([...all('[data-report-download]'), button]);
    message('PDF 파일을 준비하고 있습니다.', null, '', true);
    try {
      const pdf = server ? await server.downloadPdf(data.id) : await reportBlob(data);
      const url = URL.createObjectURL(pdf);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = data.filename || (data.title.replace(/[\\/:*?"<>|]/g, '_') || '증거 보고서') + '.pdf';
      document.body.append(anchor);
      anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      message('', null);
    } catch (error) {
      message('PDF를 다운로드하지 못했습니다. ' + error.message, () => downloadPdf(data, button), 'PDF 다운로드 다시 시도');
    } finally { downloading = false; unlock(); }
  }

  all('[data-report-download]').forEach((button) => button.addEventListener('click', event => downloadPdf(report, event.currentTarget)));
  function renderRow(data, status, count, description) {
    const row = select('#report-list-row').content.firstElementChild.cloneNode(true);
    const cells = Array.from(row.children);
    cells[0].lastElementChild.textContent = data.title;
    cells[1].textContent = `${count}건`;
    cells[2].textContent = `${data.created} (${data.legacyTime ? '시간대 미기록' : 'KST'})`;
    const badge = cells[3].firstElementChild;
    badge.classList.add('report-status-badge');
    badge.dataset.status = status.code;
    badge.title = description;
    badge.setAttribute('aria-label', `${status.label}. ${description}`);
    badge.firstElementChild.textContent = status.label;
    row.querySelector('a').href = server ? server.reportUrl('detail', data.id) : 'report_detail.html?report=current';
    row.querySelector('[data-report-download]').addEventListener('click', event => downloadPdf(data, event.currentTarget));
    return row;
  }
  if (page === 'list') {
    const empty = select('[data-report-list-empty]');
    if (server) {
      let listBusy = false;
      let renderedRows = [];
      async function reloadList() {
        if (listBusy) return;
        listBusy = true;
        empty.parentElement.setAttribute('aria-busy', 'true');
        if (!renderedRows.length) { empty.hidden = false; empty.textContent = '보고서 목록을 불러오고 있습니다.'; }
        message('', null);
        try {
          const list = await server.loadList();
          const rows = list.map(data => renderRow(data, data.listStatus, data.evidenceCount, countSummary(data.summary || {})));
          renderedRows.forEach(row => row.remove());
          rows.forEach(row => empty.before(row));
          renderedRows = rows;
          empty.hidden = rows.length > 0;
          empty.textContent = '생성한 보고서가 없습니다.';
        } catch (error) {
          if (!renderedRows.length) empty.textContent = '보고서 목록을 불러오지 못했습니다.';
          message(error.message, reloadList, '목록 다시 불러오기');
        } finally { listBusy = false; empty.parentElement.setAttribute('aria-busy', 'false'); }
      }
      reloadList();
    } else {
      select('[data-report-sample]').hidden = false;
      select('[data-report-list-heading]').textContent = '이 탭에서 최근 생성한 보고서';
      if (result?.id) {
        const data = clean(result);
        empty.replaceWith(renderRow(data, listStatus(data), itemsFor(data).length, summary(data)));
      } else empty.textContent = '이 탭에서 생성한 보고서가 없습니다.';
    }
  }
  all('[data-report-delete]').forEach((button) => button.addEventListener('click', () => {
    if (!isCurrent) { message('이 화면은 기존 보고서의 상세 보기 예시입니다.'); return; }
    if (window.confirm('이 보고서를 삭제하시겠습니까? 원본 증거는 유지됩니다.')) {
      remove(RESULT_KEY); remove(GENERATION_KEY); location.href = 'report.html';
    }
  }));
  window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    if (page === 'progress') location.replace('report_preview.html');
    else location.reload();
  });
})();
