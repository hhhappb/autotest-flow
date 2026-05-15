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
- When project rules require confirmation before code changes, list the files, change points, and reasons first, then wait for the user's confirmation.

## Project-Specific Context

Keep `SKILL.md` general. Load project-specific references only when the target repository matches their signals.

For Python/Pytest hybrid UI automation projects, read `references/hybrid-ui-automation-project-guide.md` when the repository has signals such as:

- `pytest.ini`, `runner.py`, or `requirements/requirements.txt`
- `core/base/`, `core/drivers/`, or Electron/desktop driver helpers
- `project/<module>/pages/`, `project/<module>/selectors/`, or `project/<module>/testcases/`
- Playwright, Pywinauto, Pytest, or Allure usage in existing files

Use that reference as sanitized project-family guidance. Do not copy private links, real IPs, credentials, account data, production settings, or company-internal details into generated artifacts.

## Recommended Companion Skills

Use these skills if they are available in the current agent environment:

- `boost-prompt`: refine the user's rough request into a structured, high-quality testing prompt. If Joyride clipboard tools are unavailable, still apply the refinement workflow inline.
- `playwright-expert`: create or debug Playwright E2E tests, browser automation, visual checks, and page objects.
- `test-fixing`: group failures, identify root causes, and repair broken tests.
- `agent-browser`: explore a live web app, take screenshots, click through flows, or verify UI behavior.
- `find-skills`: search for a more specific testing skill when the domain is outside the current workflow.

## Workflow

### 1. Intake And Prompt Refinement

Convert the user's raw request into a structured testing brief.

If important details are missing, ask up to three focused questions. Good questions usually cover:

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
- If the repository matches the Python/Pytest hybrid UI automation signals, read `references/hybrid-ui-automation-project-guide.md` before proposing file changes.

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

When the user wants persistent artifacts or the task is complex, prefer:

```text
.qa/
  requirements/<feature>.md
  plans/<feature>.plan.json
  cases/<feature>.cases.md
  reports/<feature>.report.md
tests/
  e2e/
  api/
  integration/
```

Do not create these directories if the repo already has a better established location.

## Quality Bar

Use `references/evaluation-prompts.md` to evaluate or improve this skill. A good result should:

- Preserve the user's actual business intent.
- Make missing information visible without blocking unnecessarily.
- Produce executable, maintainable tests when code is requested.
- Run or clearly explain why tests were not run.
- Avoid broad unrelated refactors.
