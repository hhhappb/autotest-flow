# auto-test-flow

版本：`v0.3.0`

[English README](README.md)

`auto-test-flow` 是一个用于 QA 自动化流程的 agent skill。它可以把粗略测试需求转换成增强后的需求、结构化测试方案、测试用例、自动化实现请求，以及可交给 Codex 的代码落地任务。

当前版本重点是 **Phase 2.6 pipeline**：

- DeepSeek v4-pro 负责需求分析和结构化测试产物。
- review policy gate 在交给 Codex 前审查生成结果。
- Codex/GPT-5.5 接收专门的 handoff 任务包，负责项目发现、代码修改计划、测试实现、执行与修复。
- pipeline 本身不会修改项目代码，也不会真实执行测试。

## 能力概览

- 将粗略测试需求增强为结构化测试 brief。
- 从增强需求中抽取机器可读字段。
- 生成可读的测试方案。
- 生成 Markdown 和 JSON 两种形式的结构化测试用例。
- 生成自动化实现请求和执行请求。
- 通过 `auto-review`、`ask`、`full-auto` 三种策略审查产物。
- 生成 Codex handoff 产物，用于后续测试代码落地。
- 保留明确的用户确认 gate，避免未经确认修改项目代码。

## 仓库结构

```text
skills/
  auto-test-flow/
    SKILL.md
    references/
      evaluation-prompts.md
      framework-guidance.md
      hybrid-ui-automation-project-guide.md
      test-requirement-template.md
    scripts/
      config.py
      orchestrator.py
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

pipeline 使用 Anthropic-compatible API。默认配置面向 DeepSeek：

如果本地还没有 Python client，请先安装：

```powershell
python -m pip install anthropic
```

```powershell
$env:ANTHROPIC_AUTH_TOKEN="你的 API Key"
$env:ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
$env:ANTHROPIC_MODEL="deepseek-v4-pro"
```

不要提交 API Key、账号数据、内部地址或生产配置。

## Pipeline 用法

运行 Phase 2.6 pipeline：

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

## 审查策略

| 策略 | 行为 |
|---|---|
| `auto-review` | 默认策略。自动审查生成产物，发现高风险项时阻塞 Codex handoff。 |
| `ask` | 在 Codex handoff 前始终通过命令行询问确认。适合真实项目即将落代码的场景。 |
| `full-auto` | 写入审查结果但不阻塞流程。仅适合快速草稿或低风险探索。 |

审查 gate 会检查测试用例范围过大、自动化候选用例过多、目标类型未知、框架选择未确认、环境或数据安全风险等信号。

## 输出产物

每次 pipeline 会生成一个带时间戳的输出目录：

```text
output/
  <feature>_<timestamp>/
    raw_requirement.txt
    boosted_requirement.md
    fields.json
    test_plan.md
    test_cases.md
    test_cases.json
    automation_request.json
    execution_request.json
    review_result.json
    review_notes.md
    project_context_request.json
    codex_task.json
    codex_task.md
    report.md
```

`codex_task.md` 和 `codex_task.json` 是交给 Codex/GPT-5.5 的任务包。Codex 应先读取项目、提出代码修改计划、等待用户确认，然后才能修改测试代码。

## 在 Codex 或 Claude Code 中使用

可以在对话里直接调用：

```text
使用 auto-test-flow，帮我为这个登录需求设计测试用例，并生成 Codex handoff 任务包。
```

如果只是轻量设计，agent 可以直接在对话中输出方案；如果需要可审计、可落盘的产物，就让它运行 pipeline 或生成文件。

## 安全规则

- 不要把公司或私有项目代码上传到公开仓库。
- 不要提交凭据、token、内部 URL、真实账号或生产配置。
- 如果项目已有合适测试框架，不要引入新框架。
- 有 page object 和 selector 的项目，页面操作放在 page object，元素定位放在 selector 类。
- 测试用例只编排流程和断言，避免堆叠页面细节。
- 修改代码前必须先列出目标文件、修改点、原因和验证命令，并等待用户明确确认。

## 版本

当前版本：`v0.3.0`

本版本重点：

- Phase 2.6 pipeline 编排。
- 基于 DeepSeek 的测试分析。
- Codex handoff 前的 review policy gate。
- 面向 Codex/GPT-5.5 的代码落地交接产物。
