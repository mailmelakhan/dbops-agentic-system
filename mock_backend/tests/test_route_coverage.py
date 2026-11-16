from pathlib import Path

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def test_mock_routes_equal_contract_paths(app):
    contract_ops = set()
    for name in ("agent-api", "executor", "health-analytics"):
        doc = yaml.safe_load((CONTRACTS / f"{name}.yaml").read_text())
        contract_ops |= {(m, p) for p, item in doc["paths"].items() for m in item if m in {"get", "post"}}
    mock_ops = {(m, p) for p, item in app.openapi()["paths"].items() for m in item}
    assert mock_ops - {("get", "/healthz")} == contract_ops
