# auto-test-flow

`auto-test-flow` is a Codex skill for QA automation work. It helps turn rough testing requests into refined requirements, test plans, test cases, and maintainable automated test scripts.

The skill is designed to follow the conventions already present in a project before adding new patterns. It is especially useful for Python/Pytest, Playwright, Pywinauto, API, UI, E2E, regression, and test-repair workflows.

## What It Does

- Refines rough testing requirements into structured testing briefs.
- Produces test scope, test point breakdowns, and test case tables.
- Inspects existing project structure before writing automation code.
- Reuses existing test frameworks, page objects, selectors, fixtures, helpers, and run commands.
- Keeps implementation changes narrow and auditable.
- Runs or recommends the smallest relevant verification command.

## Repository Layout

```text
skills/
  auto-test-flow/
    SKILL.md
    references/
      evaluation-prompts.md
      framework-guidance.md
      hybrid-ui-automation-project-guide.md
      test-requirement-template.md
```

## Installation

Install this skill from GitHub with the Codex skill installer:

```powershell
python "$env:USERPROFILE\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py" --repo hhhappb/autotest-flow --path skills/auto-test-flow
```

Restart Codex after installing or updating a skill so the new instructions are picked up.

## Usage Examples

Generate a test plan:

```text
Use auto-test-flow to design test cases for the login flow. Output a test scope, test point breakdown, case table, and need-confirmation questions.
```

Generate automation code:

```text
Use auto-test-flow to add E2E tests for the store opening flow. Inspect the existing project structure first and follow the current page object, selector, fixture, and assertion patterns.
```

Analyze before modifying:

```text
Use auto-test-flow to inspect this Python Pytest automation project. Do not modify files yet. First list the files you would change, the change points, and the reasons.
```

## Safety Rules

- Do not upload company or private project code to public repositories.
- Do not commit credentials, tokens, internal URLs, real accounts, or production configuration.
- Do not introduce a new framework when the repository already has a suitable test framework.
- For projects with page objects and selectors, keep page operations in page objects and element locators in selector classes.
- Keep test cases focused on flow orchestration and assertions.
- Before modifying code, list the target files, intended changes, and reasons when project rules require confirmation.

## Project-Specific Guidance

Detailed project conventions should live in `references/` rather than being hard-coded into `SKILL.md`. This repository includes a sanitized guide for Python/Pytest hybrid UI automation projects:

```text
skills/auto-test-flow/references/hybrid-ui-automation-project-guide.md
```

That guide intentionally avoids private links, IP addresses, credentials, and company-specific internal details.
