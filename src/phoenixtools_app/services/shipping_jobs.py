from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session, select

from phoenixtools_app.db.models import Base, Item, ItemGroup, ItemType, StarSystem
from phoenixtools_app.services.mining_jobs import _my_base_ids
from phoenixtools_app.services.pathing import find_quickest_path, legs_to_squad_orders, shortest_path
from phoenixtools_app.services.phoenix_order import PhoenixOrder
from phoenixtools_app.services.trade_routes import IN_SYSTEM_TRAVEL_APPROX, TYPE_LIFE, TYPE_ORE

# Rails Base#time_from_system returns this when no path exists.
NO_PATH_TIME = 1000


@dataclass(frozen=True)
class GroupSummary:
    group_id: int
    name: str
    total_quantity: int
    lines: int


def group_summaries_for_base(session: Session, base_id: int) -> list[GroupSummary]:
    rows = session.exec(select(ItemGroup).where(ItemGroup.base_id == int(base_id)).order_by(ItemGroup.group_id)).all()
    by_group: dict[int, GroupSummary] = {}
    for ig in rows:
        gid = int(ig.group_id)
        cur = by_group.get(gid)
        qty = int(ig.quantity)
        if cur is None:
            by_group[gid] = GroupSummary(group_id=gid, name=ig.name, total_quantity=qty, lines=1)
        else:
            by_group[gid] = GroupSummary(
                group_id=gid,
                name=cur.name,
                total_quantity=cur.total_quantity + qty,
                lines=cur.lines + 1,
            )
    return sorted(by_group.values(), key=lambda g: g.group_id)


def squadron_move_group_orders(
    session: Session,
    *,
    source_base_id: int,
    destination_base_id: int,
    group_id: int,
    pickup_quantity: int,
) -> str:
    src = session.get(Base, int(source_base_id))
    dst = session.get(Base, int(destination_base_id))
    if src is None or dst is None:
        raise RuntimeError("Invalid source/destination base.")
    if src.star_system_id is None or dst.star_system_id is None:
        raise RuntimeError("Source/destination base missing star system.")

    sys_names = {int(s.id): s.name for s in session.exec(select(StarSystem)).all()}
    orders: list[PhoenixOrder] = [
        PhoenixOrder.squadron_start(),
        PhoenixOrder.navigation_hazard_status(True),
        PhoenixOrder.pickup_from_item_group(int(src.id), int(pickup_quantity), str(group_id)),
        PhoenixOrder.squadron_stop(),
    ]

    if int(src.star_system_id) == int(dst.star_system_id):
        orders += [PhoenixOrder.wait_for_tus(240), PhoenixOrder.squadron_start(), PhoenixOrder.move_to_base(int(dst.id))]
    else:
        path = shortest_path(session, int(src.star_system_id), int(dst.star_system_id))
        if path is None:
            raise RuntimeError("No known jump-link path between source and destination systems.")
        orders += [PhoenixOrder.wait_for_tus(240), PhoenixOrder.squadron_start()]
        orders = legs_to_squad_orders(path.legs, orders)
        orders.append(PhoenixOrder.move_to_base(int(dst.id)))

    orders += [
        PhoenixOrder.squadron_stop(),
        PhoenixOrder.deliver_items(int(dst.id), int(pickup_quantity)),
        PhoenixOrder.squadron_stop(),
    ]

    lines = [
        f"; Item Group Shipping: {src.name or src.id} -> {dst.name or dst.id}",
        f"; Group ID: {group_id}",
        f"; Quantity: {pickup_quantity}",
    ]
    if int(src.star_system_id) != int(dst.star_system_id):
        path = shortest_path(session, int(src.star_system_id), int(dst.star_system_id))
        if path:
            pretty = " -> ".join(sys_names.get(i, str(i)) for i in path.system_ids)
            lines.append(f"; Path: {pretty}")
            lines.append(f"; TU cost: {path.tu_cost}")
    lines.append("")
    lines.extend(str(o) for o in orders)
    return "\n".join(lines)


# --- Shipping jobs overview (Rails BasesController#shipping_jobs) ---


@dataclass(frozen=True)
class ShippingGroup:
    group_id: int
    group_name: str
    total_mass: int
    total_cargo: int
    total_life: int
    total_ores: int
    lines: int


@dataclass(frozen=True)
class ShippingBaseRow:
    base_id: int
    base_name: str
    system_id: int | None
    system_name: str
    travel_time: int | None  # None when "nearest" filter is not applied
    groups: list[ShippingGroup] = field(default_factory=list)


@dataclass(frozen=True)
class ShippingJobsReport:
    nearest_system_id: int | None
    show_cargo: bool
    show_life: bool
    show_ores: bool
    rows: list[ShippingBaseRow] = field(default_factory=list)


def _item_category(type_name: str | None) -> str:
    """Rails Item#cargo?/life?/ore? -> exactly one bucket."""
    if type_name and type_name in TYPE_ORE:
        return "ore"
    if type_name and type_name in TYPE_LIFE:
        return "life"
    return "cargo"


def _grouped_item_groups(
    session: Session,
    base_id: int,
    item_mass: dict[int, int],
    item_category: dict[int, str],
) -> list[ShippingGroup]:
    rows = session.exec(
        select(ItemGroup).where(ItemGroup.base_id == int(base_id)).order_by(ItemGroup.group_id)
    ).all()
    acc: dict[int, dict] = {}
    for ig in rows:
        gid = int(ig.group_id)
        g = acc.get(gid)
        if g is None:
            g = {"group_id": gid, "group_name": ig.name, "mass": 0, "cargo": 0, "life": 0, "ores": 0, "lines": 0}
            acc[gid] = g
        total_mass = int(ig.quantity) * int(item_mass.get(int(ig.item_id), 0))
        g["lines"] += 1
        g["mass"] += total_mass
        cat = item_category.get(int(ig.item_id), "cargo")
        if cat == "ore":
            g["ores"] += total_mass
        elif cat == "life":
            g["life"] += total_mass
        else:
            g["cargo"] += total_mass
    return [
        ShippingGroup(
            group_id=g["group_id"],
            group_name=g["group_name"],
            total_mass=g["mass"],
            total_cargo=g["cargo"],
            total_life=g["life"],
            total_ores=g["ores"],
            lines=g["lines"],
        )
        for g in sorted(acc.values(), key=lambda x: x["group_id"])
    ]


def _time_from_system(session: Session, base: Base, system_id: int) -> int:
    """Rails Base#time_from_system."""
    if base.star_system_id is None:
        return NO_PATH_TIME
    if int(base.star_system_id) == int(system_id):
        return IN_SYSTEM_TRAVEL_APPROX
    p = find_quickest_path(session, int(system_id), int(base.star_system_id))
    if p is None:
        return NO_PATH_TIME
    return int(p.tu_cost) + IN_SYSTEM_TRAVEL_APPROX


def compute_shipping_jobs(
    session: Session,
    *,
    nearest_system_id: int | None = None,
    show_cargo: bool = True,
    show_life: bool = True,
    show_ores: bool = True,
) -> ShippingJobsReport:
    """
    Rails `BasesController#shipping_jobs`: every owned base with item groups, with cargo/life/ore
    mass totals, optionally restricted to bases reachable from a 'nearest' system and sorted by
    travel time from it.
    """
    item_mass: dict[int, int] = {}
    item_category: dict[int, str] = {}
    type_names = {int(t.id): t.name for t in session.exec(select(ItemType)).all()}
    for it in session.exec(select(Item)).all():
        item_mass[int(it.id)] = int(it.mass or 0)
        tn = type_names.get(int(it.item_type_id)) if it.item_type_id is not None else None
        item_category[int(it.id)] = _item_category(tn)

    systems = {int(s.id): s.name for s in session.exec(select(StarSystem)).all()}
    base_ids = set(_my_base_ids(session))

    rows: list[ShippingBaseRow] = []
    for base in session.exec(select(Base)).all():
        if int(base.id) not in base_ids:
            continue
        groups = _grouped_item_groups(session, int(base.id), item_mass, item_category)
        visible = [
            g
            for g in groups
            if (show_cargo and g.total_cargo > 0)
            or (show_life and g.total_life > 0)
            or (show_ores and g.total_ores > 0)
        ]
        if not visible:
            continue

        travel_time: int | None = None
        if nearest_system_id is not None:
            travel_time = _time_from_system(session, base, int(nearest_system_id))
            if travel_time >= NO_PATH_TIME:
                continue

        rows.append(
            ShippingBaseRow(
                base_id=int(base.id),
                base_name=base.name or f"Base {base.id}",
                system_id=int(base.star_system_id) if base.star_system_id is not None else None,
                system_name=systems.get(int(base.star_system_id), "—") if base.star_system_id is not None else "—",
                travel_time=travel_time,
                groups=visible,
            )
        )

    if nearest_system_id is not None:
        rows.sort(key=lambda r: (r.travel_time if r.travel_time is not None else NO_PATH_TIME))
    else:
        rows.sort(key=lambda r: r.base_name.lower())

    return ShippingJobsReport(
        nearest_system_id=nearest_system_id,
        show_cargo=show_cargo,
        show_life=show_life,
        show_ores=show_ores,
        rows=rows,
    )

