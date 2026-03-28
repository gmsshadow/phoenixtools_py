from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlmodel import Session, delete, select

from phoenixtools_app.db.models import (
    Base,
    CelestialBody,
    Item,
    ItemType,
    MarketBuy,
    MarketDatum,
    MarketSell,
    Path,
    TradeRoute,
)
from phoenixtools_app.services.pathing import find_quickest_path, path_requires_gate_keys, shortest_path
from phoenixtools_app.services.phoenix_order import PhoenixOrder

# Rails TradeRoute
TRANSACTION_TIME_APPROX = 20
IN_SYSTEM_TRAVEL_APPROX = 50
MAX_TUS_DEFAULT = 300
BARGE_CARGO_CAPACITY = 2400
BARGE_LIFE_CAPACITY = 1800
BARGE_ORE_CAPACITY = 9000
BARGE_WAGES = 5

TYPE_PERSONNEL = frozenset({"Troop", "Pirate", "Officer", "Employee"})
TYPE_LIFE = frozenset(
    {"Operative", "Civilian", "Prisoner", "Trade Life", "Plants", "Troop", "Pirate", "Officer", "Employee"}
)
TYPE_ORE = frozenset({"Ore", "Alloy"})


@dataclass(frozen=True)
class TradeRouteRow:
    """One persisted trade route + Rails-style economics (for UI / filters)."""

    id: int
    market_datum_id: int | None
    item_id: int
    item_name: str
    from_base_id: int
    from_base_name: str
    to_base_id: int
    to_base_name: str
    sell_price: float
    buy_price: float
    volume: int
    profit_per_unit: float
    total_profit: int
    path_tu: int
    travel_time: int
    barge_weekly_profit: int
    barges_assigned: int
    barges_max: int
    requires_gate_keys: bool
    quantity_per_barge: int


@dataclass
class TradeRouteFilter:
    start_system_id: int | None = None
    max_tus: int = MAX_TUS_DEFAULT
    item_type_id: int | None = None
    no_keys: bool = False
    exclude_dest_affiliation_id: int | None = None
    lifeform_mode: str | None = None  # None | "only" | "exclude"


def _latest_market_datum_id(session: Session) -> int | None:
    md = session.exec(select(MarketDatum).order_by(MarketDatum.market_time.desc())).first()
    return int(md.id) if md and md.id is not None else None


def _item_type_name(session: Session, item: Item) -> str | None:
    if item.item_type_id is None:
        return None
    it = session.get(ItemType, int(item.item_type_id))
    return it.name if it else None


def _item_type_category(type_name: str | None) -> str:
    if not type_name:
        return "cargo"
    if type_name in TYPE_ORE:
        return "ore"
    if type_name in TYPE_LIFE:
        return "personnel" if type_name in TYPE_PERSONNEL else "life"
    return "cargo"


def _item_personnel(session: Session, item: Item) -> bool:
    tn = _item_type_name(session, item)
    return tn in TYPE_PERSONNEL if tn else False


def _item_lifeform_proxy(session: Session, item: Item) -> bool:
    """Rails uses item_attributes 'Lifeform'; approximate with life item-type family."""
    tn = _item_type_name(session, item)
    return tn in TYPE_LIFE if tn else False


def _quantity_per_barge(session: Session, item: Item) -> int:
    mass = int(item.mass or 0)
    if mass == 0:
        return 10_000_000
    cat = _item_type_category(_item_type_name(session, item))
    if cat == "ore":
        return max(1, BARGE_ORE_CAPACITY // mass)
    if cat == "life" or cat == "personnel":
        return max(1, BARGE_LIFE_CAPACITY // mass)
    return max(1, BARGE_CARGO_CAPACITY // mass)


def _time_to_start(session: Session, from_base: Base, start_system_id: int) -> int:
    if from_base.star_system_id is None:
        return 1000
    fsid = int(from_base.star_system_id)
    if fsid == int(start_system_id):
        return IN_SYSTEM_TRAVEL_APPROX
    p = find_quickest_path(session, int(start_system_id), fsid)
    if p is None:
        return 1000
    return int(p.tu_cost) + IN_SYSTEM_TRAVEL_APPROX


def _require_keys_to_start(session: Session, from_base: Base, start_system_id: int) -> bool:
    if from_base.star_system_id is None:
        return True
    if int(from_base.star_system_id) == int(start_system_id):
        return False
    p = find_quickest_path(session, int(start_system_id), int(from_base.star_system_id))
    if p is None:
        return True
    return path_requires_gate_keys(p)


def _travel_time(path_tu: int) -> int:
    return int(path_tu) + TRANSACTION_TIME_APPROX + IN_SYSTEM_TRAVEL_APPROX


def _barge_weekly_profit(
    *, travel_time: int, quantity_per_barge: int, profit_per_mu: float, total_volume: int, total_profit: int
) -> int:
    if travel_time > 0:
        max_trips = 300.0 / float(travel_time)
        raw = (max_trips * quantity_per_barge * profit_per_mu) - BARGE_WAGES
        wk = int(round(raw, 0))
    else:
        wk = total_profit
    return min(wk, total_profit)


def _market_legs(
    session: Session, tr: TradeRoute
) -> tuple[MarketSell | None, MarketBuy | None]:
    if tr.market_datum_id is None:
        return None, None
    sell_o = session.exec(
        select(MarketSell)
        .where(MarketSell.market_datum_id == int(tr.market_datum_id))
        .where(MarketSell.base_id == int(tr.from_id))
        .where(MarketSell.item_id == int(tr.item_id))
    ).first()
    buy_o = session.exec(
        select(MarketBuy)
        .where(MarketBuy.market_datum_id == int(tr.market_datum_id))
        .where(MarketBuy.base_id == int(tr.to_id))
        .where(MarketBuy.item_id == int(tr.item_id))
    ).first()
    return sell_o, buy_o


def trade_route_row(session: Session, tr: TradeRoute) -> TradeRouteRow | None:
    sell_o, buy_o = _market_legs(session, tr)
    if sell_o is None or buy_o is None:
        return None
    if float(sell_o.price) >= float(buy_o.price):
        return None

    item = session.get(Item, int(tr.item_id))
    if item is None:
        item = Item(id=int(tr.item_id), name=f"Item {tr.item_id}", mass=0)

    from_b = session.get(Base, int(tr.from_id))
    to_b = session.get(Base, int(tr.to_id))
    if from_b is None or to_b is None:
        return None

    path_tu = 0
    if tr.path_id is not None:
        prow = session.get(Path, int(tr.path_id))
        if prow is not None:
            path_tu = int(prow.tu_cost)
    tt = _travel_time(path_tu)

    vol = min(int(sell_o.quantity), int(buy_o.quantity))
    ppu = float(buy_o.price) - float(sell_o.price)
    if _item_personnel(session, item):
        ppu -= 2.0
    mass = int(item.mass or 0)
    profit_per_mu = ppu if mass == 0 else ppu / float(mass or 1)
    total_profit = int(round(ppu * vol, 0))

    qpb = _quantity_per_barge(session, item)
    barges_max = max(1, (vol + qpb - 1) // qpb)
    bwp = _barge_weekly_profit(
        travel_time=tt,
        quantity_per_barge=qpb,
        profit_per_mu=profit_per_mu,
        total_volume=vol,
        total_profit=total_profit,
    )

    req_keys = False
    if tr.path_id is not None:
        prow = session.get(Path, int(tr.path_id))
        if prow is not None:
            req_keys = path_requires_gate_keys(prow)

    assert tr.id is not None
    return TradeRouteRow(
        id=int(tr.id),
        market_datum_id=int(tr.market_datum_id) if tr.market_datum_id is not None else None,
        item_id=int(tr.item_id),
        item_name=item.name,
        from_base_id=int(tr.from_id),
        from_base_name=from_b.name or f"Base {tr.from_id}",
        to_base_id=int(tr.to_id),
        to_base_name=to_b.name or f"Base {tr.to_id}",
        sell_price=float(sell_o.price),
        buy_price=float(buy_o.price),
        volume=vol,
        profit_per_unit=ppu,
        total_profit=total_profit,
        path_tu=path_tu,
        travel_time=tt,
        barge_weekly_profit=bwp,
        barges_assigned=int(tr.barges_assigned or 0),
        barges_max=barges_max,
        requires_gate_keys=req_keys,
        quantity_per_barge=qpb,
    )


def query_trade_routes(session: Session, f: TradeRouteFilter | None = None) -> list[TradeRouteRow]:
    f = f or TradeRouteFilter()
    rows: list[TradeRouteRow] = []
    for tr in session.exec(select(TradeRoute)).all():
        row = trade_route_row(session, tr)
        if row is None:
            continue
        if f.item_type_id is not None:
            item = session.get(Item, row.item_id)
            if item is None or int(item.item_type_id or -1) != int(f.item_type_id):
                continue
        if f.exclude_dest_affiliation_id is not None:
            to_b = session.get(Base, row.to_base_id)
            if to_b is not None and int(to_b.affiliation_id or -1) == int(f.exclude_dest_affiliation_id):
                continue
        if f.lifeform_mode == "only":
            item = session.get(Item, row.item_id) or Item(id=row.item_id, name="", mass=0)
            if not _item_lifeform_proxy(session, item):
                continue
        elif f.lifeform_mode == "exclude":
            item = session.get(Item, row.item_id) or Item(id=row.item_id, name="", mass=0)
            if _item_lifeform_proxy(session, item):
                continue
        if f.no_keys:
            if row.requires_gate_keys:
                continue
            if f.start_system_id is not None:
                fb = session.get(Base, row.from_base_id)
                if fb is None or _require_keys_to_start(session, fb, int(f.start_system_id)):
                    continue
        if f.start_system_id is not None:
            from_b = session.get(Base, row.from_base_id)
            if from_b is None:
                continue
            combined = row.travel_time + _time_to_start(session, from_b, int(f.start_system_id))
            if combined > int(f.max_tus):
                continue
        else:
            if row.travel_time > int(f.max_tus):
                continue
        if not _barge_slots_available(row):
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.barge_weekly_profit, reverse=True)
    return rows


def _barge_slots_available(row: TradeRouteRow) -> bool:
    return row.barges_assigned < row.barges_max


def run_trade_route_generation(session: Session) -> int:
    """
    Rails `TradeRoute.generate!` using latest market snapshot and jump-link paths.
    """
    md_id = _latest_market_datum_id(session)
    if md_id is None:
        return 0

    session.exec(delete(TradeRoute))
    session.commit()

    sells = session.exec(select(MarketSell).where(MarketSell.market_datum_id == md_id)).all()
    buys = session.exec(select(MarketBuy).where(MarketBuy.market_datum_id == md_id)).all()
    buys_by_item: dict[int, list[MarketBuy]] = defaultdict(list)
    for b in buys:
        buys_by_item[int(b.item_id)].append(b)

    pending: list[TradeRoute] = []
    for sell in sells:
        sell_base = session.get(Base, int(sell.base_id))
        if sell_base is None:
            continue
        for buy in buys_by_item.get(int(sell.item_id), []):
            buy_base = session.get(Base, int(buy.base_id))
            if buy_base is None:
                continue
            if getattr(buy_base, "blacklist", False):
                continue
            if buy_base.star_system_id is None or int(buy_base.star_system_id) == 0:
                continue
            if float(sell.price) >= float(buy.price):
                continue

            path_id = None
            if sell_base.star_system_id is None or int(sell_base.star_system_id) == 0:
                continue
            if int(sell_base.star_system_id) == int(buy_base.star_system_id):
                path_id = None
            else:
                path = find_quickest_path(session, int(sell_base.star_system_id), int(buy_base.star_system_id))
                if path is None or path.id is None:
                    continue
                path_id = int(path.id)

            pending.append(
                TradeRoute(
                    market_datum_id=md_id,
                    from_id=int(sell.base_id),
                    to_id=int(buy.base_id),
                    item_id=int(sell.item_id),
                    path_id=path_id,
                    barges_assigned=0,
                )
            )

    for tr in pending:
        session.add(tr)
    session.commit()
    return len(pending)


def assign_barge(session: Session, route_id: int) -> bool:
    tr = session.get(TradeRoute, int(route_id))
    if tr is None:
        return False
    row = trade_route_row(session, tr)
    if row is None or row.barges_assigned >= row.barges_max:
        return False
    tr.barges_assigned = int(tr.barges_assigned or 0) + 1
    session.add(tr)
    session.commit()
    return True


def available_volume(row: TradeRouteRow) -> int:
    if row.barges_assigned <= 0:
        return row.volume
    used = row.quantity_per_barge * row.barges_assigned
    return max(0, row.volume - used)


def orders_for_trade_route(session: Session, route_id: int) -> str:
    tr = session.get(TradeRoute, int(route_id))
    if tr is None:
        return "; ERROR: route not found"
    row = trade_route_row(session, tr)
    if row is None:
        return "; ERROR: route invalid or market legs missing"
    from_base = session.get(Base, row.from_base_id)
    to_base = session.get(Base, row.to_base_id)
    if from_base is None or to_base is None:
        return "; ERROR: missing base(s)"
    if from_base.star_system_id is None or to_base.star_system_id is None:
        return "; ERROR: base missing star_system_id"

    vol = available_volume(row)
    if vol <= 0:
        return "; ERROR: no volume left (assign barges consumed capacity)"

    from_cbody_id = None
    if from_base.celestial_body_id is not None:
        cb = session.get(CelestialBody, int(from_base.celestial_body_id))
        from_cbody_id = cb.cbody_id if cb is not None else None
    to_cbody_id = None
    if to_base.celestial_body_id is not None:
        cb = session.get(CelestialBody, int(to_base.celestial_body_id))
        to_cbody_id = cb.cbody_id if cb is not None else None

    lines: list[str] = []
    lines.append(f"; TradeRoute id={row.id}: {row.item_name}")
    lines.append(f"; Buy at: {row.from_base_name} (sell {row.sell_price})")
    lines.append(f"; Sell at: {row.to_base_name} (buy {row.buy_price})")
    lines.append(f"; Volume: {vol}")
    lines.append("")

    orders: list[PhoenixOrder] = []
    orders.append(PhoenixOrder.navigation_hazard_status(True))
    if from_cbody_id is not None:
        orders.append(PhoenixOrder.move_to_planet(int(from_base.star_system_id), int(from_cbody_id)))
    orders.append(PhoenixOrder.buy(int(from_base.id), int(row.item_id), int(vol)))

    path = shortest_path(session, int(from_base.star_system_id), int(to_base.star_system_id))
    if path is None:
        lines.append("; ERROR: no path between systems")
        return "\n".join(lines)

    if len(path.system_ids) > 1:
        orders.append(PhoenixOrder.move_to_random_jump_quad())
    for sys_id in path.system_ids[1:]:
        orders.append(PhoenixOrder.jump(int(sys_id)))

    if to_cbody_id is not None:
        orders.append(PhoenixOrder.move_to_planet(int(to_base.star_system_id), int(to_cbody_id)))
    orders.append(PhoenixOrder.sell(int(to_base.id), int(row.item_id), int(vol)))
    orders.append(PhoenixOrder.wait_for_tus(240))

    lines.extend(str(o) for o in orders)
    return "\n".join(lines)


# --- Legacy name for older imports ---
TradeRouteCandidate = TradeRouteRow


def orders_for_candidate(_c: TradeRouteRow) -> str:
    return "; Open orders preview in app (requires database session)."


def orders_for_candidate_with_session(session: Session, c: TradeRouteRow) -> str:
    return orders_for_trade_route(session, int(c.id))


def generate_trade_routes(session: Session, *, limit: int = 2000) -> list[TradeRouteRow]:
    """Load persisted routes (post-`run_trade_route_generation`), newest-first by barge profit."""
    rows = query_trade_routes(session, TradeRouteFilter())
    return rows[:limit]
