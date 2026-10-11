(function () {
  "use strict";

  const currentPage = (window.location.pathname.split("/").pop() || "index.html").toLowerCase();
  const normalize = (value) => value.replace(/\s+/g, " ").trim();

  function exactTextElements(label) {
    return Array.from(document.querySelectorAll("a, button, div, span"))
      .filter((element) => normalize(element.textContent || "") === label)
      .filter((element) => !Array.from(element.children).some(
        (child) => normalize(child.textContent || "") === label
      ));
  }

  function hasButtonShape(element) {
    if (!element || element === document.body || element === document.documentElement) {
      return false;
    }

    if (element.matches("a, button")) {
      return true;
    }

    const style = element.getAttribute("style") || "";
    const rounded = /border-radius\s*:/.test(style);
    const colored = /background\s*:/.test(style);
    const outlined = /outline\s*:|border\s*:/.test(style);
    const sized = /padding-(left|right|top|bottom)\s*:|height\s*:/.test(style);
    return rounded && sized && (colored || outlined);
  }

  function findClickSurface(textElement, allowPlainText) {
    let element = textElement;
    for (let depth = 0; depth < 4 && element; depth += 1) {
      if (hasButtonShape(element)) {
        return element;
      }
      element = element.parentElement;
    }
    return allowPlainText ? textElement : null;
  }

  function scrollToLabel(label) {
    const target = exactTextElements(label)[0];
    if (!target) {
      return;
    }
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function follow(destination) {
    if (destination.startsWith("scroll:")) {
      scrollToLabel(destination.slice(7));
      return;
    }
    window.location.href = destination;
  }

  function makeClickable(element, destination, label) {
    if (!element || element.dataset.navigationReady === "true") {
      return;
    }

    element.dataset.navigationReady = "true";
    element.dataset.navigationTarget = destination;
    element.style.cursor = "pointer";
    element.setAttribute("title", label);

    if (element.tagName === "A" && !destination.startsWith("scroll:")) {
      element.setAttribute("href", destination);
      return;
    }

    element.setAttribute("role", "link");
    if (!element.hasAttribute("tabindex")) {
      element.setAttribute("tabindex", "0");
    }

    element.addEventListener("click", function (event) {
      event.preventDefault();
      follow(destination);
    });
    element.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        follow(destination);
      }
    });
  }

  function connectFirst(label, destination, allowPlainText = true) {
    const textElement = exactTextElements(label)[0];
    const surface = textElement && findClickSurface(textElement, allowPlainText);
    makeClickable(surface, destination, label);
  }

  function connectAll(label, destinations, allowPlainText = false) {
    const destinationList = Array.isArray(destinations) ? destinations : [destinations];
    exactTextElements(label).forEach(function (textElement, index) {
      const destination = destinationList[Math.min(index, destinationList.length - 1)];
      const surface = findClickSurface(textElement, allowPlainText);
      makeClickable(surface, destination, label);
    });
  }

  const publicPages = new Set(["index.html", "login.html", "signup.html", "signup_terms.html"]);
  connectFirst("EVIDENCE", publicPages.has(currentPage) ? "index.html" : "index_home.html", true);

  if (!publicPages.has(currentPage)) {
    connectFirst("대시보드", "Dashboard.html", true);
    connectFirst("증거 관리", "evidence.html", true);
    connectFirst("검증", "verification.html", true);
    connectFirst("보고서", "report.html", true);
    connectFirst("My page", "mypage.html", true);
    connectFirst("👤 소연", "mypage.html", true);
  }

  const pageConnections = {
    "index.html": [
      ["로그인", "login.html", true],
      ["증거 수집 시작하기", "login.html", false],
      ["EVIDENCE 시작하기", "login.html", false],
      ["기능 소개", "scroll:증거 관리의 기본을, 더 명확하게.", true],
      ["이용 안내", "scroll:수집에서 검증까지, 하나의 흐름으로.", true],
      ["이용 안내 보기", "scroll:수집에서 검증까지, 하나의 흐름으로.", false]
    ],
    "index_home.html": [
      ["증거 등록하기 →", "chose_evidence.html", true],
      ["URL 수집하기 →", "evidence_url.html", true],
      ["검증하기 →", "verification.html", true],
      ["보고서 보기 →", "report.html", true]
    ],
    "dashboard.html": [
      ["증거 등록", "chose_evidence.html", false],
      ["전체 보기", "evidence.html", true]
    ],
    "chose_evidence.html": [
      ["파일 등록 →", "evidence_file.html", true],
      ["이미지 등록 →", "evidence_img.html", true],
      ["URL 수집 →", "evidence_url.html", true],
      ["미디어 등록 →", "evidence_video.html", true]
    ],
    "evidence.html": [
      ["증거 등록", "chose_evidence.html", false],
      ["선택 항목 검증", "verification.html", true],
      ["상세보기 →", ["evidence_url.html", "evidence_img.html", "evidence_file.html", "evidence_img.html", "evidence_url.html"], true]
    ],
    "evidence_file.html": [
      ["취소", "chose_evidence.html", false]
    ],
    "evidence_img.html": [
      ["취소", "chose_evidence.html", false]
    ],
    "evidence_url.html": [
      ["취소", "chose_evidence.html", false]
    ],
    "evidence_video.html": [
      ["취소", "chose_evidence.html", false]
    ],
    "evidence_video_complete.html": [
      ["증거 관리로 이동", "evidence.html", true],
      ["증거 상세보기", "evidence.html", true]
    ],
  "evidence_file_complete.html": [
    ["증거 관리로 이동", "evidence.html", true],
    ["증거 상세보기", "evidence.html", true]
  ],
  "evidence_img_complete.html": [
    ["증거 관리로 이동", "evidence.html", true],
    ["증거 상세보기", "evidence.html", true]
  ],
  "evidence_url_complete.html": [
    ["증거 관리로 이동", "evidence.html", true],
    ["증거 상세보기", "evidence.html", true]
  ],
    "verification.html": [
      ["증거 상세보기", "evidence.html", true],
      ["보고서 생성", "report_select.html", false]
    ],
    "mypage.html": [
      ["프로필 수정", "profile_edit.html", false],
      ["로그인 기록 확인", "scroll:계정 및 보안", false],
      ["상세보기 →", ["report.html", "verification.html", "evidence.html", "verification.html"], true],
      ["로그아웃", "logout_confirm.html", false]
    ]
  };

  (pageConnections[currentPage] || []).forEach(function (connection) {
    connectAll(connection[0], connection[1], connection[2]);
  });
})();
