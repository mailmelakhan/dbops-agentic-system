"""Fleet panel: every database, worst first, on the right of every page. Data from health-analytics only."""

import html
import time

import streamlit as st

from dbops_frontend.api.base import ApiError, ServiceUnavailable
from dbops_frontend.clients import health_client
from dbops_frontend.fleet_status import attention_count, db_status, sort_fleet, status_reason, worst_status
from dbops_frontend.format import md_inline, plain_error, status_badge_html
from dbops_frontend.views.common import api_error_banner, service_banner

OPEN_KEY = "fleet_open"
DOWN_KEY = "fleet_down_until"
TIMEOUT_S = 1.0  # the panel is drawn before every page: a hung service must not stall the page
BACKOFF_S = 10.0  # after health-analytics is unreachable, show the banner without calling again for this long
ALARM_STATE = {"ALARM": "⛔ ALARM", "OK": "✅ OK", "INSUFFICIENT_DATA": "⚠ INSUFFICIENT_DATA"}


def render_alarms(identifier: str) -> None:
    st.caption(f"Alarms · {md_inline(identifier)}")
    try:
        alarms = health_client(TIMEOUT_S).alarms(identifier)
    except ServiceUnavailable:
        service_banner("health-analytics")
        return
    except ApiError as err:
        st.warning(plain_error(err))
        return
    if not alarms:
        st.caption("No open alarms.")
    for a in alarms:
        st.markdown(f"{ALARM_STATE.get(a['state'], a['state'])} · **{md_inline(a['name'])}** · "
                    f"{md_inline(a['metric'])} threshold {md_inline(a['threshold'])} · since {md_inline(a['since'])}")
        st.caption(md_inline(a["description"]))


def _toggle_open(identifier: str) -> None:
    if st.session_state.get(OPEN_KEY) == identifier:
        st.session_state.pop(OPEN_KEY, None)
    else:
        st.session_state[OPEN_KEY] = identifier


@st.fragment(run_every=10)
def render() -> None:
    if time.monotonic() < st.session_state.get(DOWN_KEY, 0.0):
        service_banner("health-analytics")
        return
    try:
        fleet = health_client(TIMEOUT_S).fleet()
    except ServiceUnavailable:
        st.session_state[DOWN_KEY] = time.monotonic() + BACKOFF_S
        service_banner("health-analytics")
        return
    except ApiError as err:
        api_error_banner("health-analytics", err)
        return
    databases = fleet["databases"]
    if not databases:
        st.info("No databases yet.")
        return
    worst = worst_status(databases)
    st.markdown(f"**{attention_count(databases)} of {len(databases)} need attention** {status_badge_html(worst)}",
                unsafe_allow_html=True)
    st.caption(f"Data as of {fleet['as_of']}")
    for db in sort_fleet(databases):
        ident = db["identifier"]
        st.button(f"`{ident}`", key=f"fleet_row_{ident}", width="stretch", on_click=_toggle_open, args=(ident,))
        st.markdown(f"{status_badge_html(db_status(db))} {html.escape(status_reason(db))}", unsafe_allow_html=True)
        if st.session_state.get(OPEN_KEY) == ident:
            with st.container(border=True):
                render_alarms(ident)
