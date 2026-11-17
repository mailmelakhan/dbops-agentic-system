# Fleet Panel Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the five minors deferred from the fleet-panel final review.

**Architecture:**
- **Task 1:** a one-line rule change in the pure status module.
- **Task 2:** small edits to the panel view, one code comment in `chat.py`, and one README line.
- No contracts, mock or layout changes.

**Tech Stack:** Python 3.12, Streamlit 1.67, pytest + Streamlit AppTest against the live mock, ruff.

**Spec:** `docs/superpowers/specs/2026-11-16-fleet-panel-design.md`. The five items come from that change's final review (deferred minors, 2026-11-16).

## Global Constraints

- **Identifier format:** the spec's panel contents say "One row per database: the identifier in monospace, a status badge …, and a one-line reason".
- **Storage wording:** "Storage full" when days to full is 0. "Storage full in 1 day" and "Storage full in N days" otherwise. Status thresholds are unchanged.
- **Markdown escaping:** every API-supplied string that goes into markdown is escaped with `md_inline`.
- **Tooling:** ruff line length is 120. `uv run ruff check .` must be clean at the end of every task.

## Review Focus

1. **A non-numeric `threshold` from a real health-analytics** (contract type drift) must still render, escaped, not crash. Pinned by `test_alarm_fields_escaped` (Task 2), which passes a string threshold.
2. **Row keys must not change** when the label gains backticks. Session state (`fleet_open`) and tests key on `fleet_row_<identifier>`. Pinned by `test_rows_worst_first` and `test_row_click_toggles_alarms` (Task 2), which still click by key.

---

### Task 1: Negative days to full reads "Storage full"

**Files:**
- Modify: `frontend/src/dbops_frontend/fleet_status.py` (`_storage_text`)
- Test: `frontend/tests/test_fleet_status.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `status_reason` returns "Storage full" for any `days_to_full <= 0`. Signatures are unchanged.

- [ ] **Step 1: Write the failing test.** In `test_fleet_status.py`, add:

```python
def test_reason_negative_days():
    assert status_reason(db("a", 90, -3)) == "Storage full"
    assert db_status(db("a", 90, -3)) is Status.CRITICAL
```

- [ ] **Step 2: Run it.** Run `uv run pytest frontend/tests/test_fleet_status.py -q`. Expected: FAIL, `'Storage full in -3 days' != 'Storage full'`.
- [ ] **Step 3: Implement.** In `_storage_text`, change the condition `days == 0` to `days <= 0`.
- [ ] **Step 4: Run it again.** Run `uv run pytest frontend/tests/test_fleet_status.py -q && uv run ruff check .`. Expected: PASS.
- [ ] **Step 5: Commit.** Run `git commit -m "fix(frontend): negative days to full reads 'Storage full'"`.

### Task 2: Monospace identifiers, escaped alarm fields, chat comment, README

**Files:**
- Modify:
  - `frontend/src/dbops_frontend/views/fleet_panel.py` (row button label; `render_alarms`)
  - `frontend/src/dbops_frontend/views/chat.py:197` (comment only)
  - `README.md` (one line under "Mock behaviour worth knowing")
- Test: `frontend/tests/test_fleet_panel.py`

**Interfaces:**
- Consumes: `md_inline(text) -> str` from `format.py`. It already calls `str()` on its input.
- Produces:
  - Row buttons keep key `fleet_row_<identifier>`.
  - The label becomes `` f"`{identifier}`" ``, which Streamlit renders as inline code, in JetBrains Mono through the theme's codeFont.

- [ ] **Step 1: Write the failing tests.**
  - In `test_fleet_panel.py`, change the `rows()` helper to strip the backticks: `` return [b.label.strip("`") for b in at.button if b.key.startswith("fleet_row_")] ``.
  - Then add:

```python
def test_row_labels_are_monospace(panel):
    at = panel()
    at.run()
    labels = [b.label for b in at.button if b.key.startswith("fleet_row_")]
    assert labels and all(lbl.startswith("`") and lbl.endswith("`") for lbl in labels)


def test_alarm_fields_escaped(panel, monkeypatch):
    alarm = {"name": "a", "metric": "m", "state": "ALARM", "threshold": "*90*", "since": "[x](http://e.x)",
             "description": "d"}
    monkeypatch.setattr(HealthApi, "alarms", lambda self, ident: [alarm])
    at = panel(func="render_alarms", args=("prod-orders-db",))
    at.run()
    line = next(m.value for m in at.markdown if m.value.startswith("⛔ ALARM"))
    assert r"\*90\*" in line and r"\[x\]" in line
```

- [ ] **Step 2: Run them.** Run `uv run pytest frontend/tests/test_fleet_panel.py -q`. Expected: the two new tests FAIL. The labels have no backticks, and the threshold and since values are unescaped. All existing tests still PASS.
- [ ] **Step 3: Implement.**
  - **`fleet_panel.py`, row button:** set the label to `` f"`{ident}`" `` and keep the key unchanged.
  - **`fleet_panel.py`, `render_alarms`:** wrap the threshold and since values as `md_inline(a['threshold'])` and `md_inline(a['since'])`.
  - **`chat.py:197`:** the comment becomes `# st.bottom: pinned to the window bottom, outside the page/panel split`.
  - **README:** add this bullet under "Mock behaviour worth knowing for a demo": `- The old Health page is gone; a bookmarked /health URL shows "page not found" and opens Chat.`
- [ ] **Step 4: Run the full suite.** Run `uv run pytest -q && uv run ruff check .`. Expected: everything passes and ruff is clean.
- [ ] **Step 5: Commit.** Run `git commit -m "fix(frontend): monospace panel identifiers, escaped alarm fields; chat comment and README note"`.
