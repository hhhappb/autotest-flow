# CDP Element Evidence Gate

Use this reference before writing or changing UI selectors, page-object operations, click/read/assert logic, or test flow.

## Goal

Build selectors from real page evidence, not guesses. The evidence may come from CDP, a browser automation inspector, Playwright locator inspection, or user-provided DevTools/F12 DOM.

## When This Gate Applies

Apply this gate when the task needs any of the following:

- Click, fill, select, hover, drag, read, or assert a page element.
- Add or change a selector.
- Add or change a page-object method.
- Validate state changes in custom controls such as switches, radio groups, dropdowns, tabs, modals, tree menus, canvas-backed widgets, or hidden form controls.
- Assert product behavior based on text, value, class, checked/selected state, visibility, or computed DOM state.

For pure API, unit, or non-UI test work, note that this gate does not apply.

## Evidence To Capture

For each required element, capture a compact evidence record:

| Field | Meaning |
|---|---|
| Element purpose | What the test does with it: click, read, set value, assert, wait |
| Page/location | Page, modal, panel, tab, or component where the element appears |
| DOM evidence | Minimal `outerHTML` or relevant parent/child fragment |
| Stable attributes | `id`, `name`, `value`, `role`, `aria-*`, `data-*`, stable class, `checked`, `selected`, `disabled` |
| State change | Before/after DOM, class, value, checked, selected, visibility, or text |
| Candidate selector | Short selector candidates based on stable attributes |
| Chosen selector | Final selector to use |
| Reason | Why this selector is stable and scoped enough |

## Capture Procedure

1. Navigate to the exact page state required by the test.
2. Identify each element that will be clicked, filled, selected, read, or asserted.
3. Capture the minimal DOM around the element and any hidden input/select backing a custom widget.
4. For stateful controls, capture before and after state by performing the action once in a safe environment.
5. Prefer selectors from stable attributes in this order: `id`, `name`, `data-*`, accessible role/name, stable form structure, stable class plus attribute.
6. Keep the chosen selector as narrow as needed but no more complex than the evidence requires.
7. Present the evidence summary before proposing code changes.

## Do Not Guess

Do not invent:

- DOM hierarchy.
- Hidden input/select names.
- Clickable parent/ancestor behavior.
- Class names or selected-state classes.
- Backup selector branches.
- Body text scans.
- JS synchronization or fallback clicks.
- Broad ancestor/sibling inference.

If the real element is unclear, ask the user for F12/DevTools DOM or use an available browser/CDP tool. Stop before code changes.

## Output Template

Use this compact table in chat, `codex_task.md`, or implementation notes:

| Element | Purpose | DOM evidence | State change | Chosen selector | Reason |
|---|---|---|---|---|---|
| Example switch | Toggle feature on/off | `<input name="featureFlag" value="1"> ...` | `checked=false -> true` | `input[name="featureFlag"][value="1"]` | Stable form name/value |

When evidence is incomplete, use:

```text
Element evidence blocker:
- Missing element:
- Needed evidence:
- Why it blocks code:
- Requested user action:
```

## Review Checklist

- Does every new or changed selector trace to real DOM evidence?
- Did custom controls include their hidden form control or state class?
- Did stateful interactions include before/after evidence?
- Did the plan avoid unverified fallbacks and body text scans?
- Did code-change planning wait until evidence was collected or explicitly provided?
