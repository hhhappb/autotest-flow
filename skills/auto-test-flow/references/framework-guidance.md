# Framework Guidance

Use this file when choosing how to implement automated tests.

## Framework Selection

| Target | Preferred choice | Notes |
|---|---|---|
| Existing repo with test framework | Existing framework | Follow local conventions first. |
| Web UI / E2E | Playwright | Good default for browser automation, tracing, screenshots, multi-browser. |
| React/Vue/Next unit tests | Existing Vitest/Jest setup | Prefer Testing Library if already used. |
| Node API | Existing Jest/Vitest/Supertest or Playwright request | Keep auth and base URL configurable. |
| Python API/service | Pytest + existing clients/fixtures | Use requests/httpx only if already present or allowed. |
| Java service | Existing JUnit/TestNG setup | Follow Maven/Gradle structure. |
| No repo context | Produce plan and framework recommendation | Ask before scaffolding dependencies. |

## Playwright Rules

- Prefer TypeScript if the project is TypeScript or has Playwright TS config.
- Put specs in the repo's existing E2E directory.
- Use `test.step` only when it improves report readability.
- Prefer locators in this order: `getByRole`, `getByLabel`, `getByPlaceholder`, `getByTestId`, stable text, CSS selector.
- Avoid `waitForTimeout` except for explicit debug-only cases.
- Use `expect(locator).toBeVisible()`, `toHaveText`, `toHaveURL`, and response assertions instead of manual sleeps.
- Keep authentication reusable through storage state or fixtures when the repo already supports it.
- Use traces/screenshots/videos according to existing config.

## API Test Rules

- Parameterize base URL and credentials through environment variables.
- Test success, validation errors, auth failures, permission failures, not-found, duplicate submission, and empty data.
- Validate both protocol-level results and business-level response fields.
- Include idempotency or repeat-operation checks for create/submit/payment-like endpoints.
- Do not log secrets or tokens.

## Data And Environment Safety

- If a flow creates, deletes, pays, sends messages, or changes permissions, verify that the environment is a test environment before running.
- Prefer generated test data with unique prefixes.
- Clean up created data if the project has reliable cleanup helpers.
- If cleanup is risky or unsupported, document residual data.

## Implementation Scope

For a first pass, implement a small, valuable set:

- 1 to 2 P0 happy paths
- 2 to 4 important P1 negative or boundary cases
- Any auth/permission check that protects sensitive behavior

Expand after the first test run is stable.
