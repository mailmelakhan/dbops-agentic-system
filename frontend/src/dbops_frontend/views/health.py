"""Health (default page): fleet numbers and table, from health-analytics only."""

import pandas as pd
import streamlit as st

from dbops_frontend.api.base import ApiError, ServiceUnavailable
from dbops_frontend.clients import health_client
from dbops_frontend.format import BAND_COLOR, Band, days_band, days_label, plain_error, score_band, score_label
from dbops_frontend.views.common import service_banner, switch_to

COLUMNS = ["Database", "Engine", "Class", "Score", "Days to full", "Alarms"]
ALARM_STATE = {"ALARM": "⛔ ALARM", "OK": "✅ OK", "INSUFFICIENT_DATA": "⚠ INSUFFICIENT_DATA"}


def _worst_first(databases: list[dict]) -> list[dict]:
    return sorted(databases, key=lambda d: (d["health_score"], d["identifier"]))


def fleet_frame(databases: list[dict]) -> pd.DataFrame:
    rows = [[d["identifier"], d["engine"], d["instance_class"], score_label(d["health_score"]),
             days_label(d["storage"]["days_to_full"]), d["open_alarms"]] for d in _worst_first(databases)]
    return pd.DataFrame(rows, columns=COLUMNS)


def _cell_css(band: Band) -> str:
    if band is Band.NEUTRAL:
        return ""
    text = "#111827" if band is Band.AMBER else "#ffffff"
    return f"background-color: {BAND_COLOR[band]}; color: {text}; font-weight: 600"


def style_fleet(df: pd.DataFrame, databases: list[dict]):
    ordered = _worst_first(databases)
    score_css = [_cell_css(score_band(d["health_score"])) for d in ordered]
    days_css = [_cell_css(days_band(d["storage"]["days_to_full"])) for d in ordered]
    styler = df.style.apply(lambda _: score_css, subset=["Score"], axis=0)
    return styler.apply(lambda _: days_css, subset=["Days to full"], axis=0)


def render_alarms(identifier: str) -> None:
    with st.expander(f"Alarms · {identifier}", expanded=True):
        try:
            alarms = health_client().alarms(identifier)
        except ServiceUnavailable:
            service_banner("health-analytics")
            return
        except ApiError as err:
            st.warning(plain_error(err))
            return
        if not alarms:
            st.caption("No open alarms.")
        for a in alarms:
            st.markdown(f"{ALARM_STATE.get(a['state'], a['state'])} · **{a['name']}** · "
                        f"{a['metric']} threshold {a['threshold']} · since {a['since']}")
            st.caption(a["description"])


def _actions() -> None:
    left, right, _ = st.columns([1, 2, 5])
    if left.button("Refresh", key="health_refresh"):
        try:
            health_client().refresh()
            st.toast("Refresh requested")
        except ApiError as err:
            st.info(plain_error(err))
        except ServiceUnavailable:
            service_banner("health-analytics")
    if right.button("Generate health report", key="health_report"):
        try:
            health_client().start_fleet_report()
            st.session_state["audit_view"] = "Reports"
            st.session_state["health_report_started"] = True
        except ApiError as err:
            st.warning(plain_error(err))
        except ServiceUnavailable:
            service_banner("health-analytics")
    if st.session_state.get("health_report_started"):
        st.success("Health report is generating.")
        if st.button("Open Reports", key="health_open_reports"):
            st.session_state.pop("health_report_started", None)
            st.session_state["audit_view"] = "Reports"
            switch_to("audit")


def render() -> None:
    st.header("Health")
    try:
        client = health_client()
        summary = client.summary()
        fleet = client.fleet()
    except ServiceUnavailable:
        service_banner("health-analytics")
        return

    cols = st.columns(4)
    cols[0].metric("Databases", summary["total"])
    cols[1].metric("Unhealthy", summary["unhealthy"])
    cols[2].metric("< 30 days to full", summary["under_30_days_to_full"])
    cols[3].metric("Open alarms", summary["open_alarms"])
    st.caption(f"Data as of {fleet['as_of']} (health-analytics)")
    _actions()

    databases = fleet["databases"]
    if not databases:
        st.info("No databases yet.")
        return
    left, right, _ = st.columns([1, 1, 3])
    env = left.selectbox("Env", ["All", *sorted({d["env"] for d in databases})], key="health_env")
    engine = right.selectbox("Engine", ["All", *sorted({d["engine"] for d in databases})], key="health_engine")
    shown = [d for d in databases
             if (env == "All" or d["env"] == env) and (engine == "All" or d["engine"] == engine)]
    if not shown:
        st.info("No databases match these filters.")
        return

    df = fleet_frame(shown)
    event = st.dataframe(style_fleet(df, shown), on_select="rerun", selection_mode="single-row", hide_index=True,
                         width="stretch", key="health_table")
    rows = event.selection.rows if event and hasattr(event, "selection") else []
    if rows and rows[0] < len(df):
        render_alarms(df.iloc[rows[0]]["Database"])
    else:
        st.caption("Select a row to see that database's alarms.")
