"""Persistence for conversations, messages, runs and run events (design §8). Sync SQLAlchemy 2 Core."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

metadata = sa.MetaData()
JSONType = sa.JSON().with_variant(JSONB(), "postgresql")
TS = sa.DateTime(timezone=True)

conversations = sa.Table(
    "conversations", metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("username", sa.String, nullable=False, index=True),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)
messages = sa.Table(
    "messages", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("conversation_id", sa.String, sa.ForeignKey("conversations.id"), nullable=False, index=True),
    sa.Column("role", sa.String, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("tool_steps", JSONType, nullable=False),
    sa.Column("change_request_ids", JSONType, nullable=False),
    sa.Column("run_id", sa.String, nullable=True),
    sa.Column("created_at", TS, nullable=False),
)
runs = sa.Table(
    "runs", metadata,
    sa.Column("run_id", sa.String, primary_key=True),
    sa.Column("conversation_id", sa.String, sa.ForeignKey("conversations.id"), nullable=False, index=True),
    sa.Column("username", sa.String, nullable=False),
    sa.Column("status", sa.String, nullable=False),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column("cancel_requested", sa.Boolean, nullable=False, default=False),
    sa.Column("started_at", TS, nullable=False),
    sa.Column("ended_at", TS, nullable=True),
)
run_events = sa.Table(
    "run_events", metadata,
    sa.Column("run_id", sa.String, sa.ForeignKey("runs.run_id"), primary_key=True),
    sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("payload", JSONType, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)
_one_active = sa.Index(
    "runs_one_active", runs.c.conversation_id, unique=True,
    postgresql_where=runs.c.status == "running", sqlite_where=runs.c.status == "running",
)


class RunActive(Exception):
    """The conversation already has a running run."""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(ts: datetime) -> str:
    if ts.tzinfo is None:  # sqlite drops the zone
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _conversation(row) -> dict:
    return {"id": row.id, "title": row.title, "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at)}


def _run(row) -> dict:
    return {"run_id": row.run_id, "conversation_id": row.conversation_id, "status": row.status,
            "started_at": _iso(row.started_at), "error": row.error}


class Store:
    def __init__(self, url: str):
        url = _normalize_url(url)
        kwargs = {"connect_args": {"timeout": 30}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        self.engine = sa.create_engine(url, **kwargs)
        metadata.create_all(self.engine)

    # conversations and messages
    def create_conversation(self, user: str) -> dict:
        now = _now()
        conv_id = f"conv-{uuid.uuid4().hex[:8]}"
        with self.engine.begin() as c:
            c.execute(conversations.insert().values(id=conv_id, username=user, title="New chat",
                                                    created_at=now, updated_at=now))
        return {"id": conv_id, "title": "New chat", "created_at": _iso(now), "updated_at": _iso(now)}

    def list_conversations(self, user: str) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(sa.select(conversations).where(conversations.c.username == user)
                             .order_by(conversations.c.updated_at.desc(), conversations.c.id)).all()
        return [_conversation(r) for r in rows]

    def get_conversation(self, id: str, user: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(sa.select(conversations).where(conversations.c.id == id,
                                                           conversations.c.username == user)).first()
        return _conversation(row) if row else None

    def add_message(self, conversation_id: str, msg: dict) -> None:
        with self.engine.begin() as c:
            self._insert_message(c, conversation_id, msg)
            c.execute(conversations.update().where(conversations.c.id == conversation_id)
                      .values(updated_at=_now()))

    @staticmethod
    def _insert_message(c, conversation_id: str, msg: dict) -> None:
        c.execute(messages.insert().values(
            conversation_id=conversation_id, role=msg["role"], text=msg["text"],
            tool_steps=msg.get("tool_steps", []), change_request_ids=msg.get("change_request_ids", []),
            run_id=msg.get("run_id"), created_at=_now()))

    def list_messages(self, conversation_id: str) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(sa.select(messages).where(messages.c.conversation_id == conversation_id)
                             .order_by(messages.c.id)).all()
        return [{"role": r.role, "text": r.text, "tool_steps": r.tool_steps,
                 "change_request_ids": r.change_request_ids, "run_id": r.run_id} for r in rows]

    # runs
    def start_run(self, conversation_id: str, user: str, message: str, title_if_first: str) -> str:
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        now = _now()
        try:
            with self.engine.begin() as c:
                # Write first: takes the write lock (sqlite) or the conversation row lock (Postgres),
                # so concurrent starts on one conversation serialise.
                c.execute(conversations.update().where(conversations.c.id == conversation_id)
                          .values(updated_at=now))
                first = c.execute(sa.select(sa.func.count()).select_from(messages)
                                  .where(messages.c.conversation_id == conversation_id)).scalar_one() == 0
                if first:
                    c.execute(conversations.update().where(conversations.c.id == conversation_id)
                              .values(title=title_if_first))
                self._insert_message(c, conversation_id, {"role": "user", "text": message})
                c.execute(runs.insert().values(run_id=run_id, conversation_id=conversation_id, username=user,
                                               status="running", cancel_requested=False, started_at=now))
        except IntegrityError as e:
            raise RunActive(conversation_id) from e
        return run_id

    def append_event(self, run_id: str, event: dict) -> dict:
        for attempt in range(5):
            try:
                with self.engine.begin() as c:
                    top = c.execute(sa.select(sa.func.max(run_events.c.seq))
                                    .where(run_events.c.run_id == run_id)).scalar()
                    now = _now()
                    full = {**event, "seq": (top or 0) + 1, "ts": _iso(now)}
                    c.execute(run_events.insert().values(run_id=run_id, seq=full["seq"], payload=full,
                                                         created_at=now))
                return full
            except IntegrityError:
                if attempt == 4:
                    raise
        raise AssertionError("unreachable")

    def events_after(self, run_id: str, after_seq: int) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(sa.select(run_events.c.payload).where(run_events.c.run_id == run_id,
                                                                    run_events.c.seq > after_seq)
                             .order_by(run_events.c.seq)).all()
        return [r.payload for r in rows]

    def get_run(self, run_id: str, user: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(sa.select(runs).where(runs.c.run_id == run_id, runs.c.username == user)).first()
        return _run(row) if row else None

    def list_runs(self, conversation_id: str, user: str, status: str | None = None) -> list[dict]:
        q = sa.select(runs).where(runs.c.conversation_id == conversation_id, runs.c.username == user)
        if status is not None:
            q = q.where(runs.c.status == status)
        with self.engine.connect() as c:
            rows = c.execute(q.order_by(runs.c.started_at, runs.c.run_id)).all()
        return [_run(r) for r in rows]

    def finish_run(self, run_id: str, status: str, error: str | None) -> None:
        with self.engine.begin() as c:
            c.execute(runs.update().where(runs.c.run_id == run_id)
                      .values(status=status, error=error, ended_at=_now()))

    def request_cancel(self, run_id: str) -> None:
        with self.engine.begin() as c:
            c.execute(runs.update().where(runs.c.run_id == run_id).values(cancel_requested=True))

    def cancel_requested(self, run_id: str) -> bool:
        with self.engine.connect() as c:
            return bool(c.execute(sa.select(runs.c.cancel_requested).where(runs.c.run_id == run_id)).scalar())

    def fail_running_runs(self, error: str) -> int:
        with self.engine.begin() as c:
            return c.execute(runs.update().where(runs.c.status == "running")
                             .values(status="failed", error=error, ended_at=_now())).rowcount
