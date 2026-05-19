# auto-test-flow

Version: `v0.3.0`

[中文说明](README.zh-CN.md)

`auto-test-flow` is an agent skill for QA automation workflows. It turns rough testing requests into refined requirements, structured test plans, test cases, automation handoff artifacts, and Codex-ready implementation tasks.

The current release focuses on a Phase 2.6 pipeline:

- DeepSeek v4-pro handles requirement analysis and structured testing artifacts.
- A review policy gate checks generated artifacts before code handoff.
- Codex/GPT-5.5 receives a dedicated handoff package for project discovery, code-change planning, test implementation, execution, and repair.
- The pipeline itself does not modify project code or run tests.

## What It Does

- Refines rough testing requirements into structured testing briefs.
- Extracts machine-readable fields from the refined requirement.
- Generates human-readable test plans.
- Generates structured test cases in Markdown and JSON.
- Produces automation implementation and execution requests.
- Reviews generated artifacts with `auto-review`, `ask`, or `full-auto` policies.
- Generates Codex handoff artifacts for later code implementation.
- Keeps project code changes behind an explicit user confirmation gate.

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
    scripts/
      config.py
      orchestrator.py
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

The pipeline uses an Anthropic-compatible API. By default, it is configured for DeepSeek:

Install the Python client if it is not already available:

```powershell
python -m pip install anthropic
```

```powershell
$env:ANTHROPIC_AUTH_TOKEN="your-api-key"
$env:ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
$env:ANTHROPIC_MODEL="deepseek-v4-pro"
```

Do not commit API keys, account data, internal URLs, or production configuration.

## Pipeline Usage

Run the Phase 2.6 pipeline:

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

## Review Policies

| Policy | Behavior |
|---|---|
| `auto-review` | Default. Automatically reviews generated artifacts and blocks Codex handoff when high-risk items appear. |
| `ask` | Always prompts in the command line before Codex handoff. Use this when a real project change is likely. |
| `full-auto` | Writes review results but never blocks. Use only for quick drafts or low-risk exploration. |

The review gate checks for signals such as excessive test case scope, too many automation candidates, unknown target type, unconfirmed framework choice, and environment or data safety risks.

## Output Artifacts

Each pipeline run creates a timestamped output directory:

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

`codex_task.md` and `codex_task.json` are the handoff artifacts for Codex/GPT-5.5. Codex should read the project, propose a code-change plan, wait for user confirmation, and only then modify test code.

## Using the Skill in Codex or Claude Code

You can invoke the skill conversationally:

```text
Use auto-test-flow to design test cases for this login requirement and generate a Codex handoff package.
```

For lightweight planning, the agent can answer inline without running the pipeline. For persistent, auditable outputs, ask it to run the pipeline or generate files.

## Safety Rules

- Do not upload company or private project code to public repositories.
- Do not commit credentials, tokens, internal URLs, real accounts, or production configuration.
- Do not introduce a new test framework when the repository already has a suitable one.
- For projects with page objects and selectors, keep page operations in page objects and element locators in selector classes.
- Keep test cases focused on flow orchestration and assertions.
- Before modifying code, list the target files, intended changes, reasons, and validation commands, then wait for explicit user confirmation.

## Version

Current version: `v0.3.0`

Release focus:

- Phase 2.6 pipeline orchestration.
- DeepSeek-powered test analysis.
- Review policy gate before Codex handoff.
- Codex/GPT-5.5 implementation handoff artifacts.
