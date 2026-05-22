# Automation Code Rules

Use this reference immediately before proposing or making code changes for automated tests.

## Confirmation Gate

Before changing code, selectors, page objects, test data, or test flow, present:

- Target file path.
- Proposed change.
- Reason.
- Expected effect.
- Environment and data impact.
- Narrow validation command.

Wait for explicit user confirmation such as "可以修改", "确认修改", or "按这个改".

## Environment And Account Data

- Use the repository's existing config helpers for URL, account, team, credentials, and environment values.
- Do not hard-code environment-specific URLs, accounts, team names, tokens, passwords, or if/else branches by environment.
- Do not alter production or shared state unless the user confirms the exact impact.

## Selectors

- Put reusable selectors in the existing selector class that owns the page or module.
- Keep single-use selectors in the test only when the project convention allows it.
- Choose selectors from confirmed DOM evidence.
- Prefer `id`, `name`, `data-*`, role/name, stable form structure, stable class plus attribute.
- Do not add speculative fallback selectors, body-text scans, or broad ancestor/sibling inference.

## Page Objects

- Put reusable page operations in existing page objects.
- Add new page-object classes only when the responsibility clearly differs from existing classes.
- Use project base methods such as `_click`, `_fill`, `_wait_for_element`, `_wait_for_load_state`, and existing driver helpers.
- Add report-step decorators such as `@allure.step` when the project uses them for public page operations.
- Keep page methods focused on stable, reusable operations needed by current tests.

## Test Cases

- Test cases should orchestrate business flow and assertions.
- Avoid piling detailed DOM operations into test bodies when a page-object method already fits.
- Keep first implementation narrow: core setup, action, save/apply, open target, read result, assert result.
- Do not add broad recovery logic, repeated waits, or complex cleanup unless the environment requires it and the user accepts the tradeoff.

## Assertions

- Use the repository's assertion helper when one exists.
- Do not replace product expectations with weaker assertions to make tests pass.
- Prefer direct value/state assertions over vague page-text checks.
- Let missing elements and unexpected product behavior fail loudly.

## Cleanup

Release resources in the repository's established order. For hybrid Electron/browser projects this commonly means:

1. New tabs or pages opened by the test.
2. Store/browser page object.
3. Login/browser connection.
4. Electron or desktop process driver.

Keep cleanup simple. Avoid large compensation logic for state restoration unless the test changes shared configuration and the restore path is already stable.

## Verification

- Run the narrowest relevant command first.
- If a command requires admin privileges, GUI launch, network, or state-changing environment access, explain the impact and request approval under the workspace policy.
- Report commands run, results, and anything not run.
