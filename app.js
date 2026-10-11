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

  function verificationFlow() {
    runProgress({
      start: 22,
      title: "무결성 검증 중…",
      description: "현재 SHA-256 값을 계산하고 원본 기록과 비교하고 있습니다.",
      item: "evidence_video_001.mp4",
      meta: "EV-20261008-001 · 영상 · 18.6 MB",
      status: "해시 계산 및 비교 중",
      onComplete: verificationSuccess
    });
  }

  function verificationSuccess() {
    const layer = showDialog(`
      <div class="evidence-dialog__head">
        <div class="evidence-dialog__icon evidence-dialog__icon--success">✓</div>
        <div><h2>무결성 검증 완료</h2><p>현재 파일의 SHA-256이 등록 당시의 SHA-256과 일치합니다.</p></div>
      </div>
      <div class="evidence-dialog__item"><div><strong>evidence_video_001.mp4</strong><small>EV-20261008-001 · 영상 · 18.6 MB</small></div></div>
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
  setupVerification();
  setupEvidenceCompletion();
})();
