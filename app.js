(function () {
  "use strict";

  const page = (window.location.pathname.split("/").pop() || "index.html").toLowerCase();
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const profileNameKey = "evidence-profile-name";
  const defaultProfileName = "소연";
  let activeLayer = null;
  let activeTimer = null;

  function getProfileName() {
    try {
      return normalize(localStorage.getItem(profileNameKey)) || defaultProfileName;
    } catch (error) {
      return defaultProfileName;
    }
  }

  function applyProfileName() {
    const name = getProfileName();
    document.querySelectorAll("[data-profile-name]").forEach(function (element) {
      element.textContent = name;
    });
    document.querySelectorAll("[data-profile-greeting]").forEach(function (element) {
      element.textContent = `안녕하세요, ${name}님 👋`;
    });
  }

  function exactTextElements(label) {
    return Array.from(document.querySelectorAll("a, button, div, span"))
      .filter((element) => normalize(element.textContent) === label)
      .filter((element) => !Array.from(element.children).some(
        (child) => normalize(child.textContent) === label
      ));
  }

  function isActionSurface(element) {
    if (!element || element === document.body || element === document.documentElement) {
      return false;
    }
    if (element.matches("a, button")) {
      return true;
    }
    const style = element.getAttribute("style") || "";
    return /border-radius\s*:/.test(style) && /height\s*:|padding-(left|right|top|bottom)\s*:/.test(style);
  }

  function actionSurface(textElement) {
    let element = textElement;
    for (let depth = 0; depth < 5 && element; depth += 1) {
      if (isActionSurface(element)) {
        return element;
      }
      element = element.parentElement;
    }
    return textElement;
  }

  function actionTargets(label) {
    const targets = Array.from(new Set(exactTextElements(label).map(actionSurface)));
    const exactTargets = targets.filter((element) => normalize(element.textContent) === label);
    return exactTargets.length ? exactTargets : targets;
  }

  function bind(label, handler, options) {
    const settings = Object.assign({ all: false, primaryOnly: false }, options || {});
    let targets = actionTargets(label);
    if (settings.primaryOnly) {
      targets = targets.filter((element) => /#2E63E8|#2454C6/i.test(element.getAttribute("style") || ""));
    }
    if (!settings.all && targets.length) {
      targets = [targets[0]];
    }
    targets.forEach((target) => {
      if (!target || target.dataset.evidenceAction === "true") {
        return;
      }
      target.dataset.evidenceAction = "true";
      target.style.cursor = "pointer";
      target.setAttribute("role", "button");
      if (!target.hasAttribute("tabindex")) {
        target.tabIndex = 0;
      }
      const run = function (event) {
        event.preventDefault();
        event.stopImmediatePropagation();
        handler(target);
      };
      target.addEventListener("click", run, true);
      target.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          run(event);
        }
      }, true);
    });
  }

  function closeDialog() {
    if (activeTimer) {
      window.clearInterval(activeTimer);
      activeTimer = null;
    }
    if (activeLayer) {
      activeLayer.remove();
      activeLayer = null;
    }
    document.body.style.overflow = "";
  }

  function showDialog(content, options) {
    closeDialog();
    const settings = Object.assign({ wide: false, closeOnBackdrop: true }, options || {});
    const layer = document.createElement("div");
    layer.className = "evidence-dialog-layer";
    layer.setAttribute("role", "presentation");
    layer.innerHTML = `<section class="evidence-dialog${settings.wide ? " evidence-dialog--wide" : ""}" role="dialog" aria-modal="true">${content}</section>`;
    if (settings.closeOnBackdrop) {
      layer.addEventListener("click", function (event) {
        if (event.target === layer) {
          closeDialog();
        }
      });
    }
    document.body.appendChild(layer);
    document.body.style.overflow = "hidden";
    activeLayer = layer;
    const firstControl = layer.querySelector("input, button");
    if (firstControl) {
      firstControl.focus();
    }
    return layer;
  }

  function button(label, action, variant) {
    const className = `evidence-dialog__button${variant ? ` evidence-dialog__button--${variant}` : ""}`;
    return `<button class="${className}" type="button" data-dialog-action="${action}">${label}</button>`;
  }

  function toast(message) {
    const oldToast = document.querySelector(".evidence-toast");
    if (oldToast) oldToast.remove();
    const node = document.createElement("div");
    node.className = "evidence-toast";
    node.setAttribute("role", "status");
    node.textContent = message;
    document.body.appendChild(node);
    window.setTimeout(() => node.remove(), 2600);
  }

  function detailsMarkup(items) {
    return `<div class="evidence-dialog__details">${items.map((item) => `<div><span>${item[0]}</span><strong>${item[1]}</strong></div>`).join("")}</div>`;
  }

  function localDateStamp() {
    const now = new Date();
    return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("");
  }

  function successRegistration(kind) {
    const completionPages = {
      "파일": "evidence_file_complete.html",
      "이미지": "evidence_img_complete.html",
      "웹페이지": "evidence_url_complete.html",
      "영상·음성": "evidence_video_complete.html"
    };
    const completionPage = completionPages[kind];
    if (completionPage) {
      window.location.href = completionPage;
      return;
    }
  }

  function runProgress(config) {
    let percent = config.start || 12;
    const layer = showDialog(`
      <div class="evidence-dialog__head">
        <div class="evidence-dialog__icon">↻</div>
        <div><h2>${config.title}</h2><p>${config.description}</p></div>
      </div>
      <div class="evidence-dialog__item">
        <div><strong>${config.item}</strong><small>${config.meta}</small></div>
        <span class="evidence-dialog__percent">${percent}%</span>
      </div>
      <div class="evidence-progress"><div class="evidence-progress__bar" style="width:${percent}%"></div></div>
      <p class="evidence-dialog__status">${config.status}</p>
      <div class="evidence-dialog__actions">${button("취소", "cancel")}</div>
    `, { closeOnBackdrop: false });
    layer.querySelector('[data-dialog-action="cancel"]').addEventListener("click", closeDialog);
    const bar = layer.querySelector(".evidence-progress__bar");
    const value = layer.querySelector(".evidence-dialog__percent");
    activeTimer = window.setInterval(function () {
      percent = Math.min(100, percent + Math.ceil(Math.random() * 13));
      bar.style.width = `${percent}%`;
      value.textContent = `${percent}%`;
      if (percent >= 100) {
        window.clearInterval(activeTimer);
        activeTimer = null;
        window.setTimeout(config.onComplete, 260);
      }
    }, 160);
  }

  function failureRegistration(kind, retry) {
    const layer = showDialog(`
      <div class="evidence-dialog__head">
        <div class="evidence-dialog__icon evidence-dialog__icon--danger">!</div>
        <div><h2>${kind === "웹페이지" ? "수집 실패" : "업로드 실패"}</h2><p>다시 시도하거나 선택한 항목을 제거할 수 있습니다.</p></div>
      </div>
      <div class="evidence-dialog__item"><div><strong>네트워크 연결을 확인해 주세요.</strong><small>선택한 정보는 현재 화면에 보관되어 있습니다.</small></div></div>
      <div class="evidence-dialog__actions">
        ${button("제거", "remove")}
        ${button("다시 시도", "retry", "primary")}
      </div>
    `);
    layer.querySelector('[data-dialog-action="remove"]').addEventListener("click", closeDialog);
    layer.querySelector('[data-dialog-action="retry"]').addEventListener("click", retry);
  }

  function registerEvidence(kind, item, meta) {
    if (!navigator.onLine) {
      failureRegistration(kind, () => registerEvidence(kind, item, meta));
      return;
    }
    const isUrl = kind === "웹페이지";
    runProgress({
      start: isUrl ? 48 : 32,
      title: isUrl ? "수집 중" : "업로드 중",
      description: isUrl ? "URL의 원본 정보와 화면을 증거로 저장하고 있습니다." : "파일을 안전한 증거 보관함에 업로드하고 있습니다.",
      item,
      meta,
      status: isUrl ? "웹페이지를 분석하고 증거 스냅샷을 생성하고 있습니다." : "화면을 닫아도 업로드는 계속됩니다.",
      onComplete: () => successRegistration(kind)
    });
  }

  function createEditableField(label, settings) {
    const options = Object.assign({ multiline: false, select: null }, settings || {});
    const labelElement = exactTextElements(label).find(function (element) {
      return element.parentElement && Array.from(element.parentElement.children).some(function (child) {
        return child !== element && child.tagName === "DIV";
      });
    });
    if (!labelElement) return null;
    const field = Array.from(labelElement.parentElement.children).find(function (child) {
      return child !== labelElement && child.tagName === "DIV";
    });
    if (!field) return null;
    const existing = field.querySelector("input, textarea, select");
    if (existing) return existing;

    const value = normalize(field.textContent).replace(/\s*▼$/, "");
    const control = options.select ? document.createElement("select") : document.createElement(options.multiline ? "textarea" : "input");
    control.className = `evidence-inline-${options.multiline ? "textarea" : options.select ? "select" : "input"}`;
    control.setAttribute("aria-label", label);
    control.dataset.evidenceField = options.name || label;
    if (options.select) {
      options.select.forEach(function (option) {
        const optionElement = document.createElement("option");
        optionElement.value = option;
        optionElement.textContent = option;
        optionElement.selected = option === value;
        control.appendChild(optionElement);
      });
    } else {
      control.value = options.value === undefined ? value : options.value;
      control.placeholder = options.placeholder || "";
      if (!options.multiline) control.type = options.type || "text";
      if (options.required) control.required = true;
    }
    field.textContent = "";
    field.classList.add("evidence-editable-field");
    field.appendChild(control);
    return control;
  }

  function createUrlField(defaultUrl) {
    const textElement = exactTextElements(defaultUrl)[0];
    if (!textElement) return null;
    let field = textElement.parentElement;
    for (let depth = 0; depth < 3 && field; depth += 1) {
      const style = field.getAttribute("style") || "";
      if (/height\s*:\s*48px/.test(style) && /border-radius/.test(style)) break;
      field = field.parentElement;
    }
    if (!field) return null;
    const existing = field.querySelector("input");
    if (existing) return existing;
    const input = document.createElement("input");
    input.className = "evidence-inline-input";
    input.type = "url";
    input.required = true;
    input.value = defaultUrl;
    input.placeholder = "https://example.com/page";
    input.setAttribute("aria-label", "수집할 웹페이지 URL");
    input.dataset.evidenceField = "url";
    field.textContent = "";
    field.classList.add("evidence-editable-field");
    field.appendChild(input);
    return input;
  }

  function setupEvidenceFields(config) {
    const fields = {
      name: createEditableField("증거 이름", { name: "name" }),
      source: createEditableField("수집 출처", { name: "source", select: ["직접 수집", "제공 받음", "웹에서 수집"] }),
      description: createEditableField("설명", { name: "description", multiline: true, value: "", placeholder: "증거에 대한 설명을 입력하세요" }),
      memo: createEditableField("메모", { name: "memo", multiline: true, value: "", placeholder: "필요한 내용을 입력하세요" })
    };
    if (config.kind === "웹페이지") fields.url = createUrlField(config.defaultUrl);
    return fields;
  }

  function readSelectedFile() {
    try {
      return sessionStorage.getItem(`evidence-file:${page}`) || "";
    } catch (error) {
      return "";
    }
  }

  function setSelectedFile(file, fields) {
    try {
      sessionStorage.setItem(`evidence-file:${page}`, file.name);
    } catch (error) {
      // The page remains usable when session storage is unavailable.
    }
    if (fields.name) {
      fields.name.value = file.name.replace(/\.[^.]+$/, "");
    }
    toast(`${file.name} 파일을 선택했습니다.`);
  }

  function chooseEvidenceFile(accept, fields) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept || "*/*";
    input.hidden = true;
    document.body.appendChild(input);
    input.addEventListener("change", function () {
      const file = input.files && input.files[0];
      if (file) setSelectedFile(file, fields);
      input.remove();
    }, { once: true });
    input.click();
  }

  function removeSelectedFile(fields) {
    try {
      sessionStorage.removeItem(`evidence-file:${page}`);
    } catch (error) {
      // The selected file stays cleared in the current form when storage is unavailable.
    }
    if (fields.name) fields.name.value = "";
    toast("선택한 파일을 제거했습니다.");
  }

  function setupDropZone(config, fields) {
    const hint = config.kind === "이미지" ? "이미지를 이곳으로 드래그하거나 파일을 선택하세요." : config.kind === "영상·음성" ? "파일을 이곳으로 드래그하거나 파일을 선택하세요." : "파일을 이곳으로 드래그하거나 파일을 선택하세요.";
    const hintElement = exactTextElements(hint)[0];
    if (!hintElement) return;
    let dropZone = hintElement.parentElement;
    for (let depth = 0; depth < 3 && dropZone; depth += 1) {
      const style = dropZone.getAttribute("style") || "";
      if (/border-radius/.test(style) && /padding/.test(style)) break;
      dropZone = dropZone.parentElement;
    }
    if (!dropZone) return;
    dropZone.classList.add("evidence-drop-zone");
    dropZone.addEventListener("dragover", function (event) {
      event.preventDefault();
      dropZone.classList.add("evidence-drop-zone--active");
    });
    dropZone.addEventListener("dragleave", function () {
      dropZone.classList.remove("evidence-drop-zone--active");
    });
    dropZone.addEventListener("drop", function (event) {
      event.preventDefault();
      dropZone.classList.remove("evidence-drop-zone--active");
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) setSelectedFile(file, fields);
    });
  }

  function isHttpUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "http:" || url.protocol === "https:";
    } catch (error) {
      return false;
    }
  }

  function collectUrl(input, defaultUrl) {
    if (!input) return false;
    const value = normalize(input.value);
    input.value = value;
    input.setCustomValidity(isHttpUrl(value) ? "" : "http 또는 https로 시작하는 웹페이지 주소를 입력해 주세요.");
    if (!input.reportValidity()) return false;
    try {
      sessionStorage.setItem(`evidence-url:${page}`, value);
    } catch (error) {
      // The preview remains available in the current page when storage is unavailable.
    }
    exactTextElements(defaultUrl).forEach(function (element) {
      element.textContent = value;
    });
    toast("웹페이지 미리보기를 준비했습니다.");
    return true;
  }

  function createEvidenceListSearch() {
    const placeholder = "파일명 또는 증거 ID 검색";
    const placeholderElement = exactTextElements(placeholder)[0];
    if (!placeholderElement) return null;
    const input = document.createElement("input");
    input.className = "evidence-list-search";
    input.type = "search";
    input.placeholder = placeholder;
    input.setAttribute("aria-label", placeholder);
    placeholderElement.replaceWith(input);
    return input;
  }

  function createEvidenceListFilter(label, options, fieldName) {
    const labelElement = exactTextElements(label)[0];
    if (!labelElement) return null;
    const container = labelElement.parentElement;
    const select = document.createElement("select");
    select.className = "evidence-list-filter";
    select.setAttribute("aria-label", label);
    select.dataset.evidenceListFilter = fieldName;
    options.forEach(function (option) {
      const element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      select.appendChild(element);
    });
    container.textContent = "";
    container.appendChild(select);
    return select;
  }

  function createEvidenceFilterActions(container) {
    if (!container) return {};
    const actions = document.createElement("div");
    actions.className = "evidence-filter-actions";
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "evidence-filter-apply";
    apply.textContent = "검색";
    apply.setAttribute("aria-label", "검색 조건 적용");
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "evidence-filter-reset";
    reset.textContent = "초기화";
    reset.setAttribute("aria-label", "검색 조건 초기화");
    actions.append(apply, reset);
    container.appendChild(actions);
    return { apply, reset };
  }

  function findEvidenceRow(name, id) {
    const nameElement = exactTextElements(name)[0];
    let row = nameElement;
    for (let depth = 0; depth < 5 && row; depth += 1) {
      const style = row.getAttribute && row.getAttribute("style") || "";
      if (/height\s*:\s*(70|80)px/.test(style) && normalize(row.textContent).includes(id)) return row;
      row = row.parentElement;
    }
    return null;
  }

  function createEvidenceCheckbox(cell, label, id) {
    if (!cell) return null;
    cell.textContent = "";
    const controlLabel = document.createElement("label");
    controlLabel.className = "evidence-list-checkbox";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = id;
    input.setAttribute("aria-label", label);
    const mark = document.createElement("span");
    mark.setAttribute("aria-hidden", "true");
    controlLabel.append(input, mark);
    cell.appendChild(controlLabel);
    return input;
  }

  function readVerificationSelection() {
    try {
      const value = JSON.parse(sessionStorage.getItem("evidence-verification-selection"));
      return Array.isArray(value) ? value : [];
    } catch (error) {
      return [];
    }
  }

  function saveVerificationSelection(records) {
    try {
      sessionStorage.setItem("evidence-verification-selection", JSON.stringify(records));
    } catch (error) {
      // Selection is still available while the current page remains open.
    }
  }

  function setupEvidenceManagement() {
    if (page !== "evidence.html") return;
    const records = [
      { name: "evidence_001.html", id: "EV-20261006-001", type: "HTML", status: "정상", date: "2026-10-06" },
      { name: "screenshot_001.png", id: "EV-20261006-002", type: "PNG", status: "정상", date: "2026-10-06" },
      { name: "report_001.pdf", id: "EV-20261005-003", type: "PDF", status: "검증 필요", date: "2026-10-05" },
      { name: "image_002.jpg", id: "EV-20261005-004", type: "JPG", status: "위변조 의심", date: "2026-10-05" },
      { name: "web_capture_001", id: "EV-20261004-005", type: "URL", status: "정상", date: "2026-10-04" }
    ].map(function (record) {
      const row = findEvidenceRow(record.name, record.id);
      if (row) row.dataset.evidenceListRow = record.id;
      return Object.assign(record, { row });
    }).filter(function (record) { return record.row; });
    if (!records.length) return;

    const search = createEvidenceListSearch();
    const typeFilter = createEvidenceListFilter("전체 유형", [
      { value: "", label: "전체 유형" }, { value: "HTML", label: "HTML" }, { value: "PNG", label: "PNG" },
      { value: "PDF", label: "PDF" }, { value: "JPG", label: "JPG" }, { value: "URL", label: "URL" }
    ], "type");
    const statusFilter = createEvidenceListFilter("전체 상태", [
      { value: "", label: "전체 상태" }, { value: "정상", label: "정상" },
      { value: "검증 필요", label: "검증 필요" }, { value: "위변조 의심", label: "위변조 의심" }
    ], "status");
    const dateFilter = createEvidenceListFilter("수집 기간", [
      { value: "", label: "수집 기간" }, { value: "2026-10-06", label: "2026년 10월 6일" },
      { value: "2026-10-05", label: "2026년 10월 5일" }, { value: "2026-10-04", label: "2026년 10월 4일" }
    ], "date");
    const sortFilter = createEvidenceListFilter("최신순", [
      { value: "latest", label: "최신순" }, { value: "oldest", label: "오래된순" }, { value: "name", label: "파일명순" }
    ], "sort");
    const filterActions = createEvidenceFilterActions(dateFilter && dateFilter.parentElement && dateFilter.parentElement.parentElement);

    const title = exactTextElements("증거 목록")[0];
    const count = title && title.parentElement && Array.from(title.parentElement.children).find(function (element) {
      return normalize(element.textContent) === "24개";
    });
    const tableHeader = records[0].row.previousElementSibling;
    const allCheckbox = createEvidenceCheckbox(tableHeader && tableHeader.firstElementChild, "표시된 증거 전체 선택", "all");
    const selected = new Set();
    records.forEach(function (record) {
      record.checkbox = createEvidenceCheckbox(record.row.firstElementChild, `${record.name} 선택`, record.id);
    });

    const verifyText = exactTextElements("선택 항목 검증")[0];
    const verifyButton = verifyText && actionSurface(verifyText);
    if (verifyButton) {
      verifyButton.classList.add("evidence-selection-action");
      verifyButton.setAttribute("role", "button");
    }

    function visibleRecords() {
      return records.filter(function (record) { return !record.row.hidden; });
    }

    function updateSelection() {
      const visible = visibleRecords();
      if (allCheckbox) {
        allCheckbox.checked = visible.length > 0 && visible.every(function (record) { return selected.has(record.id); });
        allCheckbox.indeterminate = visible.some(function (record) { return selected.has(record.id); }) && !allCheckbox.checked;
      }
      if (verifyButton) {
        verifyButton.classList.toggle("evidence-selection-action--ready", selected.size > 0);
        verifyButton.setAttribute("aria-disabled", String(selected.size === 0));
        verifyButton.setAttribute("title", selected.size ? `${selected.size}개 증거 검증하기` : "검증할 증거를 선택해 주세요");
      }
    }

    function applyFilters() {
      const query = normalize(search && search.value).toLowerCase();
      const type = typeFilter && typeFilter.value;
      const status = statusFilter && statusFilter.value;
      const date = dateFilter && dateFilter.value;
      records.forEach(function (record) {
        const matched = (!query || `${record.name} ${record.id}`.toLowerCase().includes(query)) &&
          (!type || record.type === type) && (!status || record.status === status) && (!date || record.date === date);
        record.row.hidden = !matched;
      });
      if (count) {
        const visibleCount = visibleRecords().length;
        count.textContent = visibleCount === records.length ? "24개" : `${visibleCount}개 표시`;
      }
      updateSelection();
    }

    function applySort() {
      const mode = sortFilter && sortFilter.value;
      const sorted = records.slice().sort(function (left, right) {
        if (mode === "oldest") return left.date.localeCompare(right.date);
        if (mode === "name") return left.name.localeCompare(right.name);
        return right.date.localeCompare(left.date);
      });
      let anchor = tableHeader;
      sorted.forEach(function (record) {
        anchor.after(record.row);
        anchor = record.row;
      });
    }

    records.forEach(function (record) {
      record.checkbox.addEventListener("change", function () {
        if (record.checkbox.checked) selected.add(record.id);
        else selected.delete(record.id);
        updateSelection();
      });
    });
    if (allCheckbox) {
      allCheckbox.addEventListener("change", function () {
        visibleRecords().forEach(function (record) {
          record.checkbox.checked = allCheckbox.checked;
          if (allCheckbox.checked) selected.add(record.id);
          else selected.delete(record.id);
        });
        updateSelection();
      });
    }
    filterActions.apply?.addEventListener("click", applyFilters);
    filterActions.reset?.addEventListener("click", function () {
      if (search) search.value = "";
      [typeFilter, statusFilter, dateFilter].filter(Boolean).forEach(function (control) { control.value = ""; });
      applyFilters();
      search?.focus();
    });
    search?.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        applyFilters();
      }
    });
    if (sortFilter) sortFilter.addEventListener("change", applySort);
    function beginVerification(event) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!selected.size) {
        toast("검증할 증거를 한 개 이상 선택해 주세요.");
        return;
      }
      const selectedRecords = records.filter(function (record) { return selected.has(record.id); }).map(function (record) {
        return { name: record.name, id: record.id, type: record.type };
      });
      saveVerificationSelection(selectedRecords);
      window.location.href = "verification.html";
    }
    if (verifyButton) {
      verifyButton.addEventListener("click", beginVerification, true);
      verifyButton.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") beginVerification(event);
      }, true);
    }
    applyFilters();
  }

  function verificationFlow() {
    const selectedRecords = readVerificationSelection();
    if (!selectedRecords.length && sessionStorage.getItem("evidence-verification-selection") !== null) {
      toast("검증할 증거를 한 개 이상 선택해 주세요.");
      return;
    }
    const selectedEvidence = selectedRecords[0];
    const evidence = selectedEvidence || { name: "evidence_video_001.mp4", id: "EV-20261008-001", type: "영상", size: "18.6 MB" };
    runProgress({
      start: 22,
      title: "무결성 검증 중…",
      description: "현재 SHA-256 값을 계산하고 원본 기록과 비교하고 있습니다.",
      item: evidence.name,
      meta: `${evidence.id} · ${evidence.type}${evidence.size ? ` · ${evidence.size}` : ""}`,
      status: "해시 계산 및 비교 중",
      onComplete: function () { verificationSuccess(evidence); }
    });
  }

  function findVerificationSelectionCell(row) {
    if (!row) return null;
    return Array.from(row.children).find(function (child) {
      return /width\s*:\s*54px/.test(child.getAttribute("style") || "");
    }) || null;
  }

  function replaceVerificationSearch() {
    const placeholder = "증거명 또는 EV-20261008-001 입력";
    const placeholderElement = exactTextElements(placeholder)[0];
    if (!placeholderElement) return null;
    const input = document.createElement("input");
    input.type = "search";
    input.className = "verification-evidence-search";
    input.placeholder = "파일명 또는 증거 ID 검색";
    input.setAttribute("aria-label", "파일명 또는 증거 ID 검색");
    placeholderElement.replaceWith(input);
    return input;
  }

  function removeAlternativeVerificationState() {
    const heading = exactTextElements("대안 상태 예시 · 위변조 의심")[0];
    const section = heading && heading.parentElement && heading.parentElement.parentElement;
    if (section && normalize(section.textContent).includes("대안 상태 예시")) section.remove();
  }

  function setupVerificationPicker() {
    const records = [
      { name: "evidence_001.html", id: "EV-20261006-001", type: "HTML", status: "정상", date: "2026/10/06 10:32", captured: "2026/10/06 10:32:00", size: "42 KB" },
      { name: "screenshot_001.png", id: "EV-20261006-002", type: "이미지", status: "정상", date: "2026/10/06 10:21", captured: "2026/10/06 10:21:00", size: "1.8 MB" },
      { name: "evidence_video_001.mp4", id: "EV-20261008-008", type: "영상", status: "검증 필요", date: "2026/10/08 15:32", captured: "2026/10/08 15:32:21", size: "18.6 MB · 00:02:34" },
      { name: "report_001.pdf", id: "EV-20261005-003", type: "PDF", status: "검증 필요", date: "2026/10/05 18:40", captured: "2026/10/05 18:40:00", size: "856 KB" }
    ].map(function (record) {
      const row = findEvidenceRow(record.name, record.id);
      if (row) {
        row.dataset.verificationEvidenceRow = record.id;
        row.classList.add("verification-evidence-row");
      }
      return Object.assign(record, { row });
    }).filter(function (record) { return record.row; });
    if (!records.length) return;

    const search = replaceVerificationSearch();
    const typeFilter = createEvidenceListFilter("전체 유형", [
      { value: "", label: "전체 유형" }, { value: "HTML", label: "HTML" }, { value: "이미지", label: "이미지" },
      { value: "영상", label: "영상" }, { value: "PDF", label: "PDF" }
    ], "verification-type");
    const statusFilter = createEvidenceListFilter("전체 상태", [
      { value: "", label: "전체 상태" }, { value: "정상", label: "정상" }, { value: "검증 필요", label: "검증 필요" }
    ], "verification-status");
    records.forEach(function (record) {
      record.checkbox = createEvidenceCheckbox(findVerificationSelectionCell(record.row), `${record.name} 선택`, record.id);
    });

    const storedSelection = readVerificationSelection();
    const selected = new Set([storedSelection.length ? storedSelection[0].id : "EV-20261008-008"]);
    const count = exactTextElements("4개 증거 · 1개 선택됨")[0];
    const targetTitle = exactTextElements("검증 대상 증거")[0];
    const targetCard = targetTitle && targetTitle.parentElement && targetTitle.parentElement.parentElement;

    function visibleRecords() {
      return records.filter(function (record) { return !record.row.hidden; });
    }

    function updateTargetCard(record) {
      if (!targetCard || !record) return;
      const nameElement = Array.from(targetCard.querySelectorAll("div")).find(function (element) {
        return normalize(element.textContent) === "evidence_video_001.mp4" || records.some(function (item) { return normalize(element.textContent) === item.name; });
      });
      if (nameElement) nameElement.textContent = record.name;
      const metaElement = Array.from(targetCard.querySelectorAll("div")).find(function (element) {
        return /^(영상|HTML|이미지|PDF) · /.test(normalize(element.textContent));
      });
      if (metaElement) metaElement.textContent = `${record.type} · ${record.size}`;
      const idElement = Array.from(targetCard.querySelectorAll("div")).find(function (element) {
        return records.some(function (item) { return normalize(element.textContent) === item.id; });
      });
      if (idElement) idElement.textContent = record.id;
      const capturedElement = Array.from(targetCard.querySelectorAll("div")).find(function (element) {
        return /^2026\/10\/\d{2} \d{2}:\d{2}:\d{2}$/.test(normalize(element.textContent));
      });
      if (capturedElement) capturedElement.textContent = record.captured;
    }

    function updateSelection() {
      records.forEach(function (record) {
        const checked = selected.has(record.id);
        record.checkbox.checked = checked;
        record.row.classList.toggle("verification-evidence-row--selected", checked);
      });
      if (count) count.textContent = `${records.length}개 증거 · ${selected.size}개 선택됨`;
      const primary = records.find(function (record) { return selected.has(record.id); });
      updateTargetCard(primary);
      saveVerificationSelection(records.filter(function (record) { return selected.has(record.id); }).map(function (record) {
        return { name: record.name, id: record.id, type: record.type, size: record.size };
      }));
    }

    function applyFilters() {
      const query = normalize(search && search.value).toLowerCase();
      const type = typeFilter && typeFilter.value;
      const status = statusFilter && statusFilter.value;
      records.forEach(function (record) {
        record.row.hidden = !((!query || `${record.name} ${record.id}`.toLowerCase().includes(query)) &&
          (!type || record.type === type) && (!status || record.status === status));
      });
      updateSelection();
    }

    records.forEach(function (record) {
      record.checkbox.addEventListener("change", function () {
        if (record.checkbox.checked) {
          selected.clear();
          selected.add(record.id);
        }
        else selected.delete(record.id);
        updateSelection();
      });
    });
    [search, typeFilter, statusFilter].filter(Boolean).forEach(function (control) {
      control.addEventListener(control === search ? "input" : "change", applyFilters);
    });
    updateSelection();
  }

  function verificationSuccess(evidence) {
    const selectedEvidence = evidence || { name: "evidence_video_001.mp4", id: "EV-20261008-001", type: "영상", size: "18.6 MB" };
    const layer = showDialog(`
      <div class="evidence-dialog__head">
        <div class="evidence-dialog__icon evidence-dialog__icon--success">✓</div>
        <div><h2>무결성 검증 완료</h2><p>현재 파일의 SHA-256이 등록 당시의 SHA-256과 일치합니다.</p></div>
      </div>
      <div class="evidence-dialog__item"><div><strong>${selectedEvidence.name}</strong><small>${selectedEvidence.id} · ${selectedEvidence.type}${selectedEvidence.size ? ` · ${selectedEvidence.size}` : ""}</small></div></div>
      ${detailsMarkup([["검증 결과", "일치 · SHA-256"], ["검증 일시", new Date().toLocaleString("ko-KR")], ["기록 상태", "이력 저장 완료"]])}
      <div class="evidence-dialog__actions">
        ${button("증거 상세", "evidence")}
        ${button("결과 저장", "save", "primary")}
      </div>
    `, { wide: true });
    layer.querySelector('[data-dialog-action="evidence"]').addEventListener("click", () => { window.location.href = "evidence.html"; });
    layer.querySelector('[data-dialog-action="save"]').addEventListener("click", function () {
      closeDialog();
      toast("검증 결과를 증거 이력에 저장했습니다.");
    });
  }

  function setupRegistration() {
    const configs = {
      "evidence_file.html": { kind: "파일", meta: "문서 · 파일", submit: "증거 등록", select: "파일 선택", accept: "*/*" },
      "evidence_img.html": { kind: "이미지", meta: "이미지 파일", submit: "이미지 증거 등록", select: "이미지 선택", accept: "image/*" },
      "evidence_video.html": { kind: "영상·음성", meta: "영상 · 음성 파일", submit: "영상·음성 증거 등록", select: "파일 선택", accept: "video/*,audio/*" },
      "evidence_url.html": { kind: "웹페이지", meta: "본문과 메타데이터 수집", submit: "웹페이지 증거 저장", select: "URL 수집", defaultUrl: "https://example.com/article/123" }
    };
    const config = configs[page];
    if (!config) return;
    const fields = setupEvidenceFields(config);
    if (config.kind === "웹페이지") {
      bind(config.select, () => collectUrl(fields.url, config.defaultUrl), { primaryOnly: true });
    } else {
      const openPicker = () => chooseEvidenceFile(config.accept, fields);
      bind(config.select, openPicker, { primaryOnly: true });
      if (config.kind === "이미지") bind("이미지 변경", openPicker, { all: true });
      if (config.kind === "영상·음성") bind("파일 변경", openPicker, { all: true });
      bind("삭제", () => removeSelectedFile(fields), { all: true });
      setupDropZone(config, fields);
    }
    bind(config.submit, function () {
      if (config.kind === "웹페이지") {
        let selectedUrl = "";
        try {
          selectedUrl = sessionStorage.getItem(`evidence-url:${page}`) || "";
        } catch (error) {
          // Validation below keeps the action clear when browser storage is unavailable.
        }
        if (!selectedUrl) {
          toast("URL 수집을 먼저 눌러 미리보기를 확인해 주세요.");
          return;
        }
        registerEvidence(config.kind, selectedUrl, config.meta);
        return;
      }
      const selected = readSelectedFile();
      if (!selected) {
        toast("등록할 파일을 먼저 선택해 주세요.");
        return;
      }
      registerEvidence(config.kind, selected, config.meta);
    }, { primaryOnly: true });
  }

  function setupVerification() {
    if (page !== "verification.html") return;
    removeAlternativeVerificationState();
    setupVerificationPicker();
    ["증거 검증", "검증 시작", "다시 검증", "무결성 검증 시작"].forEach((label) => bind(label, verificationFlow, { primaryOnly: true, all: true }));
    bind("검증 결과 저장", () => toast("검증 결과를 증거 이력에 저장했습니다."), { all: true });
  }

  function setupEvidenceCompletion() {
    const completionSources = {
      "evidence_file_complete.html": "evidence_file.html",
      "evidence_img_complete.html": "evidence_img.html",
      "evidence_video_complete.html": "evidence_video.html"
    };
    const sourcePage = completionSources[page];
    if (!sourcePage) return;
    const selected = sessionStorage.getItem(`evidence-file:${sourcePage}`);
    if (!selected) return;
    document.querySelectorAll("[data-evidence-filename]").forEach(function (element) {
      element.textContent = selected;
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && activeLayer) closeDialog();
  });

  applyProfileName();
  window.addEventListener("storage", function (event) {
    if (event.key === profileNameKey) applyProfileName();
  });
  setupRegistration();
  setupEvidenceManagement();
  setupVerification();
  setupEvidenceCompletion();
})();
