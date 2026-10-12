(function () {
  "use strict";
  const login = document.getElementById("login-form");
  const signup = document.getElementById("signup-form");
  const terms = document.getElementById("terms-form");
  async function backendEnabled() {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      return response.ok && (await response.json()).backend === true;
    } catch (error) { return false; }
  }
  async function authenticate(path, payload) {
    const response = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "요청에 실패했습니다.");
    sessionStorage.setItem("evidence-auth-token", result.token);
    localStorage.setItem("evidence-profile-name", result.user.name);
    return result;
  }
  function showAuthError(form, message) {
    let status = form.querySelector("[role=status]");
    if (!status) {
      status = document.createElement("p");
      status.setAttribute("role", "status");
      status.className = "auth-status";
      form.append(status);
    }
    status.hidden = false;
    status.textContent = message;
  }
  function destination() {
    const next = new URLSearchParams(location.search).get("next");
    return next && /^[a-zA-Z0-9_-]+\.html(?:\?[a-zA-Z0-9_=&%-]*)?$/.test(next) ? next : "Dashboard.html";
  }
  if (login) {
    if (new URLSearchParams(window.location.search).has("signup-demo")) {
      const status = document.getElementById("auth-status");
      status.hidden = false;
      status.textContent = "회원가입 화면 시연을 마쳤습니다. 실제 계정 생성·이메일 인증은 인증 서버 연결 후 이용할 수 있습니다.";
    }
    login.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (!await backendEnabled()) {
        showAuthError(login, "서버에 연결할 수 없습니다. README의 실행 방법으로 서버를 시작해 주세요.");
        return;
      }
      try {
        await authenticate("/api/auth/login", { email: login.querySelector("#email").value, password: login.querySelector("#password").value });
        location.href = destination();
      } catch (error) { showAuthError(login, error.message); }
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
    const email = document.getElementById("email");
    const codeInput = document.getElementById("verification-code");
    const sendCode = document.getElementById("send-verification-code");
    const verifyCode = document.getElementById("verify-email-code");
    const verificationStatus = document.getElementById("email-verification-status");
    // Local UI demonstration only; this state must never authorize a real account.
    let challenge = null;
    let demoVerified = false;
    function showVerificationStatus(message, state) {
      verificationStatus.textContent = message;
      verificationStatus.dataset.state = state || "info";
    }
    email.addEventListener("input", function () {
      const hadChallenge = challenge !== null;
      challenge = null;
      demoVerified = false;
      codeInput.value = "";
      codeInput.setCustomValidity("");
      codeInput.disabled = true;
      codeInput.readOnly = false;
      verifyCode.disabled = true;
      verifyCode.textContent = "확인";
      sendCode.textContent = "인증코드 보내기";
      showVerificationStatus(hadChallenge ? "이메일이 변경되었습니다. 인증코드를 다시 요청해 주세요." : "");
    });
    sendCode.addEventListener("click", function () {
      if (!email.reportValidity()) return;
      const random = new Uint32Array(1);
      window.crypto.getRandomValues(random);
      let code = String(100000 + random[0] % 900000);
      if (challenge && code === challenge.code) code = String(100000 + (Number(code) - 100000 + 1) % 900000);
      challenge = { email: email.value, code: code, expiresAt: Date.now() + 180000 };
      demoVerified = false;
      codeInput.disabled = false;
      codeInput.readOnly = false;
      codeInput.value = "";
      codeInput.setCustomValidity("");
      verifyCode.disabled = false;
      verifyCode.textContent = "확인";
      sendCode.textContent = "재발송";
      showVerificationStatus("화면 시연용 코드: " + code + " (3분간 유효). 실제 이메일은 발송되지 않습니다.");
      codeInput.focus();
    });
    codeInput.addEventListener("input", function () { codeInput.setCustomValidity(""); });
    verifyCode.addEventListener("click", function () {
      if (!challenge || challenge.email !== email.value) {
        showVerificationStatus("인증코드를 먼저 요청해 주세요.", "error");
        return;
      }
      if (Date.now() >= challenge.expiresAt) {
        showVerificationStatus("코드 유효시간이 지났습니다. 재발송을 눌러 주세요.", "error");
        return;
      }
      if (!codeInput.reportValidity()) return;
      if (codeInput.value !== challenge.code) {
        codeInput.setCustomValidity("인증코드가 일치하지 않습니다.");
        showVerificationStatus("인증코드가 일치하지 않습니다. 다시 확인해 주세요.", "error");
        codeInput.reportValidity();
        return;
      }
      demoVerified = true;
      codeInput.readOnly = true;
      verifyCode.disabled = true;
      verifyCode.textContent = "확인 완료";
      showVerificationStatus("시연용 코드 확인이 완료되었습니다. 실제 이메일 인증은 진행되지 않았습니다.", "success");
    });
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
    signup.addEventListener("submit", async function (event) {
      event.preventDefault();
      validatePassword();
      if (!demoVerified || !challenge || challenge.email !== email.value) {
        showVerificationStatus("인증코드를 요청한 뒤 확인 버튼을 눌러 주세요.", "error");
        (challenge ? verifyCode : sendCode).focus();
        return;
      }
      if (!signup.reportValidity()) return;
      if (!await backendEnabled()) {
        showAuthError(signup, "서버에 연결할 수 없습니다. README의 실행 방법으로 서버를 시작해 주세요.");
        return;
      }
      try {
        await authenticate("/api/auth/register", {
          name: document.getElementById("name").value.trim(), email: email.value,
          password: password.value
        });
        location.href = "Dashboard.html";
      } catch (error) { showAuthError(signup, error.message); }
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
