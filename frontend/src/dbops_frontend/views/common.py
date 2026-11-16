import streamlit as st

from dbops_frontend.api.base import ApiError
from dbops_frontend.format import plain_error, risk_badge_html

_STYLES = """<style>
[data-testid="stMetricValue"], [data-testid="stTable"], [data-testid="stMarkdownContainer"] table {
  font-variant-numeric: tabular-nums;
}
</style>"""


def inject_styles() -> None:
    st.html(_STYLES)


def service_banner(service: str) -> None:
    st.error(f"{service} unavailable, showing nothing rather than stale data")


def api_error_banner(service: str, err: ApiError) -> None:
    st.error(f"{service} returned an error: {plain_error(err)}")


def risk_badge(tier: str) -> None:
    st.markdown(risk_badge_html(tier), unsafe_allow_html=True)


def sidebar_user() -> None:
    user = st.session_state["user"]
    with st.sidebar:
        st.markdown(f"**{user.display_name}** · {user.role}")
        if st.button("Log out", key="logout"):
            st.session_state.clear()
            st.rerun()


def switch_to(page_key: str) -> None:
    pages = st.session_state.get("pages")
    if pages and page_key in pages:
        st.switch_page(pages[page_key])
