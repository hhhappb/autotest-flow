let activeRunDir = "";
let activeJobId = "";
let activePreviewUrl = "";
let activePreviewRun = null;
let activeExecuteRun = null;
let selectedFiles = [];

const $ = (id) => document.getElementById(id);
const fmtTime = (iso) => (iso || "").replace("T", " ");
const viewTitles = {
  dashboard: "Dashboard",
  generate: "AI 用例生成",
  execute: "执行工作台",
  runs: "执行记录",
  reports: "测试报告",
  settings: "设置",
};

function showView(name, updateHash = true) {
  const target = viewTitles[name] ? name : "dashboard";
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.id === "view" + target[0].toUpperCase() + target.slice(1));
  }
  for (const item of document.querySelectorAll(".nav-item")) {
    item.classList.toggle("active", item.dataset.view === target);
  }
  $("crumbCurrent").textContent = viewTitles[target] || target;
  if (updateHash && viewTitles[target]) {
    history.replaceState(null, "", "#" + target);
  }
}

function setStatus(status, text) {
  $("statusDot").className = "dot " + (status || "");
  $("statusText").textContent = text;
  $("executeStatusDot").className = "dot " + (status || "");
  $("executeStatusText").textContent = text;
}

function getProjectRoot() {
  return $("projectRootInput").value.trim();
}

function getOutputDir() {
  return $("outputDirInput").value.trim();
}

function getDeepSeekModel() {
  return $("deepseekModel").value;
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
  const savedModel = window.localStorage.getItem("autoTestFlowDeepSeekModel");
  if (savedModel) {
    $("deepseekModel").value = savedModel;
  }
  setSidebarCollapsed(window.localStorage.getItem("autoTestFlowSidebarCollapsed") === "true");
}

async function saveSettings() {
  window.localStorage.setItem("autoTestFlowProjectRoot", getProjectRoot());
  window.localStorage.setItem("autoTestFlowOutputDir", getOutputDir());
  window.localStorage.setItem("autoTestFlowDeepSeekModel", getDeepSeekModel());
  await postJson("/api/settings", {
    project_root: getProjectRoot(),
    output_dir: getOutputDir(),
    model: getDeepSeekModel(),
  });
}

function setLog(lines) {
  const log = $("log");
  const scrollTop = log.scrollTop;
  log.textContent = (lines || []).join("\n");
  log.scrollTop = scrollTop;
  const executeLog = $("executeLog");
  const executeScrollTop = executeLog.scrollTop;
  executeLog.textContent = (lines || []).join("\n");
  executeLog.scrollTop = executeScrollTop;
}

function scrollLogToBottom() {
  $("log").scrollTop = $("log").scrollHeight;
}

function scrollExecuteLogToBottom() {
  $("executeLog").scrollTop = $("executeLog").scrollHeight;
}

function setSidebarCollapsed(collapsed) {
  document.querySelector(".app-shell").classList.toggle("sidebar-collapsed", collapsed);
  window.localStorage.setItem("autoTestFlowSidebarCollapsed", collapsed ? "true" : "false");
  $("sidebarToggleBtn").textContent = collapsed ? "展开" : "收起";
  $("sidebarToggleBtn").title = collapsed ? "展开侧边栏" : "收起侧边栏";
}

function toggleSidebar() {
  setSidebarCollapsed(!document.querySelector(".app-shell").classList.contains("sidebar-collapsed"));
}

function showExecuteTab(name) {
  const target = name || "summary";
  for (const tab of document.querySelectorAll(".result-tab")) {
    tab.classList.toggle("active", tab.dataset.executeTab === target);
  }
  for (const panel of document.querySelectorAll(".execute-tab-panel")) {
    panel.classList.toggle("active", panel.id === "execute" + target[0].toUpperCase() + target.slice(1) + "Panel");
  }
}

function setReviewFocus(enabled) {
  const view = $("viewGenerate");
  view.classList.toggle("review-focused", enabled);
  if (enabled) {
    view.classList.remove("log-expanded");
    $("toggleLogExpandBtn").textContent = "展开摘要";
  }
  $("focusPreviewBtn").textContent = enabled ? "还原布局" : "放大预览";
}

function reviewUrl(run) {
  if (!run || !run.url) return "";
  return run.review_url || (run.url + "#doc-md-review-notes-md");
}

function openMainReview() {
  const url = reviewUrl(activePreviewRun);
  if (!url) return;
  $("preview").src = url;
  setReviewFocus(true);
}

function openMainArtifact() {
  if (!activePreviewUrl) return;
  $("preview").src = activePreviewUrl;
}

function setReviewStatus(elementId, run, frameId) {
  const element = $(elementId);
  const decision = (run && run.review_decision) || "";
  element.className = "review-status is-empty";
  element.innerHTML = "";
  element.onclick = null;
  if (!decision) return;

  const label = reviewLabel(run);
  const counts = run.review_counts || {};
  const high = counts.high || 0;
  const medium = counts.medium || 0;
  const low = counts.low || 0;
  const titleMap = {
    blocked: "审查阻塞：先处理交接审查",
    needs_attention: "审查需关注：建议查看交接审查",
    pass: "审查通过：可以进入 Codex 交接",
  };
  element.className = "review-status " + reviewClass(run);
  const title = document.createElement("strong");
  title.textContent = titleMap[decision] || label;
  const detail = document.createElement("span");
  detail.textContent = `${run.review_summary || ""} 高 ${high} / 中 ${medium} / 低 ${low}`;
  element.appendChild(title);
  element.appendChild(detail);
  element.onclick = () => {
    const url = reviewUrl(run);
    if (url) $(frameId).src = url;
    if (url && frameId === "preview") setReviewFocus(true);
  };
}

function setMainPreview(url, run) {
  activePreviewUrl = url || "";
  activePreviewRun = run || null;
  if (!url) setReviewFocus(false);
  $("preview").src = url || "";
  document.querySelector(".preview-section").classList.toggle("has-preview", Boolean(url));
  setReviewStatus("reviewStatus", run, "preview");
}

function setRunPreview(run) {
  activeRunDir = run.path;
  $("runPreview").src = run.url || "";
  document.querySelector(".record-preview-section").classList.toggle("has-preview", Boolean(run.url));
  setReviewStatus("runReviewStatus", run, "runPreview");
  const label = reviewLabel(run);
  setStatus("", label ? "已选择 " + run.name + " · " + label : "已选择 " + run.name);
}

function setExecutePreview(url) {
  $("executePreview").src = url || "";
  $("executeSummaryPanel").classList.toggle("has-preview", Boolean(url));
  showExecuteTab("summary");
}

function setExecuteAllure(url) {
  $("executeAllureFrame").src = url || "/allure/allure_report/index.html";
  showExecuteTab("allure");
}

function setExecuteRun(run) {
  activeRunDir = run.path;
  activeExecuteRun = run;
  renderExecuteDetail();
  setExecutePreview(run.codex_summary_url || reviewUrl(run) || run.url || "");
  const label = reviewLabel(run);
  setStatus("", label ? "已选择 " + run.name + " · " + label : "已选择 " + run.name);
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

function codexLabel(run) {
  const status = (run && run.codex_status) || "";
  if (status === "success") return "Codex 已完成";
  if (status === "failed") return "Codex 失败";
  if (status === "unknown") return "Codex 待确认";
  return "未执行 Codex";
}

function codexClass(run) {
  const status = (run && run.codex_status) || "";
  if (status === "success") return "pass";
  if (status === "failed") return "blocked";
  if (status === "unknown") return "attention";
  return "";
}

function renderExecuteDetail() {
  const run = activeExecuteRun;
  $("executeDetail").classList.toggle("is-hidden", !run);
  $("executeEmpty").classList.toggle("is-hidden", Boolean(run));
  $("executeRunName").textContent = run ? run.name : "未选择产物";
  if (!run) {
    $("executeReviewStatusText").textContent = "-";
    $("executeCodexStatusText").textContent = "-";
    $("executeExitCodeText").textContent = "-";
    return;
  }

  $("executeReviewStatusText").textContent = reviewLabel(run) || "未审查";
  $("executeCodexStatusText").textContent = codexLabel(run);
  $("executeExitCodeText").textContent = run.codex_exit_code === null || run.codex_exit_code === undefined
    ? "-"
    : String(run.codex_exit_code);
  $("executeStatusDot").className = "dot " + (codexClass(run) === "pass" ? "success" : codexClass(run) === "blocked" ? "failed" : "");
  $("executeStatusText").textContent = run.codex_updated_at
    ? `最近 Codex 执行：${fmtTime(run.codex_updated_at)}`
    : "等待执行";

  $("executeOpenReviewBtn").disabled = !reviewUrl(run);
}

function openExecuteReview() {
  const url = reviewUrl(activeExecuteRun);
  if (url) setExecutePreview(url);
}

function openExecuteSummary() {
  if (activeExecuteRun) {
    setExecutePreview(activeExecuteRun.codex_summary_url || reviewUrl(activeExecuteRun) || activeExecuteRun.url || "");
  }
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
  if (expanded) view.classList.remove("review-focused");
  view.classList.toggle("log-expanded", expanded);
  $("focusPreviewBtn").textContent = "放大预览";
  $("toggleLogExpandBtn").textContent = expanded ? "还原摘要" : "展开摘要";
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
  if (job.run_url) setMainPreview(job.run_url, {
    url: job.run_url,
    review_url: job.review_url,
    review_decision: job.review_decision,
    review_summary: job.review_summary,
    review_counts: job.review_counts,
  });
  if (job.status === "running") {
    window.setTimeout(() => pollJob(jobId).catch(showError), 800);
  } else {
    await loadRuns();
    if (job.kind === "test") {
      $("testStatusDot").className = "dot " + (job.status === "success" ? "success" : "failed");
      $("testStatusText").textContent = job.status === "success"
        ? (job.test_env === "all" ? "ALL 模式执行完成" : "测试全部通过")
        : "测试存在失败";
      if ($("viewExecute").classList.contains("active")) {
        setExecuteAllure(job.allure_report_url || "/allure/allure_report/index.html");
      }
    } else if ($("viewExecute").classList.contains("active") && activeExecuteRun && activeExecuteRun.codex_summary_url) {
      setExecutePreview(activeExecuteRun.codex_summary_url);
    }
  }
}

function showError(error) {
  setStatus("failed", error.message || String(error));
  if (!$("viewExecute").classList.contains("active")) {
    showView("generate");
  }
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
  setMainPreview("", null);
  activeJobId = "";
  setFlowInputEnabled(false);
  const attachments = await collectAttachments();
  const data = await postJson("/api/generate", {
    requirement: $("requirement").value,
    attachments,
    project_root: getProjectRoot(),
    output_dir: getOutputDir(),
    model: getDeepSeekModel(),
    review_policy: $("reviewPolicy").value,
    full_artifacts: $("fullArtifacts").checked,
  });
  pollJob(data.job.id).catch(showError);
}

async function runCodex(options = {}) {
  if (!activeRunDir) {
    setStatus("failed", "请先选择或生成一个产物目录");
    showView(options.view || "execute");
    return;
  }
  showView(options.view || "execute");
  setStatus("running", "Codex 执行中");
  setLog([]);
  showExecuteTab("log");
  activeJobId = "";
  setFlowInputEnabled(false);
  const approvalPolicyId = options.approvalPolicyId || "executeApprovalPolicy";
  const extraInstructionId = options.extraInstructionId || "executeExtraInstruction";
  const data = await postJson("/api/codex", {
    run_dir: activeRunDir,
    project_root: getProjectRoot(),
    output_dir: getOutputDir(),
    approval_policy: $(approvalPolicyId).value,
    extra_instruction: $(extraInstructionId).value,
  });
  pollJob(data.job.id).catch(showError);
}

async function runTest() {
  const testPath = $("testPath").value.trim();
  if (!testPath) {
    setStatus("failed", "请输入测试文件路径");
    return;
  }
  showView("execute");
  setStatus("running", "测试执行中");
  setLog([]);
  showExecuteTab("log");
  activeJobId = "";
  setFlowInputEnabled(false);
  $("testStatusDot").className = "dot running";
  $("testStatusText").textContent = "执行中...";
  const data = await postJson("/api/run-tests", {
    test_path: testPath,
    env: $("testEnv").value,
    project_root: getProjectRoot(),
    output_dir: getOutputDir(),
  });
  pollJob(data.job.id).catch(showError);
}

function getTestProjectRoot() {
  const projectRoot = getProjectRoot().replace(/\\/g, "/").replace(/\/+$/, "");
  return projectRoot.endsWith("/auto-test") ? projectRoot : projectRoot + "/auto-test";
}

async function chooseTestFile() {
  const testProjectRoot = getTestProjectRoot();
  const testcasesDir = testProjectRoot + "/project/feikua/testcases";
  const data = await postJson("/api/select-file", {
    initial_dir: testcasesDir,
    title: "选择测试文件",
  });
  if (!data.path) return;
  const selectedPath = data.path.replace(/\\/g, "/");
  const relPath = selectedPath.startsWith(testProjectRoot + "/")
    ? selectedPath.slice(testProjectRoot.length + 1)
    : selectedPath;
  $("testPath").value = relPath || data.path;
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

function renderRunItem(run, openHandler, allowDelete, options = {}) {
  const item = document.createElement("div");
  item.className = "run-item";
  if (options.selectable) {
    item.classList.add("selectable");
    item.onclick = openHandler;
  }
  if (options.active) {
    item.classList.add("active");
  }

  const info = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = run.name;
  const meta = document.createElement("span");
  meta.textContent = fmtTime(run.modified_at);
  info.appendChild(title);
  info.appendChild(meta);
  const label = reviewLabel(run);
  if (label) {
    const badge = document.createElement("em");
    badge.className = "run-badge " + reviewClass(run);
    badge.textContent = label;
    info.appendChild(badge);
  }
  if (options.showCodex) {
    const codexBadge = document.createElement("em");
    const statusClass = codexClass(run);
    codexBadge.className = "run-badge codex" + (statusClass ? " " + statusClass : "");
    codexBadge.textContent = codexLabel(run);
    info.appendChild(codexBadge);
  }

  const actions = document.createElement("div");
  actions.className = "run-actions";
  if (!options.hideOpenButton) {
    const openButton = document.createElement("button");
    openButton.className = "secondary";
    openButton.textContent = "打开";
    openButton.onclick = (event) => {
      event.stopPropagation();
      openHandler();
    };
    actions.appendChild(openButton);
  }
  if (allowDelete) {
    const deleteButton = document.createElement("button");
    deleteButton.className = "secondary danger-lite";
    deleteButton.textContent = "删除";
    deleteButton.onclick = (event) => {
      event.stopPropagation();
      deleteRun(run).catch(showError);
    };
    actions.appendChild(deleteButton);
  }

  item.appendChild(info);
  if (actions.childNodes.length) {
    item.appendChild(actions);
  }
  return item;
}

async function loadRuns() {
  const response = await fetch("/api/runs");
  const data = await response.json();
  const runs = data.runs || [];
  const root = $("runs");
  const executeRoot = $("executeRuns");
  root.innerHTML = "";
  executeRoot.innerHTML = "";
  renderDashboard(runs);
  if (!activeExecuteRun && runs.length && $("viewExecute").classList.contains("active")) {
    activeRunDir = runs[0].path;
    activeExecuteRun = runs[0];
    renderExecuteDetail();
    setExecutePreview(runs[0].codex_summary_url || reviewUrl(runs[0]) || runs[0].url || "");
  }
  for (const run of runs) {
    root.appendChild(renderRunItem(run, () => setRunPreview(run), true));
    executeRoot.appendChild(renderRunItem(run, () => setExecuteRun(run), false, {
      showCodex: true,
      selectable: true,
      hideOpenButton: true,
      active: activeExecuteRun && activeExecuteRun.path === run.path,
    }));
  }
  if (!runs.length) {
    root.innerHTML = `<div class="empty-state">暂无执行记录</div>`;
    executeRoot.innerHTML = `<div class="empty-state">暂无待执行产物</div>`;
  }
  if (activeExecuteRun) {
    const refreshed = runs.find((run) => run.path === activeExecuteRun.path);
    if (refreshed) {
      activeExecuteRun = refreshed;
      renderExecuteDetail();
    } else {
      activeExecuteRun = null;
      renderExecuteDetail();
      setExecutePreview("");
    }
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
  $("statLatest").textContent = runs[0] ? runs[0].modified_at.replace("T", " ") : "-";
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
$("executeCodexBtn").addEventListener("click", () => runCodex({
  view: "execute",
  approvalPolicyId: "executeApprovalPolicy",
  extraInstructionId: "executeExtraInstruction",
}).catch(showError));
$("runTestBtn").addEventListener("click", () => runTest().catch(showError));
$("openAllureBtn").addEventListener("click", () => {
  setExecuteAllure("/allure/allure_report/index.html");
  showView("execute");
});
$("browseTestFileBtn").addEventListener("click", () => chooseTestFile().catch(showError));
$("refreshReportBtn").addEventListener("click", () => {
  const frame = $("allureReportFrame");
  frame.src = frame.src;
});
$("refreshRunsBtn").addEventListener("click", () => loadRuns().catch(showError));
$("refreshExecuteRunsBtn").addEventListener("click", () => loadRuns().catch(showError));
$("dashboardRefreshBtn").addEventListener("click", () => loadRuns().catch(showError));
$("scrollLogBottomBtn").addEventListener("click", scrollLogToBottom);
$("sidebarToggleBtn").addEventListener("click", toggleSidebar);
$("toggleWorkSidebarBtn").addEventListener("click", () => toggleWorkSidebar());
$("toggleLogExpandBtn").addEventListener("click", toggleLogExpand);
$("openReviewBtn").addEventListener("click", openMainReview);
$("openArtifactBtn").addEventListener("click", openMainArtifact);
$("executeOpenReviewBtn").addEventListener("click", openExecuteReview);
$("focusPreviewBtn").addEventListener("click", () => setReviewFocus(!$("viewGenerate").classList.contains("review-focused")));
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
for (const tab of document.querySelectorAll("[data-execute-tab]")) {
  tab.addEventListener("click", () => {
    if (tab.dataset.executeTab === "summary") {
      openExecuteSummary();
    }
    showExecuteTab(tab.dataset.executeTab);
  });
}
setFlowInputEnabled(false);
renderExecuteDetail();
loadSettings();
saveSettings().then(loadRuns).catch(showError);
showView(location.hash.replace("#", "") || "dashboard", false);
