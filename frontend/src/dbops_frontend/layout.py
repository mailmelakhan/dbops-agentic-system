"""Page layout after login: the page on the left (~3/4), the fleet panel on the right (~1/4), unless hidden."""

from collections.abc import Callable

import streamlit as st

from dbops_frontend.views import fleet_panel

PANEL_KEY = "fleet_panel_shown"


def panel_toggle() -> bool:
    st.session_state.setdefault(PANEL_KEY, True)
    with st.sidebar:
        return st.toggle("Show fleet panel", key=PANEL_KEY)


def run_with_panel(run_page: Callable[[], None]) -> None:
    """Draw the panel first, so a page that stops or reruns early still has it beside it."""
    if not panel_toggle():
        run_page()
        return
    main, side = st.columns([3, 1], gap="large")
    with side:
        fleet_panel.render()
    with main:
        run_page()
