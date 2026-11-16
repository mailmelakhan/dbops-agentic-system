import streamlit as st

from dbops_frontend.auth import authenticate, load_users
from dbops_frontend.config import load_settings


def render() -> None:
    st.title("DB Ops")
    settings = load_settings()
    try:
        users = load_users(settings.users_file)
    except FileNotFoundError:
        st.error(f"Users file not found: {settings.users_file}")
        return
    with st.form("login"):
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Log in", type="primary")
    if submitted:
        user = authenticate(users, username.strip(), password)
        if user is None:
            st.error("Wrong username or password")
            return
        for key in ("login_username", "login_password"):
            st.session_state.pop(key, None)
        st.session_state["user"] = user
        st.rerun()
