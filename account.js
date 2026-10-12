(function () {
  "use strict";

  const profileNameKey = "evidence-profile-name";
  const defaultProfileName = "소연";
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();

  function getProfileName() {
    try {
      return normalize(localStorage.getItem(profileNameKey)) || defaultProfileName;
    } catch (error) {
      return defaultProfileName;
    }
  }

  function saveProfileName(name) {
    try {
      localStorage.setItem(profileNameKey, name);
    } catch (error) {
      // The current page still reflects the saved value if browser storage is unavailable.
    }
    document.querySelectorAll("[data-profile-name]").forEach(function (element) {
      element.textContent = name;
    });
  }

  function showStatus(message) {
    const status = document.querySelector("[data-account-status]");
    if (!status) return;
    status.textContent = message;
    status.hidden = false;
  }

  document.querySelectorAll("[data-profile-name]").forEach(function (element) {
    element.textContent = getProfileName();
  });

  const profileForm = document.getElementById("profile-edit-form");
  if (profileForm) {
    const nameInput = profileForm.elements.name;
    nameInput.value = getProfileName();
    profileForm.addEventListener("submit", function (event) {
      event.preventDefault();
      const name = normalize(nameInput.value);
      nameInput.value = name;
      nameInput.setCustomValidity(name ? "" : "이름을 입력해 주세요.");
      if (!profileForm.reportValidity()) return;
      saveProfileName(name);
      showStatus("이름을 저장했습니다. 마이페이지로 이동합니다.");
      window.setTimeout(() => { window.location.href = "mypage.html"; }, 550);
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
    passwordForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      validatePassword();
      if (!passwordForm.reportValidity()) return;
      try {
        const token = sessionStorage.getItem("evidence-auth-token");
        if (!token) throw new Error("로그인이 필요합니다.");
        const response = await fetch("/api/auth/password", {
          method: "PUT",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ current_password: passwordForm.elements.current.value, new_password: next.value })
        });
        if (!response.ok) {
          const result = await response.json();
          throw new Error(result.detail || "비밀번호 변경에 실패했습니다.");
        }
      } catch (error) {
        showStatus(error.message);
        return;
      }
      passwordForm.reset();
      showStatus("비밀번호 변경을 완료했습니다. 마이페이지로 이동합니다.");
      window.setTimeout(() => { window.location.href = "mypage.html"; }, 550);
    });
  }

  const logoutButton = document.getElementById("logout-confirm");
  if (logoutButton) {
    logoutButton.addEventListener("click", async function () {
      const token = sessionStorage.getItem("evidence-auth-token");
      if (token) {
        try {
          await fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
        } catch (error) { /* Local token is cleared even if the server is unavailable. */ }
      }
      try {
        sessionStorage.clear();
      } catch (error) {
        // Continue to the public landing page when session storage is unavailable.
      }
      showStatus("로그아웃했습니다. 시작 화면으로 이동합니다.");
      window.setTimeout(() => { window.location.href = "index.html"; }, 350);
    });
  }
})();
