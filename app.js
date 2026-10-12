(function () {
  "use strict";

  const page = (window.location.pathname.split("/").pop() || "index.html").toLowerCase();
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const profileNameKey = "evidence-profile-name";
  const defaultProfileName = "소연";
  let activeLayer = null;
  let activeTimer = null;
  let selectedUpload = null;
  let registrationPending = false;

  const api = (function () {
    let availability;
    const tokenKey = "evidence-auth-token";
    function enabled() {
      if (!availability) availability = fetch("/api/health", { cache: "no-store" })
        .then(response => response.ok && response.headers.get("content-type")?.includes("application/json"))
        .catch(() => false);
      return availability;
    }
    async function request(path, options = {}) {
      const token = sessionStorage.getItem(tokenKey);
      if (!token) {
        location.href = `login.html?next=${encodeURIComponent(location.pathname.split("/").pop() + location.search)}`;
        throw new Error("로그인이 필요합니다.");
      }
      const response = await fetch(path, { ...options, headers: { ...options.headers, Authorization: `Bearer ${token}` }, cache: "no-store" });
      if (response.status === 401) {
        sessionStorage.removeItem(tokenKey);
        location.href = `login.html?next=${encodeURIComponent(location.pathname.split("/").pop() + location.search)}`;
        throw new Error("로그인이 만료되었습니다.");
      }
      if (!response.ok) {
        let detail = "요청을 처리하지 못했습니다.";
        try { detail = (await response.json()).detail || detail; } catch (_) { /* Response may be empty. */ }
        throw new Error(detail);
      }
      return response;
    }
    return {
      enabled,
      async list() { return (await request("/api/evidence")).json(); },
      async get(id) { return (await request(`/api/evidence/${encodeURIComponent(id)}`)).json(); },
      async upload(file, fields) {
        const data = new FormData();
        data.append("file", file, file.name);
        Object.entries(fields).forEach(([key, value]) => data.append(key, value || ""));
        return (await request("/api/evidence", { method: "POST", body: data })).json();
      },
      async verify(id) { return (await request(`/api/evidence/${encodeURIComponent(id)}/verify`, { method: "POST" })).json(); },
      async download(id) { return (await request(`/api/evidence/${encodeURIComponent(id)}/download`)).blob(); }
    };
  })();
  window.EvidenceApi = api;

  // IndexedDB remains available for static, offline previews.
  function evidenceStore(mode, operation) {
    return new Promise(function (resolve, reject) {
      const opening = indexedDB.open("evidence-local-files", 1);
      opening.onupgradeneeded = function () {
        const db = opening.result;
        db.createObjectStore("records", { keyPath: "id" });
        db.createObjectStore("files");
      };
      opening.onerror = function () { reject(opening.error); };
      opening.onblocked = function () { reject(new Error("저장소를 열 수 없습니다.")); };
      opening.onsuccess = function () {
        const db = opening.result;
        db.onversionchange = function () { db.close(); };
        let request;
        let transaction;
        try {
          transaction = db.transaction(["records", "files"], mode);
          transaction.oncomplete = function () { db.close(); resolve(request && request.result); };
          transaction.onabort = function () { db.close(); reject(transaction.error || new Error("저장 작업이 취소되었습니다.")); };
          request = operation(transaction);
        } catch (error) {
          if (transaction) { try { transaction.abort(); } catch (abortError) { /* Already finished. */ } }
          db.close();
          reject(error);
        }
      };
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character];
    });
  }

  function displayDate(value) {
    return new Intl.DateTimeFormat("ko-KR", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short"
    }).format(new Date(value));
  }

  async function registerLocalFile(kind, fields, target) {
    if (registrationPending || !selectedUpload) return;
    registrationPending = true;
    target.setAttribute("aria-disabled", "true");
    const selection = selectedUpload;
    const file = selection.file;
    const name = normalize(fields.name && fields.name.value) || file.name;
    const description = fields.description ? fields.description.value : "";
    const memo = fields.memo ? fields.memo.value : "";
    const source = fields.source ? fields.source.value : "직접 수집";
    toast("선택한 파일을 등록하고 있습니다.");
    try {
      if (await api.enabled()) {
        const record = await api.upload(file, { name, kind, source, description, memo });
        const destination = { "파일": "evidence_file_complete.html", "이미지": "evidence_img_complete.html", "영상·음성": "evidence_video_complete.html" }[kind];
        window.location.href = `${destination}?id=${encodeURIComponent(record.id)}`;
        return;
      }
      const bytes = await file.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      if (selectedUpload !== selection) {
        toast("선택한 파일이 변경되었습니다. 새 파일을 확인한 뒤 등록해 주세요.");
        return;
      }
      const now = new Date();
      const extension = /\.([^.]+)$/.exec(file.name);
      const record = {
        id: `LOCAL-${localDateStamp()}-${crypto.randomUUID()}`,
        name: name, filename: file.name, type: extension ? extension[1].toUpperCase() : file.type || "기타",
        mime: file.type, size: file.size, kind: kind, source: source, description: description, memo: memo,
        selectedAt: selection.selectedAt, registeredAt: now.toISOString(),
        date: [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("-"),
        sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join(""),
        status: "미검증", verificationStatus: "NOT_VERIFIED", storage: "browser"
      };
      await evidenceStore("readwrite", function (transaction) {
        transaction.objectStore("files").put(new Blob([bytes], { type: file.type }), record.id);
        return transaction.objectStore("records").put(record);
      });
      const destination = { "파일": "evidence_file_complete.html", "이미지": "evidence_img_complete.html", "영상·음성": "evidence_video_complete.html" }[kind];
      window.location.href = `${destination}?id=${encodeURIComponent(record.id)}`;
    } catch (error) {
      toast(error.message || "파일을 등록하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      registrationPending = false;
      target.setAttribute("aria-disabled", "false");
    }
  }

  function showLocalEvidence(record) {
    const layer = showDialog(`
      <div class="evidence-dialog__head"><div><h2>증거 상세</h2><p>${record.storage === "server" ? "서버에 보관된 원본 파일입니다." : "이 브라우저에 저장된 파일입니다."}</p></div></div>
      ${detailsMarkup([["증거 이름", record.name], ["파일명", record.filename], ["증거 ID", record.id], ["파일 유형", record.type], ["파일 크기", `${record.size.toLocaleString("ko-KR")} bytes`], ["등록 일시", displayDate(record.registeredAt)], ["파일 선택 일시", displayDate(record.selectedAt)], ["SHA-256", record.sha256], ["무결성 상태", record.status], ["수집 출처", record.source], ["설명", record.description || "없음"], ["메모", record.memo || "없음"]])}
      <div class="evidence-dialog__actions">${button("닫기", "close")}${button("파일 다운로드", "download", "primary")}</div>
    `, { wide: true });
    layer.querySelector('[data-dialog-action="close"]').addEventListener("click", closeDialog);
    layer.querySelector('[data-dialog-action="download"]').addEventListener("click", async function () {
      try {
        const file = record.storage === "server" ? await api.download(record.id) : await evidenceStore("readonly", tx => tx.objectStore("files").get(record.id));
        if (!file) throw new Error("파일 없음");
        const url = URL.createObjectURL(file);
        const link = document.createElement("a");
        link.href = url;
        link.download = record.filename;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { toast("저장한 파일을 불러오지 못했습니다."); }
    });
  }

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
    return `<div class="evidence-dialog__details">${items.map((item) => `<div><span>${escapeHtml(item[0])}</span><strong>${escapeHtml(item[1])}</strong></div>`).join("")}</div>`;
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
        <div><strong data-progress-item></strong><small>${config.meta}</small></div>
        <span class="evidence-dialog__percent">${percent}%</span>
      </div>
      <div class="evidence-progress"><div class="evidence-progress__bar" style="width:${percent}%"></div></div>
      <p class="evidence-dialog__status">${config.status}</p>
      <div class="evidence-dialog__actions">${button("취소", "cancel")}</div>
    `, { closeOnBackdrop: false });
    layer.querySelector("[data-progress-item]").textContent = config.item;
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
    if (["evidence_file.html", "evidence_img.html", "evidence_video.html"].includes(page)) return selectedUpload ? selectedUpload.file.name : "";
    try {
      return sessionStorage.getItem(`evidence-file:${page}`) || "";
    } catch (error) {
      return "";
    }
  }

  function setSelectedFile(file, fields) {
    if (registrationPending) { toast("등록 중입니다. 저장이 끝날 때까지 기다려 주세요."); return; }
    selectedUpload = { file: file, selectedAt: new Date().toISOString() };
    try {
      sessionStorage.setItem(`evidence-file:${page}`, file.name);
    } catch (error) {
      // The page remains usable when session storage is unavailable.
    }
    if (fields.name) {
      fields.name.value = file.name.replace(/\.[^.]+$/, "");
    }
    if (fields.updateUpload) fields.updateUpload(file);
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
    input.addEventListener("cancel", function () { input.remove(); }, { once: true });
    input.click();
  }

  function removeSelectedFile(fields) {
    if (registrationPending) { toast("등록 중입니다. 저장이 끝날 때까지 기다려 주세요."); return; }
    selectedUpload = null;
    try {
      sessionStorage.removeItem(`evidence-file:${page}`);
    } catch (error) {
      // The selected file stays cleared in the current form when storage is unavailable.
    }
    if (fields.name) fields.name.value = "";
    if (fields.updateUpload) fields.updateUpload(null);
    toast("선택한 파일을 제거했습니다.");
  }

  function setupFileUploadDetails(fields) {
    if (page !== "evidence_file.html") return;
    const card = document.querySelector("[data-upload-card]");
    const emptyState = document.querySelector("[data-upload-empty]");
    const badge = document.querySelector("[data-upload-badge]");
    const filename = document.querySelector("[data-upload-filename]");
    const summary = document.querySelector("[data-upload-summary]");
    const metadata = {};
    document.querySelectorAll("[data-upload-meta]").forEach(function (element) {
      metadata[element.dataset.uploadMeta] = element;
    });
    function sizeLabel(bytes) {
      if (bytes < 1024) return `${bytes} B`;
      const units = ["KB", "MB", "GB", "TB"];
      const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)) - 1, units.length - 1);
      return `${(bytes / Math.pow(1024, index + 1)).toLocaleString("ko-KR", { maximumFractionDigits: 2 })} ${units[index]} (${bytes.toLocaleString("ko-KR")} bytes)`;
    }
    fields.updateUpload = async function (file) {
      const selection = file ? { file: file, selectedAt: new Date().toISOString() } : null;
      selectedUpload = selection;
      card.hidden = !file;
      emptyState.hidden = Boolean(file);
      badge.textContent = file ? "파일 선택 완료" : "파일 선택 대기";
      filename.textContent = file ? file.name : "";
      summary.textContent = "";
      Object.values(metadata).forEach(function (element) { element.textContent = "파일 선택 후 표시"; });
      metadata.custody.textContent = "서버 등록 후 기록";
      if (!file) return;
      const extension = /\.([^.]+)$/.exec(file.name);
      const type = extension ? extension[1].toUpperCase() : file.type || "알 수 없음";
      const size = sizeLabel(file.size);
      summary.textContent = `파일 크기 ${size} · 파일 형식 ${type}`;
      metadata.size.textContent = size;
      metadata.type.textContent = type;
      metadata.type.title = file.type || "브라우저가 제공한 MIME 유형 없음";
      metadata.time.textContent = new Intl.DateTimeFormat("ko-KR", {
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
        hour12: false, timeZoneName: "short"
      }).format(new Date());
      metadata.hash.textContent = "계산 중…";
      try {
        const digest = await window.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
        if (selectedUpload !== selection) return;
        metadata.hash.textContent = Array.from(new Uint8Array(digest), function (byte) {
          return byte.toString(16).padStart(2, "0");
        }).join("");
      } catch (error) {
        if (selectedUpload !== selection) return;
        metadata.hash.textContent = "계산하지 못했습니다. 파일을 다시 선택해 주세요.";
      }
    };
    fields.name.value = "";
    fields.name.placeholder = "파일을 선택하면 이름이 표시됩니다";
    // A filename in storage does not restore the file's actual bytes after a reload.
    try { sessionStorage.removeItem(`evidence-file:${page}`); } catch (error) { /* Local state is sufficient. */ }
    fields.updateUpload(null);
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

  async function setupEvidenceManagement() {
    if (page !== "evidence.html") return;
    const exampleRows = [
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
    if (!exampleRows.length) return;
    const tableHeader = exampleRows[0].row.previousElementSibling;
    const template = exampleRows[0].row.cloneNode(true);
    exampleRows.forEach(record => record.row.remove());
    const notice = document.createElement("p");
    notice.className = "evidence-local-notice";
    notice.textContent = await api.enabled() ? "현재 계정에 등록된 파일 목록입니다." : "이 브라우저에 등록한 파일 목록입니다. 브라우저 데이터를 삭제하면 파일도 삭제됩니다.";
    tableHeader.parentElement.before(notice);
    const empty = document.createElement("p");
    empty.className = "evidence-local-empty";
    empty.setAttribute("role", "status");
    empty.textContent = "저장된 증거를 불러오고 있습니다.";
    tableHeader.after(empty);
    const counts = Array.from(document.querySelectorAll("[data-evidence-count]"));
    counts.forEach(element => { element.textContent = "불러오는 중"; });
    let stored;
    try {
      stored = await (await api.enabled() ? api.list() : evidenceStore("readonly", tx => tx.objectStore("records").getAll()));
    } catch (error) {
      empty.textContent = `증거 목록을 불러오지 못했습니다. ${error.message || "새로고침해 주세요."}`;
      counts.forEach(element => { element.textContent = "불러오기 실패"; });
      return;
    }
    const records = stored.map(function (record) {
      const row = template.cloneNode(true);
      row.dataset.evidenceListRow = record.id;
      row.classList.add("evidence-local-row");
      row.querySelectorAll("[data-navigation-ready]").forEach(function (element) {
        element.removeAttribute("data-navigation-ready");
        element.removeAttribute("data-navigation-target");
        element.removeAttribute("role");
        element.removeAttribute("tabindex");
        element.removeAttribute("title");
      });
      const cells = row.children;
      cells[1].textContent = "";
      const title = document.createElement("strong");
      title.textContent = record.name;
      const filename = document.createElement("small");
      filename.textContent = record.filename;
      cells[1].append(title, filename);
      cells[1].classList.add("evidence-local-name");
      cells[2].textContent = record.type;
      cells[3].textContent = displayDate(record.registeredAt);
      cells[4].textContent = record.id;
      cells[5].textContent = record.status;
      cells[5].classList.add("evidence-local-status");
      cells[5].dataset.status = record.verificationStatus;
      const detail = document.createElement("button");
      detail.type = "button";
      detail.className = "evidence-local-detail";
      detail.textContent = "상세보기 →";
      detail.addEventListener("click", function () { showLocalEvidence(record); });
      cells[6].replaceChildren(detail);
      empty.before(row);
      return Object.assign(record, { row });
    });

    const search = createEvidenceListSearch();
    const typeFilter = createEvidenceListFilter("전체 유형", [
      { value: "", label: "전체 유형" }, ...Array.from(new Set(records.map(record => record.type))).sort().map(type => ({ value: type, label: type }))
    ], "type");
    const statusFilter = createEvidenceListFilter("전체 상태", [
      { value: "", label: "전체 상태" }, { value: "미검증", label: "미검증" }, { value: "일치", label: "일치" }, { value: "불일치", label: "불일치" }, { value: "검증 불가", label: "검증 불가" }
    ], "status");
    const dateFilter = createEvidenceListFilter("수집 기간", [
      { value: "", label: "등록 기간" }, ...Array.from(new Set(records.map(record => record.date))).sort().reverse().map(date => ({ value: date, label: date }))
    ], "date");
    const sortFilter = createEvidenceListFilter("최신순", [
      { value: "latest", label: "최신순" }, { value: "oldest", label: "오래된순" }, { value: "name", label: "파일명순" }
    ], "sort");
    const filterActions = createEvidenceFilterActions(dateFilter && dateFilter.parentElement && dateFilter.parentElement.parentElement);

    const count = document.querySelector('[data-evidence-count="visible"]');
    document.querySelector('[data-evidence-count="total"]').textContent = `${records.length}개`;
    const allCheckbox = createEvidenceCheckbox(tableHeader && tableHeader.firstElementChild, "표시된 증거 전체 선택", "all");
    const selected = new Set();
    const pageSize = 10;
    const previousPage = document.querySelector('[data-evidence-page="previous"]');
    const nextPage = document.querySelector('[data-evidence-page="next"]');
    const pageIndicator = document.querySelector('[data-evidence-pages]');
    let currentPage = 0;
    let orderedRecords = records.slice();
    let matchingRecords = records.slice();
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
      return matchingRecords.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
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

    function renderPage() {
      const pages = Math.max(1, Math.ceil(matchingRecords.length / pageSize));
      currentPage = Math.min(currentPage, pages - 1);
      const visible = visibleRecords();
      records.forEach(function (record) { record.row.hidden = !visible.includes(record); });
      if (count) {
        count.textContent = matchingRecords.length === records.length ? `${records.length}개` : `${matchingRecords.length}개 표시`;
      }
      empty.hidden = matchingRecords.length > 0;
      empty.textContent = records.length ? "검색 조건에 맞는 증거가 없습니다." : "등록된 증거가 없습니다. 파일을 선택한 후 ‘증거 등록’을 눌러 주세요.";
      if (pageIndicator) pageIndicator.textContent = `${currentPage + 1} / ${pages}`;
      if (previousPage) previousPage.disabled = currentPage === 0;
      if (nextPage) nextPage.disabled = currentPage >= pages - 1;
      updateSelection();
    }

    function applyFilters() {
      const query = normalize(search && search.value).toLowerCase();
      const type = typeFilter && typeFilter.value;
      const status = statusFilter && statusFilter.value;
      const date = dateFilter && dateFilter.value;
      matchingRecords = orderedRecords.filter(function (record) {
        return (!query || `${record.name} ${record.filename || ""} ${record.id}`.toLowerCase().includes(query)) &&
          (!type || record.type === type) && (!status || record.status === status) && (!date || record.date === date);
      });
      currentPage = 0;
      renderPage();
    }

    function applySort() {
      const mode = sortFilter && sortFilter.value;
      orderedRecords = records.slice().sort(function (left, right) {
        if (mode === "oldest") return left.registeredAt.localeCompare(right.registeredAt);
        if (mode === "name") return left.name.localeCompare(right.name);
        return right.registeredAt.localeCompare(left.registeredAt);
      });
      let anchor = tableHeader;
      orderedRecords.forEach(function (record) {
        anchor.after(record.row);
        anchor = record.row;
      });
      applyFilters();
    }

    records.forEach(function (record) {
      record.checkbox.addEventListener("change", function () {
        if (record.checkbox.checked) selected.add(record.id);
        else selected.delete(record.id);
        updateSelection();
      });
      record.row.addEventListener("click", function (event) {
        if (event.target.closest("input, label, button, a")) return;
        record.checkbox.checked = !record.checkbox.checked;
        record.checkbox.dispatchEvent(new Event("change", { bubbles: true }));
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
    previousPage?.addEventListener("click", function () { if (currentPage > 0) { currentPage -= 1; renderPage(); } });
    nextPage?.addEventListener("click", function () {
      if ((currentPage + 1) * pageSize < matchingRecords.length) { currentPage += 1; renderPage(); }
    });
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
    applySort();
    const detailId = new URLSearchParams(window.location.search).get("id");
    const detailRecord = records.find(record => record.id === detailId);
    if (detailRecord) showLocalEvidence(detailRecord);
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
    setupFileUploadDetails(fields);
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
    bind(config.submit, function (target) {
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
      registerLocalFile(config.kind, fields, target);
    }, { primaryOnly: true });
  }

  function setupVerification() {
    if (page !== "verification.html") return;
    window.EvidenceVerification.init({
      store: evidenceStore,
      isBackend: api.enabled,
      list: api.list,
      verify: api.verify,
      readSelection: readVerificationSelection,
      saveSelection: saveVerificationSelection,
      formatDate: displayDate
    });
  }

  async function setupEvidenceCompletion() {
    const completionSources = {
      "evidence_file_complete.html": "evidence_file.html",
      "evidence_img_complete.html": "evidence_img.html",
      "evidence_video_complete.html": "evidence_video.html"
    };
    const sourcePage = completionSources[page];
    if (!sourcePage) return;
    const heading = document.querySelector(".completion-intro h1");
    const summary = document.querySelector(".completion-intro p");
    const card = document.querySelector(".completion-card");
    card.hidden = true;
    heading.textContent = "등록 정보를 확인하고 있습니다.";
    summary.textContent = "";
    const id = new URLSearchParams(window.location.search).get("id");
    try {
      const record = id && await (await api.enabled() ? api.get(id) : evidenceStore("readonly", tx => tx.objectStore("records").get(id)));
      if (!record) {
        heading.textContent = "등록된 파일을 찾을 수 없습니다.";
        summary.textContent = "증거 목록을 확인하거나 파일을 다시 등록해 주세요.";
        return;
      }
      heading.textContent = "이 브라우저에 증거를 등록했습니다.";
      summary.textContent = "파일과 입력 정보가 저장되었습니다. 검증 화면에서 등록 당시 해시와 비교할 수 있습니다.";
      card.innerHTML = detailsMarkup([["증거 ID", record.id], ["증거 이름", record.name], ["파일", record.filename], ["파일 크기", `${record.size.toLocaleString("ko-KR")} bytes`], ["SHA-256", record.sha256], ["등록 일시", displayDate(record.registeredAt)], ["무결성 상태", record.status], ["저장 위치", "현재 브라우저"]]);
      card.hidden = false;
      actionTargets("증거 상세보기").forEach(function (target) {
        const openDetail = function (event) {
          event.preventDefault();
          event.stopImmediatePropagation();
          window.location.href = `evidence.html?id=${encodeURIComponent(record.id)}`;
        };
        target.addEventListener("click", openDetail, true);
        target.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") openDetail(event);
        }, true);
      });
    } catch (error) {
      heading.textContent = "등록 정보를 불러오지 못했습니다.";
      summary.textContent = "브라우저 저장소 권한을 확인하고 새로고침해 주세요.";
    }
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
