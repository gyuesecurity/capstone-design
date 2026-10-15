(function () {
  "use strict";
  const api = window.AccountApi;
  if (!api) return;

  function textAll(selector, value) {
    document.querySelectorAll(selector).forEach(node => { node.textContent = value; });
  }
  function formatDate(value, options) {
    if (!value) return "기록 없음";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "기록 없음";
    return new Intl.DateTimeFormat("ko-KR", options || {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
    }).format(date);
  }
  function activityTitle(item) {
    const name = item.evidenceFilename || item.evidenceName || item.evidenceId;
    const actions = {
      register: "회원가입", login: "로그인", logout: "로그아웃",
      upload: "증거 등록", download: "원본 파일 다운로드",
      "verify:MATCH": "무결성 검증 · 일치", "verify:MISMATCH": "무결성 검증 · 불일치",
      "verify:UNAVAILABLE": "무결성 검증 · 검증 불가",
      profile_update: "프로필 이름 수정", password_change: "비밀번호 변경"
    };
    return name ? `${name} · ${actions[item.action] || item.action}` : actions[item.action] || item.action;
  }
  function renderActivities(items) {
    const list = document.querySelector("[data-mypage-activities]");
    list.replaceChildren();
    const shown = items.slice(0, 20);
    textAll("[data-mypage-activity-count]", `${shown.length}건`);
    if (!shown.length) {
      const empty = document.createElement("li");
      empty.className = "mypage-empty";
      empty.textContent = "아직 기록된 활동이 없습니다.";
      list.append(empty);
      return;
    }
    shown.forEach(item => {
      const row = document.createElement("li");
      const content = document.createElement(item.evidenceId ? "a" : "div");
      if (item.evidenceId) content.href = `evidence.html?id=${encodeURIComponent(item.evidenceId)}`;
      const detail = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = activityTitle(item);
      detail.append(title);
      if (item.evidenceId) {
        const code = document.createElement("small");
        code.textContent = item.evidenceId;
        detail.append(code);
      }
      const time = document.createElement("time");
      time.dateTime = item.createdAt;
      time.textContent = formatDate(item.createdAt);
      content.append(detail, time);
      row.append(content);
      list.append(row);
    });
  }
  async function load() {
    try {
      const [user, evidence, audit] = await Promise.all([api.me(), api.evidence(), api.audit()]);
      textAll("[data-profile-name]", user.name);
      textAll("[data-profile-email]", user.email);
      textAll("[data-profile-created]", formatDate(user.createdAt, { year: "numeric", month: "2-digit", day: "2-digit" }));
      textAll('[data-mypage-stat="evidence"]', String(evidence.length));
      textAll('[data-mypage-stat="matched"]', String(evidence.filter(item => item.verificationStatus === "MATCH").length));
      textAll('[data-mypage-stat="verifications"]', String(evidence.reduce((count, item) => count + (item.verifications?.length || 0), 0)));
      textAll('[data-mypage-stat="latest"]', audit.length ? formatDate(audit[0].createdAt, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "기록 없음");
      const lastLogin = audit.find(item => item.action === "login");
      textAll("[data-mypage-last-login]", lastLogin ? formatDate(lastLogin.createdAt) : "기록 없음");
      renderActivities(audit);
      try { localStorage.setItem("evidence-profile-name", user.name); } catch (_) { /* Server data still renders. */ }
    } catch (error) {
      const message = document.querySelector("[data-mypage-message]");
      message.textContent = error.message || "계정 정보를 불러오지 못했습니다.";
      message.hidden = false;
      textAll("[data-profile-name]", "계정 정보를 불러올 수 없습니다");
      renderActivities([]);
      textAll("[data-mypage-activity-count]", "불러오기 실패");
    }
  }
  load();
  window.addEventListener("pageshow", event => { if (event.persisted) load(); });
})();
