let activeRunDir = "";
let activeJobId = "";
let selectedFiles = [];

const $ = (id) => document.getElementById(id);
const viewTitles = {
  dashboard: "Dashboard",
  generate: "AI 用例生成",
  runs: "执行记录",
  reports: "测试报告",
  settings: "设置",
};

function showView(name) {
  const target = name || "dashboard";
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.id === "view" + target[0].toUpperCase() + target.slice(1));
  }
  for (const item of document.querySelectorAll(".nav-item")) {
    item.classList.toggle("active", item.dataset.view === target);
  }
  $("crumbCurrent").textContent = viewTitles[target] || target;
}

function setStatus(status, text) {
  $("statusDot").className = "dot " + (status || "");
  $("statusText").textContent = text;
}

function getProjectRoot() {
  return $("projectRootInput").value.trim();
}

function getOutputDir() {
  return $("outputDirInput").value.trim();
}

function loadSettings() {
  const savedProjectRoot = window.localStorage.getItem("autoTestFlowProjectRoot");
  if (savedProjectRoot) {
    $("projectRootInput").value = savedProjectRoot;
  }
  const savedOutputDir = window.localStorage.getItem("autoTestFlowOutputDir");
  if (savedOutputDir) {
    $("outputDirInput").value = savedOutputDir;
  }
}

async function saveSettings() {
  window.localStorage.setItem("autoTestFlowProjectRoot", getProjectRoot());
  window.localStorage.setItem("autoTestFlowOutputDir", getOutputDir());
  await postJson("/api/settings", {
    project_root: getProjectRoot(),
    output_dir: getOutputDir(),
  });
}

function setLog(lines) {
  const log = $("log");
  const scrollTop = log.scrollTop;
  log.textContent = (lines || []).join("\n");
  log.scrollTop = scrollTop;
}

function scrollLogToBottom() {
  $("log").scrollTop = $("log").scrollHeight;
}

function setMainPreview(url) {
  $("preview").src = url || "";
  document.querySelector(".preview-section").classList.toggle("has-preview", Boolean(url));
}

function setRunPreview(run) {
  activeRunDir = run.path;
  $("runPreview").src = run.url || "";
  document.querySelector(".record-preview-section").classList.toggle("has-preview", Boolean(run.url));
  setStatus("", "已选择 " + run.name);
}

function reviewLabel(run) {
  const decision = run.review_decision || "";
  if (decision === "blocked") return "待审查";
  if (decision === "needs_attention") return "需关注";
  if (decision === "pass") return "已通过";
  return "";
}

function reviewClass(run) {
  const decision = run.review_decision || "";
  if (decision === "blocked") return "blocked";
  if (decision === "needs_attention") return "attention";
  if (decision === "pass") return "pass";
  return "";
}

function toggleWorkSidebar(forceOpen) {
  const grid = $("workGrid");
  const shouldCollapse = forceOpen === true ? false : !grid.classList.contains("sidebar-collapsed");
  grid.classList.toggle("sidebar-collapsed", shouldCollapse);
  $("toggleWorkSidebarBtn").textContent = shouldCollapse ? "展开" : "收起";
}

function toggleLogExpand() {
  const view = $("viewGenerate");
  const expanded = !view.classList.contains("log-expanded");
  view.classList.toggle("log-expanded", expanded);
  $("toggleLogExpandBtn").textContent = expanded ? "还原日志" : "展开日志";
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function pollJob(jobId) {
  activeJobId = jobId;
  const response = await fetch("/api/jobs/" + encodeURIComponent(jobId));
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  const job = data.job;
  setStatus(job.status, job.kind + " · " + job.status);
  setLog(job.logs);
  setFlowInputEnabled(job.status === "running" && job.interactive);
  if (job.run_dir) activeRunDir = job.run_dir;
  if (job.run_url) setMainPreview(job.run_url);
  if (job.status === "running") {
    window.setTimeout(() => pollJob(jobId).catch(showError), 1200);
  } else {
    loadRuns();
  }
}

function showError(error) {
  setStatus("failed", error.message || String(error));
  showView("generate");
}

function setFlowInputEnabled(enabled) {
  for (const id of ["sendYesBtn", "sendEditBtn", "sendNoBtn", "jobInput", "sendInputBtn"]) {
    $(id).disabled = !enabled;
  }
}

async function sendJobInput(text) {
  if (!activeJobId) {
    setStatus("failed", "当前没有正在运行的流程");
    return;
  }
  const value = text || $("jobInput").value;
  if (!value.trim()) {
    setStatus("failed", "请输入要发送给流程的内容");
    return;
  }
  const data = await postJson(`/api/jobs/${encodeURIComponent(activeJobId)}/input`, {
    input: value,
  });
  $("jobInput").value = "";
  setLog(data.job.logs);
}

function renderFiles() {
  const root = $("fileList");
  root.innerHTML = "";
  for (const file of selectedFiles) {
    const item = document.createElement("div");
    item.className = "file-item";
    item.innerHTML = `<strong>${file.name}</strong><span>${Math.ceil(file.size / 1024)} KB · ${file.type || "file"}</span>`;
    root.appendChild(item);
  }
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function collectAttachments() {
  const result = [];
  for (const file of selectedFiles) {
    result.push({
      name: file.name,
      type: file.type,
      size: file.size,
      data: await readFileAsDataUrl(file),
    });
  }
  return result;
}

async function generate() {
  showView("generate");
  setStatus("running", "生成中");
  setLog([]);
  activeJobId = "";
  setFlowInputEnabled(false);
  const attachments = await collectAttachments();
  const data = await postJson("/api/generate", {
    requirement: $("requirement").value,
    attachments,
    project_root: getProjectRoot(),
    output_dir: getOutputDir(),
    review_policy: $("reviewPolicy").value,
    full_artifacts: $("fullArtifacts").checked,
  });
  pollJob(data.job.id).catch(showError);
}

async function runCodex() {
  if (!activeRunDir) {
    setStatus("failed", "请先选择或生成一个产物目录");
    showView("generate");
    return;
  }
  showView("generate");
  setStatus("running", "Codex 执行中");
  setLog([]);
  activeJobId = "";
  setFlowInputEnabled(false);
  const data = await postJson("/api/codex", {
    run_dir: activeRunDir,
    project_root: getProjectRoot(),
    output_dir: getOutputDir(),
    allow_edits: $("allowEdits").checked,
    approval_policy: $("approvalPolicy").value,
    extra_instruction: $("extraInstruction").value,
  });
  pollJob(data.job.id).catch(showError);
}

async function chooseProjectRoot() {
  const data = await postJson("/api/select-directory", {
    initial_dir: getProjectRoot(),
    title: "选择项目根目录",
  });
  if (!data.path) {
    return;
  }
  $("projectRootInput").value = data.path;
  await saveSettings();
}

async function chooseOutputDir() {
  const data = await postJson("/api/select-directory", {
    initial_dir: getOutputDir(),
    title: "选择输出目录",
  });
  if (!data.path) {
    return;
  }
  $("outputDirInput").value = data.path;
  await saveSettings();
  await loadRuns();
}

async function deleteRun(run) {
  if (!window.confirm(`确认删除执行记录「${run.name}」吗？`)) {
    return;
  }
  await postJson("/api/runs/delete", {
    run_dir: run.path,
  });
  if (activeRunDir === run.path) {
    activeRunDir = "";
    $("runPreview").src = "";
    document.querySelector(".record-preview-section").classList.remove("has-preview");
  }
  await loadRuns();
}

function renderRunItem(run, openHandler, allowDelete) {
  const item = document.createElement("div");
  item.className = "run-item";

  const info = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = run.name;
  const meta = document.createElement("span");
  meta.textContent = run.modified_at;
  info.appendChild(title);
  info.appendChild(meta);
  const label = reviewLabel(run);
  if (label) {
    const badge = document.createElement("em");
    badge.className = "run-badge " + reviewClass(run);
    badge.textContent = label;
    info.appendChild(badge);
  }

  const actions = document.createElement("div");
  actions.className = "run-actions";
  const openButton = document.createElement("button");
  openButton.className = "secondary";
  openButton.textContent = "打开";
  openButton.onclick = openHandler;
  actions.appendChild(openButton);
  if (allowDelete) {
    const deleteButton = document.createElement("button");
    deleteButton.className = "secondary danger-lite";
    deleteButton.textContent = "删除";
    deleteButton.onclick = () => deleteRun(run).catch(showError);
    actions.appendChild(deleteButton);
  }

  item.appendChild(info);
  item.appendChild(actions);
  return item;
}

async function loadRuns() {
  const response = await fetch("/api/runs");
  const data = await response.json();
  const runs = data.runs || [];
  const root = $("runs");
  root.innerHTML = "";
  renderDashboard(runs);
  for (const run of runs) {
    root.appendChild(renderRunItem(run, () => setRunPreview(run), true));
  }
  if (!runs.length) {
    root.innerHTML = `<div class="empty-state">暂无执行记录</div>`;
  }
}

function renderDashboard(runs) {
  const total = runs.length;
  const hasArtifacts = runs.filter((run) => run.url).length;
  const pending = Math.max(total - hasArtifacts, 0);
  const rate = total ? Math.round((hasArtifacts / total) * 100) + "%" : "0%";
  $("statTotal").textContent = total;
  $("statSuccess").textContent = hasArtifacts;
  $("statFailed").textContent = pending;
  $("statLatest").textContent = runs[0] ? runs[0].modified_at.split(" ")[0] : "-";
  $("metricTotal").textContent = total;
  $("metricSuccess").textContent = hasArtifacts;
  $("metricPending").textContent = pending;
  $("metricRate").textContent = rate;

  const recent = $("recentRuns");
  recent.innerHTML = "";
  recent.classList.toggle("empty-state", !runs.length);
  if (!runs.length) {
    recent.textContent = "暂无执行记录";
    return;
  }
  for (const run of runs.slice(0, 6)) {
    const item = renderRunItem(run, () => {
      setRunPreview(run);
      showView("runs");
    }, false);
    recent.appendChild(item);
  }
}

$("generateBtn").addEventListener("click", () => generate().catch(showError));
$("codexBtn").addEventListener("click", () => runCodex().catch(showError));
$("refreshRunsBtn").addEventListener("click", () => loadRuns().catch(showError));
$("dashboardRefreshBtn").addEventListener("click", () => loadRuns().catch(showError));
$("scrollLogBottomBtn").addEventListener("click", scrollLogToBottom);
$("toggleWorkSidebarBtn").addEventListener("click", () => toggleWorkSidebar());
$("toggleLogExpandBtn").addEventListener("click", toggleLogExpand);
$("chooseProjectRootBtn").addEventListener("click", () => chooseProjectRoot().catch(showError));
$("chooseOutputDirBtn").addEventListener("click", () => chooseOutputDir().catch(showError));
$("saveSettingsBtn").addEventListener("click", () => saveSettings().then(loadRuns).catch(showError));
$("sendYesBtn").addEventListener("click", () => sendJobInput("yes").catch(showError));
$("sendEditBtn").addEventListener("click", () => sendJobInput("edit").catch(showError));
$("sendNoBtn").addEventListener("click", () => sendJobInput("no").catch(showError));
$("sendInputBtn").addEventListener("click", () => sendJobInput("").catch(showError));
$("jobInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    sendJobInput("").catch(showError);
  }
});
$("attachments").addEventListener("change", (event) => {
  selectedFiles = Array.from(event.target.files || []);
  renderFiles();
});
for (const item of document.querySelectorAll("[data-view]")) {
  item.addEventListener("click", () => showView(item.dataset.view));
}
for (const item of document.querySelectorAll("[data-view-target]")) {
  item.addEventListener("click", () => showView(item.dataset.viewTarget));
}
for (const item of document.querySelectorAll("[data-sidebar-focus]")) {
  item.addEventListener("click", () => {
    toggleWorkSidebar(true);
    document.getElementById(item.dataset.sidebarFocus + "Block").scrollIntoView({ block: "start" });
  });
}
setFlowInputEnabled(false);
loadSettings();
saveSettings().then(loadRuns).catch(showError);
