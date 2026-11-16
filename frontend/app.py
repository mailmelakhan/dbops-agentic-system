"""DB Ops Streamlit entry point: login gate, then st.navigation over the four pages."""

import streamlit as st
from dbops_frontend.navigation import build_navigation, pending_count
from dbops_frontend.views import common, login

st.set_page_config(page_title="DB Ops", page_icon="🗄️", layout="wide")
common.inject_styles()

if "user" not in st.session_state:
    login.render()
    st.stop()

common.sidebar_user()
build_navigation(pending_count()).run()
