from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from phoenixtools_app.db.models import Base, ItemGroup, StarSystem
from phoenixtools_app.services.pathing import shortest_path
from phoenixtools_app.services.phoenix_order import PhoenixOrder


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
        if len(path.system_ids) > 1:
            orders.append(PhoenixOrder.move_to_random_jump_quad())
        for sid in path.system_ids[1:]:
            orders.append(PhoenixOrder.jump(int(sid)))
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

