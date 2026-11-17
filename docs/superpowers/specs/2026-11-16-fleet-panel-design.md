# DB Ops: Fleet Panel Replaces the Health Page: Design

Date: 2026-11-16
Status: Approved 2026-11-16
Size: small change to part 1 (frontend only). Amends [2026-11-14-db-ops-frontend-design.md](2026-11-14-db-ops-frontend-design.md): §2 (goals), §8.1, §8.3 and §8.6.

## Intent and why

A problem database should be impossible to overlook. Instead of a Health page you have to visit, the fleet's state sits in a panel on the right of **every** page, with the worst databases at the top. (Decided by user.)

## Goals, non-goals, anti-goals

**Goals**
- **Chat is the landing page** after login. (User)
- **The Health page is removed.** The panel replaces it. (User)
- **A fleet panel on the right of Chat, Approvals and Audit & reports**, open by default, which can be hidden and shown. (User)
- **Sorted worst first:** CRITICAL on top, then WARNING, then OK. (User)

**Non-goals (for now)**
- CPU sparklines or other metric history. These would need a new health-analytics endpoint. (User: "without sparkline is fine for now")
- The scrolling ticker and suggested-question chips from the reference image.

**Anti-goals**
- A page that renders without the panel (unless the user hid it).
- Colour as the only signal: every row shows its status as a word, plus a reason.

## Approaches

| | Approach | Trade-off | Outcome |
|---|---|---|---|
| **A** | **Shared layout in `app.py`:** split the screen into page area (~¾) and panel (~¼) before the page runs. Pages don't know the panel exists. | Inside a column the chat input loses its pin to the bottom of the window; handled by rendering the chat input outside the split. | **Chosen by user** |
| B | The list goes in Streamlit's left sidebar under the page links | Most robust, but it's on the left, and hiding it hides navigation too | Rejected |
| C | Each page adds the panel itself | Repeated layout code; a new page can forget it, which breaks "always visible" | Ruled out |

## The design

**Layout.** `app.py` builds the split once, after login and before `st.navigation(...).run()`: the page runs in the left column, the panel in the right. When the panel is hidden, the page runs full width. Before login there is no panel.

**Hide/Show.** A toggle, "Show fleet panel", at the top of the left sidebar, under the user box. It's on by default and kept in `st.session_state`. A browser refresh means logging in again, which turns it back on. *(My call: it's in the sidebar so it's still reachable while the panel is hidden.)*

**Chat input.** On the Chat page, the chat input is drawn outside the column split, so it stays pinned to the bottom of the window. It spans the full width, including under the panel. *(Proposed in the conversation, accepted with A.)*

**Status per database** *(my call)*. The worse of three signals, all from the existing `GET /fleet`:

| Status | When |
|---|---|
| CRITICAL | score < 50, or days to full ≤ 14 |
| WARNING | score 50–79, or days to full 15–30, or one or more open alarms |
| OK | otherwise |

These are the score and days-to-full bands already in spec §7.3. "Any open alarm is at least WARNING" is new: an alarm on an otherwise healthy database shouldn't sit among the OKs.

**Sort order.** By status (CRITICAL, WARNING, OK), then by score (lowest first), then by identifier.

**Panel contents (top to bottom)**
- Header "N of M need attention", where N counts the CRITICAL and WARNING databases, plus an overall badge with the worst status present.
- "Data as of <time>".
- One row per database: the identifier in monospace, a status badge (colours from spec §8.2: red, amber, green), and a one-line reason with the most serious cause first. Examples: "Storage full in 9 days · 2 alarms", "Score 38 · 2 alarms", "Score 92". If days to full is null, the reason doesn't mention storage.
- **Clicking a row** expands that database's alarms (`GET /fleet/{identifier}/alarms`) inside the panel. This keeps the alarm detail that the Health page had.

**Refresh.** The panel is a fragment that reloads `GET /fleet` every 10 seconds *(my call)*. Re-sorting happens on every reload, so a database turning critical moves to the top without a click.

**Errors.** If health-analytics is unreachable, the panel shows "health-analytics unavailable" in place of the list, never stale data (spec §8.7), and the page beside it keeps working.

**What moves or goes with the Health page**
- "Generate health report" moves to the Reports tab of Audit & reports, next to the reports list, and behaves as before.
- The Refresh button and the env/engine filters are dropped. The panel refreshes itself and shows every database.
- `POST /refresh` stays in the contract, unused by the frontend for now.

**Unchanged.** All three contracts, the mock backend and its 14 seeded databases.

## Left to the builder
- How the row click and expansion are implemented in Streamlit (an expander per row, or a selectable list).
- The exact column ratio and badge markup, within spec §8.2's visual rules.
- Tests: the panel renders on each page, the sort order, the status rule, the hidden state, and an unreachable health-analytics.
- Removing `views/health.py`, or reusing its parts (alarms rendering, bands) in the panel.

## Also in this change: demo user rename (user)
`config/users.yaml` and spec §5 now name the demo users lakhan (PRIVILEGED, approver), ankit (PRIVILEGED, DBA Lead), geeta (SAFE-WRITE) and amit (READ). Every other place that still uses the old names (priya → lakhan, arjun → ankit, sam → geeta, rita → amit) is updated to match: the mock's seed data, the README, and the tests.
