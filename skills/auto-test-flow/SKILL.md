---
name: auto-test-flow
description: End-to-end QA automation workflow for turning rough testing requirements into refined prompts, structured test plans, test case tables, and executable automated test scripts. Use this skill whenever the user mentions testing requirements, test plans, test cases, QA, automated testing, E2E tests, UI tests, API tests, Playwright, Pytest, Cypress, regression testing, or asks to generate/run/fix test scripts, even if they only provide a short or vague Chinese/English request.
---

# Auto Test Flow

Use this skill to act as a senior QA automation engineer. Take a rough testing request, refine it into a clear test requirement, design the test plan and cases, then implement and verify automation code when the user wants scripts or when the task clearly implies code generation.

## Operating Principles

- Prefer action over long planning. If the user asks to build or generate tests, inspect the project and implement the tests.
- Keep the workflow auditable: separate requirement refinement, test design, code generation, execution, and report.
- Ask only the minimum blocking questions. If a detail can be reasonably inferred, proceed and list it under "Assumptions" or "Need Confirmation".
- Use existing project conventions first: test framework, file layout, fixtures, page objects, naming, scripts, environment variables, and assertion style.
- Do not introduce a new test framework if the repo already has a suitable one.
- Do not run destructive tests against production data. If environment safety is unclear, ask before executing state-changing flows.

## Two Modes

This skill supports two modes. Choose based on whether the user wants a quick design answer or saved workflow artifacts:

**Inline mode** (default, lightweight): Generate the refined requirement and test plan directly in conversation. Fast, no files written. Good for quick exploration or when the user just wants to see the plan.

**Pipeline mode** (persistent, auditable): Run the bundled `scripts/orchestrator.py` to produce standardized Markdown and JSON artifacts. The Phase 2.6 pipeline turns a rough requirement into a saved handoff package for later Codex/GPT-5.5 automation implementation and execution, with a review policy gate before Codex handoff. Use this when the user:
- Wants a saved `.md` test plan file for review/sharing
- Needs intermediate artifacts (boosted requirement, structured fields JSON, structured cases JSON)
- Asks for "输出文件" or "保存方案" or "生成文档"
- Is working in a regulated/auditable context

The pipeline creates a dedicated folder per run under the current working directory:
```
output/
  <feature>_<timestamp>/
    raw_requirement.txt         # Original user requirement
    boosted_requirement.md      # Refined requirement
    fields.json                 # Structured fields (machine-readable)
    test_plan.md                # Human-readable test plan
    test_cases.md               # Human-readable test case table
    test_cases.json             # Structured test cases (machine-readable)
    automation_request.json     # Handoff request for future code generation
    execution_request.json      # Handoff request for future test execution
    review_result.json          # Machine-readable review decision and findings
    review_notes.md             # Human-readable review notes
    project_context_request.json # Project discovery request for Codex
    codex_task.json             # Machine-readable Codex handoff task
    codex_task.md               # Human-readable Codex handoff prompt
    report.md                   # Phase summary and remaining questions
```

All artifacts are produced together so later LangChain/LangGraph nodes can consume stable files instead of scraping chat text. In Phase 2.6, DeepSeek handles test analysis and structured artifacts, the review gate checks whether the generated artifacts are safe to hand off, and `codex_task.md`/`codex_task.json` hand code implementation to Codex/GPT-5.5. The pipeline still does not modify project code or run tests by itself.

## Model Responsibility Split

- DeepSeek v4-pro: requirement boosting, field extraction, test plan generation, structured test case generation, automation request generation, and execution request generation.
- Codex/GPT-5.5: project discovery, code-change planning, automated test implementation, test execution, failure repair, and final code-level report.
- User confirmation gate: before Codex modifies code, selectors, page objects, or test flow, it must first list the files, intended changes, reasons, and validation commands, then wait for explicit user confirmation.

## Review Policy

Use `--review-policy` to choose how much the pipeline should pause before Codex handoff:

| Policy | Behavior |
|---|---|
| `auto-review` | Default. Automatically reviews generated artifacts and blocks Codex handoff when high-risk items appear. |
| `ask` | Always prompts in the command line before Codex handoff. Use this when a real project change is likely. |
| `full-auto` | Writes review results but never blocks. Use only for quick drafts or low-risk exploration. |

The review gate checks for signals such as excessive test case scope, too many automation candidates, unknown target type, unconfirmed framework choice, and environment/data safety risks. If the gate blocks handoff, read `review_notes.md` before continuing.

## Recommended Companion Skills

- `boost-prompt`: refine the user's rough request into a structured, high-quality testing prompt. For Inline mode. In Pipeline mode, the orchestrator script handles boosting via API.
- `playwright-expert`: create or debug Playwright E2E tests, browser automation, visual checks, and page objects.
- `test-fixing`: group failures, identify root causes, and repair broken tests.
- `agent-browser`: explore a live web app, take screenshots, click through flows, or verify UI behavior.
- `find-skills`: search for a more specific testing skill when the domain is outside the current workflow.

## Workflow

### 1. Intake And Prompt Refinement

Convert the user's raw request into a structured testing brief.

**Pipeline mode**: Run the orchestrator script. It executes a Phase 2.6 pipeline (boost → extract fields → generate plan → generate cases → build automation request → build execution request → review gate → build Codex handoff → save report) via API and saves all artifacts as files.

```bash
cd <skill-dir>/scripts
python orchestrator.py "用户的原始测试需求"
python orchestrator.py "用户的原始测试需求" --review-policy ask
python orchestrator.py "用户的原始测试需求" --review-policy full-auto
```

Use `--skip-boost` if the user already provides a well-structured requirement.

Read `report.md`, `test_plan.md`, `test_cases.md`, and `codex_task.md` to present the result to the user.

**Inline mode**: Refine the requirement directly. Ask up to three focused questions:
- The test target: module, page, interface, feature, or user flow.
- The system context: app URL, repo location, environment, credentials, test data safety.
- The expected deliverable: plan only, test cases only, automation code, test execution, or full report.

If the user wants quick progress, continue with explicit assumptions instead of waiting.

Use `references/test-requirement-template.md` for the canonical Chinese requirement structure and output format.

### 2. Project Discovery

Before writing test code, inspect the repository:

- Look for framework signals: `package.json`, `playwright.config.*`, `cypress.config.*`, `pytest.ini`, `pyproject.toml`, `pom.xml`, `build.gradle`, existing `tests/`, `e2e/`, `specs/`, or CI files.
- Read nearby existing tests and helper utilities before adding new patterns.
- Identify how tests are run: `npm test`, `npm run test:e2e`, `npx playwright test`, `pytest`, Maven/Gradle, or repo-specific scripts.
- Check whether dependencies are already installed. Ask before installing new dependencies unless the user has clearly requested setup.

Use `references/framework-guidance.md` to choose the implementation style.

### 3. Test Plan And Case Design

Produce these sections before or alongside code:

1. Test scope
2. Test point breakdown
3. Test case table with: case ID, title, preconditions, steps, test data, expected result, priority
4. Need-confirmation questions

Coverage must include, as applicable:

- Happy path
- Exception path
- Boundary values
- Permission control
- Data states
- Interface validation
- Security risks
- Regression points

When the user asks for automation, select a practical subset for the first implementation. Prefer P0/P1 cases and high-risk paths.

**Pipeline mode note**: When using the orchestrator script, the plan and cases are saved as both human-readable Markdown and machine-readable JSON. Read them to present results. If the user wants code, treat `codex_task.md`, `codex_task.json`, and `project_context_request.json` as the handoff artifacts for Step 4 and still inspect the target project before writing files.

### 4. Automation Implementation

Generate code only after understanding the target and project conventions.

For Web UI and E2E:

- Prefer Playwright if available or if no framework exists and the user allows a choice.
- Use stable locators: role, label, text, test IDs, and accessible names.
- Avoid arbitrary waits. Prefer web-first assertions and event-based waits.
- Keep tests independent. Use setup/teardown or fixtures for state.
- Add page objects only when they match existing patterns or reduce repeated flow logic across multiple tests.

For API:

- Prefer the repo's existing HTTP test style.
- Validate status codes, response schema, business fields, error messages, auth behavior, idempotency, and boundary cases.
- Keep secrets in environment variables.

For unit or integration tests:

- Follow existing test runner, mocking style, fixtures, and naming.
- Cover behavior, not implementation details.

### 5. Execution And Repair

After writing tests, run the narrowest relevant command first. If tests fail:

- Classify failures: test bug, product bug, environment issue, missing dependency, flaky timing, bad data, or unclear requirement.
- Repair test bugs directly.
- Do not mask real product bugs by weakening assertions.
- Re-run the targeted test command after changes.
- Escalate only when dependency installation, network, browser launch, or protected filesystem access is required.

### 6. Final Report

End with a concise report:

- What requirement was refined
- What test plan/cases were produced
- What files changed
- What command was run and result
- Remaining assumptions or questions

If no code was written, clearly say the output is a test design artifact only.

## Artifact Layout

When the user wants persistent artifacts or the task is complex, prefer the orchestrator output folder:

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

For actual automated test code, still follow the target repository's established directories. Do not create new test directories if the repo already has a better established location.

## Quality Bar

Use `references/evaluation-prompts.md` to evaluate or improve this skill. A good result should:

- Preserve the user's actual business intent.
- Make missing information visible without blocking unnecessarily.
- Produce executable, maintainable tests when code is requested.
- Run or clearly explain why tests were not run.
- Avoid broad unrelated refactors.
