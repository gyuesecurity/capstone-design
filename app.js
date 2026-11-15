(function () {
  "use strict";

  const page = (window.location.pathname.split("/").pop() || "index.html").toLowerCase();
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  let activeLayer = null;
  let activeTimer = null;
  let selectedUpload = null;
  let registrationPending = false;
  let uploadNeedsConfirmation = null;
  let confirmationTarget = null;

  const api = (function () {
    let availability;
    let profileToken;
    let profileRequest;
    const tokenKey = "evidence-auth-token";
    function enabled() {
      if (sessionStorage.getItem("evidence-demo-mode") === "1") return Promise.resolve(false);
      if (sessionStorage.getItem(tokenKey)) return Promise.resolve(true);
      if (!availability) availability = fetch("/api/health", { cache: "no-store" })
        .then(response => response.ok && response.headers.get("content-type")?.includes("application/json"))
        .catch(() => false);
      return availability;
    }
    async function request(path, options = {}) {
      if (typeof path !== "string" || !path.startsWith("/api/")) throw new Error("API 주소를 확인해 주세요.");
      const token = sessionStorage.getItem(tokenKey);
      const loginUrl = `login.html?next=${encodeURIComponent((location.pathname.split("/").pop() || "index.html") + location.search)}`;
      if (!token) {
        location.href = loginUrl;
        throw Object.assign(new Error("로그인이 필요합니다."), { status: 401 });
      }
      let response;
      try {
        response = await fetch(path, { ...options, headers: { ...options.headers, Authorization: `Bearer ${token}` }, cache: "no-store" });
      } catch (_) {
        throw new Error("서버에 연결할 수 없습니다. 네트워크 연결을 확인해 주세요.");
      }
      if (sessionStorage.getItem(tokenKey) !== token) throw new Error("로그인 계정이 변경되었습니다. 새로고침해 주세요.");
      if (response.status === 401) {
        sessionStorage.removeItem(tokenKey);
        location.href = loginUrl;
        throw Object.assign(new Error("로그인이 만료되었습니다."), { status: 401 });
      }
      if (!response.ok) {
        let detail;
        try { detail = (await response.json()).detail; } catch (_) { /* Response may be empty. */ }
        const messages = {
          400: "요청한 내용을 확인해 주세요.", 403: "이 작업을 수행할 권한이 없습니다.",
          404: "요청한 항목을 찾을 수 없습니다.", 409: "항목 상태가 변경되었습니다. 확인 후 다시 시도해 주세요.",
          413: "파일이 허용된 크기를 초과했습니다.", 422: "입력값을 확인해 주세요.",
          503: "요청이 몰리고 있습니다. 잠시 후 다시 시도해 주세요."
        };
        let message = messages[response.status] || `서버가 요청을 처리하지 못했습니다. (HTTP ${response.status})`;
        if (Array.isArray(detail)) message = "입력값을 확인해 주세요.";
        else if (typeof detail === "string" && detail.trim() && detail !== response.statusText &&
                 !/^(Bad Request|Forbidden|Not Found|Conflict|Payload Too Large|Unprocessable (Entity|Content)|Internal Server Error|Service Unavailable)$/i.test(detail)) {
          message = detail;
          if (response.status === 503 && !/다시|잠시/.test(message)) message += " 잠시 후 다시 시도해 주세요.";
        }
        throw Object.assign(new Error(message), { status: response.status });
      }
      return response;
    }
    async function profile(refresh = false) {
      if (sessionStorage.getItem("evidence-demo-mode") === "1") return null;
      const token = sessionStorage.getItem(tokenKey);
      if (!token && !await enabled()) return null;
      if (refresh || !profileRequest || profileToken !== token) {
        profileToken = token;
        profileRequest = request("/api/me").then(response => response.json()).then(user => {
          if (typeof user?.id !== "string" || !user.id.trim() || typeof user.name !== "string" || !user.name.trim()) {
            throw new Error("계정 정보를 확인할 수 없습니다.");
          }
          return user;
        });
      }
      const pending = profileRequest;
      try {
        const user = await pending;
        if (sessionStorage.getItem(tokenKey) !== token) throw new Error("로그인 계정이 변경되었습니다. 새로고침해 주세요.");
        return user;
      } catch (error) {
        if (pending === profileRequest) profileRequest = null;
        throw error;
      }
    }
    return {
      enabled, request, profile,
      async shares(id, options, recipient) {
        const url = "/api/evidence/" + encodeURIComponent(id) + "/shares";
        return (await request(recipient ? url + "/" + encodeURIComponent(recipient) : url, options)).json();
      },
      async list(scope) {
        const path = scope ? "/api/evidence?scope=" + encodeURIComponent(scope) : "/api/evidence";
        const records = (await (await request(path)).json()).filter(record => record.access?.canView !== false);
        // Earlier servers return owned and shared evidence together, even without a scope.
        if (!scope || scope === "owned") return records.filter(record => record.access?.isOwner !== false);
        if (scope === "shared") return records.filter(record => record.access?.isOwner === false);
        return records;
      },
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
    if (!value || !Number.isFinite(Date.parse(value))) return "미기록";
    return new Intl.DateTimeFormat("ko-KR", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul", timeZoneName: "short"
    }).format(new Date(value));
  }

  const registrationFormatter = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  });

  function registrationTime(record) {
    const value = record.registeredAt;
    if (typeof value === "string" && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
      const timestamp = Date.parse(value);
      if (Number.isFinite(timestamp)) {
        const parts = Object.fromEntries(registrationFormatter.formatToParts(timestamp).map(part => [part.type, part.value]));
        const day = `${parts.year}-${parts.month}-${parts.day}`;
        return { day, text: `${day} ${parts.hour}:${parts.minute}`, dateTime: value, timestamp };
      }
    }
    // An older record may contain only a calendar date, with no known time.
    const calendarDay = /^(\d{4}-\d{2}-\d{2})(?:$|[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$)/.exec(value);
    const day = calendarDay ? calendarDay[1] : record.date;
    if (/^\d{4}-\d{2}-\d{2}$/.test(day)) {
      const midnight = Date.parse(day + "T00:00:00Z");
      if (Number.isFinite(midnight) && new Date(midnight).toISOString().startsWith(day)) {
        return { day, text: day, dateTime: day, timestamp: Date.parse(day + "T00:00:00+09:00") };
      }
    }
    return { day: "", text: "미기록", dateTime: "", timestamp: -Infinity };
  }

  window.EvidenceListDisplay = { registrationTime, renderEvidenceId };

  async function registerLocalFile(kind, fields, target) {
    if (registrationPending || !selectedUpload || uploadNeedsConfirmation === selectedUpload) return;
    registrationPending = true;
    let saved = false;
    let serverUploadStarted = false;
    const releaseControls = lockRegistrationControls();
    const previousLabel = target.textContent;
    if (target.tagName === "BUTTON") target.textContent = "등록 중…";
    target.setAttribute("aria-busy", "true");
    registrationError("");
    setRegistrationState("submitting");
    const selection = selectedUpload;
    const file = selection.file;
    const name = normalize(fields.name && fields.name.value) || file.name;
    const description = fields.description ? fields.description.value : "";
    const memo = fields.memo ? fields.memo.value : "";
    const source = fields.source ? fields.source.value : "직접 수집";
    toast("선택한 파일을 등록하고 있습니다.");
    try {
      if (await api.enabled()) {
        serverUploadStarted = true;
        const record = await api.upload(file, { name, kind, source, description, memo, selectedAt: selection.selectedAt });
        saved = true;
        setRegistrationState("complete");
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
        selectedAt: selection.selectedAt, registeredAt: now.toISOString(), registeredBy: "user",
        date: [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("-"),
        sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join(""),
        status: "미검증", verificationStatus: "NOT_VERIFIED", storage: "browser"
      };
      await evidenceStore("readwrite", function (transaction) {
        transaction.objectStore("files").put(new Blob([bytes], { type: file.type }), record.id);
        return transaction.objectStore("records").put(record);
      });
      saved = true;
      setRegistrationState("complete");
      const destination = { "파일": "evidence_file_complete.html", "이미지": "evidence_img_complete.html", "영상·음성": "evidence_video_complete.html" }[kind];
      window.location.href = `${destination}?id=${encodeURIComponent(record.id)}`;
    } catch (error) {
      const message = error.message || "파일을 등록하지 못했습니다. 다시 시도해 주세요.";
      if (serverUploadStarted && (!error.status || error.status >= 500 || error.status === 408)) {
        uploadNeedsConfirmation = selectedUpload;
        confirmationTarget = target;
        registrationError(message + " 등록 여부를 확인하지 못했습니다. 중복 등록을 피하려면 증거 목록에서 먼저 확인해 주세요.", () => { location.href = "evidence.html"; }, "등록 결과 확인");
      } else {
        registrationError(message, () => registerLocalFile(kind, fields, target));
      }
      toast(message);
    } finally {
      registrationPending = false;
      releaseControls();
      target.removeAttribute("aria-busy");
      if (target.tagName === "BUTTON") target.textContent = previousLabel;
      if (!saved) setRegistrationState(selectedUpload ? "ready" : "empty");
      if (uploadNeedsConfirmation === selectedUpload && selectedUpload) {
        if ("disabled" in target) target.disabled = true;
        target.setAttribute("aria-disabled", "true");
      }
    }
  }

  function permitted(record, permission) {
    if (!record.access) return true;
    const flag = permission === "VERIFY" ? record.access.canVerify : record.access.canDownload;
    if (typeof flag === "boolean") return flag;
    return Array.isArray(record.access.permissions) && record.access.permissions.includes(permission);
  }

  function showShares(record) {
    const layer = showDialog(`
      <h2>공유 관리</h2><p>${escapeHtml(record.name)}</p>
      <form class="share-form"><label>공유받을 계정 이메일 <input name="email" type="email" required></label>
        <p>공유한 계정은 이 증거의 상세 정보를 볼 수 있습니다.</p>
        <label><input type="checkbox" name="download"> 원본 다운로드</label>
        <label><input type="checkbox" name="verify"> 무결성 검증</label>
        <button class="evidence-dialog__button" type="submit">권한 저장</button>
      </form><ul class="share-list" aria-label="공유 중인 계정"></ul>
      <p role="status"></p>${button("닫기", "close")}`, { wide: true });
    const form = layer.querySelector("form");
    const list = layer.querySelector("ul");
    const status = layer.querySelector('[role="status"]');
    layer.querySelector('[data-dialog-action="close"]').onclick = closeDialog;
    async function refresh() {
      const shares = await api.shares(record.id);
      list.replaceChildren();
      for (const share of shares) {
        const item = document.createElement("li");
        const grants = ["조회", share.download && "다운로드", share.verify && "검증"].filter(Boolean);
        item.textContent = share.email + " · " + grants.join(", ") + " ";
        const edit = document.createElement("button");
        edit.type = "button";
        edit.textContent = "수정";
        edit.onclick = () => {
          form.elements.email.value = share.email;
          form.elements.download.checked = Boolean(share.download);
          form.elements.verify.checked = Boolean(share.verify);
          form.elements.email.focus();
        };
        const revoke = document.createElement("button");
        revoke.type = "button";
        revoke.textContent = "공유 해제";
        revoke.onclick = async () => {
          revoke.disabled = true;
          try {
            await api.shares(record.id, { method: "DELETE" }, share.id);
            if (form.elements.email.value === share.email) form.reset();
            await refresh();
            status.textContent = "공유를 해제했습니다.";
          } catch (error) { status.textContent = error.message; revoke.disabled = false; }
        };
        item.append(edit, revoke);
        list.append(item);
      }
      if (!shares.length) list.textContent = "현재 공유 중인 계정이 없습니다.";
    }
    form.onsubmit = async event => {
      event.preventDefault();
      const submit = form.querySelector("button");
      submit.disabled = true;
      try {
        const values = form.elements;
        await api.shares(record.id, { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: values.email.value, download: values.download.checked, verify: values.verify.checked }) });
        form.reset();
        await refresh();
        status.textContent = "공유 권한을 저장했습니다.";
      } catch (error) { status.textContent = error.message; }
      finally { submit.disabled = false; }
    };
    refresh().catch(error => { status.textContent = error.message; });
  }

  function showLocalEvidence(record, server) {
    const selectionTime = server ? "기록 안 함" : displayDate(record.selectedAt);
    const layer = showDialog(`
      <div class="evidence-dialog__head"><div><h2>증거 상세</h2><p>${server ? "서버에 보관된 원본 파일입니다." : "이 브라우저에 저장된 파일입니다."}</p></div></div>
      ${detailsMarkup([["증거 이름", record.name], ["파일명", record.filename], ["증거 ID", record.id], ["파일 유형", record.type], ["파일 크기", `${record.size.toLocaleString("ko-KR")} bytes`], ["등록 일시 (KST)", registrationTime(record).text], ["파일 선택 일시 (브라우저 기준)", selectionTime], ["SHA-256", record.sha256], ["무결성 상태", record.status], ["수집 출처", record.source], ["설명", record.description || "없음"], ["메모", record.memo || "없음"]])}
      <div class="evidence-dialog__actions">${button("닫기", "close")}${record.access?.isOwner ? button("공유 관리", "share") : ""}${permitted(record, "DOWNLOAD") ? button("파일 다운로드", "download", "primary") : ""}</div>
    `, { wide: true });
    layer.querySelector('[data-dialog-action="close"]').addEventListener("click", closeDialog);
    layer.querySelector('[data-dialog-action="share"]')?.addEventListener("click", () => showShares(record));
    const download = layer.querySelector('[data-dialog-action="download"]');
    let downloading = false;
    download?.addEventListener("click", async function () {
      if (downloading) return;
      downloading = true;
      download.disabled = true;
      download.setAttribute("aria-busy", "true");
      download.textContent = "파일을 불러오는 중…";
      let status = layer.querySelector("[data-download-error]");
      if (!status) {
        status = document.createElement("p");
        status.dataset.downloadError = "";
        status.className = "operation-error";
        status.setAttribute("role", "alert");
        download.parentElement.before(status);
      }
      status.hidden = true;
      let failed = false;
      try {
        const file = server ? await api.download(record.id) : await evidenceStore("readonly", tx => tx.objectStore("files").get(record.id));
        if (!file) throw new Error("저장한 원본 파일을 찾을 수 없습니다.");
        const url = URL.createObjectURL(file);
        const link = document.createElement("a");
        link.href = url;
        link.download = record.filename;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) {
        failed = true;
        status.textContent = error.message || "파일을 불러오지 못했습니다. 다시 시도해 주세요.";
        status.hidden = false;
      } finally {
        downloading = false;
        download.disabled = false;
        download.removeAttribute("aria-busy");
        download.textContent = failed ? "다운로드 다시 시도" : "원본 다운로드";
      }
    });
  }

  function renderProfileName(name) {
    document.querySelectorAll("[data-profile-name]").forEach(function (element) {
      element.textContent = name;
    });
    document.querySelectorAll("[data-profile-greeting]").forEach(function (element) {
      element.textContent = `안녕하세요, ${name}님 👋`;
    });
  }

  async function applyProfileName(refresh = false) {
    if (!document.querySelector("[data-profile-name], [data-profile-greeting]")) return;
    try {
      const user = await api.profile(refresh);
      renderProfileName(user?.name ?? "user");
    } catch (_) { renderProfileName("사용자"); }
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
        if (target.disabled || target.getAttribute("aria-disabled") === "true") {
          event.preventDefault();
          event.stopImmediatePropagation();
          return;
        }
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

  function setRegistrationState(state) {
    window.EvidenceRegistration?.setState(state);
  }

  function lockRegistrationControls() {
    const controls = Array.from(document.querySelectorAll('[data-evidence-field], [data-evidence-action], [data-registration-retry], [data-navigation-ready][title="취소"]'));
    const block = event => {
      if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    const snapshots = controls.map(node => ({ node, disabled: node.disabled, aria: node.getAttribute("aria-disabled"), tab: node.getAttribute("tabindex") }));
    controls.forEach(node => {
      if ("disabled" in node) node.disabled = true;
      node.setAttribute("aria-disabled", "true");
      node.classList.add("operation-control-busy");
      node.addEventListener("click", block, true);
      node.addEventListener("keydown", block, true);
      if (!node.matches("input, textarea, select, button")) node.tabIndex = -1;
    });
    return () => snapshots.forEach(({ node, disabled, aria, tab }) => {
      if ("disabled" in node) node.disabled = disabled;
      if (aria === null) node.removeAttribute("aria-disabled"); else node.setAttribute("aria-disabled", aria);
      if (tab === null) node.removeAttribute("tabindex"); else node.setAttribute("tabindex", tab);
      node.classList.remove("operation-control-busy");
      node.removeEventListener("click", block, true);
      node.removeEventListener("keydown", block, true);
    });
  }

  function registrationError(message, retry, retryLabel = "등록 다시 시도") {
    let node = document.querySelector("[data-registration-error]");
    if (!node && !message) return;
    if (!node) {
      node = document.createElement("div");
      node.dataset.registrationError = "";
      node.className = "registration-feedback";
      node.setAttribute("role", "alert");
      const submit = document.querySelector("[data-media-submit], [data-image-submit]") || actionTargets("증거 등록").find(target => /#2E63E8|#2454C6/i.test(target.getAttribute("style") || ""));
      if (submit) submit.parentElement.before(node);
      else document.querySelector(".registration-steps")?.after(node);
    }
    node.replaceChildren();
    if (message) {
      const description = document.createElement("p");
      description.textContent = message;
      node.append(description);
      if (retry) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "operation-retry";
        button.dataset.registrationRetry = "";
        button.textContent = retryLabel;
        if (retryLabel === "등록 결과 확인") button.dataset.registrationCheck = "";
        button.addEventListener("click", retry);
        node.append(button);
      }
    }
    node.hidden = !message;
  }

  function setSelectedFile(file, fields) {
    if (registrationPending) { toast("등록 중입니다. 저장이 끝날 때까지 기다려 주세요."); return; }
    if (fields.validateUpload && !fields.validateUpload(file)) return;
    registrationError("");
    uploadNeedsConfirmation = null;
    if (confirmationTarget) {
      if ("disabled" in confirmationTarget) confirmationTarget.disabled = false;
      confirmationTarget.removeAttribute("aria-disabled");
      confirmationTarget = null;
    }
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
    uploadNeedsConfirmation = null;
    if (confirmationTarget) {
      if ("disabled" in confirmationTarget) confirmationTarget.disabled = false;
      confirmationTarget.removeAttribute("aria-disabled");
      confirmationTarget = null;
    }
    try {
      sessionStorage.removeItem(`evidence-file:${page}`);
    } catch (error) {
      // The selected file stays cleared in the current form when storage is unavailable.
    }
    if (fields.name) fields.name.value = "";
    registrationError("");
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
      metadata.custody.textContent = "준비 중";
      setRegistrationState(file ? "ready" : "empty");
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
      }).format(new Date(selection.selectedAt));
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

  function setupImageUploadDetails(fields) {
    if (page !== "evidence_img.html") return;
    const card = document.querySelector("[data-image-card]");
    const empty = document.querySelector("[data-image-empty]");
    const preview = document.querySelector("[data-image-preview]");
    const filename = document.querySelector("[data-image-filename]");
    const summary = document.querySelector("[data-image-summary]");
    const badge = document.querySelector("[data-image-badge]");
    const message = document.querySelector("[data-image-error]");
    const submit = document.querySelector("[data-image-submit]");
    const metadata = Object.fromEntries(Array.from(document.querySelectorAll("[data-image-meta]"), node => [node.dataset.imageMeta, node]));
    let previewUrl = null;
    let ready = false;

    function releasePreview() {
      preview.hidden = true;
      preview.removeAttribute("src");
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }

    function showError(text) {
      message.textContent = text;
      message.hidden = false;
      toast(text);
    }

    fields.canRegister = () => ready;
    fields.validateUpload = function (file) {
      message.hidden = true;
      if (!/\.(jpe?g|png|gif|webp)$/i.test(file.name) || (file.type && !/^image\/(jpeg|png|gif|webp)$/i.test(file.type))) {
        showError("JPG, JPEG, PNG, GIF, WEBP 이미지 파일을 선택해 주세요.");
        return false;
      }
      if (!file.size || file.size > 50 * 1024 * 1024) {
        showError("크기가 0보다 크고 50 MB 이하인 이미지를 선택해 주세요.");
        return false;
      }
      return true;
    };

    fields.updateUpload = async function (file) {
      const selection = selectedUpload;
      ready = false;
      submit.disabled = true;
      message.hidden = true;
      releasePreview();
      card.hidden = !file;
      empty.hidden = Boolean(file);
      filename.textContent = file ? file.name : "";
      summary.textContent = "";
      badge.textContent = file ? "이미지 확인 중…" : "이미지 선택 대기";
      Object.values(metadata).forEach(node => { node.textContent = "이미지 선택 후 표시"; node.removeAttribute("title"); });
      metadata.custody.textContent = "준비 중";
      setRegistrationState(file ? "loading" : "empty");
      if (!file) return;

      const type = /\.([^.]+)$/.exec(file.name)[1].toUpperCase();
      const size = file.size < 1024 ? `${file.size} B` : `${(file.size / (file.size < 1048576 ? 1024 : 1048576)).toLocaleString("ko-KR", { maximumFractionDigits: 2 })} ${file.size < 1048576 ? "KB" : "MB"}`;
      metadata.filename.textContent = file.name;
      metadata.type.textContent = type;
      metadata.type.title = file.type || "브라우저가 제공한 MIME 유형 없음";
      metadata.size.textContent = `${size} (${file.size.toLocaleString("ko-KR")} bytes)`;
      metadata.resolution.textContent = "확인 중…";
      metadata.color.textContent = "현재 화면에서 추출하지 않음";
      metadata.time.textContent = new Intl.DateTimeFormat("ko-KR", {
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Seoul", timeZoneName: "short"
      }).format(new Date(selection.selectedAt));
      metadata.hash.textContent = "계산 중…";

      try {
        previewUrl = URL.createObjectURL(file);
        preview.src = previewUrl;
        await preview.decode();
        if (selectedUpload !== selection) return;
        const resolution = `${preview.naturalWidth} × ${preview.naturalHeight}`;
        metadata.resolution.textContent = resolution;
        summary.textContent = `${size} · ${type} · ${resolution}`;
        preview.hidden = false;
        badge.textContent = "✓ 이미지 선택 완료 · 등록 대기";
        ready = true;
        submit.disabled = false;
        setRegistrationState("ready");
      } catch (_) {
        if (selectedUpload !== selection) return;
        selectedUpload = null;
        try { sessionStorage.removeItem(`evidence-file:${page}`); } catch (_) { /* Local state is sufficient. */ }
        if (fields.name) fields.name.value = "";
        fields.updateUpload(null);
        showError("이미지를 읽을 수 없습니다. 손상되지 않은 JPG, PNG, GIF 또는 WEBP 파일을 다시 선택해 주세요.");
        return;
      }

      try {
        const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
        if (selectedUpload !== selection) return;
        metadata.hash.textContent = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
      } catch (_) {
        if (selectedUpload !== selection) return;
        metadata.hash.textContent = "계산하지 못했습니다. 등록할 때 다시 계산합니다.";
      }
    };

    if (fields.name) {
      fields.name.value = "";
      fields.name.placeholder = "이미지를 선택하면 이름이 표시됩니다";
    }
    // Reloading restores no File bytes, even if a filename was previously saved.
    try { sessionStorage.removeItem(`evidence-file:${page}`); } catch (_) { /* Local state is sufficient. */ }
    fields.updateUpload(null);
    window.addEventListener("pagehide", event => { if (!event.persisted) releasePreview(); });
  }

  function setupDropZone(config, fields) {
    const hint = config.kind === "이미지" ? "이미지를 이곳으로 드래그하거나 파일을 선택하세요." : config.kind === "영상·음성" ? "파일을 이곳으로 드래그하거나 파일을 선택하세요." : "파일을 이곳으로 드래그하거나 파일을 선택하세요.";
    const hintElement = document.querySelector("[data-image-empty] p, [data-media-empty] p") || exactTextElements(hint)[0];
    if (!hintElement) return;
    let dropZone = hintElement.parentElement;
    for (let depth = 0; depth < 3 && dropZone; depth += 1) {
      if (dropZone.matches("[data-image-empty], [data-media-empty]")) break;
      const style = dropZone.getAttribute("style") || "";
      if (/border-radius/.test(style) && /padding/.test(style)) break;
      dropZone = dropZone.parentElement;
    }
    if (!dropZone) return;
    dropZone.classList.add("evidence-drop-zone");
    dropZone.addEventListener("dragover", function (event) {
      event.preventDefault();
      if (registrationPending) return;
      dropZone.classList.add("evidence-drop-zone--active");
    });
    dropZone.addEventListener("dragleave", function () {
      dropZone.classList.remove("evidence-drop-zone--active");
    });
    dropZone.addEventListener("drop", function (event) {
      event.preventDefault();
      dropZone.classList.remove("evidence-drop-zone--active");
      if (registrationPending) return;
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

  function setupUrlRegistration(fields) {
    if (!fields.url) return;
    fields.url.value = "";
    function reset() {
      fields.url.setCustomValidity("");
      try { sessionStorage.removeItem(`evidence-url:${page}`); } catch (_) { /* No URL has been collected. */ }
      const badge = document.querySelector("[data-url-badge]");
      if (badge) badge.textContent = "주소 입력 대기";
      const checked = document.querySelector("[data-url-checked]");
      if (checked) checked.textContent = "주소 확인 후 표시";
      setRegistrationState("empty");
    }
    fields.url.addEventListener("input", reset);
    reset();
  }

  function collectUrl(input) {
    if (!input) return false;
    const value = normalize(input.value);
    input.value = value;
    input.setCustomValidity(isHttpUrl(value) ? "" : "http 또는 https로 시작하는 웹페이지 주소를 입력해 주세요.");
    if (!input.reportValidity()) {
      setRegistrationState("empty");
      return false;
    }
    const badge = document.querySelector("[data-url-badge]");
    if (badge) badge.textContent = "주소 확인 완료 · 미수집";
    const checked = document.querySelector("[data-url-checked]");
    if (checked) checked.textContent = value;
    // Validating an address is not a collection request or a saved evidence record.
    setRegistrationState("unavailable");
    toast("주소를 확인했습니다. 실제 웹페이지 수집과 저장은 준비 중입니다.");
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

  function renderEvidenceId(cell, id, index) {
    const value = String(id);
    const abbreviated = value.length > 22;
    const localPrefix = /^LOCAL-\d{8}(?=-)/.exec(value);
    const prefix = localPrefix ? localPrefix[0] : value.slice(0, 8);
    const shortId = abbreviated ? `${prefix}…${value.slice(-6)}` : value;
    const valueId = `evidence-id-${index}`;

    cell.classList.add("evidence-id-cell");
    cell.innerHTML = `
      <code class="evidence-id-value" id="${valueId}"></code>
      <div class="evidence-id-actions">
        ${abbreviated ? `<button type="button" aria-expanded="false" aria-controls="${valueId}">전체 ID 보기</button>` : ""}
        <button type="button" data-copy-evidence-id aria-label="전체 ID 복사" aria-describedby="${valueId}">복사</button>
      </div>
      <p class="evidence-id-feedback" role="status"></p>`;
    const code = cell.querySelector("code");
    const toggle = cell.querySelector("[aria-expanded]");
    const copy = cell.querySelector("[data-copy-evidence-id]");
    const feedback = cell.querySelector(".evidence-id-feedback");
    code.textContent = shortId;

    function showFullId(expanded) {
      code.textContent = expanded ? value : shortId;
      if (toggle) {
        toggle.textContent = expanded ? "접기" : "전체 ID 보기";
        toggle.setAttribute("aria-expanded", String(expanded));
      }
      feedback.textContent = "";
    }

    toggle?.addEventListener("click", () => showFullId(toggle.getAttribute("aria-expanded") === "false"));
    copy.addEventListener("click", async () => {
      if (copy.getAttribute("aria-disabled") === "true") return;
      copy.setAttribute("aria-disabled", "true");
      feedback.textContent = "";
      try {
        await navigator.clipboard.writeText(value);
        feedback.textContent = "전체 ID를 복사했습니다.";
      } catch {
        if (!code.isConnected) return;
        showFullId(true);
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        feedback.textContent = "자동 복사를 사용할 수 없습니다. 선택된 전체 ID를 직접 복사해 주세요.";
      } finally {
        copy.removeAttribute("aria-disabled");
      }
    });
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
    const server = await api.enabled();
    notice.textContent = server ? "내가 등록했거나 공유받은 파일 목록입니다." : "이 브라우저에 등록한 파일 목록입니다. 브라우저 데이터를 삭제하면 파일도 삭제됩니다.";
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
      stored = await (server ? api.list("all") : evidenceStore("readonly", tx => tx.objectStore("records").getAll()));
    } catch (error) {
      empty.textContent = `증거 목록을 불러오지 못했습니다. ${error.message || "새로고침해 주세요."}`;
      counts.forEach(element => { element.textContent = "불러오기 실패"; });
      empty.setAttribute("role", "alert");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "operation-retry";
      retry.dataset.evidenceListRetry = "";
      retry.textContent = "목록 다시 불러오기";
      retry.addEventListener("click", () => {
        retry.disabled = true;
        retry.textContent = "목록을 불러오는 중…";
        location.reload();
      });
      empty.append(document.createElement("br"), retry);
      return;
    }
    const records = stored.map(function (record, index) {
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
      if (record.access?.isOwner === false) {
        const owner = record.access.owner?.name;
        filename.textContent += owner ? ` · ${owner}님이 공유` : " · 공유받음";
      }
      cells[1].append(title, filename);
      cells[1].classList.add("evidence-local-name");
      cells[2].textContent = record.type;
      const registration = registrationTime(record);
      const time = document.createElement(registration.dateTime ? "time" : "span");
      time.textContent = registration.text;
      if (registration.dateTime) time.dateTime = registration.dateTime;
      cells[3].replaceChildren(time);
      renderEvidenceId(cells[4], record.id, index);
      cells[5].textContent = record.status;
      cells[5].classList.add("evidence-local-status");
      cells[5].dataset.status = record.verificationStatus;
      const detail = document.createElement("button");
      detail.type = "button";
      detail.className = "evidence-local-detail";
      detail.textContent = "상세보기 →";
      detail.addEventListener("click", function () { showLocalEvidence(record, server); });
      cells[6].replaceChildren(detail);
      empty.before(row);
      return Object.assign(record, { row, registration });
    });

    const search = createEvidenceListSearch();
    const typeFilter = createEvidenceListFilter("전체 유형", [
      { value: "", label: "전체 유형" }, ...Array.from(new Set(records.map(record => record.type))).sort().map(type => ({ value: type, label: type }))
    ], "type");
    const statusFilter = createEvidenceListFilter("전체 상태", [
      { value: "", label: "전체 상태" }, { value: "미검증", label: "미검증" }, { value: "일치", label: "일치" }, { value: "불일치", label: "불일치" }, { value: "검증 불가", label: "검증 불가" }
    ], "status");
    const dateFilter = createEvidenceListFilter("등록 기간", [
      { value: "", label: "등록 기간" }, ...Array.from(new Set(records.map(record => record.registration.day).filter(Boolean))).sort().reverse().map(day => ({ value: day, label: day }))
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
      const visible = visibleRecords().filter(record => permitted(record, "VERIFY"));
      if (allCheckbox) {
        allCheckbox.disabled = !visible.length;
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
          (!type || record.type === type) && (!status || record.status === status) && (!date || record.registration.day === date);
      });
      currentPage = 0;
      renderPage();
    }

    function applySort() {
      const mode = sortFilter && sortFilter.value;
      orderedRecords = records.slice().sort(function (left, right) {
        if (mode === "name") return left.name.localeCompare(right.name);
        if (!left.registration.day || !right.registration.day) return Number(!left.registration.day) - Number(!right.registration.day);
        const difference = left.registration.timestamp - right.registration.timestamp;
        return mode === "oldest" ? difference : -difference;
      });
      let anchor = tableHeader;
      orderedRecords.forEach(function (record) {
        anchor.after(record.row);
        anchor = record.row;
      });
      applyFilters();
    }

    records.forEach(function (record) {
      record.checkbox.disabled = !permitted(record, "VERIFY");
      if (record.checkbox.disabled) record.checkbox.title = "검증 권한이 없습니다.";
      record.checkbox.addEventListener("change", function () {
        if (record.checkbox.checked) selected.add(record.id);
        else selected.delete(record.id);
        updateSelection();
      });
      record.row.addEventListener("click", function (event) {
        if (record.checkbox.disabled || event.target.closest("input, label, button, a, .evidence-id-cell")) return;
        record.checkbox.checked = !record.checkbox.checked;
        record.checkbox.dispatchEvent(new Event("change", { bubbles: true }));
      });
    });
    if (allCheckbox) {
      allCheckbox.addEventListener("change", function () {
        visibleRecords().filter(record => permitted(record, "VERIFY")).forEach(function (record) {
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
    if (detailRecord) showLocalEvidence(detailRecord, server);
  }

  function setupRegistration() {
    const configs = {
      "evidence_file.html": { kind: "파일", meta: "문서 · 파일", submit: "증거 등록", select: "파일 선택", accept: "*/*" },
      "evidence_img.html": { kind: "이미지", meta: "이미지 파일", submit: "이미지 증거 등록", select: "이미지 선택", accept: "image/*" },
      "evidence_video.html": { kind: "영상·음성", meta: "영상 · 음성 파일", submit: "영상·음성 증거 등록", select: "파일 선택", accept: "video/*,audio/*" },
      "evidence_url.html": { kind: "웹페이지", meta: "본문과 메타데이터 수집", submit: "웹페이지 증거 저장", select: "주소 확인", defaultUrl: "https://example.com/article/123" }
    };
    const config = configs[page];
    if (!config) return;
    const fields = setupEvidenceFields(config);
    setupFileUploadDetails(fields);
    setupImageUploadDetails(fields);
    if (config.kind === "영상·음성") window.EvidenceMediaUpload.setup(fields, { getSelection: () => selectedUpload, toast });
    if (config.kind !== "웹페이지") {
      window.addEventListener("pageshow", event => {
        if (event.persisted && !registrationPending) setRegistrationState(selectedUpload ? "ready" : "empty");
      });
    }
    if (config.kind === "웹페이지") {
      setupUrlRegistration(fields);
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
        toast("실제 웹페이지 수집과 증거 저장은 준비 중입니다.");
        return;
      }
      if (fields.canRegister && !fields.canRegister()) {
        registrationError("파일을 선택하고 정보 확인이 끝난 후 등록해 주세요.");
        toast("파일을 선택하고 정보 확인이 끝난 후 등록해 주세요.");
        return;
      }
      const selected = readSelectedFile();
      if (!selected) {
        registrationError("등록할 파일을 먼저 선택해 주세요.");
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
      canVerify: record => permitted(record, "VERIFY"),
      list: () => api.list("all"),
      verify: api.verify,
      readSelection: readVerificationSelection,
      saveSelection: saveVerificationSelection,
      formatDate: displayDate
    });
  }

  async function setupEvidenceCompletion() {
    if (page === "evidence_url_complete.html") {
      const url = document.querySelector("[data-completion-url]");
      if (url) {
        try { url.textContent = sessionStorage.getItem("evidence-url:evidence_url.html") || "입력 기록 없음"; }
        catch (_) { url.textContent = "입력 기록 없음"; }
      }
      return;
    }
    if (!["evidence_file_complete.html", "evidence_img_complete.html", "evidence_video_complete.html"].includes(page)) return;
    const intro = document.querySelector(".completion-intro");
    const heading = intro.querySelector("h1");
    const summary = intro.querySelector("p");
    const statusIcon = intro.querySelector(".completion-check");
    const card = document.querySelector(".completion-card");
    const details = card.querySelector("[data-completion-details]");
    const detailButton = document.querySelector("[data-completion-detail]");
    card.hidden = true;
    detailButton.disabled = true;
    intro.dataset.state = "loading";
    heading.textContent = "등록 정보를 확인하고 있습니다.";
    summary.textContent = "";
    const id = new URLSearchParams(window.location.search).get("id");
    let server = false;
    try {
      server = Boolean(id && await api.enabled());
      const record = id && await (server ? api.get(id) : evidenceStore("readonly", tx => tx.objectStore("records").get(id)));
      if (!record || record.id !== id) {
        intro.dataset.state = "error";
        statusIcon.textContent = "!";
        heading.textContent = "등록된 파일을 찾을 수 없습니다.";
        summary.textContent = "증거 목록을 확인하거나 파일을 다시 등록해 주세요.";
        return;
      }
      const fields = [
        ["증거 이름", record.name],
        ["파일", record.filename],
        ["파일 크기", Number.isFinite(record.size) ? `${record.size.toLocaleString("ko-KR")} bytes` : null],
        ["증거 ID", record.id],
        ["등록 일시 (KST)", registrationTime(record).text],
        ["저장 위치", server ? "서버" : "현재 브라우저"],
        ["무결성 상태", record.status, "status"],
        ["SHA-256", record.sha256, "hash"]
      ];
      details.replaceChildren(...fields.map(([label, value, field]) => {
        const row = document.createElement("div");
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        if (field) row.dataset.field = field;
        if (field === "status" && record.verificationStatus) row.dataset.status = record.verificationStatus;
        term.textContent = label;
        description.textContent = value || "미기록";
        row.append(term, description);
        return row;
      }));
      intro.dataset.state = "success";
      statusIcon.textContent = "✓";
      heading.textContent = server ? "서버에 증거를 등록했습니다." : "이 브라우저에 증거를 등록했습니다.";
      summary.textContent = "파일과 입력 정보가 저장되었습니다. 검증 화면에서 등록 당시 해시와 비교할 수 있습니다.";
      card.hidden = false;
      detailButton.addEventListener("click", () => {
        window.location.href = `evidence.html?id=${encodeURIComponent(record.id)}`;
      });
      detailButton.disabled = false;
    } catch (error) {
      intro.dataset.state = "error";
      statusIcon.textContent = "!";
      heading.textContent = "등록 정보를 불러오지 못했습니다.";
      summary.textContent = server ? error.message || "잠시 후 다시 시도해 주세요." : "브라우저 저장소 권한을 확인하고 새로고침해 주세요.";
    }
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && activeLayer) closeDialog();
  });

  applyProfileName();
  window.addEventListener("pageshow", event => { if (event.persisted) applyProfileName(true); });
  setupRegistration();
  setupEvidenceManagement();
  setupVerification();
  setupEvidenceCompletion();
})();
