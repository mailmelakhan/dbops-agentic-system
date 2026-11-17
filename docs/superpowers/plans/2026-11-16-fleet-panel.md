# Fleet Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Health page with an always-visible, worst-first fleet panel on the right of every page, make Chat the landing page, and rename the demo users everywhere.

**Architecture:** Pure status logic lives in a new `fleet_status.py`. A new `views/fleet_panel.py` renders the panel as a 10-second fragment. A new `layout.py` splits the screen once in `app.py` (page area ¾, panel ¼) and owns the Show/Hide toggle, so pages never know about the panel. Chat moves its input to `st.bottom` so the input stays pinned to the bottom of the window inside the column. Contracts and mock behaviour are unchanged.

**Tech Stack:** Python 3.12, Streamlit 1.67 (`st.fragment`, `st.bottom`, `st.toggle`, `st.navigation`), pytest + Streamlit AppTest against the live uvicorn mock, ruff.

**Spec:** `docs/superpowers/specs/2026-11-16-fleet-panel-design.md` (amends `docs/superpowers/specs/2026-11-14-db-ops-frontend-design.md`)

## Global Constraints

- **Status rule:**
  - CRITICAL means score < 50 or days to full ≤ 14.
  - WARNING means score 50–79, days to full 15–30, or one or more open alarms.
  - Anything else is OK.
- **Sort order:** status (CRITICAL, WARNING, OK), then score with the lowest first, then identifier.
- **Panel:**
  - It sits on the right of every page and is shown by default. The toggle is labelled "Show fleet panel" and sits in the left sidebar under the user box.
  - It refreshes every 10 seconds.
  - Its header reads "N of M need attention", where N counts the CRITICAL and WARNING databases. Next to it is a badge with the worst status present.
  - Below the header: "Data as of <time>".
- **Colour is never the only signal.** Badges show the status word. Colours follow spec §8.2: red `#dc2626` with white text, amber `#f59e0b` with dark text `#111827`, green `#16a34a` with white text.
- **Clickable things are built-in Streamlit controls only.** No styled text pretending to be a button.
- **health-analytics unreachable:** the panel shows `health-analytics unavailable, showing nothing rather than stale data` (existing `service_banner`) and the page beside it keeps working.
- **Chat is the default page.** The Health page and its Refresh button and env/engine filters are removed. "Generate health report" moves to the Reports tab.
- **Contracts, the mock's behaviour and its 14 seeded databases are unchanged.**
- **Demo user rename:** priya → lakhan, arjun → ankit, sam → geeta, rita → amit (case-preserving: Priya → Lakhan, PRIYA → LAKHAN). Historical docs under `docs/superpowers/plans/` and the 2026-11-14 spec text other than §5 are left as written.
- **Tooling:** ruff line length is 120. `uv run ruff check .` must be clean at the end of every task.

## Review Focus

1. **A page that stops early.** The panel must still be drawn when a page calls `st.rerun()`, `st.stop()` or `switch_page`. `run_with_panel` therefore draws the panel *before* it runs the page. Pinned by `test_panel_drawn_before_page_runs` (Task 4).
2. **health-analytics down while the user is on Chat or Approvals.** The page renders normally and only the panel shows the banner. Pinned by `test_page_works_when_health_down` (Task 4).
3. **Edge storage values:**
   - `days_to_full` of null never mentions storage.
   - 1 reads "1 day" and 0 reads "Storage full".
   - An alarm on an otherwise healthy database is WARNING, not OK.
   - Pinned by `test_reason_edge_cases` and `test_alarm_only_is_warning` (Task 2).
4. **Chat input loses its bottom pin inside the column.** It must keep `is_auto_positioned_at_bottom`. Pinned by `test_chat_input_pinned_with_panel` (Task 4).
5. **Hiding the panel.** Hidden means no fleet calls and a full-width page. Showing it again restores it. Pinned by `test_toggle_hides_and_shows_panel` (Task 4).

---

### Task 1: Rename the demo users

**Files:**
- Modify (mechanical, whole-word, case-preserving):
  - `mock_backend/src/mock_backend/seed.py`
  - `README.md`
  - `tests/test_contracts.py`
  - `mock_backend/tests/*.py`
  - `frontend/tests/*.py`
- Commit as-is: the user's existing uncommitted edits to `config/users.yaml` and to §5 of `docs/superpowers/specs/2026-11-14-db-ops-frontend-design.md` (do not commit `.DS_Store`).

**Interfaces:**
- Produces: test fixtures `USERS = [("lakhan", "PRIVILEGED"), ("ankit", "PRIVILEGED"), ("geeta", "SAFE-WRITE"), ("amit", "READ")]` in both conftests. Later tasks log in as these.

- [ ] **Step 1: Confirm the failing test.** Run `uv run pytest tests/test_contracts.py -q`. Expected: FAIL in `test_users_file_has_four_demo_users`, because `users.yaml` already has the new names.
- [ ] **Step 2: Substitute.**
  - Use a small throwaway script in the scratchpad (not committed). With regex `\b<old>\b`, replace each name in its lower, Title and UPPER forms (priya/Priya/PRIYA → lakhan/Lakhan/LAKHAN, and so on) across the files listed above.
  - In `README.md`, the demo-user table becomes:

    | User | Role | Use in the demo |
    |---|---|---|
    | `lakhan` | PRIVILEGED | approver |
    | `ankit` | PRIVILEGED | DBA lead; requests resizes (shows the no-self-approval rule) |
    | `geeta` | SAFE-WRITE | tags and snapshots |
    | `amit` | READ | read-only viewer |
- [ ] **Step 3: Verify nothing old remains.** Run `grep -rnwiE "priya|arjun|sam|rita" --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=docs .`. Expected: no output.
- [ ] **Step 4: Run the full suite.** Run `uv run pytest -q && uv run ruff check .`. Expected: all pass.
  - If a test fails only because it expected the old alphabetical order of usernames (the new sorted order is amit, ankit, geeta, lakhan), update the expected value, not the code, and ledger it.
- [ ] **Step 5: Commit.**
  ```bash
  git add config/users.yaml docs/superpowers/specs/2026-11-14-db-ops-frontend-design.md README.md mock_backend tests frontend/tests
  git commit -m "chore: rename demo users to lakhan, ankit, geeta, amit"
  ```

### Task 2: Fleet status logic and status badge

**Files:**
- Create: `frontend/src/dbops_frontend/fleet_status.py`
- Modify: `frontend/src/dbops_frontend/format.py` (add a status badge and share the badge markup with `risk_badge_html`)
- Test: `frontend/tests/test_fleet_status.py`

**Interfaces:**
- Consumes:
  - `score_band(score) -> Band`
  - `days_band(days) -> Band`
  - Both come from `format.py`.
- Produces, in `fleet_status.py`:
  - `class Status(StrEnum)` with the members `CRITICAL`, `WARNING` and `OK`. Each value equals its name.
  - `db_status(db: dict) -> Status`
  - `status_reason(db: dict) -> str`
  - `sort_fleet(dbs: list[dict]) -> list[dict]`
  - `attention_count(dbs: list[dict]) -> int`
  - `worst_status(dbs: list[dict]) -> Status | None`. Returns `None` for an empty list.
- Produces, in `format.py`:
  - `status_badge_html(status: str) -> str`
  - Both badge functions use `_badge_html(text, bg, fg)`.

- [ ] **Step 1: Write the failing tests** in `frontend/tests/test_fleet_status.py`:

```python
from dbops_frontend.fleet_status import Status, attention_count, db_status, sort_fleet, status_reason, worst_status
from dbops_frontend.format import risk_badge_html, status_badge_html


def db(ident, score, days, alarms=0):
    return {"identifier": ident, "health_score": score, "storage": {"days_to_full": days}, "open_alarms": alarms}


def test_status_rule():
    assert db_status(db("a", 42, 9, 2)) is Status.CRITICAL
    assert db_status(db("a", 90, 14)) is Status.CRITICAL
    assert db_status(db("a", 49, None)) is Status.CRITICAL
    assert db_status(db("a", 79, 200)) is Status.WARNING
    assert db_status(db("a", 90, 30)) is Status.WARNING
    assert db_status(db("a", 80, 31)) is Status.OK
    assert db_status(db("a", 95, None)) is Status.OK


def test_alarm_only_is_warning():
    assert db_status(db("a", 95, None, 1)) is Status.WARNING


def test_reason_most_serious_first():
    assert status_reason(db("a", 42, 9, 2)) == "Storage full in 9 days · Score 42 · 2 alarms"
    assert status_reason(db("a", 38, None, 1)) == "Score 38 · 1 alarm"
    assert status_reason(db("a", 68, 27, 1)) == "Storage full in 27 days · Score 68 · 1 alarm"
    assert status_reason(db("a", 70, 9)) == "Storage full in 9 days · Score 70"   # red storage before amber score
    assert status_reason(db("a", 91, 120)) == "Score 91"


def test_reason_edge_cases():
    assert status_reason(db("a", 90, 1)) == "Storage full in 1 day"
    assert status_reason(db("a", 90, 0)) == "Storage full"
    assert "Storage" not in status_reason(db("a", 30, None))
    assert status_reason(db("a", 95, None, 1)) == "1 alarm"


def test_sort_worst_first():
    dbs = [db("ok-b", 90, None), db("warn", 70, None), db("ok-a", 90, None), db("crit-hi", 45, None),
           db("crit-lo", 30, None)]
    assert [d["identifier"] for d in sort_fleet(dbs)] == ["crit-lo", "crit-hi", "warn", "ok-a", "ok-b"]


def test_counts_and_worst():
    dbs = [db("a", 90, None), db("b", 70, None), db("c", 30, None)]
    assert attention_count(dbs) == 2 and worst_status(dbs) is Status.CRITICAL
    assert attention_count([]) == 0 and worst_status([]) is None
    assert worst_status([db("a", 90, None)]) is Status.OK


def test_status_badge_html():
    crit, warn, ok = (status_badge_html(s) for s in ("CRITICAL", "WARNING", "OK"))
    assert "#dc2626" in crit and ">CRITICAL<" in crit and "#ffffff" in crit
    assert "#f59e0b" in warn and "#111827" in warn and ">WARNING<" in warn
    assert "#16a34a" in ok and ">OK<" in ok
    assert "#dc2626" in risk_badge_html("HIGH") and ">HIGH<" in risk_badge_html("HIGH")
```

- [ ] **Step 2: Run the tests.** Run `uv run pytest frontend/tests/test_fleet_status.py -q`. Expected: FAIL with `ModuleNotFoundError: dbops_frontend.fleet_status`.
- [ ] **Step 3: Implement.**
  - **`db_status`:** take the worse of `score_band` and `days_band`. Red maps to CRITICAL and amber to WARNING. Then raise OK to WARNING when `open_alarms > 0`.
  - **`status_reason`:** build up to three causes, in this order:
    - storage, if the days band is red or amber: "Storage full in N days", "… 1 day", or "Storage full" at 0;
    - score, if the score band is red or amber: "Score N";
    - alarms, if there are any: "1 alarm" or "N alarms". Alarms always rank amber.
  - Then stable-sort the causes so red ones come before amber ones, and join them with " · ". With no causes, return "Score N".
  - **`sort_fleet`:** sort on the key `(rank, health_score, identifier)`.
  - **`format.py`:** add `STATUS_STYLE = {"CRITICAL": ("#dc2626", WHITE), "WARNING": ("#f59e0b", DARK_TEXT), "OK": ("#16a34a", WHITE)}`.
- [ ] **Step 4: Run the tests again.** Run `uv run pytest frontend/tests/test_fleet_status.py frontend/tests/test_format.py -q && uv run ruff check .`. Expected: PASS.
- [ ] **Step 5: Commit.** Run `git commit -m "feat(frontend): fleet status rule, reasons and status badge"` with the three files.

### Task 3: The fleet panel view

**Files:**
- Create: `frontend/src/dbops_frontend/views/fleet_panel.py`
- Test: `frontend/tests/test_fleet_panel.py`

**Interfaces:**
- Consumes:
  - Task 2's functions.
  - `health_client()`, `service_banner`, `api_error_banner`, `md_inline`, `plain_error`.
  - The `view_app` fixture, which calls `views.fleet_panel.render()`.
- Produces:
  - `render() -> None`, decorated with `@st.fragment(run_every=10)`.
  - `render_alarms(identifier: str) -> None`.
  - Session key `fleet_open`, which holds the identifier whose alarms are open, or is absent.
  - Row buttons with key `fleet_row_<identifier>` and the identifier as their label.

**Layout of `render()`, top to bottom:**
1. A header line: `**N of M need attention**` and `status_badge_html(worst)`, as one `st.markdown(..., unsafe_allow_html=True)`.
2. A caption: `Data as of <as_of>`.
3. For each database in `sort_fleet` order:
   - `st.button(identifier, key=f"fleet_row_{id}", width="stretch")`. A click toggles `fleet_open` between this identifier and absent.
   - Under the button, an `st.markdown` holding the status badge followed by the HTML-escaped reason.
   - If this row is open, `render_alarms(id)` inside `st.container(border=True)`.
4. Empty fleet: `st.info("No databases yet.")` with no header.
5. `ServiceUnavailable` shows `service_banner("health-analytics")`. `ApiError` shows `api_error_banner("health-analytics", err)`.

**`render_alarms`:** keep the old Health page's alarm lines and its `No open alarms.` caption. Move the logic out of `views/health.py`, minus the expander, and give it a caption heading `Alarms · <identifier>`.

- [ ] **Step 1: Write the failing tests:**

```python
import pytest
from dbops_frontend.api.health import HealthApi

ORDER = ["legacy-reports-db", "prod-orders-db", "prod-analytics-db", "dev-sandbox-db", "staging-legacy-db",
         "staging-users-db", "prod-search-db", "dev-mysql-db", "prod-inventory-db", "staging-orders-db",
         "prod-users-db", "staging-payments-db", "prod-payments-db", "dev-orders-db"]


@pytest.fixture
def panel(view_app):
    def make(**kw):
        kw.setdefault("logged_in", "amit")
        return view_app("fleet_panel", **kw)
    return make


def rows(at):
    return [b.label for b in at.button if b.key.startswith("fleet_row_")]


def test_header_and_as_of(panel):
    at = panel(); at.run()
    assert not at.exception
    assert "6 of 14 need attention" in at.markdown[0].value and ">CRITICAL<" in at.markdown[0].value
    assert at.caption[0].value.startswith("Data as of ")


def test_rows_worst_first(panel):
    at = panel(); at.run()
    assert rows(at) == ORDER


def test_row_shows_badge_and_reason(panel):
    at = panel(); at.run()
    texts = [m.value for m in at.markdown]
    assert any(">CRITICAL<" in t and "Storage full in 9 days · Score 42 · 2 alarms" in t for t in texts)


def test_row_click_toggles_alarms(panel):
    at = panel(); at.run()
    at.button(key="fleet_row_prod-orders-db").click().run()
    assert at.session_state["fleet_open"] == "prod-orders-db"
    assert any(m.value.startswith("⛔ ALARM") for m in at.markdown)
    at.button(key="fleet_row_prod-orders-db").click().run()
    assert "fleet_open" not in at.session_state
    assert not any(m.value.startswith("⛔ ALARM") for m in at.markdown)


def test_unreachable(panel):
    at = panel(health_url="http://127.0.0.1:9"); at.run()
    assert not at.exception
    assert at.error[0].value == "health-analytics unavailable, showing nothing rather than stale data"
    assert rows(at) == []


def test_empty_fleet(panel, monkeypatch):
    monkeypatch.setattr(HealthApi, "fleet", lambda self, **k: {"as_of": "2026-11-16T10:00:00Z", "databases": []})
    at = panel(); at.run()
    assert at.info[0].value == "No databases yet." and rows(at) == []


def test_alarms_empty(panel):
    at = panel(func="render_alarms", args=("dev-orders-db",)); at.run()
    assert at.caption[0].value == "Alarms · dev-orders-db" and at.caption[1].value == "No open alarms."
```

- [ ] **Step 2: Run the tests.** Run `uv run pytest frontend/tests/test_fleet_panel.py -q`. Expected: FAIL with a module-not-found error for `dbops_frontend.views.fleet_panel`.
- [ ] **Step 3: Implement `views/fleet_panel.py`** as laid out above.
- [ ] **Step 4: Run the tests again.** Run `uv run pytest frontend/tests/test_fleet_panel.py -q && uv run ruff check .`. Expected: PASS.
- [ ] **Step 5: Commit.** Run `git commit -m "feat(frontend): fleet panel view"`.

### Task 4: Shell layout, toggle, Chat as default, remove Health

**Files:**
- Create: `frontend/src/dbops_frontend/layout.py`
- Modify:
  - `frontend/app.py`
  - `frontend/src/dbops_frontend/navigation.py`
  - `frontend/src/dbops_frontend/views/chat.py:197` (chat input moves to `st.bottom`)
  - `frontend/tests/conftest.py` (`view_app(..., with_panel=False)`)
  - `frontend/tests/test_shell.py`
  - `frontend/tests/test_review_fixes.py:17`
- Delete:
  - `frontend/src/dbops_frontend/views/health.py`
  - `frontend/tests/test_health_view.py`
- Test: `frontend/tests/test_layout.py`

**Interfaces:**
- Consumes: `fleet_panel.render()` from Task 3.
- Produces, in `layout.py`:
  - `PANEL_KEY = "fleet_panel_shown"`.
  - `panel_toggle() -> bool`. Draws `st.toggle("Show fleet panel", key=PANEL_KEY)` in the sidebar, after setting the session default to `True`.
  - `run_with_panel(run_page: Callable[[], None]) -> None`.
- **When the panel is shown,** `run_with_panel` does the following:
  - calls `panel_toggle()`;
  - creates `main, side = st.columns([3, 1], gap="large")`;
  - renders `fleet_panel.render()` in `side` **first**, then `run_page()` in `main`.
- **When the panel is hidden,** it just calls `run_page()`.
- **`app.py`:** calls `common.sidebar_user()`, then `run_with_panel(build_navigation(pending_count()).run)`.
- **`navigation.build_navigation`:**
  - Pages are `chat` (`default=True`), `approvals` and `audit`. The `health` page is removed.
  - The `session_state["pages"]` keys become `["chat", "approvals", "audit"]`.
- **conftest:** the `view_app` fixture gains a `with_panel: bool = False` keyword. When it is true, the test script calls `run_with_panel(lambda: <view>.<func>(*args))`. This is test-utility code only.

- [ ] **Step 1: Write the failing tests.** Create `frontend/tests/test_layout.py`:

```python
import pytest
from dbops_frontend.views import approvals


def panel_shown(at):
    return any("need attention" in m.value for m in at.markdown)


@pytest.mark.parametrize("module,header", [("chat", "Chat"), ("approvals", "Approvals"),
                                           ("audit_reports", "Audit & reports")])
def test_panel_on_every_page(view_app, module, header):
    at = view_app(module, logged_in="amit", with_panel=True); at.run()
    assert not at.exception
    assert at.header[0].value == header and panel_shown(at)


def test_toggle_hides_and_shows_panel(view_app):
    at = view_app("chat", logged_in="amit", with_panel=True); at.run()
    assert at.toggle(key="fleet_panel_shown").value is True
    at.toggle(key="fleet_panel_shown").set_value(False).run()
    assert not panel_shown(at) and at.header[0].value == "Chat"
    assert not [b for b in at.button if b.key and b.key.startswith("fleet_row_")]   # no fleet calls drawn
    at.toggle(key="fleet_panel_shown").set_value(True).run()
    assert panel_shown(at)


def test_page_works_when_health_down(view_app):
    at = view_app("approvals", logged_in="lakhan", with_panel=True, health_url="http://127.0.0.1:9"); at.run()
    assert not at.exception and at.header[0].value == "Approvals"
    assert at.error[0].value == "health-analytics unavailable, showing nothing rather than stale data"
    assert any(e.label.startswith("CR-") for e in at.expander)


def test_panel_drawn_before_page_runs(view_app, monkeypatch):
    def stops():
        import streamlit as st
        st.stop()
    monkeypatch.setattr(approvals, "render", stops)
    at = view_app("approvals", logged_in="amit", with_panel=True); at.run()
    assert panel_shown(at)


def test_chat_input_pinned_with_panel(view_app):
    at = view_app("chat", logged_in="amit", with_panel=True); at.run()
    assert at.chat_input[0].proto.is_auto_positioned_at_bottom
```

  Then change `test_shell.py`:
  - Rename `test_login_shows_health_and_user` to `test_login_lands_on_chat_with_panel`.
  - Assert `at.header[0].value == "Chat"`, that `panel_shown(at)` is true, and that `list(at.session_state["pages"]) == ["chat", "approvals", "audit"]`.
  - In `test_review_fixes.py:17`, the parametrize list becomes `["fleet_panel", "chat", "approvals", "audit_reports"]`.
- [ ] **Step 2: Run the tests.** Run `uv run pytest frontend/tests/test_layout.py frontend/tests/test_shell.py -q`. Expected: FAIL. `view_app` does not accept `with_panel`, and the shell lands on Health.
- [ ] **Step 3: Implement.**
  - Write `layout.py` and update `app.py`, `navigation.py` and the conftest as above.
  - In `chat.render`, change `st.chat_input(...)` to `st.bottom.chat_input(...)`, with the same arguments and key.
  - Delete `views/health.py` and `test_health_view.py`. Its alarm tests now live in Task 3.
  - Remove the health import from `navigation.py`.
- [ ] **Step 4: Run the full frontend suite.** Run `uv run pytest frontend -q && uv run ruff check .`. Expected: all pass, including the existing chat tests that use `at.chat_input[0]`.
- [ ] **Step 5: Commit.** Run `git commit -m "feat(frontend): fleet panel on every page; Chat is the landing page; remove Health page"`.

### Task 5: "Generate health report" on the Reports tab, plus docs

**Files:**
- Modify:
  - `frontend/src/dbops_frontend/views/audit_reports.py` (`_reports`)
  - `README.md`
  - `docs/superpowers/specs/2026-11-14-db-ops-frontend-design.md` (a pointer line only)
- Test: `frontend/tests/test_audit_reports_view.py`

**Interfaces:**
- Consumes:
  - `health_client().start_fleet_report() -> str`
  - `service_banner`
  - `plain_error`
- Produces: a button with key `reports_generate_health` and label "Generate health report", drawn at the top of the Reports view before the list.
  - On success it calls `st.rerun()`, so the new generating report appears and polling starts.
  - `ServiceUnavailable` shows `service_banner("health-analytics")`, and the list still renders.
  - `ApiError` shows `st.warning(plain_error(err))`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_generate_health_report_from_reports(view_app):
    at = view_app("audit_reports", logged_in="amit", session={"audit_view": "Reports"}); at.run()
    at.button(key="reports_generate_health").click().run()
    assert not at.exception
    assert any("fleet_health · ⏳ generating…" in m.value for m in at.markdown)


def test_generate_health_report_health_down(view_app):
    at = view_app("audit_reports", logged_in="amit", session={"audit_view": "Reports"},
                  health_url="http://127.0.0.1:9"); at.run()
    at.button(key="reports_generate_health").click().run()
    assert at.error[0].value == "health-analytics unavailable, showing nothing rather than stale data"
    assert any("restore_test" in m.value for m in at.markdown)   # seeded reports still listed
```

- [ ] **Step 2: Run the tests.** Run `uv run pytest frontend/tests/test_audit_reports_view.py -q`. Expected: FAIL, because there is no `reports_generate_health` button.
- [ ] **Step 3: Implement the button** in `_reports()`.
- [ ] **Step 4: Update the docs.**
  - **README:** the frontend row reads "The Streamlit app: Chat, Approvals, Audit & reports, with a fleet health panel on every page". Add a mock-behaviour bullet: "The fleet panel shows 6 of 14 databases needing attention; legacy-reports-db and prod-orders-db are CRITICAL."
  - **2026-11-14 spec:** under `Status:` add `Amended: 2026-11-16-fleet-panel-design.md replaces the Health page (§8.3) with a fleet panel on every page and makes Chat the default (§8.1).`
- [ ] **Step 5: Verify.** Run `uv run pytest -q && uv run ruff check .`. Expected: the whole suite passes and ruff is clean.
- [ ] **Step 6: Commit.** Run `git commit -m "feat(frontend): generate health report from Reports; docs for fleet panel"`.
