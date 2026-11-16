"""What the UI lets a user do. The backend enforces the same rules; this only hides or disables controls."""

from dataclasses import dataclass

from dbops_frontend.auth import Role, User

_ROLLBACK_TIER = {"modify_tags": "LOW", "create_snapshot": "LOW", "modify_instance_class": "HIGH"}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None


def _needed(tier: str) -> Role:
    return Role.SAFE_WRITE if tier == "LOW" else Role.PRIVILEGED


def can_propose(role: Role, tier: str) -> bool:
    return role.rank >= _needed(tier).rank


def can_decide(user: User, cr: dict) -> Decision:
    """Approve and reject share one rule."""
    if cr["requested_by"] == user.username:
        return Decision(False, "You can't approve your own request")
    if not can_propose(user.role, cr["risk_tier"]):
        return Decision(False, f"Your role ({user.role}) can't approve {cr['risk_tier']} changes")
    if cr["status"] != "pending":
        return Decision(False, "Not pending")
    return Decision(True, None)


def can_withdraw(user: User, cr: dict) -> bool:
    return cr["status"] == "pending" and cr["requested_by"] == user.username


def can_cancel(user: User, cr: dict) -> bool:
    return cr["status"] == "scheduled" and user.role is Role.PRIVILEGED


def can_retry_cleanup(user: User, cr: dict) -> bool:
    return cr["status"] == "failed_cleanup_pending" and user.role is Role.PRIVILEGED


def rollback_tier(cr: dict) -> str | None:
    return _ROLLBACK_TIER.get(cr["change_type"])


def can_propose_rollback(user: User, cr: dict) -> bool:
    tier = rollback_tier(cr)
    return cr["status"] == "succeeded" and tier is not None and can_propose(user.role, tier)
