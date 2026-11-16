from dbops_frontend.auth import Role, User, authenticate, load_users


def test_authenticate(users_file):
    users = load_users(users_file)
    assert authenticate(users, "priya", "pw") == User("priya", "Priya", Role.PRIVILEGED)
    assert authenticate(users, "priya", "nope") is None
    assert authenticate(users, "mallory", "pw") is None
    assert authenticate(users, "", "") is None


def test_role_rank():
    assert [r.rank for r in (Role.READ, Role.SAFE_WRITE, Role.PRIVILEGED)] == [0, 1, 2]
    assert Role("SAFE-WRITE") is Role.SAFE_WRITE
