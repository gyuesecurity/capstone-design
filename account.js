(function () {
  "use strict";

  const tokenKey = "evidence-auth-token";
  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  let availability;

  function enabled() {
    if (sessionStorage.getItem("evidence-demo-mode") === "1") return Promise.resolve(false);
    if (!availability) availability = fetch("/api/health", { cache: "no-store" })
      .then(response => {
        if (response.status >= 500) throw new Error("계정 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
        return response.ok && response.headers.get("content-type")?.includes("application/json");
      }).catch(error => {
        availability = undefined;
        throw new Error(error.message === "Failed to fetch" ? "서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요." : error.message);
      });
    return availability;
  }

  async function request(path, options = {}) {
    if (!await enabled()) throw new Error("계정 정보를 보려면 서버에서 로그인해 주세요.");
    const token = sessionStorage.getItem(tokenKey);
    if (!token) {
      location.href = `login.html?next=${encodeURIComponent(location.pathname.split("/").pop())}`;
      throw new Error("로그인이 필요합니다.");
    }
    let response;
    try {
      response = await fetch(path, {
        ...options, cache: "no-store",
        headers: { ...options.headers, Authorization: `Bearer ${token}` }
      });
    } catch (_) { throw new Error("서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요."); }
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

  function showStatus(message, state = "error", retry) {
    const status = document.querySelector("[data-account-status]");
    if (!status) return;
    status.replaceChildren();
    status.dataset.state = state;
    status.setAttribute("role", state === "error" ? "alert" : "status");
    status.setAttribute("aria-live", state === "error" ? "assertive" : "polite");
    status.hidden = !message;
    if (message) {
      const text = document.createElement("span");
      text.textContent = message;
      status.append(text);
    }
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.accountRetry = "load";
      button.className = "account-button account-status-retry";
      button.textContent = "다시 불러오기";
      button.addEventListener("click", retry);
      status.append(button);
    }
  }

  function lockForm(form, label) {
    form.setAttribute("aria-busy", "true");
    const controls = Array.from(form.querySelectorAll("input, button, select, textarea"), node => ({ node, disabled: node.disabled }));
    const submit = form.querySelector('button[type="submit"]');
    const text = submit?.textContent;
    controls.forEach(({ node }) => { node.disabled = true; });
    if (submit) submit.textContent = label;
    return () => {
      controls.forEach(({ node, disabled }) => { node.disabled = disabled; });
      if (submit) submit.textContent = text;
      form.setAttribute("aria-busy", "false");
    };
  }

  const profileForm = document.getElementById("profile-edit-form");
  if (profileForm) {
    const nameInput = profileForm.elements.name;
    const save = profileForm.querySelector('button[type="submit"]');
    let loading = false;
    let saving = false;
    let loaded = false;
    async function loadProfile() {
      if (loading || saving) return;
      loading = true;
      save.disabled = true;
      const release = lockForm(profileForm, "정보 확인 중…");
      showStatus("계정 정보를 불러오고 있습니다.", "processing");
      availability = undefined;
      try {
        const user = await api.me();
        nameInput.value = user.name;
        profileForm.elements.email.value = user.email;
        loaded = true;
        showStatus("", "success");
      } catch (error) { showStatus(error.message, "error", loadProfile); }
      finally {
        release();
        loading = false;
        nameInput.disabled = !loaded;
        save.disabled = !loaded;
      }
    }
    loadProfile();
    profileForm.addEventListener("submit", async event => {
      event.preventDefault();
      if (loading || saving || !loaded) return;
      const name = normalize(nameInput.value);
      nameInput.value = name;
      nameInput.setCustomValidity(name ? "" : "이름을 입력해 주세요.");
      if (!profileForm.reportValidity()) { showStatus("이름을 입력해 주세요."); return; }
      saving = true;
      const release = lockForm(profileForm, "저장 중…");
      let navigating = false;
      showStatus("이름을 저장하고 있습니다.", "processing");
      try {
        await api.updateProfile(name);
        navigating = true;
        showStatus("이름을 저장했습니다. 마이페이지로 이동합니다.", "success");
        window.setTimeout(() => { location.href = "mypage.html"; }, 500);
      } catch (error) { showStatus(error.message); }
      finally {
        if (!navigating) { release(); saving = false; save.textContent = "다시 저장"; }
      }
    });
  }

  const passwordForm = document.getElementById("password-change-form");
  if (passwordForm) {
    const next = passwordForm.elements.next;
    const confirm = passwordForm.elements.confirm;
    const submit = passwordForm.querySelector('button[type="submit"]');
    let changing = false;
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
      if (changing) return;
      validatePassword();
      if (!passwordForm.reportValidity()) return;
      const current = passwordForm.elements.current.value, replacement = next.value;
      changing = true;
      const release = lockForm(passwordForm, "변경 중…");
      let navigating = false;
      showStatus("비밀번호를 변경하고 있습니다.", "processing");
      try {
        await api.changePassword(current, replacement);
        navigating = true;
        passwordForm.reset();
        showStatus("비밀번호 변경을 완료했습니다. 마이페이지로 이동합니다.", "success");
        window.setTimeout(() => { location.href = "mypage.html"; }, 500);
      } catch (error) { showStatus(error.message); }
      finally {
        if (!navigating) { release(); changing = false; submit.textContent = "다시 변경"; }
      }
    });
  }

  const logoutButton = document.getElementById("logout-confirm");
  if (logoutButton) {
    let loggingOut = false;
    logoutButton.addEventListener("click", async () => {
      if (loggingOut) return;
      loggingOut = true;
      logoutButton.disabled = true;
      logoutButton.setAttribute("aria-busy", "true");
      logoutButton.textContent = "로그아웃 중…";
      showStatus("로그아웃 요청을 처리하고 있습니다.", "processing");
      let navigating = false;
      try {
        const token = sessionStorage.getItem(tokenKey);
        if (token) {
          let response;
          try { response = await fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }); }
          catch (_) { throw new Error("로그아웃 요청 결과를 확인하지 못했습니다. 연결을 확인하고 다시 시도해 주세요."); }
          if (!response.ok && response.status !== 401) throw new Error("로그아웃을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.");
        }
        sessionStorage.removeItem(tokenKey);
        sessionStorage.removeItem("evidence-demo-mode");
        navigating = true;
        showStatus("로그아웃했습니다. 시작 화면으로 이동합니다.", "success");
        window.setTimeout(() => { location.href = "index.html"; }, 350);
      } catch (error) { showStatus(error.message); }
      finally {
        if (!navigating) {
          loggingOut = false;
          logoutButton.disabled = false;
          logoutButton.setAttribute("aria-busy", "false");
          logoutButton.textContent = "로그아웃 다시 시도";
        }
      }
    });
  }
})();
