# Evaluation Prompts

Use these prompts to test whether `auto-test-flow` behaves well.

## Prompt 1: Rough Requirement, Design Only

```text
我想测登录功能，帮我设计测试方案和用例。
```

Expected behavior:

- Refine the requirement with assumptions.
- Ask at most three useful questions if needed.
- Produce test scope, test points, case table, and need-confirmation questions.
- Do not write code unless the user asks for scripts.

## Prompt 2: Generate Web Automation

```text
使用 auto-test-flow。我要给购物车结算页面写自动化测试，覆盖优惠券、地址为空、重复提交、网络异常。项目里用 Playwright。
```

Expected behavior:

- Inspect the repo for Playwright config and existing tests.
- Produce a short plan and prioritized cases.
- Add Playwright tests following existing conventions.
- Run a targeted Playwright command if possible.
- Report changed files and test result.

## Prompt 3: API Test With Missing Details

```text
我要测试创建订单接口，帮我写自动化测试脚本。
```

Expected behavior:

- Identify missing endpoint/auth/schema/test environment details.
- Ask focused questions if these are blockers.
- If the repo contains API docs or existing tests, inspect them before asking.
- Avoid inventing endpoint URLs or credentials.

## Prompt 4: Existing Failures

```text
自动化测试失败了，帮我修复。
```

Expected behavior:

- Use the test-fixing style if available.
- Run or inspect the failing command/log.
- Group failures by root cause.
- Fix test bugs without hiding product bugs.
- Re-run the narrowest relevant test command.

## Rubric

Score each run from 1 to 5:

- Requirement clarity: Does it preserve and clarify the user's intent?
- Coverage quality: Does it include happy path, exception path, boundary, permissions, data state, API validation, security, regression where relevant?
- Code fit: Does generated code match the repo's framework and style?
- Verification: Did it run tests or clearly explain why not?
- Practicality: Are assumptions and open questions explicit without over-blocking progress?
