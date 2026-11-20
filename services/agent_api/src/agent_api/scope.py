"""Scope resolver: the single source of the set of databases the agent may see."""

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError

_ARN = re.compile(r"^arn:aws[\w-]*:rds:([^:]+):[^:]*:(db|cluster):(.+)$", re.IGNORECASE)


class ScopeUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ScopedDb:
    region: str
    identifier: str
    kind: Literal["instance", "cluster"]
    engine: str
    instance_class: str | None
    status: str
    cluster: str | None
    members: tuple[tuple[str, bool], ...]
    arn: str


class ScopeSet:
    def __init__(self, dbs: Iterable[ScopedDb]):
        self._dbs = list(dbs)

    def all(self) -> list[ScopedDb]:
        return list(self._dbs)

    def _find(self, identifier: str, region: str | None, kind: str | None) -> ScopedDb | None:
        m = _ARN.match(identifier.strip())
        if m:
            arn_region, arn_kind, identifier = m.group(1), m.group(2).lower(), m.group(3)
            arn_kind = "instance" if arn_kind == "db" else "cluster"
            if kind is not None and kind != arn_kind:
                return None
            if region is not None and region.lower() != arn_region.lower():
                return None
            region, kind = arn_region, arn_kind
        ident = identifier.strip().lower()
        for want in ((kind,) if kind else ("instance", "cluster")):
            for db in self._dbs:
                if db.identifier == ident and db.kind == want and (
                        region is None or db.region.lower() == region.lower()):
                    return db
        return None

    def contains(self, identifier: str, region: str | None = None,
                 kind: Literal["instance", "cluster"] | None = None) -> bool:
        return self._find(identifier, region, kind) is not None

    def get(self, identifier: str, region: str | None = None,
            kind: Literal["instance", "cluster"] | None = None) -> ScopedDb | None:
        """With kind=None (and no ARN) the instance match wins, else the cluster match."""
        return self._find(identifier, region, kind)


class ScopeResolver:
    def __init__(self, rds_client_factory: Callable[[str], Any], regions: Iterable[str],
                 tag_key: str, tag_value: str, ttl_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self._factory = rds_client_factory
        self._regions = tuple(regions)
        self._key = tag_key
        self._value = tag_value
        self._ttl = ttl_s
        self._clock = clock
        self._cached: ScopeSet | None = None
        self._at = 0.0

    def current(self) -> ScopeSet:
        if self._cached is not None and self._clock() - self._at < self._ttl:
            return self._cached
        try:
            dbs = [db for region in self._regions for db in self._resolve_region(region)]
        except (ClientError, BotoCoreError) as exc:
            raise ScopeUnavailable("Couldn't read the database list from AWS") from exc
        self._cached = ScopeSet(dbs)
        self._at = self._clock()
        return self._cached

    def _tagged(self, item: dict) -> bool:
        return any(t.get("Key") == self._key and t.get("Value") == self._value
                   for t in item.get("TagList", []))

    def _resolve_region(self, region: str) -> list[ScopedDb]:
        client = self._factory(region)
        instances = [i for page in client.get_paginator("describe_db_instances").paginate()
                     for i in page.get("DBInstances", [])]
        clusters = [c for page in client.get_paginator("describe_db_clusters").paginate()
                    for c in page.get("DBClusters", [])]
        by_id = {i["DBInstanceIdentifier"].lower(): i for i in instances}
        chosen: dict[tuple[str, str], ScopedDb] = {}

        def add_instance(i: dict) -> None:
            ident = i["DBInstanceIdentifier"].lower()
            cluster = i.get("DBClusterIdentifier")
            chosen[("instance", ident)] = ScopedDb(
                region=region, identifier=ident, kind="instance", engine=i.get("Engine", ""),
                instance_class=i.get("DBInstanceClass"), status=i.get("DBInstanceStatus", ""),
                cluster=cluster.lower() if cluster else None, members=(),
                arn=i.get("DBInstanceArn", ""))

        for i in instances:
            if self._tagged(i):
                add_instance(i)
        for c in clusters:
            if not self._tagged(c):
                continue
            ident = c["DBClusterIdentifier"].lower()
            members = tuple((m["DBInstanceIdentifier"].lower(), bool(m.get("IsClusterWriter")))
                            for m in c.get("DBClusterMembers", []))
            chosen[("cluster", ident)] = ScopedDb(
                region=region, identifier=ident, kind="cluster", engine=c.get("Engine", ""),
                instance_class=None, status=c.get("Status", ""), cluster=None, members=members,
                arn=c.get("DBClusterArn", ""))
            for member_id, _ in members:
                if member_id in by_id and ("instance", member_id) not in chosen:
                    add_instance(by_id[member_id])
        return list(chosen.values())
