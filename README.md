# auto-test-flow

Version: `v0.6`

[中文说明](README.zh-CN.md)

`auto-test-flow` is an agent skill for QA automation workflows. It turns rough testing requests and local requirement materials into validated requirement context, structured test plans, test cases, exports, automation handoff artifacts, and Codex-ready implementation tasks.

The current release focuses on a local workbench and safer UI automation handoff:

- Inline mode provides a lightweight conversational draft of requirement analysis, test design, and implementation planning.
- Pipeline mode persists the same workflow into auditable artifacts with a cleaner human-facing layout.
- Workbench mode starts a local browser control panel for requirements, attachments, generated reports, Codex execution, test runs, and Allure report preview.
- DeepSeek v4-pro handles requirement analysis and structured testing artifacts.
- The pipeline discovers the existing local project structure before generating downstream prompts.
- CDP/F12 element evidence is required before Web UI selector, page-object, click, read, or assertion changes.
- Each run writes `index.html`, Markdown reports, Excel and XMind exports, JSON handoff files, and optional full audit artifacts.
- Codex receives a compact handoff package that prioritizes generated `json/` and `md/` artifacts instead of reparsing raw spreadsheets.
- The pipeline itself does not modify project code or run tests.

### v0.6 Highlights

- Adds workbench page-evidence capture from a direct URL or Feikua CDP, producing DOM evidence and candidate selector artifacts.
- Adds clearer executable-step structure to `execution_request.json`.
- Adds failure evidence diff reports to compare baseline evidence with current DOM after failed test runs.
- Splits workbench evidence, Codex execution, and CDP preflight logic into focused modules for easier maintenance.
- Documents the script dependency install path for `openpyxl`, `mistune`, FastAPI, and Uvicorn.

## What It Does

- Refines rough testing requirements into structured testing briefs.
- Reads local materials supplied through the workbench, including images, `.xlsx`, `.csv`, `.txt`, and `.md`.
- Extracts machine-readable fields from the requirement.
- Discovers local project context and injects it into downstream prompts.
- Generates human-readable test plans.
- Generates structured test cases in Markdown and JSON.
- Exports test cases as `.xlsx` and `.xmind`.
- Produces automation implementation and execution requests.
- Reviews generated artifacts with `auto-review`, `ask`, or `full-auto` policies.
- Generates compact Codex handoff artifacts for later code implementation.
- Serves a local workbench at `127.0.0.1` for browser-based operation.
- Provides an execution workbench for Codex execution, test-file selection, environment selection, real-time logs, and embedded Allure reports.
- Keeps project code changes behind explicit user confirmation and UI element evidence gates.

## Inline, Pipeline, And Workbench

`auto-test-flow` has three operating surfaces. Inline is the lightweight draft version. Pipeline writes repeatable artifacts. Workbench wraps the pipeline in a local page.

| Mode | Best For | Output | Gate |
|---|---|---|---|
| Inline | Fast exploration, early requirement shaping, quick test design | Chat output: refined requirement, assumptions, test scope, test points, case table, implementation plan, confirmation questions | User confirmation before code edits |
| Pipeline | Formal review, reusable artifacts, auditable handoff, shared workflows | Run folder with `md/`, `json/`, `exports/`, `raw/`, and `index.html` | Review policy gate plus user confirmation before code edits |
| Workbench | Browser-like local operation, attachments, preview, optional Codex execution | Local page at `http://127.0.0.1:8765/` | Same confirmation and evidence gates as CLI |

Inline minimum output:

1. Refined requirement
2. Assumptions and need-confirmation questions
3. Test scope
4. Test point breakdown
5. Test case table with priority
6. Element evidence plan or collected evidence summary for UI work
7. Automation implementation plan
8. Proposed code-change files, reasons, data/environment impact, and validation commands when code is requested

Pipeline persists the same conceptual stages:

```text
raw requirement
  -> requirement intake validation
  -> structured fields
  -> project context discovery
  -> test plan
  -> test cases
  -> automation and execution requests
  -> review gate
  -> Codex handoff
  -> report and exports
```

### Promoting Inline To Pipeline

Inline can be promoted into Pipeline. If the user refines the inline output, those edits become the canonical draft. When the user later asks to save files, generate documents, enter pipeline mode, or create a Codex handoff package, use the latest inline draft as the Pipeline input instead of restarting from the original rough request.

Promotion should preserve:

- Original raw requirement
- Latest refined requirement
- User-confirmed assumptions
- User-rejected assumptions or scope exclusions
- Selected test scope and test point breakdown
- Test case table
- Element evidence plan or evidence summary
- Automation implementation notes
- Need-confirmation questions

## Repository Layout

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

## Installation

Install this skill from GitHub with the Codex skill installer:

```powershell
python "$env:USERPROFILE\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py" --repo hhhappb/autotest-flow --path skills/auto-test-flow
```

Restart Codex or Claude Code after installing or updating the skill so the new instructions are picked up.

## API Configuration

The pipeline uses an Anthropic-compatible API. By default, it is configured for DeepSeek V4 Flash for faster requirement, plan, and test-case generation. Use V4 Pro only when you need higher-quality reasoning for unusually complex or ambiguous testing work.

Install the Python dependencies if they are not already available:

```powershell
python -m pip install -r skills\auto-test-flow\scripts\requirements.txt
```

```powershell
$env:ANTHROPIC_AUTH_TOKEN="your-api-key"
$env:ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
$env:ANTHROPIC_MODEL="deepseek-v4-flash"
```

Optionally switch to the stronger model for complex runs:

```powershell
$env:ANTHROPIC_MODEL="deepseek-v4-pro"
```

Do not commit API keys, account data, internal URLs, or production configuration.

## Workbench Usage

Use the local workbench when you want a browser-like control panel for requirements, local materials, generated reports, and optional Codex handoff.

From this repository:

```powershell
cd C:\Users\admin1\Desktop\copy\autotest-flow
python .\skills\auto-test-flow\scripts\workbench.py --project-root C:\Users\admin1\Desktop\copy\auto-test --output-dir C:\Users\admin1\Desktop\copy\output --port 8765 --open-browser
```

If the skill is installed globally under `.agents`:

```powershell
cd C:\Users\admin1\Desktop\copy
.\auto-test\venv\Scripts\python.exe C:\Users\admin1\.agents\skills\auto-test-flow\scripts\workbench.py --project-root C:\Users\admin1\Desktop\copy\auto-test --output-dir C:\Users\admin1\Desktop\copy\output --port 8765 --open-browser
```

Then open:

```text
http://127.0.0.1:8765/
```

If port `8765` is already in use, change `--port 8765` to another local port, such as `--port 8766`, then open `http://127.0.0.1:8766/`.

The workbench can:

- Accept raw requirement text.
- Attach local materials such as images, `.xlsx`, `.csv`, `.txt`, and `.md`.
- Run `orchestrator.py`.
- Preview generated run folders.
- Use the execution workbench to select a generated artifact, approve Codex execution, and keep Codex in one run instead of a separate read-only pass.
- Run generated or selected pytest files through `runner.py` with `test`, `prod`, or `all` environment choices.
- Stream Codex and test logs into the page while preserving raw logs under the run folder.
- Preview Codex summaries, execution logs, and Allure reports in a single tabbed result area.

## Pipeline Usage

Run the pipeline:

```powershell
cd skills\auto-test-flow\scripts
python orchestrator.py "Test the login page, covering valid login, wrong password, empty account, duplicate submit, and permission denied scenarios"
```

Save output to a specific directory:

```powershell
python orchestrator.py "Test the login page" --output-dir C:\path\to\output
```

Read a requirement from a text or Markdown file:

```powershell
python orchestrator.py --file C:\path\to\requirement.md
```

Choose a review policy:

```powershell
python orchestrator.py "Test the login page" --review-policy auto-review
python orchestrator.py "Test the login page" --review-policy ask
python orchestrator.py "Test the login page" --review-policy full-auto
```

Serve the generated run folder after pipeline completion:

```powershell
python orchestrator.py "Test the login page" --serve --port 8765
```

Generate the default slim artifacts plus full audit/handoff extras:

```powershell
python orchestrator.py "Test the login page" --full-artifacts
```

## Review Policies

| Policy | Behavior |
|---|---|
| `auto-review` | Default. Automatically reviews generated artifacts and blocks Codex handoff when high-risk items appear. |
| `ask` | Always prompts in the command line before Codex handoff. Use this when a real project change is likely. |
| `full-auto` | Writes review results but never blocks. Use only for quick drafts or low-risk exploration. |

The review gate checks for signals such as excessive test case scope, too many automation candidates, unknown target type, unconfirmed framework choice, and environment or data safety risks. The visible viewer exposes this as the `Handoff Review` / `交接审查` section backed by `md/review_notes.md`.

## Output Artifacts

Each pipeline run creates a timestamped output directory:

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
    attachments/            # workbench uploads, when provided
    logs/                   # command logs, when commands are run
    full/                   # only when --full-artifacts is used
```

Open `index.html` in a browser to review the human-readable Markdown, the handoff review result, and export files. JSON and Codex handoff files remain available on disk for automation and debugging, but they are no longer the primary manual-review surface.

`md/codex_task.md` and `json/codex_task.json` are the handoff artifacts for Codex. Codex should read the project, collect UI element evidence when needed, propose a code-change plan, wait for user confirmation, and only then modify test code.

## UI Element Evidence Gate

For Web UI automation, do not guess selectors. Before adding or changing selectors, page-object operations, click/read/assert logic, or test flow, collect or request compact evidence:

- Element purpose
- Minimal DOM or `outerHTML`
- Stable attributes such as `id`, `name`, `value`, `role`, `aria-*`, `data-*`, checked/selected/disabled state, or stable class
- State changes before and after clicking, selecting, saving, or expanding
- Final selector and why it is stable enough

The bundled helper `scripts/element_evidence.py` can scan a live Playwright page and format selector evidence tables.

## Using The Skill In Codex Or Claude Code

You can invoke the skill conversationally:

```text
Use auto-test-flow to design test cases for this login requirement and generate a Codex handoff package.
```

For lightweight planning, the agent can answer inline without running the pipeline, but it should still show the requirement analysis, test scope, test points, case table, implementation plan, evidence status, and confirmation questions. For persistent, auditable outputs, ask it to run the pipeline or promote the latest inline draft into pipeline artifacts.

## Safety Rules

- Do not upload company or private project code to public repositories.
- Do not commit credentials, tokens, internal URLs, real accounts, or production configuration.
- Do not introduce a new test framework when the repository already has a suitable one.
- For projects with page objects and selectors, keep page operations in page objects and element locators in selector classes.
- Keep test cases focused on flow orchestration and assertions.
- Before modifying code, list target files, intended changes, reasons, data/environment impact, and validation commands, then wait for explicit user confirmation.
- Before Web UI selector or page-object changes, collect real CDP/F12/live DOM element evidence.

## Version

Current version: `v0.5`

Release focus:

- Local browser workbench for requirement input, local materials, generated runs, Codex execution, test runs, and Allure report preview.
- Workbench startup instructions for opening `http://127.0.0.1:8765/`.
- Redesigned execution workbench with a collapsible artifact queue, Codex status, test-run panel, result tabs, and embedded Allure iframe.
- Codex execution now uses a single workspace-write run guarded by approval policy and project instructions, avoiding the old read-only rerun loop.
- Codex prompt handoff is slimmer: generated `json/` and `md/` artifacts are referenced by path, while large embedded JSON and repeated gates are removed.
- Slimmer default artifact layout with `md/`, `json/`, `exports/`, and `raw/`.
- Excel and XMind test case exports.
- `--serve --port` local viewer support.
- `--full-artifacts` for audit-heavy runs.
- CDP/F12 element evidence gate for Web UI automation.
- Scope control for user-specified test points so the pipeline does not expand unrelated functions from the same material set.
- Existing test points/cases are treated as the source of truth; the pipeline does not add exception, boundary, or permission cases unless requested.
- Existing-project convention checks before automation planning.
