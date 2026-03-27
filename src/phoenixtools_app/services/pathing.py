from __future__ import annotations

import heapq
from dataclasses import dataclass

from sqlmodel import Session, select

from phoenixtools_app.db.models import JumpLink


@dataclass(frozen=True)
class PathResult:
    system_ids: list[int]
    tu_cost: int


def shortest_path(session: Session, from_system_id: int, to_system_id: int) -> PathResult | None:
    """
    Dijkstra over JumpLink edges.
    Edge cost uses `jump.tu_cost` if present, otherwise `50 * jumps`.
    """
    if from_system_id == to_system_id:
        return PathResult(system_ids=[from_system_id], tu_cost=0)

    edges = session.exec(select(JumpLink)).all()
    adj: dict[int, list[tuple[int, int]]] = {}
    for e in edges:
        cost = int(e.tu_cost or 0) if getattr(e, "tu_cost", None) is not None else 0
        if cost <= 0:
            cost = 50 * int(e.jumps or 1)
        adj.setdefault(int(e.from_id), []).append((int(e.to_id), cost))

    dist: dict[int, int] = {from_system_id: 0}
    prev: dict[int, int] = {}
    pq: list[tuple[int, int]] = [(0, from_system_id)]

    while pq:
        d, u = heapq.heappop(pq)
        if u == to_system_id:
            break
        if d != dist.get(u, 0):
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, 1_000_000_000):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if to_system_id not in dist:
        return None

    # reconstruct
    path = [to_system_id]
    cur = to_system_id
    while cur != from_system_id:
        cur = prev[cur]
        path.append(cur)
    path.reverse()
    return PathResult(system_ids=path, tu_cost=dist[to_system_id])

