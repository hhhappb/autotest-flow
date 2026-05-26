---
name: auto-test-flow
description: End-to-end QA automation workflow for turning raw testing requirements and local materials into validated requirement context, structured test plans, test case tables, element-evidence discovery, and executable automated test scripts. Use this skill whenever the user mentions testing requirements, test plans, test cases, QA, automated testing, E2E tests, UI tests, API tests, Playwright, Pytest, Cypress, regression testing, CDP/F12 element evidence, selector confirmation, or asks to generate/run/fix test scripts, even if the request is short or vague.
---

# Auto Test Flow

Use this skill as the coordinator for QA automation work. Keep the main flow short and load the referenced guides only when the current stage needs them.

## Operating Principles

- Preserve the user's business intent and expose assumptions early.
- Follow existing project conventions before proposing new framework, directory, selector, fixture, runner, or report patterns.
- Keep implementation small: first automate the highest-value P0/P1 path, then expand only when requested or justified by risk.
- For UI automation, do not guess selectors. Element evidence from CDP, DevTools/F12, or an equivalent live DOM inspection is a gate before selector, page-object, or test-flow changes.
- Before modifying code, selectors, page objects, test data, or test flow, list target files, intended changes, reasons, data/environment impact, and validation commands, then wait for explicit user confirmation.
- During Codex code implementation, repair, review, or execution handoff, invoke or follow `karpathy-12-rules` first. If that skill is unavailable, apply its core discipline directly: read before writing, keep changes surgical, avoid speculative abstractions and fallbacks, surface assumptions, and verify the narrowest meaningful result.

## Reference Map

Read only the references needed for the current stage:

- `references/test-requirement-template.md`: Chinese requirement refinement structure.
- `references/framework-guidance.md`: choose implementation style by framework.
- `references/element-evidence-cdp.md`: CDP/F12 DOM evidence gate before UI selector or page-object work.
- `references/automation-code-rules.md`: project code layering, selectors, assertions, Allure, cleanup, and confirmation rules.
- `references/pipeline-artifacts.md`: pipeline output files, handoff fields, and report expectations.
- `references/local-viewer.md`: local clickable artifact viewer, interactive workbench, and optional Codex execution handoff.
- `references/hybrid-ui-automation-project-guide.md`: Python/Pytest + Playwright/Pywinauto/Electron project conventions.
- `references/evaluation-prompts.md`: evaluate generated plans or code-level results.

## Modes

### Inline Mode

Use this by default for quick progress in chat. Produce:

1. Refined requirement.
2. Assumptions and need-confirmation questions.
3. Test scope.
4. Test point breakdown.
5. Test case table with priority.
6. Element evidence plan or collected evidence summary for UI work.
7. Automation implementation plan.
8. Proposed code-change files, reasons, data/environment impact, and validation commands when code is requested.

Do not jump from a rough requirement directly to code. If UI elements are unclear, stop at the element-evidence request instead of inventing selectors.

### Pipeline Mode

Use `scripts/orchestrator.py` when the user wants saved artifacts, a formal handoff package, or repeatable pipeline output. The pipeline creates a run folder under `output/<feature>_<timestamp>/`.

Run from the skill's `scripts` directory:

```bash
python orchestrator.py "<raw testing requirement>"
python orchestrator.py "<raw testing requirement>" --review-policy ask
python orchestrator.py "<raw testing requirement>" --review-policy full-auto
```

Install script dependencies first when running outside an environment that already provides them:

```bash
python -m pip install -r scripts/requirements.txt
```

The pipeline does not modify project code or run tests by itself. It generates planning and handoff artifacts. Codex still performs local project verification, CDP/F12 evidence discovery when needed, code-change planning, user confirmation, implementation, execution, repair, and final report.

By default the pipeline writes a slim workbench:

```text
index.html
raw/raw_requirement.txt
md/requirement.md
md/test_plan.md
md/test_cases.md
md/report.md
exports/test_cases.xlsx
exports/test_cases.xmind
json/automation_request.json
json/test_cases.json
json/execution_request.json
```

The local viewer shows the human-readable files, exports, and a visible `交接审查` section backed by `md/review_notes.md`. JSON files and `md/codex_task.md` are kept as background machine handoff artifacts for Codex and are not meant for routine manual review. Use `--full-artifacts` for audit/detail files and `--serve --port 8765` for a local clickable viewer. See `references/local-viewer.md` and `references/pipeline-artifacts.md`.

### Workbench Mode

Use `scripts/workbench.py` when the user wants a browser-like local control panel instead of a one-shot command. The workbench can:

1. Accept a raw testing requirement and local materials such as images, xlsx/csv, txt, or md files.
2. Run `orchestrator.py` and show the generated run folder.
3. Preview `index.html` and prior runs.
4. Call `codex.cmd exec` with `md/codex_task.md`, pass uploaded images with `--image`, and write logs under the run folder.

Run from any workspace:

```bash
python path/to/workbench.py --project-root path/to/project --port 8765
```

The workbench still honors the same confirmation gates. By default Codex can be run in read-only mode for a proposal, and code edits require the user to explicitly allow them in the page.

## Workflow

### 1. Intake And Requirement Refinement

Clarify the target, business behavior, environment, data safety, deliverable, and success criteria. Use `references/test-requirement-template.md` when a structured Chinese requirement helps.

Ask only blocking questions. If a detail can be safely inferred, continue and list it as an assumption.

### 2. Project Discovery

Inspect the repository before implementation planning:

- Framework signals: `pytest.ini`, `playwright.config.*`, `cypress.config.*`, `pyproject.toml`, `package.json`, `pom.xml`, `build.gradle`.
- Existing `tests/`, `e2e/`, `specs/`, `project/<module>/testcases/`, page objects, selectors, fixtures, helpers, and runners.
- Existing test commands and report workflow.
- Existing environment/config patterns for URL, account, team, credentials, and test data.

Use `references/framework-guidance.md`; for hybrid Pytest/Playwright/Electron projects, also use `references/hybrid-ui-automation-project-guide.md`.

### 3. Test Plan And Cases

Produce:

- Test scope.
- Test point breakdown.
- Test case table with case ID, title, preconditions, steps, test data, expected result, and priority.
- Need-confirmation questions.
- Recommended first automation subset.

If the user input or attached material already contains explicit test points or test cases, treat those rows as the source of truth: structure only those items, do not add exception, boundary, permission, security, compatibility, or regression cases unless the user or material explicitly asks for them. If one row expresses one test point, generate one corresponding case by default. Only generate a small P0/P1 set yourself when no explicit test point or case exists. Put missing details in need-confirmation questions instead of inventing extra cases.

### 4. CDP Element Evidence Gate

Use `references/element-evidence-cdp.md` before writing or changing UI selectors, page objects, click/read/assert logic, or test flow.

Collect or request evidence for each UI element involved:

- Purpose in the test.
- Minimal `outerHTML` or DOM fragment.
- Stable attributes such as `id`, `name`, `value`, `class`, `role`, `aria-*`, `data-*`, `checked`, `selected`, `disabled`.
- Visibility/clickability/readability state.
- Before/after state changes for switches, radio buttons, dropdowns, tabs, popups, and custom widgets.
- Chosen selector and why it is stable.

If evidence is unavailable, do not guess. Ask the user to provide F12/DevTools DOM or use an available CDP/browser tool to capture it.

### 5. Automation Implementation

Use `references/automation-code-rules.md` before editing code.

Implementation rules:

- Apply `karpathy-12-rules` before writing or changing code. Treat this as the coding discipline layer on top of the QA workflow.
- Reuse existing page objects, selector classes, fixtures, base helpers, and runner patterns.
- Keep test cases focused on flow and assertions; put reusable page details in page objects.
- Add selectors to existing selector classes only when they are reusable; single-use selectors may stay in the test when the project allows it.
- Use existing assertion helpers and report-step conventions.
- Avoid body-text scanning, speculative fallback selectors, excessive waits, broad retries, and swallowed exceptions.
- Let real product or selector failures surface.

### 6. Execution And Repair

Run the narrowest relevant command first. If execution needs admin permissions, network, GUI/browser launch, or state-changing environment access, follow the workspace approval rules and explain impact.

When tests fail, classify the failure as test bug, product bug, environment issue, missing dependency, data issue, flaky timing, or unclear requirement. Fix only test bugs. Do not weaken assertions to hide product defects.

### 7. Final Report

Report:

- Refined requirement and selected cases.
- Element evidence status for UI work.
- Files changed.
- Commands run and results.
- Remaining assumptions, risks, or user confirmations still needed.

If no code was written, say the output is a design/handoff artifact only.

## Inline To Pipeline Promotion

When the user confirms or edits an inline plan and later asks for saved artifacts, promote the latest inline draft into Pipeline Mode. Include:

- Original raw requirement.
- Latest refined requirement.
- Confirmed and rejected assumptions.
- Selected test scope and case table.
- Element evidence plan or evidence summary.
- Automation implementation notes.
- Need-confirmation questions.

Do not restart from the original vague request if the conversation already refined it.
