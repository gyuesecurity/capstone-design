(function () {
  "use strict";
  const login = document.getElementById("login-form");
  const signup = document.getElementById("signup-form");
  const terms = document.getElementById("terms-form");
  async function backendEnabled() {
    let response;
    try { response = await fetch("/api/health", { cache: "no-store" }); }
    catch (_) { throw new Error("서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요."); }
    if (response.status === 404 || response.status === 405) return false;
    if (!response.ok) throw new Error("인증 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    if (!response.headers.get("content-type")?.includes("application/json")) return false;
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error("인증 서버 응답을 확인하지 못했습니다. 다시 시도해 주세요."); }
    return result.backend === true;
  }
  async function authenticate(path, payload) {
    let response;
    try {
      response = await fetch(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch (_) {
      throw new Error(path.endsWith("register")
        ? "가입 요청 결과를 확인하지 못했습니다. 다시 가입하기 전에 입력한 계정으로 로그인을 확인해 주세요."
        : "서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 로그인해 주세요.");
    }
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error("서버 응답을 확인하지 못했습니다. 입력한 계정 정보를 확인한 뒤 다시 시도해 주세요."); }
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "요청에 실패했습니다. 입력 내용을 확인한 뒤 다시 시도해 주세요.");
    if (typeof result.token !== "string" || !result.token) throw new Error("서버에서 로그인 정보를 받지 못했습니다. 다시 로그인해 주세요.");
    sessionStorage.setItem("evidence-auth-token", result.token);
    sessionStorage.removeItem("evidence-demo-mode");
    return result;
  }
  function showAuthStatus(form, message, state = "error") {
    let status = form.querySelector("[data-auth-feedback], #auth-status");
    if (!status) {
      status = document.createElement("p");
      status.className = "auth-status";
      form.append(status);
    }
    status.dataset.authFeedback = "";
    status.dataset.state = state;
    status.setAttribute("role", state === "error" ? "alert" : "status");
    status.setAttribute("aria-live", state === "error" ? "assertive" : "polite");
    status.hidden = !message;
    status.textContent = message;
    return status;
  }
  function showAuthError(form, message) { return showAuthStatus(form, message, "error"); }
  function beginAuth(form, message) {
    form.dataset.authBusy = "true";
    form.setAttribute("aria-busy", "true");
    const controls = Array.from(form.querySelectorAll("input, button, select, textarea"), node => ({ node, disabled: node.disabled }));
    const submit = form.querySelector('button[type="submit"]');
    const label = submit?.textContent;
    controls.forEach(({ node }) => { node.disabled = true; });
    if (submit) submit.textContent = message;
    showAuthStatus(form, message, "processing");
    return () => {
      controls.forEach(({ node, disabled }) => { node.disabled = disabled; });
      if (submit) submit.textContent = label;
      form.dataset.authBusy = "false";
      form.setAttribute("aria-busy", "false");
    };
  }
  function showStaticDemoLink(form) {
    const status = showAuthStatus(form, "이 배포에는 인증 서버가 없습니다. 실제 계정과 파일은 로컬 서버에서 사용할 수 있습니다.", "error");
    if (document.querySelector("[data-demo-entry]")) return;
    const link = document.createElement("a");
    link.href = "index_home.html?demo=1";
    link.textContent = "화면 시연 계속하기";
    status.append(" ", link);
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
      if (login.dataset.authBusy === "true" || !login.reportValidity()) return;
      const payload = { email: login.querySelector("#email").value, password: login.querySelector("#password").value };
      const release = beginAuth(login, "로그인 중…");
      let navigating = false;
      try {
        if (!await backendEnabled()) {
          showStaticDemoLink(login);
          return;
        }
        await authenticate("/api/auth/login", payload);
        navigating = true;
        location.href = destination();
      } catch (error) { showAuthError(login, error.message); }
      finally { if (!navigating) release(); }
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
      verificationStatus.setAttribute("role", state === "error" ? "alert" : "status");
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
      document.querySelector(".auth-strength-bars").hidden = !value;
      document.getElementById("password-strength").hidden = !value;
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
      if (signup.dataset.authBusy === "true") return;
      validatePassword();
      if (!demoVerified || !challenge || challenge.email !== email.value) {
        showVerificationStatus("인증코드를 요청한 뒤 확인 버튼을 눌러 주세요.", "error");
        (challenge ? verifyCode : sendCode).focus();
        return;
      }
      if (!signup.reportValidity()) return;
      const payload = { name: document.getElementById("name").value.trim(), email: email.value, password: password.value };
      const release = beginAuth(signup, "가입 요청 중…");
      let navigating = false;
      try {
        if (!await backendEnabled()) {
          navigating = true;
          location.href = "login.html?signup-demo=1";
          return;
        }
        await authenticate("/api/auth/register", payload);
        navigating = true;
        location.href = "Dashboard.html";
      } catch (error) { showAuthError(signup, error.message); }
      finally { if (!navigating) release(); }
    });
  }
  if (terms) {
    if (new URLSearchParams(location.search).get("view") === "terms") {
      terms.querySelector(".auth-actions").hidden = true;
      const close = document.querySelector(".auth-close");
      close.textContent = "닫기";
      close.addEventListener("click", event => { event.preventDefault(); window.close(); });
    }
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
