# Hybrid UI Automation Project Guide

Use this reference when a repository is a Python/Pytest automation project that combines browser automation, desktop/Electron automation, page objects, selector classes, YAML test data, and local runners.

This guide is intentionally sanitized. Do not add private links, real IP addresses, credentials, account data, production configuration, or company-internal documentation links here.

## Recognition Signals

Treat a repository as this project family when several of these signals are present:

- `pytest.ini`
- `runner.py`
- `requirements/requirements.txt` or `requirements.txt`
- `config/config.yaml` and `config/config.py`
- `core/base/` with shared page or driver base classes
- `core/drivers/` with browser, Electron, driver manager, or driver factory modules
- `project/<module>/pages/`
- `project/<module>/selectors/`
- `project/<module>/testcases/`
- `project/<module>/testdatas/`
- Allure result or report folders
- Playwright, Pywinauto, Pytest, or Electron-related helpers

## Architecture

Follow the existing layered structure:

```text
testcases/       -> orchestrate scenario flow and assertions
pages/           -> encapsulate page or desktop-window operations
selectors/       -> store element locators and selector constants
testdatas/       -> store reusable test data, often YAML
core/base/       -> shared page, driver, and operation base classes
core/drivers/    -> browser, desktop, Electron, and driver lifecycle code
config/          -> environment and runtime configuration
runner.py        -> local project runner when present
pytest.ini       -> Pytest configuration
testreport/      -> generated test reports
logs/            -> generated logs
```

Prefer the actual repository layout over this generic map if they differ.

## Implementation Rules

- Keep test cases focused on scenario orchestration and assertions.
- Put page or window interaction logic in existing page objects.
- Put element locators in existing selector classes.
- Add selector constants to an existing selector class when the responsibility matches.
- Add page object methods to an existing page class when the operation belongs to that page.
- Add test cases under `project/<module>/testcases/` or the repository's established test case directory.
- Add test data under `project/<module>/testdatas/` or the repository's established data directory.
- Reuse existing base methods such as click, fill, wait, screenshot, driver access, logging, and assertion helpers.
- Do not bypass the project's driver manager, fixtures, base test classes, or runner unless there is no existing path.
- Do not introduce a new automation framework if Pytest, Playwright, Pywinauto, or local wrappers already cover the need.

## Change Planning

Before modifying files, identify the smallest coherent change set:

1. Selector updates, if new elements are needed.
2. Page object updates, if new user operations are needed.
3. Test data updates, if reusable input data is needed.
4. Test case updates, keeping business flow readable.
5. Runner or config updates only if the requested task explicitly requires them.

When project rules require confirmation, list:

- Target file path
- Proposed change
- Reason
- Expected effect
- Verification command

Wait for user confirmation before editing.

## Running Tests

Prefer the narrowest command that verifies the changed behavior.

Common patterns:

```powershell
pytest path\to\test_file.py -v
pytest path\to\test_file.py -v -s
python runner.py
```

For Allure-enabled projects, use the repository's existing Allure result directory and report workflow. Do not invent a new report path if one already exists.

## Environment And Data Safety

- Verify that the target environment is safe before running state-changing tests.
- Do not modify production or sensitive configuration files unless the user explicitly asks and confirms.
- Do not commit generated logs, reports, virtual environments, caches, screenshots, videos, traces, or local machine files.
- Keep credentials, tokens, account data, and URLs in environment variables or existing configuration patterns.
- If a test creates persistent data and cleanup is not reliable, document the residual data risk.

## Review Checklist

Before finalizing:

- Did the change follow existing project layout?
- Did selector changes stay in selector classes?
- Did operation logic stay in page objects?
- Did test cases remain readable and focused?
- Did the implementation avoid arbitrary sleeps and brittle locators when better helpers exist?
- Did the verification command run, or is there a clear reason it could not run?
- Did the final report mention changed files, commands, results, and remaining assumptions?
