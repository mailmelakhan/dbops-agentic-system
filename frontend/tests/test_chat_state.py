from dbops_frontend.chat_state import ActiveRun, apply_events, build_turn, turn_from_message

E = [{"seq": 1, "type": "run_started"}, {"seq": 2, "type": "text_delta", "text": "Hello "},
     {"seq": 3, "type": "tool_call_started", "tool_call_id": "t1", "name": "rds.x", "args": {"a": 1}},
     {"seq": 4, "type": "text_delta", "text": "world"},
     {"seq": 5, "type": "tool_call_finished", "tool_call_id": "t1", "status": "ok", "result": {}, "duration_ms": 1200},
     {"seq": 6, "type": "change_proposed", "change_request_id": "CR-0150", "change_type": "create_snapshot",
      "risk_tier": "LOW"},
     {"seq": 7, "type": "run_completed"}]


def test_apply_events_ignores_already_seen_seq():
    run = ActiveRun("r")
    apply_events(run, E[:4])
    apply_events(run, E[2:])
    assert [e["seq"] for e in run.events] == [1, 2, 3, 4, 5, 6, 7] and run.last_seq == 7


def test_apply_events_sorts_out_of_order_page():
    run = ActiveRun("r")
    apply_events(run, [E[1], E[0]])
    assert [e["seq"] for e in run.events] == [1, 2]


def test_active_runs_do_not_share_event_lists():
    a, b = ActiveRun("a"), ActiveRun("b")
    apply_events(a, E[:1])
    assert b.events == []


def test_build_turn():
    t = build_turn(E)
    assert (t.role, t.text, t.status, len(t.tool_steps), t.tool_steps[0].duration_ms) == (
        "assistant", "Hello world", "completed", 1, 1200)
    assert t.proposals[0]["change_request_id"] == "CR-0150"


def test_build_turn_running_and_failed():
    running = build_turn(E[:3])
    assert running.status == "running" and running.tool_steps[0].status is None
    t = build_turn(E[:2] + [{"seq": 3, "type": "run_failed", "error": "boom"}])
    assert (t.status, t.error) == ("failed", "boom")
    assert build_turn(E[:2] + [{"seq": 3, "type": "run_cancelled"}]).status == "cancelled"


def test_build_turn_keeps_truncated_flag():
    events = E[:3] + [{**E[4], "truncated": True}]
    assert build_turn(events).tool_steps[0].truncated is True


def test_turn_from_message():
    msg = {"role": "assistant", "text": "Hi", "run_id": "r1", "change_request_ids": ["CR-0150"],
           "tool_steps": [{"tool_call_id": "t1", "name": "rds.x", "args": {}, "status": "ok", "result": {},
                           "truncated": False, "duration_ms": 5}]}
    t = turn_from_message(msg)
    assert (t.role, t.text, t.status, t.tool_steps[0].name) == ("assistant", "Hi", "completed", "rds.x")
    assert t.proposals == [{"change_request_id": "CR-0150"}]
    assert turn_from_message({"role": "user", "text": "q", "tool_steps": [], "change_request_ids": [],
                              "run_id": None}).role == "user"
