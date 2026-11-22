(function () {
  "use strict";

  function setup(fields, actions) {
    const card = document.querySelector("[data-media-card]");
    if (!card) return;
    const empty = document.querySelector("[data-media-empty]");
    const video = document.querySelector("[data-media-video]");
    const audio = document.querySelector("[data-media-audio]");
    const filename = document.querySelector("[data-media-filename]");
    const summary = document.querySelector("[data-media-summary]");
    const badge = document.querySelector("[data-media-badge]");
    const message = document.querySelector("[data-media-error]");
    const submit = document.querySelector("[data-media-submit]");
    const metadata = Object.fromEntries(Array.from(document.querySelectorAll("[data-media-meta]"), node => [node.dataset.mediaMeta, node]));
    let previewUrl = null;
    let version = 0;
    let ready = false;
    let cancelLoading = null;
    let removePlaybackError = null;

    function state(value) { window.EvidenceRegistration?.setState(value); }

    function releasePreview() {
      if (cancelLoading) cancelLoading();
      cancelLoading = null;
      if (removePlaybackError) removePlaybackError();
      removePlaybackError = null;
      for (const player of [video, audio]) {
        player.pause();
        player.hidden = true;
        player.removeAttribute("src");
        player.load();
      }
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }

    function showMessage(text) {
      message.textContent = text;
      message.hidden = false;
    }

    function durationLabel(seconds) {
      if (!Number.isFinite(seconds) || seconds < 0) return "브라우저에서 확인할 수 없음";
      const value = Math.floor(seconds);
      const parts = [Math.floor(value / 60), value % 60];
      if (value >= 3600) parts.unshift(Math.floor(parts[0] / 60));
      if (parts.length === 3) parts[1] %= 60;
      return parts.map(n => String(n).padStart(2, "0")).join(":");
    }

    function sizeLabel(bytes) {
      if (bytes < 1024) return `${bytes} B`;
      const divisor = bytes < 1048576 ? 1024 : 1048576;
      return `${(bytes / divisor).toLocaleString("ko-KR", { maximumFractionDigits: 2 })} ${divisor === 1024 ? "KB" : "MB"}`;
    }

    function loadMetadata(player, url) {
      return new Promise(resolve => {
        const finish = result => {
          clearTimeout(timer);
          player.removeEventListener("loadedmetadata", onLoad);
          player.removeEventListener("error", onError);
          cancelLoading = null;
          resolve(result);
        };
        const onLoad = () => finish(true);
        const onError = () => finish(false);
        const timer = setTimeout(onError, 10000);
        cancelLoading = () => finish(null);
        player.addEventListener("loadedmetadata", onLoad, { once: true });
        player.addEventListener("error", onError, { once: true });
        player.src = url;
        player.load();
      });
    }

    fields.canRegister = () => ready;
    fields.validateUpload = file => {
      message.hidden = true;
      let error = "";
      if (!/\.(mp4|mov|webm|mp3|wav|m4a)$/i.test(file.name) ||
          (file.type && !/^(audio\/|video\/|application\/octet-stream$)/i.test(file.type))) {
        error = "MP4, MOV, WEBM 영상 또는 MP3, WAV, M4A 음성 파일을 선택해 주세요.";
      } else if (!file.size || file.size > 50 * 1024 * 1024) {
        error = "크기가 0보다 크고 50 MB 이하인 파일을 선택해 주세요.";
      }
      if (!error) return true;
      showMessage(error);
      actions.toast(error);
      return false;
    };

    fields.updateUpload = async file => {
      const current = ++version;
      const selection = actions.getSelection();
      const isCurrent = () => current === version && actions.getSelection() === selection;
      ready = false;
      submit.disabled = true;
      message.hidden = true;
      releasePreview();
      card.hidden = !file;
      empty.hidden = Boolean(file);
      filename.textContent = file ? file.name : "";
      summary.textContent = "";
      badge.textContent = file ? "파일 확인 중…" : "파일 선택 대기";
      for (const node of Object.values(metadata)) {
        node.textContent = "파일 선택 후 표시";
        node.removeAttribute("title");
      }
      state(file ? "loading" : "empty");
      if (!file) return;

      const extension = /\.([^.]+)$/.exec(file.name)[1].toUpperCase();
      const isAudio = /^audio\//i.test(file.type) || /^(MP3|WAV|M4A)$/.test(extension);
      const player = isAudio ? audio : video;
      metadata.filename.textContent = file.name;
      metadata.type.textContent = `${isAudio ? "음성" : "영상"} · ${extension}`;
      metadata.type.title = file.type || "브라우저가 제공한 MIME 유형 없음";
      metadata.size.textContent = `${sizeLabel(file.size)} (${file.size.toLocaleString("ko-KR")} bytes)`;
      metadata.duration.textContent = "확인 중…";
      metadata.resolution.textContent = isAudio ? "해당 없음 · 음성 파일" : "확인 중…";
      metadata.time.textContent = new Intl.DateTimeFormat("ko-KR", {
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Seoul", timeZoneName: "short"
      }).format(new Date(selection.selectedAt));
      metadata.hash.textContent = "계산 중…";

      // Hash and metadata reads are independent, and both ignore an older selection.
      const hashReady = (async () => {
        try {
          const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
          if (isCurrent()) metadata.hash.textContent = Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, "0")).join("");
        } catch (_) {
          if (isCurrent()) metadata.hash.textContent = "계산하지 못했습니다. 등록할 때 다시 계산합니다.";
        }
      })();

      function previewUnavailable() {
        if (!isCurrent()) return;
        player.pause();
        player.hidden = true;
        metadata.duration.textContent = "브라우저에서 확인할 수 없음";
        if (!isAudio) metadata.resolution.textContent = "브라우저에서 확인할 수 없음";
        badge.textContent = "파일 선택 완료 · 미리보기 지원 안 됨";
        summary.textContent = `${sizeLabel(file.size)} · ${extension}`;
        showMessage("이 브라우저에서 미리보기를 재생할 수 없습니다. 파일 형식을 확인하거나 다른 브라우저에서 열어 주세요. 원본 파일은 그대로 등록할 수 있습니다.");
      }

      try {
        previewUrl = URL.createObjectURL(file);
        const loaded = await loadMetadata(player, previewUrl);
        if (!isCurrent()) return;
        if (loaded) {
          metadata.duration.textContent = durationLabel(player.duration);
          if (!isAudio) metadata.resolution.textContent = player.videoWidth && player.videoHeight
            ? `${player.videoWidth} × ${player.videoHeight}` : "브라우저에서 확인할 수 없음";
          summary.textContent = `${sizeLabel(file.size)} · ${extension} · ${durationLabel(player.duration)}`;
          player.hidden = false;
          badge.textContent = "✓ 파일 선택 완료 · 등록 대기";
          player.addEventListener("error", previewUnavailable);
          removePlaybackError = () => player.removeEventListener("error", previewUnavailable);
        } else {
          previewUnavailable();
        }
      } catch (_) {
        if (!isCurrent()) return;
        previewUnavailable();
      }
      ready = true;
      submit.disabled = false;
      state("ready");
      await hashReady;
    };

    if (fields.name) {
      fields.name.value = "";
      fields.name.placeholder = "파일을 선택하면 이름이 표시됩니다";
    }
    try { sessionStorage.removeItem("evidence-file:evidence_video.html"); } catch (_) { /* Current File bytes stay local. */ }
    fields.updateUpload(null);
    window.addEventListener("pagehide", event => { if (!event.persisted) releasePreview(); });
  }

  window.EvidenceMediaUpload = { setup };
})();
