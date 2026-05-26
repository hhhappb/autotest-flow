# auto-test-flow

版本：`v0.6`

[English README](README.md)

`auto-test-flow` 是一个用于 QA 自动化流程的 agent skill。它可以把粗略测试需求和本地需求材料转换成已校验的需求上下文、结构化测试方案、测试用例、导出文件、自动化实现请求，以及可交给 Codex 的代码落地任务。

当前版本重点是 **本地工作台 + 更安全的 UI 自动化交接**：

- Inline 模式用于在对话中快速产出需求分析、测试设计和实现计划草稿。
- Pipeline 模式用于把同一套流程落盘成更清晰的可审计产物。
- Workbench 模式提供本地浏览器控制台，用于输入需求、上传附件、查看报告、执行 Codex、运行测试并预览 Allure 报告。
- DeepSeek v4-pro 负责需求分析和结构化测试产物。
- pipeline 在生成后续 prompt 前会先发现本地项目已有结构。
- Web UI 自动化在修改 selector、page object、点击、读取或断言逻辑前，必须先有 CDP/F12 元素证据。
- 每次运行会生成 `index.html`、Markdown 报告、Excel/XMind 导出、JSON 交接文件，以及可选的 full 审计产物。
- Codex 接收精简后的 handoff 任务包，优先读取已生成的 `json/` 和 `md/` 产物，不再默认反复解析原始表格。
- pipeline 本身不会修改项目代码，也不会真实执行测试。

### v0.6 重点更新

- 工作台新增页面证据采集入口，支持直接输入 URL 或连接飞跨 CDP，生成 DOM 证据和候选 selector 产物。
- `execution_request.json` 增加更清晰的可执行步骤结构。
- 测试失败后可生成证据差异报告，对比 baseline evidence 和失败后的当前 DOM。
- 将 workbench 的页面证据、Codex 执行、CDP 预检逻辑拆成独立模块，方便后续维护。
- 补充脚本依赖安装说明，覆盖 `openpyxl`、`mistune`、FastAPI 和 Uvicorn。

## 能力概览

- 将粗略测试需求整理为结构化测试 brief。
- 读取通过工作台提供的本地材料，包括图片、`.xlsx`、`.csv`、`.txt` 和 `.md`。
- 从需求中抽取机器可读字段。
- 发现本地项目上下文，并注入后续 prompt。
- 生成可读的测试方案。
- 生成 Markdown 和 JSON 两种形式的结构化测试用例。
- 导出 `.xlsx` 和 `.xmind` 测试用例文件。
- 生成自动化实现请求和执行请求。
- 通过 `auto-review`、`ask`、`full-auto` 三种策略审查产物。
- 生成精简后的 Codex handoff 产物，用于后续测试代码落地。
- 在 `127.0.0.1` 启动本地工作台。
- 提供执行工作台，用于 Codex 执行、测试文件选择、环境选择、实时日志和嵌入式 Allure 报告预览。
- 保留明确的用户确认 gate 和 UI 元素证据 gate，避免未经确认或缺少真实 DOM 证据就修改项目代码。

## Inline、Pipeline 与 Workbench

`auto-test-flow` 有三种操作入口。Inline 是轻量草稿版，Pipeline 负责写入可复用产物，Workbench 把 pipeline 包装成本地页面。

| 模式 | 适合场景 | 输出形式 | 门禁 |
|---|---|---|---|
| Inline | 快速探索、早期需求澄清、快速测试设计 | 对话输出：增强需求、假设、测试范围、测试点、用例表、实现计划、待确认问题 | 修改代码前仍需用户确认 |
| Pipeline | 正式评审、复用产物、审计留痕、多人交接 | 运行目录：`md/`、`json/`、`exports/`、`raw/`、`index.html` | review policy gate + 修改代码前用户确认 |
| Workbench | 本地页面操作、附件上传、报告预览、可选 Codex 执行 | 本地页面：`http://127.0.0.1:8765/` | 与命令行相同的确认和证据门禁 |

Inline 最少应该输出：

1. 增强后的测试需求
2. 假设和待确认问题
3. 测试范围
4. 测试点拆解
5. 带优先级的测试用例表
6. UI 工作涉及的元素证据计划或已采集证据摘要
7. 自动化实现计划
8. 如果涉及代码实现，列出拟修改文件、修改原因、数据/环境影响和验证命令

Pipeline 会把同一套概念流程持久化：

```text
原始需求
  -> 需求 intake 校验
  -> 结构化字段
  -> 项目上下文发现
  -> 测试方案
  -> 测试用例
  -> 自动化实现和执行请求
  -> review gate
  -> Codex handoff
  -> 报告和导出文件
```

### Inline 晋升为 Pipeline

Inline 可以晋升为 Pipeline。如果用户在 Inline 阶段补充、确认、否定或修改了需求、测试范围、测试点或用例表，这些变更应成为最新草稿。用户后续要求“保存方案”“生成文档”“进入 pipeline”“生成 handoff 包”时，应使用最新 Inline 草稿作为 Pipeline 输入，而不是从最初的粗略需求重新开始。

晋升时应保留：

- 原始需求
- 最新增强需求
- 用户已确认的假设
- 用户否定的假设或排除范围
- 已选择的测试范围和测试点拆解
- 测试用例表
- 元素证据计划或证据摘要
- 自动化实现说明
- 待确认问题

## 仓库结构

```text
skills/
  auto-test-flow/
    SKILL.md
    assets/
      workbench.html
      workbench.css
      workbench.js
    references/
      automation-code-rules.md
      element-evidence-cdp.md
      framework-guidance.md
      hybrid-ui-automation-project-guide.md
      local-viewer.md
      pipeline-artifacts.md
      test-requirement-template.md
    scripts/
      config.py
      element_evidence.py
      exporters.py
      orchestrator.py
      project_discovery.py
      review.py
      viewer.py
      workbench.py
      templates/
        test_plan_prompt.py
```

## 安装

使用 Codex skill installer 从 GitHub 安装：

```powershell
python "$env:USERPROFILE\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py" --repo hhhappb/autotest-flow --path skills/auto-test-flow
```

安装或更新后，请重启 Codex 或 Claude Code，让新的 skill 指令生效。

## API 配置

pipeline 使用 Anthropic-compatible API。默认配置面向 DeepSeek V4 Flash，用于更快地完成需求整理、测试方案和测试用例生成。只有遇到特别复杂或歧义很强的测试任务时，才建议临时切到 V4 Pro。

如果本地还没有 Python client，请先安装：

```powershell
python -m pip install -r skills\auto-test-flow\scripts\requirements.txt
```

```powershell
$env:ANTHROPIC_AUTH_TOKEN="你的 API Key"
$env:ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
$env:ANTHROPIC_MODEL="deepseek-v4-flash"
```

复杂任务可以临时切换到更强模型：

```powershell
$env:ANTHROPIC_MODEL="deepseek-v4-pro"
```

不要提交 API Key、账号数据、内部地址或生产配置。

## 本地工作台用法

当你想用浏览器页面输入需求、上传本地材料、查看生成报告，并可选择交给 Codex 时，使用本地工作台。

从这个仓库启动：

```powershell
cd C:\Users\admin1\Desktop\copy\autotest-flow
python .\skills\auto-test-flow\scripts\workbench.py --project-root C:\Users\admin1\Desktop\copy\auto-test --output-dir C:\Users\admin1\Desktop\copy\output --port 8765 --open-browser
```

如果 skill 已安装到全局 `.agents`：

```powershell
cd C:\Users\admin1\Desktop\copy
.\auto-test\venv\Scripts\python.exe C:\Users\admin1\.agents\skills\auto-test-flow\scripts\workbench.py --project-root C:\Users\admin1\Desktop\copy\auto-test --output-dir C:\Users\admin1\Desktop\copy\output --port 8765 --open-browser
```

然后打开：

```text
http://127.0.0.1:8765/
```

如果 `8765` 端口被占用，把 `--port 8765` 改成其他端口，例如 `--port 8766`，然后打开 `http://127.0.0.1:8766/`。

工作台可以：

- 输入原始测试需求。
- 上传图片、`.xlsx`、`.csv`、`.txt`、`.md` 等本地材料。
- 运行 `orchestrator.py`。
- 预览生成的运行目录。
- 在执行工作台选择生成产物、批准 Codex 执行，并保持一次运行闭环，不再拆成只读预审和二次落地。
- 通过 `runner.py` 运行生成或选中的 pytest 文件，支持 `test`、`prod`、`all` 环境选择。
- 将 Codex 和测试日志实时显示在页面中，同时保留运行目录下的原始日志。
- 在同一个结果区域预览 Codex 摘要、执行日志和 Allure 报告。

## Pipeline 用法

运行 pipeline：

```powershell
cd skills\auto-test-flow\scripts
python orchestrator.py "测试登录页面，覆盖正确账号登录、错误密码、账号为空、重复提交和权限不足场景"
```

指定输出目录：

```powershell
python orchestrator.py "测试登录页面" --output-dir C:\path\to\output
```

从文本或 Markdown 文件读取需求：

```powershell
python orchestrator.py --file C:\path\to\requirement.md
```

选择审查策略：

```powershell
python orchestrator.py "测试登录页面" --review-policy auto-review
python orchestrator.py "测试登录页面" --review-policy ask
python orchestrator.py "测试登录页面" --review-policy full-auto
```

生成完成后启动本地查看服务：

```powershell
python orchestrator.py "测试登录页面" --serve --port 8765
```

生成默认精简产物，同时额外保留完整审计和机器交接产物：

```powershell
python orchestrator.py "测试登录页面" --full-artifacts
```

## 审查策略

| 策略 | 行为 |
|---|---|
| `auto-review` | 默认策略。自动审查生成产物，发现高风险项时阻塞 Codex handoff。 |
| `ask` | 在 Codex handoff 前始终通过命令行询问确认。适合真实项目即将落代码的场景。 |
| `full-auto` | 写入审查结果但不阻塞流程。仅适合快速草稿或低风险探索。 |

审查 gate 会检查测试用例范围过大、自动化候选用例过多、目标类型未知、框架选择未确认、环境或数据安全风险等信号。前台会把它显示成 `交接审查`，内容来自 `md/review_notes.md`。

## 输出产物

每次 pipeline 会生成一个带时间戳的输出目录：

```text
output/
  <feature>_<timestamp>/
    index.html
    raw/
      raw_requirement.txt
    md/
      requirement.md
      review_notes.md
      test_plan.md
      test_cases.md
      report.md
      codex_task.md
    exports/
      test_cases.xlsx
      test_cases.xmind
    json/
      test_cases.json
      automation_request.json
      execution_request.json
      codex_task.json
    attachments/            # 工作台上传附件时出现
    logs/                   # 执行命令时出现
    full/                   # 仅使用 --full-artifacts 时出现
```

可以在浏览器中打开 `index.html`，查看面向人工阅读的 Markdown、交接审查结果和导出文件。JSON 和 Codex handoff 文件仍保留在磁盘上，供自动化和排查使用，但不再作为主要人工审查入口。

`md/codex_task.md` 和 `json/codex_task.json` 是交给 Codex 的任务包。Codex 应先读取项目，必要时采集 UI 元素证据，提出代码修改计划，等待用户确认，然后才能修改测试代码。

## UI 元素证据 Gate

Web UI 自动化不要猜选择器。新增或修改 selector、page object 操作、点击、读取、断言或测试流程前，需要采集或请求紧凑证据：

- 元素用途
- 最小 DOM 或 `outerHTML`
- 稳定属性，例如 `id`、`name`、`value`、`role`、`aria-*`、`data-*`、checked/selected/disabled 状态或稳定 class
- 点击、选择、保存或展开前后的状态变化
- 最终选择器和稳定性理由

内置的 `scripts/element_evidence.py` 可以扫描 live Playwright page，并格式化 selector 证据表。

## 在 Codex 或 Claude Code 中使用

可以在对话里直接调用：

```text
使用 auto-test-flow，帮我为这个登录需求设计测试用例，并生成 Codex handoff 任务包。
```

如果只是轻量设计，agent 可以直接在对话中输出方案，但仍应展示需求分析、测试范围、测试点、用例表、实现计划、证据状态和待确认问题。如果需要可审计、可落盘的产物，就让它运行 pipeline，或把最新 Inline 草稿晋升为 pipeline 产物。

## 安全规则

- 不要把公司或私有项目代码上传到公开仓库。
- 不要提交凭据、token、内部 URL、真实账号或生产配置。
- 如果项目已有合适测试框架，不要引入新框架。
- 有 page object 和 selector 的项目，页面操作放在 page object，元素定位放在 selector 类。
- 测试用例只编排流程和断言，避免堆叠页面细节。
- 修改代码前必须先列出目标文件、修改点、原因、数据/环境影响和验证命令，并等待用户明确确认。
- Web UI selector 或 page object 修改前，必须采集真实 CDP/F12/live DOM 元素证据。

## 版本

当前版本：`v0.5`

本版本重点：

- 新增本地浏览器工作台，用于输入需求、上传本地材料、查看生成结果、执行 Codex、运行测试并预览 Allure 报告。
- 增加打开 `http://127.0.0.1:8765/` 的工作台启动说明。
- 重设计执行工作台，支持可收起产物队列、Codex 状态、测试运行面板、结果 tabs 和嵌入式 Allure iframe。
- Codex 执行改为一次 workspace-write 运行，由审批策略和项目指令兜底，避免旧的 read-only 二次重跑流程。
- Codex prompt 交接进一步瘦身：通过路径引用生成的 `json/` 和 `md/` 产物，删除大段内嵌 JSON 和重复 Gate。
- 默认产物结构改为更清晰的 `md/`、`json/`、`exports/`、`raw/`。
- 新增 Excel 和 XMind 测试用例导出。
- 支持 `--serve --port` 本地查看服务。
- 支持 `--full-artifacts` 保留完整审计产物。
- 增加 CDP/F12 元素证据 gate，约束 Web UI 自动化不要猜 selector。
- 增强测试范围控制，避免用户只指定一个测试点时扩展同一材料里的无关功能。
- 已有测试点/测试用例会被视为唯一事实来源，除非用户要求，不再自动补异常、边界、权限等额外用例。
- 在自动化规划前继续优先检查已有项目约定。
