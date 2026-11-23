(function () {
  'use strict';

  function show(reportId, serverMode) {
    const panel = document.querySelector('[data-report-packages]');
    if (!panel || !panel.hidden) return;
    panel.hidden = false;
    const create = panel.querySelector('[data-package-create]');
    const download = panel.querySelector('[data-package-download]');
    const chooser = panel.querySelector('[data-package-chooser]');
    const select = panel.querySelector('select');
    const status = panel.querySelector('[role="status"]');
    const packages = new Map();
    const pendingKey = 'evidence-package:pending:' + reportId;
    let uncertain = false;
    try { uncertain = sessionStorage.getItem(pendingKey) === '1'; } catch (_) { /* Creation validates storage before sending. */ }
    let busy = false;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'package-button';
    retry.dataset.packageRetry = '';
    retry.hidden = true;
    status.after(retry);

    function message(text, isError = false) {
      status.textContent = text;
      status.setAttribute('role', isError ? 'alert' : 'status');
      status.classList.toggle('package-error', isError);
    }
    function offer(label, action) {
      retry.textContent = label;
      retry.hidden = false;
      retry.onclick = () => { if (!busy) action(); };
    }
    function updateButtons() {
      create.disabled = busy || uncertain || !serverMode;
      download.disabled = busy || !packages.has(select.value) || !serverMode;
      select.disabled = busy;
      retry.disabled = busy;
      panel.setAttribute('aria-busy', String(busy));
    }
    function remember(item, newest = false) {
      if (!item || typeof item.id !== 'string' || !item.id || item.reportId !== reportId || packages.has(item.id)) return;
      packages.set(item.id, item);
      const option = new Option([item.created || item.createdAt, item.id].filter(Boolean).join(' · '), item.id);
      if (newest) { select.prepend(option); select.value = item.id; }
      else select.add(option);
      chooser.hidden = false;
    }
    function clearPending() {
      uncertain = false;
      try { sessionStorage.removeItem(pendingKey); } catch (_) { /* The current page remains usable. */ }
    }

    if (!serverMode) {
      updateButtons();
      message('서버에 로그인해 만든 보고서에서 패키지를 생성하고 다운로드할 수 있습니다.');
      return;
    }
    const api = window.EvidenceApi;

    async function loadPackages() {
      if (busy) return;
      busy = true;
      updateButtons();
      retry.hidden = true;
      message('패키지 목록을 확인하고 있습니다.');
      try {
        const response = await api.request('/api/packages?reportId=' + encodeURIComponent(reportId));
        const items = await response.json();
        if (!Array.isArray(items)) throw new Error('패키지 목록의 형식을 확인할 수 없습니다.');
        items.sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
        items.forEach(item => remember(item));
        if (uncertain) {
          message(items.length
            ? '생성 요청의 완료 여부를 확인하지 못했습니다. 목록의 생성 시각과 패키지를 확인한 뒤 다운로드해 주세요. 새 생성 요청은 보내지 않았습니다.'
            : '목록에서 생성 결과를 찾지 못했습니다. 아직 처리 중일 수 있으므로 잠시 후 다시 확인해 주세요.', true);
          offer('생성 결과 다시 확인', loadPackages);
        } else message(items.length ? '다운로드할 패키지를 선택하세요.' : '아직 생성한 패키지가 없습니다.');
      } catch (error) {
        if (!uncertain && [404, 405, 501].includes(error.status)) {
          message('이전 패키지 목록을 제공하지 않는 서버입니다. 새 패키지를 생성할 수 있습니다.');
        } else {
          message('패키지 목록을 확인하지 못했습니다. ' + error.message + (uncertain ? ' 중복 생성을 막기 위해 새 생성 요청은 보내지 않았습니다.' : ''), true);
          offer(uncertain ? '생성 결과 다시 확인' : '목록 다시 불러오기', loadPackages);
        }
      } finally { busy = false; updateButtons(); }
    }

    async function createPackage() {
      if (busy || uncertain) return;
      busy = true;
      retry.hidden = true;
      updateButtons();
      create.textContent = '생성 중…';
      message('증거 원본과 이력을 묶고 있습니다.');
      let posted = false;
      try {
        // Keep an unknown POST outcome across refreshes rather than sending it twice.
        try { sessionStorage.setItem(pendingKey, '1'); }
        catch (_) { throw new Error('요청 상태를 저장하지 못했습니다. 브라우저 저장소 설정을 확인한 뒤 다시 시도해 주세요.'); }
        posted = true;
        const response = await api.request('/api/packages', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reportId })
        });
        const item = await response.json();
        if (item?.reportId !== reportId || typeof item.id !== 'string' || !item.id) throw new Error('생성된 패키지 정보를 확인할 수 없습니다.');
        clearPending();
        remember(item, true);
        select.value = item.id;
        message('패키지가 생성되었습니다. ZIP을 다운로드할 수 있습니다.');
      } catch (error) {
        const definite = !posted || (error.status >= 400 && error.status < 500 && error.status !== 408);
        if (definite) {
          clearPending();
          message('패키지를 생성하지 못했습니다. ' + error.message, true);
          offer('패키지 생성 다시 시도', createPackage);
        } else {
          uncertain = true;
          message(error.message + ' 생성 여부를 확인하지 못했습니다. 중복 생성을 막기 위해 목록에서 결과를 먼저 확인해 주세요.', true);
          offer('생성 결과 다시 확인', loadPackages);
        }
      } finally {
        busy = false;
        create.textContent = '패키지 생성';
        updateButtons();
      }
    }

    async function downloadPackage() {
      const item = packages.get(select.value);
      if (busy || !item) return;
      busy = true;
      retry.hidden = true;
      updateButtons();
      download.textContent = '다운로드 중…';
      message('다운로드 권한과 파일을 확인하고 있습니다.');
      try {
        const response = await api.request('/api/packages/' + encodeURIComponent(item.id) + '/download');
        const blob = await response.blob();
        if (!blob.size || !['application/zip', 'application/x-zip-compressed', 'application/octet-stream'].includes(blob.type.split(';')[0])) throw new Error('서버에서 ZIP 파일을 받지 못했습니다.');
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = (item.file?.filename || item.id + '.zip').replace(/[\\/:*?"<>|]/g, '_');
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        message('ZIP 다운로드를 시작했습니다.');
        // The user explicitly reviewed and downloaded an available result.
        clearPending();
      } catch (error) {
        message('ZIP을 다운로드하지 못했습니다. ' + error.message, true);
        offer('ZIP 다운로드 다시 시도', downloadPackage);
      } finally {
        busy = false;
        download.textContent = 'ZIP 다운로드';
        updateButtons();
      }
    }

    create.addEventListener('click', createPackage);
    download.addEventListener('click', downloadPackage);
    select.addEventListener('change', updateButtons);
    updateButtons();
    loadPackages();
  }
  window.ReportPackages = { show };
})();
