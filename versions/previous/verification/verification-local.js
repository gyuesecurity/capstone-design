(function () {
  "use strict";
  const labels = { NOT_VERIFIED: "미검증", MATCH: "일치", MISMATCH: "불일치", UNAVAILABLE: "검증 불가" };

  async function init(api) {
    const root = document.getElementById("local-verification");
    if (!root) return;
    const search = root.querySelector("#verification-search"), type = root.querySelector("#verification-type"), status = root.querySelector("#verification-status");
    const rows = root.querySelector("#verification-rows"), count = root.querySelector("#verification-count"), selectAll = root.querySelector("#verification-select-all");
    const message = root.querySelector("#verification-message"), targets = root.querySelector("#verification-targets"), results = root.querySelector("#verification-results"), history = root.querySelector("#verification-history");
    const feedback = root.querySelector("[data-verification-feedback]"), feedbackMessage = feedback.querySelector("[data-verification-feedback-message]");
    const loadRetry = feedback.querySelector("[data-verification-load-retry]"), retry = feedback.querySelector("[data-verification-retry]");
    const buttons = Array.from(root.querySelectorAll("[data-run-verification]"));
    const cards = new Map(), failedIds = new Set();
    let records = [], selected = new Set(api.readSelection().map(record => record.id));
    let busy = false, loading = false, loaded = false, backend;

    function element(tag, text, className) {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    }
    function setMessage(text, error = false) {
      message.textContent = text;
      message.dataset.state = error ? "error" : "status";
      message.setAttribute("role", error ? "alert" : "status");
      message.setAttribute("aria-live", error ? "assertive" : "polite");
    }
    function showFeedback(text, options = {}) {
      feedback.hidden = !text;
      feedback.dataset.state = options.error ? "error" : "loading";
      feedback.setAttribute("role", options.error ? "alert" : "status");
      feedback.setAttribute("aria-live", options.error ? "assertive" : "polite");
      feedbackMessage.textContent = text || "";
      loadRetry.hidden = !options.load;
      retry.hidden = !options.retry;
      updateControls();
    }
    function visibleRecords() {
      const query = search.value.trim().toLowerCase();
      return records.filter(record => (!query || `${record.name} ${record.filename} ${record.id}`.toLowerCase().includes(query)) &&
        (!type.value || record.type === type.value) && (!status.value || (record.verificationStatus || "NOT_VERIFIED") === status.value));
    }
    function retryableRecords() { return records.filter(record => failedIds.has(record.id) && api.canVerify(record)); }
    function updateControls() {
      const locked = busy || loading, visible = visibleRecords(), eligible = visible.filter(api.canVerify);
      root.setAttribute("aria-busy", String(locked));
      results.setAttribute("aria-busy", String(busy));
      feedback.setAttribute("aria-busy", String(locked));
      selectAll.checked = eligible.length > 0 && eligible.every(record => selected.has(record.id));
      selectAll.indeterminate = !selectAll.checked && visible.some(record => selected.has(record.id));
      selectAll.disabled = locked || !loaded || !eligible.length;
      buttons.forEach(button => { button.disabled = locked || !loaded || !selected.size; button.textContent = busy ? "검증 중…" : loading ? "불러오는 중…" : "증거 검증"; });
      root.querySelector("#verification-clear").disabled = locked || !loaded || !selected.size;
      [search, type, status].forEach(control => { control.disabled = locked || !loaded; });
      rows.querySelectorAll('input[type="checkbox"]').forEach(control => {
        const id = control.closest("[data-verification-evidence-row]").dataset.verificationEvidenceRow;
        const record = records.find(item => item.id === id);
        control.disabled = locked || !loaded || !record || !api.canVerify(record);
      });
      loadRetry.disabled = locked;
      retry.disabled = locked || !loaded || !retryableRecords().length;
    }
    function badge(code) {
      const node = element("span", code === "REQUEST_FAILED" ? "요청 실패" : labels[code] || "미검증", "verification-badge");
      node.dataset.status = code || "NOT_VERIFIED";
      return node;
    }
    function addDetails(parent, entries) {
      const list = element("dl", undefined, "verification-details");
      entries.forEach(([label, value]) => { const item = element("div"); item.append(element("dt", label), element("dd", value || "없음")); list.append(item); });
      parent.append(list);
    }
    function updateSelection() {
      const visible = visibleRecords();
      count.textContent = loaded ? `${visible.length} / ${records.length}개 증거 · ${selected.size}개 선택됨` : loading ? "불러오는 중" : "불러오기 실패";
      targets.replaceChildren();
      const chosen = records.filter(record => selected.has(record.id));
      if (!chosen.length) targets.append(element("p", loaded ? "검증할 파일을 목록에서 선택해 주세요." : "목록을 불러온 뒤 선택한 파일을 확인할 수 있습니다.", "verification-empty"));
      chosen.forEach(record => {
        const card = element("article", undefined, "verification-target");
        card.append(element("h3", record.name), element("p", record.filename));
        addDetails(card, [["증거 ID", record.id], ["파일 정보", `${record.type} · ${Number.isFinite(record.size) ? record.size.toLocaleString("ko-KR") + " bytes" : "크기 기록 없음"}`], ["등록 일시 (KST)", record.registration.text], ["등록 당시 SHA-256", record.sha256]]);
        targets.append(card);
      });
      // A failed first load must not erase the selection passed from the evidence list.
      if (loaded) api.saveSelection(chosen.map(record => ({ id: record.id, name: record.name, type: record.type })));
      updateControls();
    }
    function renderRows() {
      rows.replaceChildren();
      const visible = visibleRecords();
      visible.forEach((record, index) => {
        const row = element("tr"); row.dataset.verificationEvidenceRow = record.id; row.classList.toggle("verification-row-selected", selected.has(record.id));
        const cell = element("td"), checkbox = element("input");
        checkbox.type = "checkbox"; checkbox.checked = selected.has(record.id);
        if (!api.canVerify(record)) checkbox.title = "검증 권한이 없습니다.";
        checkbox.setAttribute("aria-label", `${record.filename} 선택`);
        checkbox.addEventListener("change", () => {
          if (busy || loading) { checkbox.checked = selected.has(record.id); return; }
          if (checkbox.checked) selected.add(record.id); else selected.delete(record.id);
          renderRows();
        });
        cell.append(checkbox);
        const name = element("td"), state = element("td");
        name.append(element("strong", record.name), element("small", record.filename)); state.append(badge(record.verificationStatus));
        const time = element("time", record.registration.text);
        if (record.registration.dateTime) time.dateTime = record.registration.dateTime;
        const dateCell = element("td");
        dateCell.append(time);
        const idCell = element("td", undefined, "verification-id");
        window.EvidenceListDisplay.renderEvidenceId(idCell, record.id, index);
        row.append(cell, name, element("td", record.type), dateCell, idCell, state);
        rows.append(row);
      });
      if (!visible.length) {
        const row = element("tr");
        const text = !loaded ? (loading ? "등록한 파일을 불러오고 있습니다." : "파일 목록을 불러오지 못했습니다. ‘목록 다시 불러오기’를 눌러 주세요.")
          : records.length ? "검색 조건에 맞는 증거가 없습니다." : "등록된 파일이 없습니다. 증거 관리에서 파일을 등록해 주세요.";
        const cell = element("td", text, "verification-empty"); cell.colSpan = 6; row.append(cell); rows.append(row);
      }
      updateSelection();
    }
    function resultCard(result, saved, requestFailed = false) {
      const card = element("article", undefined, "verification-result");
      card.dataset.verificationResult = requestFailed ? "REQUEST_FAILED" : result.status;
      card.dataset.saved = String(saved); card.dataset.evidenceId = result.evidenceId;
      if (requestFailed) card.dataset.verificationRequestFailed = "true";
      const heading = element("div", undefined, "verification-card-heading");
      heading.append(element("h3", result.filename), badge(requestFailed ? "REQUEST_FAILED" : result.status));
      card.append(heading, element("p", result.reason));
      if (requestFailed) addDetails(card, [["증거 ID", result.evidenceId]]);
      else addDetails(card, [["증거 ID", result.evidenceId], ["검증 실행 ID", result.id], ["등록 당시 SHA-256", result.expectedHash], ["현재 SHA-256", result.currentHash || "계산하지 못함"], ["검증 일시", result.completedAt ? api.formatDate(result.completedAt) : "기록 없음"]]);
      const destination = backend ? "서버" : "이 브라우저";
      const text = requestFailed ? "서버 응답을 확인하지 못해 검증 결과와 저장 여부를 확인할 수 없습니다."
        : saved ? `결과와 이력을 ${destination}에 저장했습니다.` : `결과를 ${destination}에 저장하지 못했습니다. 아래 결과는 저장되지 않은 계산 결과입니다.`;
      const storage = element("p", text, "verification-save-state"); storage.dataset.saved = String(saved); card.append(storage);
      const link = element("a", "증거 상세보기"); link.href = `evidence.html?id=${encodeURIComponent(result.evidenceId)}`; card.append(link);
      return card;
    }
    function renderHistory() {
      history.replaceChildren();
      const entries = records.flatMap(record => Array.isArray(record.verifications) ? record.verifications : [])
        .filter(entry => entry && typeof entry === "object").sort((a, b) => String(b.completedAt || "").localeCompare(String(a.completedAt || ""))).slice(0, 20);
      if (!entries.length) history.append(element("p", "아직 실행한 검증이 없습니다.", "verification-empty"));
      entries.forEach(entry => {
        const detail = element("details", undefined, "verification-history-entry");
        const summary = element("summary", `${entry.filename} · ${labels[entry.status]} · ${entry.completedAt ? api.formatDate(entry.completedAt) : "기록 없음"}`);
        detail.append(summary, resultCard(entry, true)); history.append(detail);
      });
    }
    async function loadRecords() {
      if (backend === undefined) backend = await api.isBackend();
      const next = backend ? await api.list() : await api.store("readonly", tx => tx.objectStore("records").getAll());
      if (!Array.isArray(next)) throw new Error("서버의 파일 목록 형식을 확인할 수 없습니다.");
      const oldType = type.value;
      records = next.map(record => ({ ...record, registration: window.EvidenceListDisplay.registrationTime(record) }))
        .sort((a, b) => b.registration.timestamp - a.registration.timestamp);
      selected = new Set(Array.from(selected).filter(id => records.some(record => record.id === id && api.canVerify(record))));
      type.replaceChildren(new Option("전체 유형", ""));
      Array.from(new Set(records.map(record => record.type))).sort().forEach(value => type.add(new Option(value, value)));
      if (oldType && !Array.from(type.options).some(option => option.value === oldType)) type.add(new Option(oldType, oldType));
      type.value = oldType; loaded = true;
      const storage = root.querySelector("[data-verification-storage]");
      if (storage) storage.textContent = backend ? "서버에 저장된 파일을 검사합니다." : "이 브라우저에 저장된 파일을 검사합니다.";
      renderRows(); renderHistory();
    }
    function showFailedItems(prefix = "") {
      if (failedIds.size) showFeedback(`${prefix}${failedIds.size}개 항목의 검증 또는 결과 저장을 완료하지 못했습니다. 사유를 확인하고 해당 항목만 다시 검증할 수 있습니다.`, { error: true, retry: true, load: !retryableRecords().length });
      else showFeedback("");
    }
    async function reloadRecords() {
      if (busy || loading) return;
      loading = true; showFeedback("파일 목록을 불러오고 있습니다."); renderRows();
      try {
        await loadRecords(); showFailedItems();
        setMessage(cards.size ? "목록을 새로 불러왔습니다. 기존 검증 결과와 저장 여부는 아래에서 확인하세요." : "파일을 선택하고 ‘증거 검증’을 눌러 주세요.");
      } catch (error) {
        const text = `등록한 파일을 불러오지 못했습니다. ${error.message || "연결을 확인한 후 다시 시도해 주세요."}`;
        showFeedback(text, { error: true, load: true, retry: failedIds.size > 0 }); setMessage(text, true);
      } finally { loading = false; renderRows(); }
    }
    async function checkFile(record) {
      if (backend) {
        try {
          const checked = await api.verify(record.id);
          if (!checked?.result || checked.result.evidenceId !== record.id || !["MATCH", "MISMATCH", "UNAVAILABLE"].includes(checked.result.status) || typeof checked.saved !== "boolean") throw new Error("서버의 검증 결과 형식을 확인할 수 없습니다.");
          return checked;
        } catch (error) {
          // A failed request is not a measured verification result: do not invent hashes or timestamps.
          return { saved: false, requestFailed: true, result: { evidenceId: record.id, filename: record.filename, reason: error.message || "서버 검증 요청을 완료하지 못했습니다." } };
        }
      }
      const result = { id: `VERIFY-${crypto.randomUUID()}`, evidenceId: record.id, filename: record.filename, performedBy: "user",
        startedAt: new Date().toISOString(), expectedHash: null, currentHash: null, status: "UNAVAILABLE", reason: "파일을 읽지 못했습니다." };
      try {
        const snapshot = await api.store("readonly", tx => {
          const data = {}, metadata = tx.objectStore("records").get(record.id), file = tx.objectStore("files").get(record.id);
          metadata.onsuccess = () => { data.record = metadata.result; }; file.onsuccess = () => { data.file = file.result; };
          return { result: data };
        });
        result.expectedHash = snapshot.record && snapshot.record.sha256;
        if (!snapshot.record) result.reason = "등록 정보를 찾을 수 없어 검증을 완료하지 못했습니다.";
        else if (!/^[a-f0-9]{64}$/i.test(result.expectedHash || "")) result.reason = "유효한 등록 기준 해시가 없어 비교할 수 없습니다.";
        else if (!(snapshot.file instanceof Blob)) result.reason = "저장된 파일을 찾을 수 없어 검증을 완료하지 못했습니다.";
        else {
          const digest = await crypto.subtle.digest("SHA-256", await snapshot.file.arrayBuffer());
          result.currentHash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
          result.status = result.currentHash === result.expectedHash.toLowerCase() ? "MATCH" : "MISMATCH";
          result.reason = result.status === "MATCH" ? "현재 파일의 SHA-256이 등록 당시의 기준값과 일치합니다." : "현재 파일의 SHA-256이 등록 당시의 기준값과 일치하지 않습니다.";
        }
      } catch (error) { result.reason = "파일 읽기 또는 해시 계산에 실패해 검증을 완료하지 못했습니다."; }
      result.completedAt = new Date().toISOString();
      let saved = false;
      try {
        await api.store("readwrite", tx => {
          const store = tx.objectStore("records"), request = store.get(record.id);
          request.onsuccess = () => {
            const latest = request.result;
            if (!latest || latest.sha256 !== result.expectedHash) { tx.abort(); return; }
            latest.verifications = (latest.verifications || []).concat(result);
            if (!latest.lastVerification || latest.lastVerification.completedAt <= result.completedAt) { latest.lastVerification = result; latest.verificationStatus = result.status; latest.status = labels[result.status]; }
            store.put(latest);
          };
          return request;
        }); saved = true;
      } catch (error) { /* Keep the computed result visible and explicitly marked as unsaved. */ }
      return { result, saved };
    }
    async function run(retryOnly = false) {
      if (busy || loading || !loaded) return;
      const chosen = retryOnly ? retryableRecords() : records.filter(record => selected.has(record.id) && api.canVerify(record));
      if (!chosen.length) return;
      busy = true; showFeedback(retryOnly ? "완료하지 못한 항목만 다시 검증하고 있습니다." : "선택한 파일을 검증하고 있습니다."); renderRows();
      let stored = 0, refreshError = "";
      const completedIds = new Set();
      try {
        for (let index = 0; index < chosen.length; index += 1) {
          const record = chosen[index]; setMessage(`${index + 1} / ${chosen.length}개 파일의 SHA-256을 확인하고 있습니다.`);
          cards.get(record.id)?.setAttribute("aria-busy", "true");
          const checked = await checkFile(record);
          if (checked.saved) stored += 1;
          if (checked.requestFailed || !checked.saved || checked.result.status === "UNAVAILABLE") failedIds.add(record.id); else failedIds.delete(record.id);
          const card = resultCard(checked.result, checked.saved, checked.requestFailed), previous = cards.get(record.id);
          if (previous) previous.replaceWith(card);
          else { if (!cards.size) results.replaceChildren(); results.append(card); }
          cards.set(record.id, card);
          completedIds.add(record.id);
        }
        try { await loadRecords(); } catch (error) { refreshError = `검증 후 파일 목록을 갱신하지 못했습니다. ${error.message || "연결을 확인해 주세요."}`; }
        setMessage(`${chosen.length}개 검증 처리 · ${stored}개 결과 저장${failedIds.size ? " · 완료하지 못한 항목의 사유와 저장 여부를 확인해 주세요." : ""}${refreshError ? " · 목록 갱신 실패" : ""}`, Boolean(failedIds.size || refreshError));
        if (refreshError) showFeedback(`${refreshError} 기존 결과는 아래에서 확인할 수 있습니다.`, { error: true, load: true, retry: failedIds.size > 0 }); else showFailedItems();
      } catch (error) {
        const text = `검증 처리를 완료하지 못했습니다. ${error.message || "아래 결과를 확인하고 다시 시도해 주세요."}`;
        chosen.forEach(record => { if (!completedIds.has(record.id)) failedIds.add(record.id); });
        showFeedback(text, { error: true, retry: failedIds.size > 0, load: true }); setMessage(text, true);
      } finally { busy = false; cards.forEach(card => card.removeAttribute("aria-busy")); renderRows(); }
    }
    [search, type, status].forEach(control => control.addEventListener(control === search ? "input" : "change", () => { if (!busy && !loading) renderRows(); }));
    selectAll.addEventListener("change", () => { if (busy || loading || !loaded) return; visibleRecords().filter(api.canVerify).forEach(record => { if (selectAll.checked) selected.add(record.id); else selected.delete(record.id); }); renderRows(); });
    root.querySelector("#verification-clear").addEventListener("click", () => { if (busy || loading || !loaded) return; selected.clear(); renderRows(); });
    buttons.forEach(button => button.addEventListener("click", () => run()));
    retry.addEventListener("click", () => run(true)); loadRetry.addEventListener("click", reloadRecords);
    await reloadRecords();
  }
  window.EvidenceVerification = { init };
})();
