import tomllib
from pathlib import Path

from dbops_frontend.navigation import approvals_title

FRONTEND = Path(__file__).resolve().parents[1]


def login(at, username, password):
    at.text_input(key="login_username").input(username)
    at.text_input(key="login_password").input(password)
    at.button[0].click().run()


def test_login_required(shell):
    at = shell()
    at.run()
    assert [t.label for t in at.text_input] == ["Username", "Password"]
    assert not at.exception


def test_wrong_password(shell):
    at = shell()
    at.run()
    login(at, "lakhan", "bad")
    assert at.error[0].value == "Wrong username or password"
    assert "user" not in at.session_state


def test_login_lands_on_chat_with_panel(shell):
    at = shell()
    at.run()
    login(at, "lakhan", "pw")
    assert not at.exception
    assert at.session_state["user"].username == "lakhan"
    assert any("Lakhan" in m.value and "PRIVILEGED" in m.value for m in at.sidebar.markdown)
    assert at.header[0].value == "Chat"
    assert any("need attention" in m.value for m in at.markdown)
    assert list(at.session_state["pages"]) == ["chat", "approvals", "audit"]


def test_logout(shell):
    at = shell(logged_in="lakhan")
    at.run()
    next(b for b in at.sidebar.button if b.label == "Log out").click().run()
    assert "user" not in at.session_state
    assert [t.label for t in at.text_input] == ["Username", "Password"]


def test_approvals_title():
    assert (approvals_title(6), approvals_title(0)) == ("Approvals (6)", "Approvals")


def test_theme_fonts_configured():
    cfg = tomllib.loads((FRONTEND / ".streamlit/config.toml").read_text())
    assert cfg["server"]["enableStaticServing"] is True
    assert "Inter" in cfg["theme"]["font"]
    assert "Space Grotesk" in cfg["theme"]["headingFont"] and "JetBrains Mono" in cfg["theme"]["codeFont"]
    families = {face["family"] for face in cfg["theme"]["fontFaces"]}
    assert families == {"Inter", "Space Grotesk", "JetBrains Mono"}
    for face in cfg["theme"]["fontFaces"]:
        assert (FRONTEND / face["url"].removeprefix("app/")).stat().st_size > 1000
