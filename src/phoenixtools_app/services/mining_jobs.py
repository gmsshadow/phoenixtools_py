"""
Mining jobs report (Rails BasesController#mining_jobs + Base#resource_report).

For every owned hub base (a base that has outposts), compute per-item resource
production (hub + outposts), consumption (mass production raw materials) and
stock, then flag items that will run out in under 26 weeks. For each such item
either point at the best replacement resource deposit within the hub's own
bases, or — if there is none — list it as a "rare ore" with the best deposits
anywhere in the known universe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from phoenixtools_app.db.models import (
    Base,
    BaseItem,
    BaseResource,
    Item,
    ItemAttribute,
    MassProduction,
    NexusConfig,
)

WEEKS_WARNING_THRESHOLD = 26  # Rails: weeks_remaining < 26
MIN_WEEK_YIELDS = 5  # Rails: BaseResource.min_week_yields(5)


# ---------------------------------------------------------------------------
# BaseResource math (Rails app/models/base_resource.rb)
# ---------------------------------------------------------------------------

def _complexes(br: BaseResource) -> int:
    return max(int(br.ore_mines or 0), int(br.resource_complexes or 0))


def _complex_output(br: BaseResource, number_of_complexes: int) -> int:
    output = 0.0
    output_modifier = 1.0
    n = 0
    while n < number_of_complexes:
        diff = number_of_complexes - n
        x = int(br.resource_drop or 0)
        if x > diff:
            x = diff
        if x <= 0:
            break
        output += float(br.resource_yield or 0.0) * output_modifier * x
        n += x
        output_modifier = max(0.0, output_modifier - 0.1)
    size = int(br.resource_size or 0)
    if size != -999 and size < output:
        output = float(size)
    return int(output)


def current_output(br: BaseResource) -> int:
    c = _complexes(br)
    return _complex_output(br, c) if c else 0


def next_complex_output(br: BaseResource) -> int:
    c = _complexes(br)
    if c:
        return _complex_output(br, c + 1) - current_output(br)
    return _complex_output(br, 1)


def _min_week_yield_ok(br: BaseResource) -> bool:
    """Rails scope min_week_yields(5): resource_size >= yield * drop * 5.

    An infinite deposit is stored as size == -999. It trivially has enough to
    last 5 weeks (it never runs out), so it must pass — Rails' literal
    `size >= yield*drop*5` wrongly rejects it because -999 is a small number.
    """
    if int(br.resource_size or 0) == -999:
        return True
    return int(br.resource_size or 0) >= float(br.resource_yield or 0.0) * int(br.resource_drop or 0) * MIN_WEEK_YIELDS


# ---------------------------------------------------------------------------
# Item attribute helpers (Rails Item#raw_materials / #production)
# ---------------------------------------------------------------------------

_RAW_MAT_RE = re.compile(r"([\d.]+)\s+(.+?)\s*\((\d+)\)")


def _item_attr(session: Session, item_id: int, key: str) -> str | None:
    row = session.exec(
        select(ItemAttribute).where(ItemAttribute.item_id == int(item_id), ItemAttribute.attr_key == key)
    ).first()
    return row.attr_value if row else None


def item_raw_materials(session: Session, item_id: int) -> dict[int, float] | None:
    """Parse 'Raw Materials' attribute: '1 Metals (1)2.5 Foo (45)' -> {1: 1.0, 45: 2.5}."""
    raw = _item_attr(session, item_id, "Raw Materials")
    if not raw:
        return None
    out: dict[int, float] = {}
    for qty, _name, iid in _RAW_MAT_RE.findall(raw):
        try:
            out[int(iid)] = float(qty)
        except ValueError:
            continue
    return out or None


def item_production(session: Session, item_id: int) -> float | None:
    raw = _item_attr(session, item_id, "Production")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# MassProduction math (Rails app/models/mass_production.rb)
# ---------------------------------------------------------------------------

def _mp_production(mp: MassProduction) -> int | None:
    """Weekly factory output in 'production points' (tiered efficiency)."""
    if not mp.factories or (mp.status or "") != "Running":
        return None
    production = 0
    remaining = int(mp.factories)
    for rate, tier_cap in ((45, 10), (50, 10), (55, 20)):
        facts = min(remaining, tier_cap)
        production += rate * facts
        remaining -= facts
        if remaining < 1:
            return production
    return production + remaining * 50


def _mp_item_output(session: Session, mp: MassProduction) -> float:
    production = _mp_production(mp)
    item_prod = item_production(session, int(mp.item_id))
    if production is None or not item_prod:
        return 0.0
    return round(production / item_prod, 1)


def mp_raw_materials(session: Session, mp: MassProduction) -> dict[int, int] | None:
    mats = item_raw_materials(session, int(mp.item_id))
    if mats is None:
        return None
    output = _mp_item_output(session, mp)
    return {iid: int(round(qty * output)) for iid, qty in mats.items()}


# ---------------------------------------------------------------------------
# Report rows
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceCandidate:
    base_id: int
    base_name: str
    resource_id: int
    resource_yield: float
    resource_drop: int
    resource_size: int
    complexes: int
    next_complex_output: int


@dataclass(frozen=True)
class MiningJobRow:
    base_id: int
    base_name: str
    item_id: int
    item_name: str
    available: int
    production: int
    consumption: int
    weekly_burn: int
    weeks_remaining: int
    best_resource: ResourceCandidate | None


@dataclass(frozen=True)
class RareOreRow:
    item_id: int
    item_name: str
    candidates: list[ResourceCandidate] = field(default_factory=list)


@dataclass(frozen=True)
class MiningJobsReport:
    jobs: list[MiningJobRow]
    rare_ores: list[RareOreRow]
    hub_count: int


@dataclass(frozen=True)
class ResourceBalanceRow:
    """Full per-item production vs consumption for a hub (incl. healthy items)."""

    base_id: int
    base_name: str
    item_id: int
    item_name: str
    available: int
    production: int
    consumption: int
    weekly_burn: int  # consumption - production (negative = net surplus)
    weeks_remaining: int | None  # None == 'Forever' (no net depletion)
    best_resource: ResourceCandidate | None


def _my_base_ids(session: Session) -> list[int]:
    cfg = session.exec(select(NexusConfig)).first()
    my_aff = int(cfg.affiliation_id) if cfg and cfg.affiliation_id is not None else None
    bases = session.exec(select(Base)).all()
    if my_aff is not None:
        return [int(b.id) for b in bases if b.affiliation_id is not None and int(b.affiliation_id) == my_aff]
    # No affiliation configured: bases with any affiliation were created from
    # our own positions, so treat them as ours.
    return [int(b.id) for b in bases if b.affiliation_id is not None]


def _to_candidate(br: BaseResource, base_names: dict[int, str]) -> ResourceCandidate:
    return ResourceCandidate(
        base_id=int(br.base_id),
        base_name=base_names.get(int(br.base_id), f"Base {br.base_id}"),
        resource_id=int(br.resource_id),
        resource_yield=float(br.resource_yield or 0.0),
        resource_drop=int(br.resource_drop or 0),
        resource_size=int(br.resource_size or 0),
        complexes=_complexes(br),
        next_complex_output=next_complex_output(br),
    )


def _owned_hub_clusters(session: Session) -> list[tuple[int, set[int]]]:
    """Owned hub base ids paired with their (hub + outposts) cluster.

    Mirrors Rails: only hubs that actually have outposts are reported.
    """
    my_ids = set(_my_base_ids(session))
    outposts_by_hub: dict[int, list[int]] = {}
    for b in session.exec(select(Base).where(Base.hub_id.is_not(None))).all():  # type: ignore[union-attr]
        outposts_by_hub.setdefault(int(b.hub_id), []).append(int(b.id))

    out: list[tuple[int, set[int]]] = []
    for hub_id in sorted(my_ids):
        cluster = _cluster_base_ids(hub_id, outposts_by_hub)
        if len(cluster) > 1:
            out.append((hub_id, cluster))
    return out


def _hub_production_consumption(
    session: Session,
    hub_id: int,
    cluster: set[int],
    resources_by_base: dict[int, list[BaseResource]],
) -> tuple[dict[int, float], dict[int, float]]:
    # Production: current output per item across hub + outposts.
    production: dict[int, float] = {}
    for bid in cluster:
        for br in resources_by_base.get(bid, []):
            production[int(br.item_id)] = production.get(int(br.item_id), 0.0) + current_output(br)

    # Consumption: raw materials of the hub's running mass production lines.
    consumption: dict[int, float] = {}
    for mp in session.exec(select(MassProduction).where(MassProduction.base_id == hub_id)).all():
        mats = mp_raw_materials(session, mp)
        if not mats:
            continue
        for iid, qty in mats.items():
            consumption[iid] = consumption.get(iid, 0.0) + qty

    return production, consumption


def _resources_by_base(all_resources: list[BaseResource]) -> dict[int, list[BaseResource]]:
    out: dict[int, list[BaseResource]] = {}
    for br in all_resources:
        out.setdefault(int(br.base_id), []).append(br)
    return out


def _best_cluster_deposit(
    eligible: list[BaseResource],
    cluster: set[int],
    item_id: int,
    base_names: dict[int, str],
) -> ResourceCandidate | None:
    local = [br for br in eligible if int(br.base_id) in cluster and int(br.item_id) == item_id]
    if not local:
        return None
    local.sort(key=next_complex_output, reverse=True)
    return _to_candidate(local[0], base_names)


def compute_mining_jobs(session: Session, *, weeks_threshold: int = WEEKS_WARNING_THRESHOLD) -> MiningJobsReport:
    base_names = {int(b.id): (b.name or f"Base {b.id}") for b in session.exec(select(Base)).all()}
    item_names = {int(i.id): i.name for i in session.exec(select(Item)).all()}

    all_resources = session.exec(select(BaseResource)).all()
    resources_by_base = _resources_by_base(all_resources)
    eligible = [br for br in all_resources if _min_week_yield_ok(br)]

    clusters = _owned_hub_clusters(session)

    jobs: list[MiningJobRow] = []
    rare: dict[int, RareOreRow] = {}

    for hub_id, cluster in clusters:
        production, consumption = _hub_production_consumption(session, hub_id, cluster, resources_by_base)

        for item_id, cons in consumption.items():
            if cons <= 0:
                continue
            prod = production.get(item_id, 0.0)
            weekly_burn = int(round(cons - prod))
            if weekly_burn <= 0:
                continue  # 'Forever' in Rails — never a mining job
            available = _count_item(session, hub_id, item_id)
            weeks_remaining = int(round(available / weekly_burn))
            if weeks_remaining >= weeks_threshold:
                continue

            best = _best_cluster_deposit(eligible, cluster, item_id, base_names)

            if best is not None:
                jobs.append(
                    MiningJobRow(
                        base_id=hub_id,
                        base_name=base_names.get(hub_id, f"Base {hub_id}"),
                        item_id=item_id,
                        item_name=item_names.get(item_id, f"Item {item_id}"),
                        available=available,
                        production=int(round(prod)),
                        consumption=int(round(cons)),
                        weekly_burn=weekly_burn,
                        weeks_remaining=weeks_remaining,
                        best_resource=best,
                    )
                )
            elif item_id not in rare:
                # No local deposit anywhere in the cluster: list the best
                # deposits across all known bases (Rails resources_for_item).
                anywhere = [br for br in eligible if int(br.item_id) == item_id]
                seen_bases: set[int] = set()
                unique: list[BaseResource] = []
                for br in sorted(anywhere, key=next_complex_output, reverse=True):
                    if int(br.base_id) in seen_bases:
                        continue
                    seen_bases.add(int(br.base_id))
                    unique.append(br)
                rare[item_id] = RareOreRow(
                    item_id=item_id,
                    item_name=item_names.get(item_id, f"Item {item_id}"),
                    candidates=[_to_candidate(br, base_names) for br in unique[:21]],
                )

    jobs.sort(key=lambda j: j.weeks_remaining)
    rare_rows = sorted(rare.values(), key=lambda r: r.item_name.lower())
    return MiningJobsReport(jobs=jobs, rare_ores=rare_rows, hub_count=len(clusters))


def compute_resource_balance(session: Session) -> list[ResourceBalanceRow]:
    """Full production-vs-consumption for every item touched by an owned hub.

    Unlike `compute_mining_jobs`, this keeps healthy items (weeks_remaining
    is None == 'Forever') so you can see resource usage even when nothing is
    running low.
    """
    base_names = {int(b.id): (b.name or f"Base {b.id}") for b in session.exec(select(Base)).all()}
    item_names = {int(i.id): i.name for i in session.exec(select(Item)).all()}

    all_resources = session.exec(select(BaseResource)).all()
    resources_by_base = _resources_by_base(all_resources)
    eligible = [br for br in all_resources if _min_week_yield_ok(br)]

    rows: list[ResourceBalanceRow] = []
    for hub_id, cluster in _owned_hub_clusters(session):
        production, consumption = _hub_production_consumption(session, hub_id, cluster, resources_by_base)

        for item_id in sorted(set(production) | set(consumption)):
            prod = production.get(item_id, 0.0)
            cons = consumption.get(item_id, 0.0)
            if prod <= 0 and cons <= 0:
                continue
            weekly_burn = int(round(cons - prod))
            available = _count_item(session, hub_id, item_id)
            weeks_remaining = int(round(available / weekly_burn)) if weekly_burn > 0 else None
            rows.append(
                ResourceBalanceRow(
                    base_id=hub_id,
                    base_name=base_names.get(hub_id, f"Base {hub_id}"),
                    item_id=item_id,
                    item_name=item_names.get(item_id, f"Item {item_id}"),
                    available=available,
                    production=int(round(prod)),
                    consumption=int(round(cons)),
                    weekly_burn=weekly_burn,
                    weeks_remaining=weeks_remaining,
                    best_resource=_best_cluster_deposit(eligible, cluster, item_id, base_names),
                )
            )

    # Most urgent first; 'Forever' (None) last.
    rows.sort(key=lambda r: (r.weeks_remaining is None, r.weeks_remaining or 0, -r.consumption))
    return rows


def _cluster_base_ids(hub_id: int, outposts_by_hub: dict[int, list[int]]) -> set[int]:
    """Hub + its outposts, following nested hub links (mirrors Rails recursion)."""
    cluster: set[int] = set()
    stack = [hub_id]
    while stack:
        bid = stack.pop()
        if bid in cluster:
            continue
        cluster.add(bid)
        stack.extend(outposts_by_hub.get(bid, []))
    return cluster


def _count_item(session: Session, base_id: int, item_id: int) -> int:
    rows = session.exec(
        select(BaseItem).where(BaseItem.base_id == int(base_id), BaseItem.item_id == int(item_id))
    ).all()
    if not rows:
        return 0
    for bi in rows:
        if bi.category == "Inventory":
            return int(bi.quantity)
    return int(rows[0].quantity)
