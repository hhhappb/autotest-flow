# Pipeline Artifacts

Use this reference when running or consuming `scripts/orchestrator.py`.

## Default Output Folder

The pipeline writes one slim run folder by default:

```text
output/
  <feature>_<timestamp>/
    index.html
    raw/
      raw_requirement.txt
    md/
      input_materials.md    # created by workbench runs with uploaded materials
      requirement.md
      review_notes.md
      test_plan.md
      test_cases.md
      codex_task.md
      report.md
    exports/
      test_cases.xlsx
      test_cases.xmind
    json/
      input_materials.json  # created by workbench runs with uploaded materials
      automation_request.json
      test_cases.json
      execution_request.json
    attachments/            # created by workbench runs with uploaded files
      <uploaded files>
    logs/                 # created by workbench runs when commands are executed
      pipeline.log
      codex_exec_<job>.log
      codex_last_message_<job>.md
```

This keeps human-readable artifacts under `md/`, user-friendly exports under `exports/`, machine-readable artifacts under `json/`, and raw input under `raw/`.
`attachments/`, `md/input_materials.md`, `json/input_materials.json`, and `logs/` are optional and appear only when the interactive workbench receives uploads or executes commands.
The local viewer should show `md/report.md`, `md/review_notes.md`, `md/requirement.md`, `md/test_plan.md`, `md/test_cases.md`, and the `exports/` files. `md/review_notes.md` is the manual review entrypoint before Codex handoff. `json/` and `md/codex_task.md` are background machine handoff artifacts, not routine manual-review files.

## Full Artifacts

Use `--full-artifacts` when an audit trail or downstream machine process needs the extra detail:

```text
full/
  fields.json
  project_context_discovery.md
  project_context_discovery.json
  review_result.json
  review_notes.md
  project_context_request.json
  codex_task.json
```

Do not default to full artifacts for routine test planning. The default workbench should stay easy to scan.

The pipeline does not modify project code, run tests, or collect live DOM evidence by itself.
The interactive workbench can call the pipeline and can hand `md/codex_task.md` to Codex, but it must preserve the same confirmation and evidence gates.

## Element Evidence In Pipeline Handoff

For Web UI work, `json/automation_request.json` should include an element-evidence plan:

- `element_evidence_required`: true or false.
- `cdp_capture_targets`: UI elements or states that must be inspected before code changes.
- `selector_change_gate`: rules for whether selector/page-object changes may proceed.

Codex must collect or request the actual evidence before modifying selectors, page objects, test cases, or UI test flow.

## Codex Handoff Expectations

The next agent may read these background handoff artifacts:

- `md/codex_task.md`
- `md/test_plan.md`
- `md/test_cases.md`
- `md/report.md`
- `json/test_cases.json`
- `json/automation_request.json`
- `json/execution_request.json`

If `full/` exists, use it only as supporting context.

Before code changes, the next agent must:

1. Re-check the local project context.
2. Collect or request CDP/F12 evidence for any UI selector or stateful control work.
3. Present target files, proposed changes, reasons, environment/data impact, and validation commands.
4. Wait for explicit user confirmation.

## Report Expectations

The final report should mention:

- Requirement context validated.
- Test plan and selected cases.
- Element evidence status.
- Files changed.
- Commands run and results.
- Remaining assumptions, blockers, or data safety risks.
