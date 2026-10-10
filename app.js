(function () {
  "use strict";

  const page = (window.location.pathname.split("/").pop() || "index.html").toLowerCase();
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  let activeLayer = null;
  let activeTimer = null;

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

  function bind(label, handler, options) {
    const settings = Object.assign({ all: false, primaryOnly: false }, options || {});
    let targets = exactTextElements(label).map(actionSurface);
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
    const isUrl = kind === "웹페이지";
    const title = isUrl ? "웹페이지 증거가 저장되었습니다." : `${kind} 증거가 등록되었습니다.`;
    const layer = showDialog(`
      <div class="evidence-dialog__head">
        <div class="evidence-dialog__icon evidence-dialog__icon--success">✓</div>
        <div><h2>${title}</h2><p>증거와 무결성 확인을 위한 기록이 안전하게 보존되었습니다.</p></div>
      </div>
      ${detailsMarkup([
        ["증거 ID", `EV-${localDateStamp()}-001`],
        ["SHA-256", "a8f5f167…9c21"],
        ["수집 시간", new Date().toLocaleString("ko-KR")]
      ])}
      <div class="evidence-dialog__actions">
        ${button("증거 관리로 이동", "evidence")}
        ${button("증거 상세보기", "detail", "primary")}
      </div>
    `, { wide: true });
    layer.querySelector('[data-dialog-action="evidence"]').addEventListener("click", () => { window.location.href = "evidence.html"; });
    layer.querySelector('[data-dialog-action="detail"]').addEventListener("click", () => { window.location.href = "evidence.html"; });
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

  function chooseFile(accept) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept || "*/*";
    input.hidden = true;
    document.body.appendChild(input);
    input.addEventListener("change", function () {
      const file = input.files && input.files[0];
      if (file) {
        sessionStorage.setItem(`evidence-file:${page}`, file.name);
        toast(`${file.name} 파일이 선택되었습니다.`);
      }
      input.remove();
    }, { once: true });
    input.click();
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

  function reportFlow() {
    runProgress({
      start: 68,
      title: "PDF 보고서 생성 중…",
      description: "선택한 증거와 무결성 검증 정보를 문서에 포함하고 있습니다.",
      item: "디지털 증거 검증 보고서_001.pdf",
      meta: "선택 증거 2건 · 무결성 검증 정보 포함",
      status: "문서 구성 및 PDF 변환 중",
      onComplete: reportSuccess
    });
  }

  function reportSuccess() {
    const layer = showDialog(`
      <div class="evidence-dialog__head">
        <div class="evidence-dialog__icon evidence-dialog__icon--success">✓</div>
        <div><h2>보고서가 생성되었습니다.</h2><p>디지털 증거 검증 보고서_001.pdf</p></div>
      </div>
      ${detailsMarkup([["보고서 ID", "RPT-20261008-001"], ["포함 증거", "2건"], ["검증 결과", "정상 2건"]])}
      <div class="evidence-dialog__actions">
        ${button("증거 관리로 이동", "evidence")}
        ${button("보고서 보기", "view")}
        ${button("PDF 다운로드", "download", "primary")}
      </div>
    `, { wide: true });
    layer.querySelector('[data-dialog-action="evidence"]').addEventListener("click", () => { window.location.href = "evidence.html"; });
    layer.querySelector('[data-dialog-action="view"]').addEventListener("click", function () {
      closeDialog();
      const target = exactTextElements("보고서 상세 보기 예시")[0];
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    layer.querySelector('[data-dialog-action="download"]').addEventListener("click", function () {
      const lines = ["EVIDENCE", "Digital Evidence Verification Report", "Report ID: RPT-20261008-001", "Included evidence: 2", "Integrity result: 2 verified"];
      const escapePdf = (value) => value.replace(/([\\()])/g, "\\$1");
      const stream = `BT /F1 18 Tf 72 760 Td (${escapePdf(lines[0])}) Tj /F1 12 Tf 0 -34 Td (${escapePdf(lines[1])}) Tj 0 -28 Td (${escapePdf(lines[2])}) Tj 0 -22 Td (${escapePdf(lines[3])}) Tj 0 -22 Td (${escapePdf(lines[4])}) Tj ET`;
      const objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
      ];
      let pdf = "%PDF-1.4\n";
      const offsets = [0];
      objects.forEach((object, index) => {
        offsets.push(pdf.length);
        pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
      });
      const xref = pdf.length;
      pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
      offsets.slice(1).forEach((offset) => { pdf += `${String(offset).padStart(10, "0")} 00000 n \n`; });
      pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
      const blob = new Blob([pdf], { type: "application/pdf" });
      const anchor = document.createElement("a");
      anchor.href = URL.createObjectURL(blob);
      anchor.download = "digital-evidence-report.pdf";
      anchor.click();
      URL.revokeObjectURL(anchor.href);
      toast("데모 보고서 파일을 다운로드했습니다.");
    });
  }

  function profileDialog() {
    const layer = showDialog(`
      <h2>프로필 정보 수정</h2>
      <form class="evidence-dialog__form" id="profile-form">
        <label>이름<input name="name" value="소연" autocomplete="name"></label>
        <label>이메일<input name="email" type="email" value="soyeon@example.com" autocomplete="email"></label>
      </form>
      <div class="evidence-dialog__actions">
        ${button("취소", "cancel")}
        ${button("저장", "save", "primary")}
      </div>
    `);
    layer.querySelector('[data-dialog-action="cancel"]').addEventListener("click", closeDialog);
    layer.querySelector('[data-dialog-action="save"]').addEventListener("click", function () {
      const form = layer.querySelector("#profile-form");
      if (!form.reportValidity()) return;
      closeDialog();
      toast("프로필 정보를 저장했습니다.");
    });
  }

  function passwordDialog() {
    const layer = showDialog(`
      <h2>비밀번호 변경</h2>
      <form class="evidence-dialog__form" id="password-form">
        <label>현재 비밀번호<input name="current" type="password" required autocomplete="current-password" placeholder="현재 비밀번호를 입력하세요"></label>
        <label>새 비밀번호<input name="next" type="password" required minlength="8" autocomplete="new-password" placeholder="8자 이상 입력하세요"></label>
        <label>새 비밀번호 확인<input name="confirm" type="password" required minlength="8" autocomplete="new-password" placeholder="새 비밀번호를 다시 입력하세요"></label>
      </form>
      <div class="evidence-dialog__actions">
        ${button("취소", "cancel")}
        ${button("비밀번호 변경", "save", "primary")}
      </div>
    `);
    layer.querySelector('[data-dialog-action="cancel"]').addEventListener("click", closeDialog);
    layer.querySelector('[data-dialog-action="save"]').addEventListener("click", function () {
      const form = layer.querySelector("#password-form");
      if (!form.reportValidity()) return;
      const values = new FormData(form);
      if (values.get("next") !== values.get("confirm")) {
        toast("새 비밀번호가 서로 일치하지 않습니다.");
        return;
      }
      closeDialog();
      toast("비밀번호가 변경되었습니다.");
    });
  }

  function logoutDialog() {
    const layer = showDialog(`
      <div class="evidence-dialog__head">
        <div class="evidence-dialog__icon evidence-dialog__icon--danger">↪</div>
        <div><h2>로그아웃하시겠습니까?</h2><p>현재 계정에서 로그아웃합니다.</p></div>
      </div>
      <div class="evidence-dialog__actions">
        ${button("취소", "cancel")}
        ${button("로그아웃", "logout", "danger")}
      </div>
    `);
    layer.querySelector('[data-dialog-action="cancel"]').addEventListener("click", closeDialog);
    layer.querySelector('[data-dialog-action="logout"]').addEventListener("click", () => { window.location.href = "index.html"; });
  }

  function setupRegistration() {
    const configs = {
      "evidence_file.html": { kind: "파일", item: "contract_2026.pdf", meta: "1.8 MB · 문서", submit: "증거 등록", select: "파일 선택", accept: "*/*" },
      "evidence_img.html": { kind: "이미지", item: "screenshot_001.png", meta: "1.8 MB · 이미지", submit: "이미지 증거 등록", select: "이미지 선택", accept: "image/*" },
      "evidence_video.html": { kind: "영상·음성", item: "evidence_video_001.mp4", meta: "18.6 MB · 미디어", submit: "영상·음성 증거 등록", select: "파일 선택", accept: "video/*,audio/*" },
      "evidence_url.html": { kind: "웹페이지", item: "https://example.com/article", meta: "본문과 메타데이터 수집", submit: "웹페이지 증거 저장", select: "URL 수집" }
    };
    const config = configs[page];
    if (!config) return;
    if (config.kind !== "웹페이지") {
      bind(config.select, () => chooseFile(config.accept));
    } else {
      bind(config.select, () => toast("웹페이지 미리보기를 준비했습니다."), { primaryOnly: true });
    }
    bind(config.submit, function () {
      const selected = sessionStorage.getItem(`evidence-file:${page}`);
      registerEvidence(config.kind, selected || config.item, config.meta);
    }, { primaryOnly: true });
  }

  function setupVerification() {
    if (page !== "verification.html") return;
    ["증거 검증", "검증 시작", "다시 검증"].forEach((label) => bind(label, verificationFlow, { primaryOnly: true, all: true }));
  }

  function setupReport() {
    if (page !== "report.html") return;
    const scrollTo = (label) => {
      const target = exactTextElements(label)[0];
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    bind("다음: 보고서 정보", () => scrollTo("2. 보고서 정보"));
    bind("다음: 포함 내용 확인", () => scrollTo("3. 보고서 포함 내용"));
    bind("다음: 미리보기", () => scrollTo("보고서 미리보기"));
    bind("PDF 보고서 생성", reportFlow, { primaryOnly: true, all: true });
    bind("PDF 다운로드", function () {
      reportSuccess();
    }, { primaryOnly: true, all: true });
  }

  function setupMyPage() {
    if (page !== "mypage.html") return;
    bind("프로필 수정", profileDialog, { all: true });
    bind("비밀번호 변경", passwordDialog, { all: true });
    bind("로그아웃", logoutDialog);
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && activeLayer) closeDialog();
  });

  setupRegistration();
  setupVerification();
  setupReport();
  setupMyPage();
})();
