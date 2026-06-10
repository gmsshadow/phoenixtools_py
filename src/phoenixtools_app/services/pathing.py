from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from sqlmodel import Session, select

from phoenixtools_app.db.models import CelestialBody, JumpLink, Path, Stargate, StargateRoute, Wormhole
from phoenixtools_app.services.phoenix_order import PhoenixOrder

# Rails Path costs
JUMP_COST = 50
STARGATE_COST = 100
WORMHOLE_COST = 100


@dataclass(frozen=True)
class PathLeg:
    kind: str  # "jump" | "gate" | "wormhole"
    from_system_id: int
    to_system_id: int
    # Game cbody id of the gate/wormhole body in the *from* system (for move_to_planet).
    from_cbody_game_id: int | None = None


@dataclass(frozen=True)
class PathResult:
    system_ids: list[int]
    tu_cost: int
    legs: list[PathLeg] = field(default_factory=list)

    @property
    def requires_gate_keys(self) -> bool:
        return any(leg.kind == "gate" for leg in self.legs)


def _cbody_game_id(session: Session, celestial_body_id: int | None) -> int | None:
    if celestial_body_id is None:
        return None
    cb = session.get(CelestialBody, int(celestial_body_id))
    return int(cb.cbody_id) if cb is not None else None


def _build_edges(session: Session) -> dict[int, list[tuple[int, int, PathLeg]]]:
    """adjacency: from_system -> [(to_system, cost, leg)]"""
    adj: dict[int, list[tuple[int, int, PathLeg]]] = {}

    for e in session.exec(select(JumpLink)).all():
        cost = int(e.tu_cost or 0)
        if cost <= 0:
            cost = JUMP_COST * int(e.jumps or 1)
        leg = PathLeg(kind="jump", from_system_id=int(e.from_id), to_system_id=int(e.to_id))
        adj.setdefault(int(e.from_id), []).append((int(e.to_id), cost, leg))

    gates = {int(g.id): g for g in session.exec(select(Stargate)).all() if g.id is not None}
    for r in session.exec(select(StargateRoute)).all():
        gf = gates.get(int(r.from_id))
        gt = gates.get(int(r.to_id))
        if gf is None or gt is None:
            continue
        # Rails `known?` for gate routes only needs both ends to exist.
        leg = PathLeg(
            kind="gate",
            from_system_id=int(gf.star_system_id),
            to_system_id=int(gt.star_system_id),
            from_cbody_game_id=_cbody_game_id(session, gf.celestial_body_id),
        )
        adj.setdefault(int(gf.star_system_id), []).append((int(gt.star_system_id), STARGATE_COST, leg))

    for w in session.exec(select(Wormhole)).all():
        # Rails Wormhole#known? requires a celestial body at the entry side.
        if w.celestial_body_id is None:
            continue
        leg = PathLeg(
            kind="wormhole",
            from_system_id=int(w.star_system_id),
            to_system_id=int(w.to_id),
            from_cbody_game_id=_cbody_game_id(session, w.celestial_body_id),
        )
        adj.setdefault(int(w.star_system_id), []).append((int(w.to_id), WORMHOLE_COST, leg))

    return adj


def shortest_path(session: Session, from_system_id: int, to_system_id: int) -> PathResult | None:
    """Dijkstra over jump links, stargate routes, and wormholes (Rails Path costs)."""
    if from_system_id == to_system_id:
        return PathResult(system_ids=[from_system_id], tu_cost=0, legs=[])

    adj = _build_edges(session)

    dist: dict[int, int] = {from_system_id: 0}
    prev: dict[int, tuple[int, PathLeg]] = {}
    pq: list[tuple[int, int]] = [(0, from_system_id)]

    while pq:
        d, u = heapq.heappop(pq)
        if u == to_system_id:
            break
        if d != dist.get(u, 0):
            continue
        for v, w, leg in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, 1_000_000_000):
                dist[v] = nd
                prev[v] = (u, leg)
                heapq.heappush(pq, (nd, v))

    if to_system_id not in dist:
        return None

    legs: list[PathLeg] = []
    path = [to_system_id]
    cur = to_system_id
    while cur != from_system_id:
        parent, leg = prev[cur]
        legs.append(leg)
        path.append(parent)
        cur = parent
    path.reverse()
    legs.reverse()
    return PathResult(system_ids=path, tu_cost=dist[to_system_id], legs=legs)


def find_quickest_path(session: Session, from_system_id: int, to_system_id: int) -> Path | None:
    """
    Rails `Path.find_quickest`: reuse stored `Path` row with lowest `tu_cost`, or compute via
    the link graph and persist a new row (including whether gate keys are needed).
    """
    if from_system_id == to_system_id:
        return None
    existing = session.exec(
        select(Path)
        .where(Path.from_id == int(from_system_id))
        .where(Path.to_id == int(to_system_id))
        .order_by(Path.tu_cost)
    ).first()
    if existing is not None:
        return existing
    sp = shortest_path(session, from_system_id, to_system_id)
    if sp is None:
        return None
    row = Path(
        from_id=int(from_system_id),
        to_id=int(to_system_id),
        tu_cost=int(sp.tu_cost),
        gate_keys=sp.requires_gate_keys,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def path_requires_gate_keys(path: Path) -> bool:
    return bool(path.gate_keys)


def legs_to_orders(legs: list[PathLeg], orders: list[PhoenixOrder] | None = None) -> list[PhoenixOrder]:
    """
    Rails `Path#to_orders` + `PathPoint#add_orders`:
    - move to a random jump quad before the first jump of each run of jumps
    - jump legs -> jump order
    - gate legs -> move to gate body, enter stargate
    - wormhole legs -> move to wormhole body, enter wormhole
    """
    orders = orders if orders is not None else []
    previous_kind: str | None = None
    for leg in legs:
        if leg.kind == "jump":
            if previous_kind != "jump":
                orders.append(PhoenixOrder.move_to_random_jump_quad())
            orders.append(PhoenixOrder.jump(int(leg.to_system_id)))
        elif leg.kind == "gate":
            if leg.from_cbody_game_id is not None:
                orders.append(PhoenixOrder.move_to_planet(int(leg.from_system_id), int(leg.from_cbody_game_id)))
            orders.append(PhoenixOrder.enter_stargate(int(leg.to_system_id)))
        elif leg.kind == "wormhole":
            if leg.from_cbody_game_id is not None:
                orders.append(PhoenixOrder.move_to_planet(int(leg.from_system_id), int(leg.from_cbody_game_id)))
            orders.append(PhoenixOrder.enter_wormhole())
        previous_kind = leg.kind
    return orders


def legs_to_squad_orders(legs: list[PathLeg], orders: list[PhoenixOrder] | None = None) -> list[PhoenixOrder]:
    """Rails `PathPoint#add_squad_orders` within `Path#to_orders(squadron=true)`."""
    orders = orders if orders is not None else []
    previous_kind: str | None = None
    for leg in legs:
        if leg.kind == "jump":
            if previous_kind != "jump":
                orders.append(PhoenixOrder.move_to_random_jump_quad())
            orders.append(PhoenixOrder.jump(int(leg.to_system_id)))
        else:
            if leg.from_cbody_game_id is not None:
                orders.append(PhoenixOrder.move_to_planet(int(leg.from_system_id), int(leg.from_cbody_game_id)))
            orders.append(PhoenixOrder.squadron_stop())
            orders.append(PhoenixOrder.wait_for_tus(120))
            orders.append(PhoenixOrder.squadron_start())
            orders.append(PhoenixOrder.enter_stargate(int(leg.to_system_id)))
            orders.append(PhoenixOrder.squadron_stop())
            orders.append(PhoenixOrder.wait_for_tus(240))
        previous_kind = leg.kind
    return orders
