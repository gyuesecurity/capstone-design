(function () {
  "use strict";
  const login = document.getElementById("login-form");
  const signup = document.getElementById("signup-form");
  const terms = document.getElementById("terms-form");
  // Static navigation demo only. Never persist credentials or claim authentication.
  if (login) {
    if (new URLSearchParams(window.location.search).has("signup-demo")) {
      const status = document.getElementById("auth-status");
      status.hidden = false;
      status.textContent = "회원가입 화면 시연을 마쳤습니다. 실제 계정 생성·이메일 인증은 인증 서버 연결 후 이용할 수 있습니다.";
    }
    login.addEventListener("submit", function (event) {
      event.preventDefault();
      window.location.href = "index_home.html";
    });
    document.querySelectorAll("[data-recovery]").forEach(function (button) {
      button.addEventListener("click", function () {
        const dialog = document.getElementById("recovery-dialog");
        dialog.querySelector("h2").textContent = button.dataset.recovery === "id" ? "아이디 찾기" : "비밀번호 찾기";
        dialog.showModal();
      });
    });
  }
  if (signup) {
    const password = document.getElementById("password");
    const confirm = document.getElementById("password-confirm");
    function validatePassword() {
      const value = password.value;
      const valid = value.length >= 8 && value.length <= 16 && /[A-Za-z]/.test(value) && /[0-9]/.test(value) && /[^A-Za-z0-9\s]/.test(value);
      password.setCustomValidity(value && !valid ? "8~16자 영문·숫자·특수문자를 모두 포함해 주세요." : "");
      confirm.setCustomValidity(confirm.value && confirm.value !== value ? "비밀번호가 일치하지 않습니다." : "");
      const score = value ? [value.length >= 8, /[A-Za-z]/.test(value), /[0-9]/.test(value), /[^A-Za-z0-9\s]/.test(value)].filter(Boolean).length : 0;
      document.querySelectorAll(".auth-strength-bars i").forEach(function (bar, index) {
        bar.style.background = index < score ? (valid ? "var(--evidence-success)" : "#c25c5c") : "var(--evidence-surface)";
      });
      document.getElementById("password-strength").textContent = "비밀번호 강도: " + (valid ? "강함" : score >= 2 ? "보통" : "약함");
    }
    password.addEventListener("input", validatePassword);
    confirm.addEventListener("input", validatePassword);
    document.getElementById("name").addEventListener("input", function (event) {
      event.target.setCustomValidity(event.target.value.trim() ? "" : "이름을 입력해 주세요.");
    });
    signup.addEventListener("submit", function (event) {
      event.preventDefault();
      validatePassword();
      if (signup.reportValidity()) window.location.href = "signup_terms.html";
    });
  }
  if (terms) {
    const all = document.getElementById("agree-all");
    const items = Array.from(terms.querySelectorAll("input[type=checkbox]")).filter(function (item) { return item !== all; });
    all.addEventListener("change", function () { items.forEach(function (item) { item.checked = all.checked; }); });
    items.forEach(function (item) {
      item.addEventListener("change", function () {
        all.checked = items.every(function (input) { return input.checked; });
        all.indeterminate = !all.checked && items.some(function (input) { return input.checked; });
      });
    });
    document.querySelectorAll("[data-terms-toggle]").forEach(function (button) {
      const detail = document.getElementById("term-" + button.dataset.termsToggle);
      button.setAttribute("aria-controls", detail.id);
      button.addEventListener("click", function () {
        detail.hidden = !detail.hidden;
        button.setAttribute("aria-expanded", String(!detail.hidden));
      });
    });
    terms.addEventListener("submit", function (event) {
      event.preventDefault();
      window.location.href = "login.html?signup-demo=1";
    });
  }
})();
