(function () {
  'use strict';
  if (location.pathname.split('/').pop().toLowerCase() !== 'dashboard.html') return;

  const labels = { NOT_VERIFIED: '미검증', MATCH: '일치', MISMATCH: '불일치', UNAVAILABLE: '검증 불가' };
  const activityList = document.querySelector('[data-dashboard-activities]');
  const recentHeader = document.querySelector('[data-dashboard-recent-header]');
  if (!activityList || !recentHeader) return;
  let refreshVersion = 0;

  async function readRecords() {
    if (window.EvidenceApi && await window.EvidenceApi.enabled()) return window.EvidenceApi.list();
    return new Promise((resolve, reject) => {
      const opening = indexedDB.open('evidence-local-files', 1);
      opening.onupgradeneeded = () => {
        const database = opening.result;
        if (!database.objectStoreNames.contains('records')) database.createObjectStore('records', { keyPath: 'id' });
        if (!database.objectStoreNames.contains('files')) database.createObjectStore('files');
      };
      opening.onerror = () => reject(opening.error);
      opening.onblocked = () => reject(new Error('브라우저 저장소를 열 수 없습니다.'));
      opening.onsuccess = () => {
        const database = opening.result;
        database.onversionchange = () => database.close();
        const transaction = database.transaction('records', 'readonly');
        const request = transaction.objectStore('records').getAll();
        transaction.oncomplete = () => { database.close(); resolve(request.result); };
        transaction.onabort = () => { database.close(); reject(transaction.error || new Error('증거 목록을 읽을 수 없습니다.')); };
      };
    });
  }

  function setText(selector, value) {
    const element = document.querySelector(selector);
    if (element) element.textContent = value;
  }

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function formatTime(value) {
    const time = new Date(value);
    return Number.isNaN(time.getTime()) ? '시간 기록 없음' : new Intl.DateTimeFormat('ko-KR', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
    }).format(time);
  }

  function clearExamples() {
    activityList.replaceChildren();
    while (recentHeader.nextElementSibling) recentHeader.nextElementSibling.remove();
  }

  function renderRecent(records) {
    const recent = records.slice().sort((a, b) => (b.registeredAt || '').localeCompare(a.registeredAt || '')).slice(0, 3);
    setText('[data-dashboard-recent-count]', `${recent.length}건`);
    if (!recent.length) {
      const empty = element('p', '등록된 증거가 없습니다. 증거를 등록하면 이곳에 표시됩니다.', 'dashboard-empty');
      recentHeader.after(empty);
      return;
    }
    let anchor = recentHeader;
    recent.forEach((record) => {
      const link = element('a', undefined, 'dashboard-recent-row');
      link.dataset.dashboardRecentRow = record.id;
      link.href = `evidence.html?id=${encodeURIComponent(record.id)}`;
      const name = element('span', undefined, 'dashboard-recent-name');
      name.append(element('strong', record.name || record.filename || record.id));
      if (record.filename && record.filename !== record.name) name.append(element('small', record.filename));
      const type = element('span', record.type || '기타', 'dashboard-recent-type');
      const time = element('time', formatTime(record.registeredAt), 'dashboard-recent-time');
      if (record.registeredAt) time.dateTime = record.registeredAt;
      const code = labels[record.verificationStatus] ? record.verificationStatus : 'NOT_VERIFIED';
      const status = element('span', labels[code], 'dashboard-recent-status');
      status.dataset.status = code;
      link.append(name, type, time, status);
      anchor.after(link);
      anchor = link;
    });
  }

  function renderActivities(records) {
    const events = [];
    records.forEach((record) => {
      const name = record.name || record.filename || record.id;
      if (record.registeredAt) events.push({ at: record.registeredAt, title: '증거 등록 완료', name, id: record.id, kind: 'registration' });
      const verifications = Array.isArray(record.verifications) && record.verifications.length ? record.verifications : (record.lastVerification ? [record.lastVerification] : []);
      verifications.forEach((result) => {
        if (!result.completedAt) return;
        const title = result.status === 'MATCH' ? '무결성 검증: 일치' : result.status === 'MISMATCH' ? '무결성 검증: 불일치' : '무결성 검증 불가';
        events.push({ at: result.completedAt, title, name, id: record.id, kind: result.status || 'UNAVAILABLE' });
      });
    });
    events.sort((a, b) => b.at.localeCompare(a.at));
    if (!events.length) {
      activityList.append(element('p', '최근 활동이 없습니다.', 'dashboard-empty'));
      return;
    }
    events.slice(0, 3).forEach((event) => {
      const link = element('a', undefined, 'dashboard-activity');
      link.href = `evidence.html?id=${encodeURIComponent(event.id)}`;
      const symbol = element('span', event.kind === 'registration' ? '+' : event.kind === 'MATCH' ? '✓' : '!', 'dashboard-activity-symbol');
      symbol.dataset.kind = event.kind;
      const description = element('span', undefined, 'dashboard-activity-description');
      description.append(element('strong', event.title), element('small', event.name));
      const time = element('time', formatTime(event.at), 'dashboard-activity-time');
      time.dateTime = event.at;
      link.append(symbol, description, time);
      activityList.append(link);
    });
  }

  function render(records) {
    clearExamples();
    const counts = { total: records.length, match: 0, pending: 0, mismatch: 0 };
    records.forEach((record) => {
      if (record.verificationStatus === 'MATCH') counts.match += 1;
      else if (record.verificationStatus === 'MISMATCH') counts.mismatch += 1;
      else counts.pending += 1;
    });
    Object.entries(counts).forEach(([key, value]) => setText(`[data-dashboard-count="${key}"]`, String(value)));
    setText('[data-dashboard-chart-total]', `전체 ${counts.total}건`);
    ['match', 'pending', 'mismatch'].forEach((key) => {
      const percentage = counts.total ? Math.round(counts[key] / counts.total * 100) : 0;
      setText(`[data-dashboard-chart-count="${key}"]`, `${counts[key]}건`);
      setText(`[data-dashboard-chart-percent="${key}"]`, `${percentage}%`);
      const bar = document.querySelector(`[data-dashboard-chart-bar="${key}"]`);
      if (bar) bar.style.width = `${percentage}%`;
    });
    renderActivities(records);
    renderRecent(records);
    document.body.dataset.dashboardReady = 'true';
  }

  async function refresh() {
    const version = ++refreshVersion;
    setText('[data-dashboard-date]', new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()));
    try {
      const records = await readRecords();
      if (version === refreshVersion) render(records);
    } catch (error) {
      if (version !== refreshVersion) return;
      clearExamples();
      activityList.append(element('p', '최근 활동을 불러오지 못했습니다.', 'dashboard-empty'));
      recentHeader.after(element('p', `증거 목록을 불러오지 못했습니다. ${error.message || '새로고침해 주세요.'}`, 'dashboard-empty'));
      document.body.dataset.dashboardReady = 'true';
    }
  }

  window.addEventListener('pageshow', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
