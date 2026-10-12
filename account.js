(function () {
  "use strict";

  const tokenKey = "evidence-auth-token";
  const profileNameKey = "evidence-profile-name";
  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  let availability;

  function enabled() {
    if (!availability) availability = fetch("/api/health", { cache: "no-store" })
      .then(response => response.ok && response.headers.get("content-type")?.includes("application/json"))
      .catch(() => false);
    return availability;
  }

  async function request(path, options = {}) {
    if (!await enabled()) throw new Error("계정 정보는 FastAPI 서버에서만 이용할 수 있습니다.");
    const token = sessionStorage.getItem(tokenKey);
    if (!token) {
      location.href = `login.html?next=${encodeURIComponent(location.pathname.split("/").pop())}`;
      throw new Error("로그인이 필요합니다.");
    }
    const response = await fetch(path, {
      ...options, cache: "no-store",
      headers: { ...options.headers, Authorization: `Bearer ${token}` }
    });
    if (response.status === 401) {
      sessionStorage.removeItem(tokenKey);
      location.href = `login.html?next=${encodeURIComponent(location.pathname.split("/").pop())}`;
      throw new Error("로그인이 만료되었습니다.");
    }
    if (!response.ok) {
      let detail = "요청을 처리하지 못했습니다.";
      try { detail = (await response.json()).detail || detail; } catch (_) { /* Empty response. */ }
      throw new Error(typeof detail === "string" ? detail : "입력 내용을 확인해 주세요.");
    }
    return response.json();
  }

  const api = {
    enabled,
    me: () => request("/api/me"),
    evidence: () => request("/api/evidence"),
    audit: () => request("/api/audit"),
    updateProfile: name => request("/api/me", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name })
    }),
    changePassword: (currentPassword, newPassword) => request("/api/auth/password", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
    })
  };
  window.AccountApi = api;

  function showStatus(message) {
    const status = document.querySelector("[data-account-status]");
    if (!status) return;
    status.textContent = message;
    status.hidden = false;
  }

  const profileForm = document.getElementById("profile-edit-form");
  if (profileForm) {
    const nameInput = profileForm.elements.name;
    const save = profileForm.querySelector('button[type="submit"]');
    save.disabled = true;
    api.me().then(user => {
      nameInput.value = user.name;
      profileForm.elements.email.value = user.email;
      save.disabled = false;
    }).catch(error => showStatus(error.message));
    profileForm.addEventListener("submit", async event => {
      event.preventDefault();
      const name = normalize(nameInput.value);
      nameInput.value = name;
      nameInput.setCustomValidity(name ? "" : "이름을 입력해 주세요.");
      if (!profileForm.reportValidity()) return;
      save.disabled = true;
      try {
        const user = await api.updateProfile(name);
        localStorage.setItem(profileNameKey, user.name);
        showStatus("이름을 저장했습니다. 마이페이지로 이동합니다.");
        window.setTimeout(() => { location.href = "mypage.html"; }, 500);
      } catch (error) {
        showStatus(error.message);
        save.disabled = false;
      }
    });
  }

  const passwordForm = document.getElementById("password-change-form");
  if (passwordForm) {
    const next = passwordForm.elements.next;
    const confirm = passwordForm.elements.confirm;
    function validatePassword() {
      const value = next.value;
      const valid = value.length >= 8 && /[A-Za-z]/.test(value) && /[0-9]/.test(value) && /[^A-Za-z0-9\s]/.test(value);
      next.setCustomValidity(value && !valid ? "영문, 숫자, 특수문자를 조합해 8자 이상 입력해 주세요." : "");
      confirm.setCustomValidity(confirm.value && confirm.value !== value ? "새 비밀번호가 서로 일치하지 않습니다." : "");
    }
    next.addEventListener("input", validatePassword);
    confirm.addEventListener("input", validatePassword);
    passwordForm.addEventListener("submit", async event => {
      event.preventDefault();
      validatePassword();
      if (!passwordForm.reportValidity()) return;
      try {
        await api.changePassword(passwordForm.elements.current.value, next.value);
        passwordForm.reset();
        showStatus("비밀번호 변경을 완료했습니다. 마이페이지로 이동합니다.");
        window.setTimeout(() => { location.href = "mypage.html"; }, 500);
      } catch (error) { showStatus(error.message); }
    });
  }

  const logoutButton = document.getElementById("logout-confirm");
  if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
      const token = sessionStorage.getItem(tokenKey);
      if (token) {
        try { await fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }); }
        catch (_) { /* Clear the browser token even when the server is unavailable. */ }
      }
      sessionStorage.removeItem(tokenKey);
      showStatus("로그아웃했습니다. 시작 화면으로 이동합니다.");
      window.setTimeout(() => { location.href = "index.html"; }, 350);
    });
  }
})();
