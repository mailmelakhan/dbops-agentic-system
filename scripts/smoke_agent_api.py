"""Smoke test against a running agent-api (never run in CI; it needs real AWS and the configured LLM behind it).

    docker compose exec agent-api python /app/scripts/smoke_agent_api.py --base-url http://localhost:8000 \\
        --user lakhan --question "List my databases" [--propose-snapshot <db>]

Reads DBOPS_SERVICE_TOKEN from the environment (set in the agent-api container). agent-api is not published to the
host, so run this inside the container.
"""

import argparse
import json
import os
import sys
import time

import httpx

TERMINAL = {"run_completed", "run_failed", "run_cancelled"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--user", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--propose-snapshot", metavar="DB", help="also ask the agent to propose a snapshot of DB")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--timeout", type=float, default=360)
    args = ap.parse_args(argv)

    headers = {"X-DBOps-User": args.user, "X-DBOps-Service-Token": os.environ.get("DBOPS_SERVICE_TOKEN", "")}
    messages = [args.question]
    if args.propose_snapshot:
        messages.append(f"Please propose a snapshot of {args.propose_snapshot}.")
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as http:
        conv = http.post("/conversations").raise_for_status().json()
        for text in messages:
            print(f"> {text}")
            run = http.post(f"/conversations/{conv['id']}/runs", json={"message": text}).raise_for_status().json()
            run_id, seq, deadline, last = run["run_id"], 0, time.monotonic() + args.timeout, None
            while last is None and time.monotonic() < deadline:
                body = http.get(f"/runs/{run_id}/events", params={"after_seq": seq}).raise_for_status().json()
                for event in body["events"]:
                    seq = event["seq"]
                    print(json.dumps(event))
                    if event["type"] in TERMINAL:
                        last = event
                time.sleep(1)
            if last is None:
                print("timed out waiting for the run", file=sys.stderr)
                return 1
            if last["type"] == "run_failed":
                print(f"run failed: {last.get('error')}", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
