"""The Task 2 store behaviours against a real Postgres. Skipped unless DBOPS_TEST_PG_URL is set, e.g.
postgresql+psycopg://t:t@127.0.0.1:55432/t (a throwaway postgres:16 container)."""

import os
import uuid

import pytest
import sqlalchemy as sa
from agent_api.store import RunActive, Store, metadata

URL = os.environ.get("DBOPS_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DBOPS_TEST_PG_URL not set")


@pytest.fixture
def pg_store():
    store = Store(URL)
    yield store
    metadata.drop_all(store.engine)  # leave the database as found
    store.engine.dispose()


def test_conversations_create_and_list(pg_store):
    user = f"u-{uuid.uuid4().hex[:6]}"
    conv = pg_store.create_conversation(user)
    assert [c["id"] for c in pg_store.list_conversations(user)] == [conv["id"]]
    assert pg_store.list_conversations("someone-else") == []


def test_second_start_raises_run_active(pg_store):
    conv = pg_store.create_conversation("lakhan")
    run_id = pg_store.start_run(conv["id"], "lakhan", "hello", "hello")
    with pytest.raises(RunActive):
        pg_store.start_run(conv["id"], "lakhan", "again", "again")
    pg_store.finish_run(run_id, "completed", None)
    assert pg_store.start_run(conv["id"], "lakhan", "next", "next")


def test_append_event_assigns_increasing_seq(pg_store):
    conv = pg_store.create_conversation("lakhan")
    run_id = pg_store.start_run(conv["id"], "lakhan", "hello", "hello")
    seqs = [pg_store.append_event(run_id, {"type": "run_started"})["seq"],
            pg_store.append_event(run_id, {"type": "text_delta", "text": "x"})["seq"]]
    assert seqs == [1, 2]
    assert [e["seq"] for e in pg_store.events_after(run_id, 1)] == [2]


def test_fail_running_runs(pg_store):
    conv = pg_store.create_conversation("lakhan")
    run_id = pg_store.start_run(conv["id"], "lakhan", "hello", "hello")
    assert pg_store.fail_running_runs("agent-api restarted") == 1
    assert pg_store.get_run(run_id, "lakhan")["status"] == "failed"
    with pg_store.engine.connect() as c:
        assert c.execute(sa.text("select 1")).scalar() == 1
