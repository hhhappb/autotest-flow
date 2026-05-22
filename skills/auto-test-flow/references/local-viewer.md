# Local Viewer And Workbench

Use this reference when the user wants a smoother way to review pipeline artifacts.

## Purpose

Each pipeline run writes an `index.html` workbench in the output folder. The workbench links the human-readable Markdown, raw input, and machine-readable JSON artifacts in one page.

For a browser-like control panel that can create runs and hand them to Codex, use `scripts/workbench.py`.

## Default Layout

```text
output/<feature>_<timestamp>/
  index.html
  raw/
    raw_requirement.txt
  md/
    requirement.md
    test_plan.md
    test_cases.md
    review_notes.md
    codex_task.md
    report.md
  exports/
    test_cases.xlsx
    test_cases.xmind
  json/
    automation_request.json
    test_cases.json
    execution_request.json
```

## Full Artifacts

Use `--full-artifacts` when an audit trail or detailed machine handoff is needed:

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

Default runs should stay slim. Do not generate full artifacts unless the user asks for full audit output or a downstream machine process needs them.
The visible viewer should stay human-focused: report, 交接审查, requirement context, test plan, test cases, Excel export, and XMind export. `md/review_notes.md` is the manual review entrypoint before Codex handoff. JSON and Codex handoff files should remain available on disk for background automation, but they should not be shown as primary viewer navigation.

## Static Local Server

Use:

```bash
python orchestrator.py "<requirement>" --serve
python orchestrator.py "<requirement>" --serve --port 8765
```

The server binds to `127.0.0.1` and serves only the generated output folder. It does not upload data.

Stop it with `Ctrl+C`.

## Interactive Workbench

Use the workbench when the user wants to operate the workflow from a local page:

```bash
python workbench.py --project-root C:\path\to\project --port 8765
python workbench.py --project-root C:\path\to\project --output-dir C:\path\to\output --port 8765
```

The backend is `scripts/workbench.py`; the page is split into `assets/workbench.html`, `assets/workbench.css`, and `assets/workbench.js` so UI changes stay separate from pipeline/API code.

The workbench binds to `127.0.0.1` by default and provides:

- Requirement input from text and local materials.
- Pipeline generation through `orchestrator.py`.
- Flow interaction controls that send input to the running pipeline process, such as `yes`, `edit`, and `no`.
- Artifact preview for generated `index.html`.
- Previous run selection.
- Optional `codex.cmd exec` handoff using `md/codex_task.md`.
- Logs under `logs/` in the selected run folder.

Supported input materials:

- Images: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`.
- Spreadsheets: `.xlsx`, `.xls`.
- Text files: `.csv`, `.txt`, `.md`.

The workbench saves uploads under `attachments/` in the run folder and records them in `json/input_materials.json` and `md/input_materials.md`.

Extraction behavior:

- `.xlsx`, `.csv`, `.txt`, and `.md` are converted into text summaries for requirement generation.
- `.xls` is saved and referenced, but not parsed by the standard-library workbench.
- Images are saved and passed to Codex with `--image` during handoff; the pipeline itself records their paths instead of doing OCR.

Codex execution has two modes:

- Read-only proposal mode: do not allow edits in the page.
- Edit mode: the user explicitly checks that Codex may edit files in the current task scope.

Do not use dangerous Codex bypass flags by default. Keep project confirmation, CDP evidence, environment-impact, and command-approval rules intact.

Do not bypass command-line confirmation prompts in the workbench. When the pipeline asks for confirmation, the page should send user input to the running process stdin so CLI and web flows keep the same gates.

The pipeline performs AI intake validation before test plan generation. If the requirement and uploaded materials are too vague to identify a test object or test point, it should stop and show clarification questions instead of inventing a test scope.
