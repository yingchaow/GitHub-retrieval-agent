const state = {
  selectedRepoId: "",
  selectedRepoName: "",
  busy: false,
};

const els = {
  workspace: document.querySelector("#workspace"),
  toggleSidebar: document.querySelector("#toggleSidebar"),
  keyword: document.querySelector("#keyword"),
  minStars: document.querySelector("#minStars"),
  searchButton: document.querySelector("#searchButton"),
  searchResults: document.querySelector("#searchResults"),
  repoFilter: document.querySelector("#repoFilter"),
  repoList: document.querySelector("#repoList"),
  refreshRepos: document.querySelector("#refreshRepos"),
  selectedRepo: document.querySelector("#selectedRepo"),
  repoBadge: document.querySelector("#repoBadge"),
  modeBadge: document.querySelector("#modeBadge"),
  vectorBadge: document.querySelector("#vectorBadge"),
  askForm: document.querySelector("#askForm"),
  copyAllAnswers: document.querySelector("#copyAllAnswers"),
  chatMessages: document.querySelector("#chatMessages"),
  sourcesPanel: document.querySelector("#sourcesPanel"),
  toggleSources: document.querySelector("#toggleSources"),
  question: document.querySelector("#question"),
  askButton: document.querySelector("#askButton"),
  contexts: document.querySelector("#contexts"),
  toast: document.querySelector("#toast"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderMarkdown(value) {
  let html = escapeHtml(value);
  html = html.replace(/^### (.*)$/gm, "<h4>$1</h4>");
  html = html.replace(/^## (.*)$/gm, "<h3>$1</h3>");
  html = html.replace(/^# (.*)$/gm, "<h2>$1</h2>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/^\s*[-*]\s+(.+)$/gm, "<div class=\"md-list-item\">$1</div>");
  return html.replace(/\n/g, "<br>");
}

function githubSourceUrl(repoUrl, item) {
  if (!repoUrl || !item.path) return "";
  const encodedPath = item.path
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `${repoUrl}/blob/HEAD/${encodedPath}#L${item.start_line}-L${item.end_line}`;
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    els.toast.hidden = true;
  }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

async function copyText(text) {
  if (!text.trim()) {
    showToast("没有可复制的内容");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showToast("已复制");
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
    showToast("已复制");
  }
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = label;
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
}

let indexedRepos = [];
let answerMessages = [];

function scrollChatToBottom() {
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

function appendMessage(role, text = "") {
  const article = document.createElement("article");
  article.className = `message ${role === "user" ? "user-message" : "assistant-message"}`;
  article.innerHTML = `
    <div class="message-meta">
      <span>${role === "user" ? "You" : "Assistant"}</span>
      ${role === "assistant" ? '<button class="copy-message" type="button">复制</button>' : ""}
    </div>
    <div class="message-body"></div>
  `;
  const body = article.querySelector(".message-body");
  body.dataset.raw = text;
  body.innerHTML = renderMarkdown(text);
  if (role === "assistant") {
    answerMessages.push(body);
  }
  els.chatMessages.appendChild(article);
  scrollChatToBottom();
  return body;
}

function renderSearchResults(items) {
  if (!items.length) {
    els.searchResults.className = "result-list empty";
    els.searchResults.textContent = "没有筛出值得学习的目标项目，可以降低 Star 或换一个关键词";
    return;
  }
  els.searchResults.className = "result-list";
  els.searchResults.innerHTML = items
    .map(
      (item) => {
        const screen = item.screen || {};
        const paths = (screen.important_paths || []).slice(0, 5);
        const questions = (screen.suggested_questions || []).slice(0, 3);
        return `
        <article class="result-item">
          <div class="result-title">
            <a href="${escapeHtml(item.html_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.full_name)}</a>
            <div class="actions">
              <button class="small-action" data-ingest="${escapeHtml(item.full_name)}">索引</button>
            </div>
          </div>
          <p class="description">${escapeHtml(item.description || "暂无描述")}</p>
          <div class="meta">
            <span>${escapeHtml(item.language || "Unknown")}</span>
            <span>${Number(item.stars).toLocaleString()} stars</span>
            <span>${Number(item.forks).toLocaleString()} forks</span>
            <span>updated ${escapeHtml((item.updated_at || "").slice(0, 10))}</span>
          </div>
          <div class="screen-result">
            <div class="screen-card good">
              <div class="screen-top">
                <strong>目标项目</strong>
                <span>${Number(screen.score || 0)}/100 · ${escapeHtml(screen.method || "screen")}${screen.memory_hit ? " · memory" : ""}</span>
              </div>
              <ul>
                ${(screen.reasons || []).slice(0, 4).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}
              </ul>
              ${screen.risk ? `<p class="risk">${escapeHtml(screen.risk)}</p>` : ""}
              ${
                paths.length
                  ? `<div class="path-list">${paths.map((path) => `<code>${escapeHtml(path)}</code>`).join("")}</div>`
                  : ""
              }
              ${
                questions.length
                  ? `<div class="question-list">${questions.map((question) => `<button class="question-action" data-fill-question="${escapeHtml(question)}">${escapeHtml(question)}</button>`).join("")}</div>`
                  : ""
              }
            </div>
          </div>
        </article>
      `;
      },
    )
    .join("");
}

function renderRepos(items) {
  indexedRepos = items || indexedRepos;
  const filter = (els.repoFilter.value || "").trim().toLowerCase();
  const visibleItems = filter
    ? indexedRepos.filter((item) => `${item.full_name} ${item.repo_id}`.toLowerCase().includes(filter))
    : indexedRepos;
  if (!visibleItems.length) {
    els.repoList.className = "repo-list empty";
    els.repoList.textContent = indexedRepos.length ? "没有匹配仓库" : "暂无索引";
    return;
  }
  els.repoList.className = "repo-list";
  els.repoList.innerHTML = visibleItems
    .map(
      (item) => `
        <article class="repo-item">
          <div class="repo-title">
            <button data-select-repo="${escapeHtml(item.repo_id)}" data-repo-name="${escapeHtml(item.full_name)}">${escapeHtml(item.full_name)}</button>
          </div>
          <div class="meta">
            <span>${Number(item.chunk_count).toLocaleString()} chunks</span>
            <span>${new Date(item.created_at * 1000).toLocaleString()}</span>
          </div>
        </article>
      `,
    )
    .join("");
}

function selectRepo(repoId, repoName) {
  state.selectedRepoId = repoId;
  state.selectedRepoName = repoName;
  els.selectedRepo.textContent = repoName;
  els.repoBadge.textContent = repoName;
  showToast(`已选择 ${repoName}`);
}

async function loadRepos() {
  const data = await api("/api/repos");
  indexedRepos = data.items || [];
  renderRepos(indexedRepos);
  if (!state.selectedRepoId && data.items && data.items[0]) {
    selectRepo(data.items[0].repo_id, data.items[0].full_name);
  }
}

async function searchRepos() {
  const params = new URLSearchParams({
    query: els.keyword.value.trim(),
    min_stars: els.minStars.value || "0",
    limit: "3",
  });
  setBusy(els.searchButton, true, "搜索中");
  try {
    const data = await api(`/api/search?${params.toString()}`);
    renderSearchResults(data.items || []);
    const rewrite =
      data.rewrite_method === "raw"
        ? data.query
        : `${data.original_query} -> ${data.rewritten_query}`;
    const relaxed = data.query_attempts && data.query_attempts.length > 1 ? "，已自动放宽 star 限制" : "";
    const memory = data.memory_hit ? "，命中记忆库" : `，已初筛 ${data.screened_count || 0} 个候选`;
    showToast(`GitHub 查询：${rewrite}${relaxed}${memory}`);
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(els.searchButton, false);
  }
}

function renderScreenResult(fullName, result) {
  const box = Array.from(document.querySelectorAll("[data-screen-result]")).find(
    (item) => item.dataset.screenResult === fullName,
  );
  if (!box) return;
  const verdict = result.worth_indexing ? "值得索引" : "先别索引";
  const paths = (result.important_paths || []).slice(0, 6);
  const questions = (result.suggested_questions || []).slice(0, 3);
  box.innerHTML = `
    <div class="screen-card ${result.worth_indexing ? "good" : "warn"}">
      <div class="screen-top">
        <strong>${escapeHtml(verdict)}</strong>
        <span>${Number(result.score || 0)}/100 · ${escapeHtml(result.method || "heuristic")}</span>
      </div>
      <ul>
        ${(result.reasons || []).slice(0, 4).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
      ${result.risk ? `<p class="risk">${escapeHtml(result.risk)}</p>` : ""}
      ${
        paths.length
          ? `<div class="path-list">${paths.map((path) => `<code>${escapeHtml(path)}</code>`).join("")}</div>`
          : ""
      }
      ${
        questions.length
          ? `<div class="question-list">${questions.map((item) => `<button class="question-action" data-fill-question="${escapeHtml(item)}">${escapeHtml(item)}</button>`).join("")}</div>`
          : ""
      }
    </div>
  `;
}

async function screenRepo(fullName, button) {
  setBusy(button, true, "初筛中");
  try {
    const data = await api("/api/screen", {
      method: "POST",
      body: JSON.stringify({
        full_name: fullName,
        goal: els.keyword.value.trim(),
      }),
    });
    renderScreenResult(fullName, data);
    showToast(`${fullName} 初筛：${data.worth_indexing ? "值得索引" : "先别索引"}，${data.score}/100`);
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function ingestRepo(fullName, button) {
  setBusy(button, true, "索引中");
  showToast("正在下载并索引仓库，稍等一下");
  try {
    const data = await api("/api/ingest", {
      method: "POST",
      body: JSON.stringify({ full_name: fullName }),
    });
    await loadRepos();
    selectRepo(data.repo_id, data.full_name);
    const vector = data.vector && data.vector.enabled ? `，已同步 ${data.vector.backend}` : "，本地知识库";
    const warning = data.warnings && data.warnings.length ? `；${data.warnings[0]}` : "";
    showToast(`索引完成：${data.file_count} 个文件，${data.chunk_count} 个片段${vector}${warning}`);
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(button, false);
  }
}

function renderContexts(contexts, repoUrl = "") {
  if (!contexts.length) {
    els.contexts.className = "contexts empty";
    els.contexts.textContent = "暂无引用";
    return;
  }
  els.contexts.className = "contexts";
  els.contexts.innerHTML = contexts
    .map(
      (item, index) => `
        <article class="context-item">
          <strong>[${index + 1}] ${escapeHtml(item.path)}:${item.start_line}-${item.end_line}</strong>
          ${
            githubSourceUrl(repoUrl, item)
              ? `<a class="source-link" href="${githubSourceUrl(repoUrl, item)}" target="_blank" rel="noreferrer">GitHub</a>`
              : ""
          }
          <div class="meta"><span>score ${item.score}</span></div>
          <pre><code>${escapeHtml(item.content)}</code></pre>
        </article>
      `,
    )
    .join("");
  if (contexts.length) {
    els.toggleSources.textContent = `引用 ${contexts.length}`;
  } else {
    els.toggleSources.textContent = "引用";
  }
}

async function askQuestion() {
  if (!state.selectedRepoId) {
    showToast("请先选择一个已索引仓库");
    return;
  }
  const question = els.question.value.trim();
  if (!question) {
    showToast("请输入问题");
    return;
  }
  setBusy(els.askButton, true, "回答中");
  appendMessage("user", question);
  const assistantBody = appendMessage("assistant", "正在检索相关代码片段...");
  els.question.value = "";
  try {
    const response = await fetch("/api/ask_stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_id: state.selectedRepoId, question }),
    });
    if (!response.ok || !response.body) {
      throw new Error("流式请求失败");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let started = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const lines = part.split("\n");
        const event = (lines.find((line) => line.startsWith("event:")) || "event: message")
          .replace("event:", "")
          .trim();
        const dataLine = lines.find((line) => line.startsWith("data:"));
        if (!dataLine) continue;
        const payload = JSON.parse(dataLine.replace("data:", "").trim());
        if (event === "meta") {
          els.modeBadge.textContent = payload.mode === "llm" ? "LLM 流式" : "本地抽取";
          els.vectorBadge.textContent = payload.retrieval_backend === "qdrant" ? "Qdrant 检索" : "本地知识库";
          renderContexts(payload.contexts || [], payload.repo_url);
          if (payload.warnings && payload.warnings.length) showToast(payload.warnings[0]);
        }
        if (event === "delta") {
          if (!started) {
            assistantBody.dataset.raw = "";
            assistantBody.innerHTML = "";
            started = true;
          }
          assistantBody.dataset.raw += payload.text || "";
          assistantBody.innerHTML = renderMarkdown(assistantBody.dataset.raw);
        }
        if (event === "error") {
          throw new Error(payload.error || "回答失败");
        }
      }
    }
  } catch (error) {
    assistantBody.dataset.raw = `回答失败：${error.message}`;
    assistantBody.innerHTML = renderMarkdown(assistantBody.dataset.raw);
    showToast(error.message);
  } finally {
    setBusy(els.askButton, false);
  }
}

document.addEventListener("click", (event) => {
  const copyMessage = event.target.closest(".copy-message");
  if (copyMessage) {
    const body = copyMessage.closest(".message")?.querySelector(".message-body");
    if (body) copyText(body.dataset.raw || body.textContent || "");
    return;
  }

  const fold = event.target.closest("[data-fold-target]");
  if (fold) {
    const section = document.querySelector(`#${fold.dataset.foldTarget}`)?.closest(".fold-section");
    if (section) section.classList.toggle("collapsed");
    return;
  }

  const screen = event.target.closest("[data-screen]");
  if (screen) {
    screenRepo(screen.dataset.screen, screen);
    return;
  }

  const ingest = event.target.closest("[data-ingest]");
  if (ingest) {
    ingestRepo(ingest.dataset.ingest, ingest);
    return;
  }

  const select = event.target.closest("[data-select-repo]");
  if (select) {
    selectRepo(select.dataset.selectRepo, select.dataset.repoName);
    return;
  }

  const fillQuestion = event.target.closest("[data-fill-question]");
  if (fillQuestion) {
    els.question.value = fillQuestion.dataset.fillQuestion;
  }
});

els.searchButton.addEventListener("click", searchRepos);
els.copyAllAnswers.addEventListener("click", () => {
  const text = answerMessages
    .map((body) => body.dataset.raw || body.textContent || "")
    .filter(Boolean)
    .join("\n\n---\n\n");
  copyText(text);
});
els.askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  askQuestion();
});
els.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    askQuestion();
  }
});
els.toggleSidebar.addEventListener("click", () => {
  els.workspace.classList.toggle("sidebar-collapsed");
});
els.toggleSources.addEventListener("click", () => {
  els.sourcesPanel.classList.toggle("collapsed");
});
els.repoFilter.addEventListener("input", () => {
  renderRepos(indexedRepos);
});
els.refreshRepos.addEventListener("click", () => {
  loadRepos().catch((error) => showToast(error.message));
});

async function loadStatus() {
  const data = await api("/api/status");
  els.modeBadge.textContent = data.llm ? "LLM 可用" : "本地抽取";
  els.vectorBadge.textContent = data.qdrant ? "Qdrant 已连接" : "本地知识库";
}

loadStatus().catch(() => {});
loadRepos().catch((error) => showToast(error.message));
