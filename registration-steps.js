(function () {
  "use strict";

  const rawPage = document.documentElement.dataset.pageFile || location.pathname.split("/").pop() || "";
  const page = rawPage.toLowerCase().replace(/\.html$/, "");
  const fileLabels = {
    evidence_file: "파일 선택",
    evidence_img: "이미지 선택",
    evidence_video: "영상·음성 선택"
  };
  const isUrl = page === "evidence_url";
  if (!isUrl && !fileLabels[page]) return;

  const bar = Array.from(document.querySelectorAll('.layout-section .layout-wrap[style*="height: 56px"], .layout-section .layout-wrap[style*="height: 64px"]'))
    .find(node => Array.from(node.querySelectorAll("div"))
      .filter(child => child.children.length === 0 && /^[1-6]$/.test(child.textContent.trim())).length >= 3);
  if (!bar) return;

  const labels = isUrl
    ? ["URL 입력", "주소 확인", "웹페이지 수집", "증거 저장"]
    : [fileLabels[page], "정보 확인", "증거 등록"];
  const list = document.createElement("ol");
  list.className = "registration-steps__list";
  list.setAttribute("aria-label", "증거 등록 단계");
  list.style.setProperty("--registration-step-count", String(labels.length));

  const items = labels.map((label, index) => {
    const item = document.createElement("li");
    item.className = "registration-steps__item";
    item.dataset.registrationStep = String(index + 1);
    const number = document.createElement("span");
    number.className = "registration-steps__number";
    number.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "registration-steps__label";
    name.textContent = label;
    const state = document.createElement("span");
    state.className = "registration-steps__item-state";
    item.append(number, name, state);
    list.append(item);
    return { item, number, state };
  });
  const status = document.createElement("p");
  status.className = "registration-steps__status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.setAttribute("aria-atomic", "true");
  bar.classList.add("registration-steps");
  bar.setAttribute("role", "region");
  bar.setAttribute("aria-label", "증거 등록 진행 상태");
  bar.replaceChildren(list, status);

  const validStates = new Set(["empty", "loading", "ready", "submitting", "error", "complete", "unavailable"]);
  let lastSettledState = "empty";
  let currentState = "empty";

  function setState(state) {
    if (!validStates.has(state)) return false;
    currentState = state;
    if (["empty", "ready", "unavailable"].includes(state)) lastSettledState = state;
    const current = state === "empty" ? 1
      : state === "complete" ? labels.length
      : state === "submitting" ? 3
      : state === "error" && lastSettledState === "empty" ? 1
      : 2;
    const isUnavailable = isUrl && (state === "unavailable" || (state === "error" && lastSettledState === "unavailable"));

    items.forEach(({ item, number, state: itemState }, index) => {
      const step = index + 1;
      const completed = state === "complete" || step < current;
      const active = step === current;
      const pendingUnavailable = isUnavailable && step > 2;
      const itemStatus = completed ? "complete" : active ? "current" : pendingUnavailable ? "unavailable" : "pending";
      item.dataset.status = itemStatus;
      if (active) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
      number.textContent = completed ? "✓" : String(step);
      itemState.textContent = completed ? "완료" : active ? "현재" : pendingUnavailable ? "준비 중" : "대기";
    });

    const messages = {
      empty: isUrl ? "수집할 웹페이지 주소를 입력하세요." : "등록할 파일을 선택하세요.",
      loading: isUrl ? "웹페이지 주소 확인 중…" : "파일 확인 중…",
      ready: isUrl ? "주소를 확인했습니다. 수집 정보를 확인해 주세요." : "정보를 확인한 후 등록하세요.",
      submitting: isUrl ? "웹페이지 수집 중…" : "원본 파일 등록 중…",
      error: "처리하지 못했습니다. 안내를 확인한 후 다시 시도해 주세요.",
      complete: "증거 등록이 완료되었습니다.",
      unavailable: isUrl ? "주소 확인 완료 · 실제 수집 준비 중" : "미리보기를 재생할 수 없습니다. 파일 안내를 확인하세요."
    };
    status.textContent = messages[state];
    bar.dataset.state = state;
    bar.setAttribute("aria-busy", String(state === "loading" || state === "submitting"));
    document.body.dataset.registrationState = state;
    return true;
  }

  window.EvidenceRegistration = {
    setState,
    get state() { return currentState; }
  };
  setState("empty");
})();
