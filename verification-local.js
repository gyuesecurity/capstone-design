(function () {
  "use strict";
  const labels = { NOT_VERIFIED: "미검증", MATCH: "일치", MISMATCH: "불일치", UNAVAILABLE: "검증 불가" };

  async function init(api) {
    const root = document.getElementById("local-verification");
    if (!root) return;
    const search = root.querySelector("#verification-search");
    const type = root.querySelector("#verification-type");
    const status = root.querySelector("#verification-status");
    const rows = root.querySelector("#verification-rows");
    const count = root.querySelector("#verification-count");
    const selectAll = root.querySelector("#verification-select-all");
    const message = root.querySelector("#verification-message");
    const targets = root.querySelector("#verification-targets");
    const results = root.querySelector("#verification-results");
    const history = root.querySelector("#verification-history");
    const buttons = Array.from(root.querySelectorAll("[data-run-verification]"));
    let records = [];
    let selected = new Set(api.readSelection().map(record => record.id));
    let busy = false;
    let loaded = false;
    const backend = await api.isBackend();

    function element(tag, text, className) {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    }

    function badge(code) {
      const node = element("span", labels[code] || "미검증", "verification-badge");
      node.dataset.status = code || "NOT_VERIFIED";
      return node;
    }

    function addDetails(parent, entries) {
      const list = element("dl", undefined, "verification-details");
      entries.forEach(([label, value]) => {
        const item = element("div");
        item.append(element("dt", label), element("dd", value || "없음"));
        list.append(item);
      });
      parent.append(list);
    }

    function visibleRecords() {
      const query = search.value.trim().toLowerCase();
      return records.filter(record =>
        (!query || `${record.name} ${record.filename} ${record.id}`.toLowerCase().includes(query)) &&
        (!type.value || record.type === type.value) &&
        (!status.value || (record.verificationStatus || "NOT_VERIFIED") === status.value));
    }

    function updateSelection() {
      const visible = visibleRecords();
      count.textContent = `${visible.length} / ${records.length}개 증거 · ${selected.size}개 선택됨`;
      selectAll.checked = visible.length > 0 && visible.every(record => selected.has(record.id));
      selectAll.indeterminate = !selectAll.checked && visible.some(record => selected.has(record.id));
      selectAll.disabled = busy || visible.length === 0;
      buttons.forEach(button => {
        button.disabled = busy || !loaded || selected.size === 0;
        button.textContent = busy ? "검증 중…" : "증거 검증";
      });
      root.querySelector("#verification-clear").disabled = busy || !selected.size;
      [search, type, status].forEach(control => { control.disabled = busy || !loaded; });
      targets.replaceChildren();
      const chosen = records.filter(record => selected.has(record.id));
      if (!chosen.length) targets.append(element("p", "검증할 파일을 목록에서 선택해 주세요.", "verification-empty"));
      chosen.forEach(record => {
        const card = element("article", undefined, "verification-target");
        card.append(element("h3", record.name), element("p", record.filename));
        addDetails(card, [["증거 ID", record.id], ["파일 정보", `${record.type} · ${record.size.toLocaleString("ko-KR")} bytes`], ["등록 일시", api.formatDate(record.registeredAt)], ["등록 당시 SHA-256", record.sha256]]);
        targets.append(card);
      });
      api.saveSelection(chosen.map(record => ({ id: record.id, name: record.name, type: record.type })));
    }

    function renderRows() {
      rows.replaceChildren();
      const visible = visibleRecords();
      visible.forEach(record => {
        const row = element("tr");
        row.dataset.verificationEvidenceRow = record.id;
        row.classList.toggle("verification-row-selected", selected.has(record.id));
        const cell = element("td");
        const checkbox = element("input");
        checkbox.type = "checkbox";
        checkbox.checked = selected.has(record.id);
        checkbox.disabled = busy;
        checkbox.setAttribute("aria-label", `${record.filename} 선택`);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selected.add(record.id); else selected.delete(record.id);
          renderRows();
        });
        cell.append(checkbox);
        const name = element("td");
        name.append(element("strong", record.name), element("small", record.filename));
        const state = element("td");
        state.append(badge(record.verificationStatus));
        row.append(cell, name, element("td", record.type), element("td", api.formatDate(record.registeredAt)), element("td", record.id, "verification-id"), state);
        rows.append(row);
      });
      if (!visible.length) {
        const row = element("tr");
        const cell = element("td", records.length ? "검색 조건에 맞는 증거가 없습니다." : "등록된 파일이 없습니다. 증거 관리에서 파일을 등록해 주세요.", "verification-empty");
        cell.colSpan = 6;
        row.append(cell);
        rows.append(row);
      }
      updateSelection();
    }

    function resultCard(result, saved) {
      const card = element("article", undefined, "verification-result");
      card.dataset.verificationResult = result.status;
      card.dataset.saved = String(saved);
      const heading = element("div", undefined, "verification-card-heading");
      heading.append(element("h3", result.filename), badge(result.status));
      card.append(heading, element("p", result.reason));
      addDetails(card, [["증거 ID", result.evidenceId], ["검증 실행 ID", result.id], ["등록 당시 SHA-256", result.expectedHash], ["현재 SHA-256", result.currentHash || "계산하지 못함"], ["검증 일시", api.formatDate(result.completedAt)]]);
      const storage = element("p", saved ? (backend ? "결과와 이력을 서버에 저장했습니다." : "결과와 이력을 이 브라우저에 저장했습니다.") : "결과 저장에 실패했습니다. 목록 상태는 변경되지 않았습니다. 다시 검증해 주세요.", "verification-save-state");
      storage.dataset.saved = String(saved);
      card.append(storage);
      const link = element("a", "증거 상세보기");
      link.href = `evidence.html?id=${encodeURIComponent(result.evidenceId)}`;
      card.append(link);
      return card;
    }

    function renderHistory() {
      history.replaceChildren();
      const entries = records.flatMap(record => Array.isArray(record.verifications) ? record.verifications : [])
        .sort((a, b) => b.completedAt.localeCompare(a.completedAt)).slice(0, 20);
      if (!entries.length) history.append(element("p", "아직 실행한 검증이 없습니다.", "verification-empty"));
      entries.forEach(entry => {
        const detail = element("details", undefined, "verification-history-entry");
        const summary = element("summary", `${entry.filename} · ${labels[entry.status]} · ${api.formatDate(entry.completedAt)}`);
        detail.append(summary, resultCard(entry, true));
        history.append(detail);
      });
    }

    async function loadRecords() {
      records = backend ? await api.list() : await api.store("readonly", tx => tx.objectStore("records").getAll());
      records.sort((a, b) => b.registeredAt.localeCompare(a.registeredAt));
      selected = new Set(Array.from(selected).filter(id => records.some(record => record.id === id)));
      const oldType = type.value;
      type.replaceChildren(new Option("전체 유형", ""));
      Array.from(new Set(records.map(record => record.type))).sort().forEach(value => type.add(new Option(value, value)));
      if (Array.from(type.options).some(option => option.value === oldType)) type.value = oldType;
      loaded = true;
      renderRows();
      renderHistory();
    }

    async function checkFile(record) {
      const result = { id: `VERIFY-${crypto.randomUUID()}`, evidenceId: record.id, filename: record.filename,
        startedAt: new Date().toISOString(), expectedHash: null, currentHash: null, status: "UNAVAILABLE", reason: "파일을 읽지 못했습니다." };
      if (backend) {
        try { return await api.verify(record.id); }
        catch (error) {
          result.reason = error.message || "서버 검증을 완료하지 못했습니다.";
          result.completedAt = new Date().toISOString();
          return { result, saved: false };
        }
      }
      try {
        // Read the metadata and immutable Blob in one transaction, then hash that snapshot.
        const snapshot = await api.store("readonly", tx => {
          const data = {};
          const metadata = tx.objectStore("records").get(record.id);
          const file = tx.objectStore("files").get(record.id);
          metadata.onsuccess = () => { data.record = metadata.result; };
          file.onsuccess = () => { data.file = file.result; };
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
      } catch (error) {
        result.reason = "파일 읽기 또는 해시 계산에 실패해 검증을 완료하지 못했습니다.";
      }
      result.completedAt = new Date().toISOString();
      let saved = false;
      try {
        await api.store("readwrite", tx => {
          const store = tx.objectStore("records");
          const request = store.get(record.id);
          request.onsuccess = () => {
            const latest = request.result;
            if (!latest || latest.sha256 !== result.expectedHash) { tx.abort(); return; }
            latest.verifications = (latest.verifications || []).concat(result);
            if (!latest.lastVerification || latest.lastVerification.completedAt <= result.completedAt) {
              latest.lastVerification = result;
              latest.verificationStatus = result.status;
              latest.status = labels[result.status];
            }
            store.put(latest);
          };
          return request;
        });
        saved = true;
      } catch (error) { /* Keep the computed result visible, explicitly marked as unsaved. */ }
      return { result, saved };
    }

    async function run() {
      if (busy || !loaded || !selected.size) return;
      busy = true;
      const chosen = records.filter(record => selected.has(record.id));
      renderRows();
      results.replaceChildren();
      let stored = 0;
      try {
        for (let index = 0; index < chosen.length; index += 1) {
          message.textContent = `${index + 1} / ${chosen.length}개 파일의 SHA-256을 계산하고 있습니다.`;
          const checked = await checkFile(chosen[index]);
          if (checked.saved) stored += 1;
          results.append(resultCard(checked.result, checked.saved));
        }
        message.textContent = `${chosen.length}개 검증 처리 · ${stored}개 결과 저장${stored !== chosen.length ? " · 저장 실패 항목을 확인해 주세요." : ""}`;
        await loadRecords();
      } catch (error) {
        message.textContent = "검증 처리 또는 목록 갱신에 실패했습니다. 표시된 결과와 저장 여부를 확인하고 다시 시도해 주세요.";
      } finally {
        busy = false;
        renderRows();
      }
    }

    [search, type, status].forEach(control => control.addEventListener(control === search ? "input" : "change", renderRows));
    selectAll.addEventListener("change", () => {
      visibleRecords().forEach(record => { if (selectAll.checked) selected.add(record.id); else selected.delete(record.id); });
      renderRows();
    });
    root.querySelector("#verification-clear").addEventListener("click", () => { selected.clear(); renderRows(); });
    buttons.forEach(button => button.addEventListener("click", run));
    try {
      await loadRecords();
      message.textContent = "파일을 선택하고 ‘증거 검증’을 눌러 주세요.";
    } catch (error) {
      message.textContent = `등록한 파일을 불러오지 못했습니다. ${error.message || "새로고침해 주세요."}`;
      count.textContent = "불러오기 실패";
    }
  }

  window.EvidenceVerification = { init };
})();
