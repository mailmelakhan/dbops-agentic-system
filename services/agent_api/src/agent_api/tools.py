"""Our own Strands tools: list_demo_databases and the role-gated propose tools (design §7.2)."""

import json
import logging
from collections.abc import Callable
from typing import Any

from strands import tool

from agent_api.errors import ApiProblem
from agent_api.identity import Caller, Role
from agent_api.proposals import ProposalError, Proposer
from agent_api.scope import ScopeResolver, ScopeUnavailable

log = logging.getLogger(__name__)
PENDING_NOTE = "Needs approval on the Approvals page"


def _error(message: str) -> dict:
    return {"status": "error", "content": [{"text": json.dumps({"error": message})}]}


def build_tools(caller: Caller, conversation_id: str, scope: ScopeResolver, proposer: Proposer,
                emit: Callable[[dict], None]) -> list:
    def propose(change: Callable[[], dict]) -> dict:
        try:
            cr = change()
        except (ProposalError, ScopeUnavailable) as exc:
            return _error(str(exc))
        except ApiProblem as exc:
            return _error(exc.message)
        emit({"type": "change_proposed", "change_request_id": cr["id"], "change_type": cr["change_type"],
              "risk_tier": cr["risk_tier"]})
        return {"change_request_id": cr["id"], "status": "pending", "note": PENDING_NOTE}

    @tool
    def list_demo_databases() -> Any:
        """List the databases in the demo set (the only ones you can see), with kind, engine, class, region,
        status, the Aurora cluster an instance belongs to, and a cluster's member instances."""
        try:
            dbs = scope.current().all()
        except ScopeUnavailable as exc:
            return _error(str(exc))
        return [{"identifier": db.identifier, "kind": db.kind, "engine": db.engine,
                 "instance_class": db.instance_class, "region": db.region, "status": db.status,
                 "cluster": db.cluster,
                 "members": [{"identifier": m, "role": "writer" if w else "reader"} for m, w in db.members]}
                for db in dbs]

    @tool
    def propose_modify_tags(identifier: str, add: dict[str, str] | None = None,
                            remove: list[str] | None = None) -> dict:
        """Propose adding, changing or removing tags on a database. Creates a pending change request.

        Args:
            identifier: The database (instance or cluster) identifier.
            add: Tags to add or change, as {key: value}.
            remove: Tag keys to remove.
        """
        return propose(lambda: proposer.modify_tags(caller, conversation_id, identifier, add or {}, remove or []))

    @tool
    def propose_create_snapshot(identifier: str) -> dict:
        """Propose a manual snapshot of a database (a cluster snapshot for an Aurora cluster).

        Args:
            identifier: The database (instance or cluster) identifier.
        """
        return propose(lambda: proposer.create_snapshot(caller, conversation_id, identifier))

    @tool
    def propose_restore_test(identifier: str, snapshot_id: str | None = None) -> dict:
        """Propose a restore test: restore a temporary copy from a snapshot to prove backups work.

        Args:
            identifier: The database (instance or cluster) identifier.
            snapshot_id: The snapshot to restore from; the latest available one if omitted.
        """
        return propose(lambda: proposer.restore_test(caller, conversation_id, identifier, snapshot_id))

    @tool
    def propose_modify_instance_class(identifier: str, to_class: str) -> dict:
        """Propose resizing one DB instance to another instance class. For an Aurora cluster, name one member.

        Args:
            identifier: The DB instance identifier.
            to_class: The target instance class, for example db.r6g.xlarge.
        """
        return propose(lambda: proposer.modify_instance_class(caller, conversation_id, identifier, to_class))

    safe_write = [propose_modify_tags, propose_create_snapshot]
    by_role = {Role.READ: [], Role.SAFE_WRITE: safe_write,
               Role.PRIVILEGED: [*safe_write, propose_restore_test, propose_modify_instance_class]}
    return [list_demo_databases, *by_role[caller.role]]
