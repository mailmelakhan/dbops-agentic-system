from dbops_frontend.auth import Role, User
from dbops_frontend.permissions import (
    Decision,
    can_cancel,
    can_decide,
    can_propose,
    can_propose_rollback,
    can_retry_cleanup,
    can_withdraw,
    rollback_tier,
)

SAM, PRIYA, ARJUN, RITA = (User(n, n, r) for n, r in [("sam", Role.SAFE_WRITE), ("priya", Role.PRIVILEGED),
                                                      ("arjun", Role.PRIVILEGED), ("rita", Role.READ)])


def cr(**kw):
    return {"id": "CR-1", "status": "pending", "risk_tier": "LOW", "requested_by": "arjun",
            "change_type": "modify_tags", **kw}


def test_self_approval():
    assert can_decide(ARJUN, cr()) == Decision(False, "You can't approve your own request")


def test_role_too_low():
    assert can_decide(SAM, cr(risk_tier="HIGH")) == Decision(False, "Your role (SAFE-WRITE) can't approve HIGH changes")


def test_read_cannot_approve_low():
    assert can_decide(RITA, cr()) == Decision(False, "Your role (READ) can't approve LOW changes")


def test_not_pending():
    assert can_decide(PRIYA, cr(status="approved")) == Decision(False, "Not pending")


def test_priya_can_approve():
    assert can_decide(PRIYA, cr(risk_tier="HIGH")) == Decision(True, None)
    assert can_decide(SAM, cr()) == Decision(True, None)


def test_withdraw_only_requester():
    assert can_withdraw(ARJUN, cr()) and not can_withdraw(PRIYA, cr())
    assert not can_withdraw(ARJUN, cr(status="approved"))


def test_cancel_scheduled_privileged_only():
    assert can_cancel(PRIYA, cr(status="scheduled")) and not can_cancel(SAM, cr(status="scheduled"))
    assert not can_cancel(PRIYA, cr(status="pending"))


def test_retry_cleanup_privileged_only():
    assert can_retry_cleanup(PRIYA, cr(status="failed_cleanup_pending"))
    assert not can_retry_cleanup(SAM, cr(status="failed_cleanup_pending"))


def test_rollback_tier():
    assert [rollback_tier(cr(change_type=t)) for t in
            ("modify_tags", "create_snapshot", "modify_instance_class", "restore_test")] == ["LOW", "LOW", "HIGH", None]


def test_rollback_rules():
    assert can_propose_rollback(SAM, cr(status="succeeded"))
    assert not can_propose_rollback(SAM, cr(status="succeeded", change_type="modify_instance_class"))
    assert can_propose_rollback(PRIYA, cr(status="succeeded", change_type="modify_instance_class"))
    assert not can_propose_rollback(PRIYA, cr(status="succeeded", change_type="restore_test"))
    assert not can_propose_rollback(RITA, cr(status="succeeded"))
    assert not can_propose_rollback(PRIYA, cr(status="failed"))


def test_can_propose():
    assert can_propose(Role.SAFE_WRITE, "LOW") and not can_propose(Role.SAFE_WRITE, "MEDIUM")
    assert not can_propose(Role.READ, "LOW") and can_propose(Role.PRIVILEGED, "HIGH")
