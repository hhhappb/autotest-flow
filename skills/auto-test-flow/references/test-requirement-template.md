# Test Requirement Template

Use this template when refining a rough Chinese testing request into a structured requirement.

## Refined Testing Prompt

```markdown
你是一名资深测试工程师。请根据以下测试需求，帮我设计测试方案和测试用例，并在需要时生成自动化测试脚本。

【测试对象】
说明要测试的功能、模块、接口或页面：

【业务背景】
这个功能是用来做什么的：

【核心需求】
1.
2.
3.

【用户角色】
涉及哪些角色，例如普通用户、管理员、游客：

【输入条件】
有哪些字段、参数、按钮、状态或前置条件：

【预期结果】
功能正常时应该发生什么：

【异常情况】
请重点考虑输入错误、权限不足、重复操作、网络异常、数据为空等情况：

【测试重点】
请重点覆盖：
- 正常流程
- 异常流程
- 边界值
- 权限控制
- 数据状态
- 接口校验
- 安全风险
- 回归测试点

【自动化要求】
说明希望使用的测试框架、运行环境、浏览器、接口地址、账号数据、是否需要落盘生成脚本：

【输出格式】
请按以下格式输出：
1. 测试范围
2. 测试点拆分
3. 测试用例表格：用例编号、用例标题、前置条件、测试步骤、测试数据、预期结果、优先级
4. 自动化测试实现方案
5. 需要确认的问题
```

## Test Case Table

Use this table for human-readable cases:

```markdown
| 用例编号 | 用例标题 | 前置条件 | 测试步骤 | 测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|---|---|
| TC_<FEATURE>_001 |  |  |  |  |  | P0 |
```

Priority guide:

- P0: core happy path, revenue/security/login/data correctness risk.
- P1: common exception path, permission, important boundary.
- P2: lower-frequency edge cases, compatibility, regression-only checks.

## Plan JSON Shape

When a machine-readable plan is useful, write:

```json
{
  "feature": "",
  "target": "",
  "businessBackground": "",
  "roles": [],
  "assumptions": [],
  "needConfirmation": [],
  "coverageFocus": [
    "happy_path",
    "exception_path",
    "boundary_values",
    "permission_control",
    "data_state",
    "api_validation",
    "security_risk",
    "regression"
  ],
  "cases": [
    {
      "id": "TC_FEATURE_001",
      "title": "",
      "priority": "P0",
      "preconditions": [],
      "steps": [],
      "testData": {},
      "expectedResult": ""
    }
  ]
}
```
