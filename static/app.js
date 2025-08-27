(function () {
  const $ = (sel) => document.querySelector(sel);
  const urlsEl = $("#urls");
  const resultEl = $("#result");
  const runBtn = $("#runBtn");
  const runIcon = $("#runIcon");
  const clearBtn = $("#clearBtn");
  const copyBtn = $("#copyBtn");
  const preferAltEl = $("#prefer_alt");

  async function extract() {
    const urls = (urlsEl.value || "").trim();
    if (!urls) {
      alert("URLを入力してください（1行=1URL）");
      return;
    }
    runBtn.disabled = true;
    runIcon.innerHTML = '<span class="spinner"></span> 抽出中...';

    try {
      const res = await fetch("/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          urls,
          prefer_alt: !!preferAltEl.checked
        }),
      });
      const text = await res.text();
      resultEl.value = text;
      resultEl.scrollTop = 0;
    } catch (e) {
      alert("抽出に失敗しました: " + e.message);
    } finally {
      runBtn.disabled = false;
      runIcon.textContent = "抽出する";
    }
  }

  async function copyText() {
    const text = resultEl.value || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      copyBtn.textContent = "コピーしました";
      setTimeout(() => (copyBtn.textContent = "コピー"), 1200);
    } catch (e) {
      // Fallback
      resultEl.select();
      document.execCommand("copy");
      copyBtn.textContent = "コピーしました";
      setTimeout(() => (copyBtn.textContent = "コピー"), 1200);
    }
  }

  runBtn.addEventListener("click", extract);
  clearBtn.addEventListener("click", () => {
    urlsEl.value = "";
    resultEl.value = "";
  });
  copyBtn.addEventListener("click", copyText);
})();
