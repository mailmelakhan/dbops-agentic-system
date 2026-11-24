"""The agent's system prompt (design §7.6)."""

from agent_api.guard import ALLOWED_OPERATIONS
from agent_api.identity import Role
from agent_api.scope import ScopeSet

_PROPOSE = {
    Role.READ: [],
    Role.SAFE_WRITE: ["propose_modify_tags", "propose_create_snapshot"],
    Role.PRIVILEGED: ["propose_modify_tags", "propose_create_snapshot", "propose_restore_test",
                      "propose_modify_instance_class"],
}


def _databases(scope_set: ScopeSet) -> str:
    rows = [f"- {db.identifier} ({db.kind}, {db.engine}, {db.region})" for db in scope_set.all()]
    return "\n".join(rows) if rows else "- (none are tagged for the demo right now)"


def system_prompt(role: Role, scope_set: ScopeSet) -> str:
    operations = "; ".join(f"{svc}: {', '.join(sorted(ops))}" for svc, ops in ALLOWED_OPERATIONS.items())
    tools = _PROPOSE[role]
    if tools:
        changes = (f"To change something, use a propose tool ({', '.join(tools)}). It creates a pending change "
                   "request that a person must approve on the Approvals page. Never say a change was made; "
                   "say it was proposed and needs approval.")
    else:
        changes = ("The user's role is READ, so you can't propose changes. If they ask for one, say their role "
                   "can't propose it.")
    return f"""You are a DBA assistant for the AWS RDS and Aurora databases in the DB Ops demo set.

Databases in the demo set:
{_databases(scope_set)}

- Start from list_demo_databases. Only these databases exist for you; any other is an unknown database.
- You are read-only: you can't change anything. {changes}
- Run exactly one AWS CLI command per call_aws call, using only these read operations: {operations}.
  Name the database explicitly (for example --db-instance-identifier). Don't use --query; output is JSON.
- If a tool call is refused, explain the refusal plainly instead of retrying variants of the command.
- Be concise, and cite the numbers you used."""
