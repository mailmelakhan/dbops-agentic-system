from pathlib import Path

import bcrypt
import openapi_spec_validator
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "agent-api": {
        ("get", "/conversations"), ("post", "/conversations"),
        ("get", "/conversations/{id}/messages"), ("post", "/conversations/{id}/runs"),
        ("get", "/runs"), ("get", "/runs/{run_id}/events"), ("post", "/runs/{run_id}/cancel"),
        ("get", "/change-requests"), ("get", "/change-requests/{id}"),
        ("post", "/change-requests/{id}/rollback"),
        ("get", "/audit"), ("get", "/audit/facets"),
        ("get", "/reports"), ("get", "/reports/{id}"), ("get", "/reports/{id}/download"),
        ("post", "/reports/change-history"),
    },
    "executor": {("post", f"/change-requests/{{id}}/{a}")
                 for a in ("approve", "reject", "withdraw", "cancel", "retry-cleanup")},
    "health-analytics": {
        ("get", "/fleet"), ("get", "/fleet/summary"), ("get", "/fleet/{identifier}/alarms"),
        ("post", "/refresh"), ("post", "/reports/fleet-health"),
    },
}


def load(name):
    return yaml.safe_load((ROOT / "contracts" / f"{name}.yaml").read_text())


@pytest.mark.parametrize("name", EXPECTED)
def test_contract_is_valid_openapi_31(name):
    doc = load(name)
    assert doc["openapi"].startswith("3.1")
    openapi_spec_validator.validate(doc)


@pytest.mark.parametrize("name", EXPECTED)
def test_contract_paths_match_spec(name):
    doc = load(name)
    ops = {(m, p) for p, item in doc["paths"].items() for m in item if m in {"get", "post"}}
    assert ops == EXPECTED[name]


@pytest.mark.parametrize("name", EXPECTED)
def test_contract_requires_both_identity_headers(name):
    doc = load(name)
    names = {s["name"] for s in doc["components"]["securitySchemes"].values()}
    assert names == {"X-DBOps-User", "X-DBOps-Service-Token"}
    assert len(doc["security"]) == 1 and len(doc["security"][0]) == 2


def test_users_file_has_four_demo_users():
    users = yaml.safe_load((ROOT / "config/users.yaml").read_text())["users"]
    assert {u["username"]: u["role"] for u in users} == {
        "lakhan": "PRIVILEGED", "ankit": "PRIVILEGED", "geeta": "SAFE-WRITE", "amit": "READ"}
    assert all(bcrypt.checkpw(b"dbops-demo", u["password_hash"].encode()) for u in users)


def test_agent_iam_policy_read_only():
    import json
    import re
    policy = json.loads((ROOT / "services/agent_api/iam-policy.json").read_text())
    allowed = re.compile(r"^(rds:Describe.*|rds:ListTagsForResource"
                         r"|cloudwatch:(GetMetricData|GetMetricStatistics|ListMetrics|DescribeAlarms))$")
    forbidden = ("Modify", "Delete", "Create", "Restore", "AddTags", "RemoveTags")
    actions = []
    for stmt in policy["Statement"]:
        assert stmt["Effect"] == "Allow"
        acts = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        actions += acts
    assert actions
    for action in actions:
        assert allowed.match(action), action
        assert not any(word in action for word in forbidden), action
