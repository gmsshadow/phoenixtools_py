from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from phoenixtools_app.db.models import Base, CelestialBody, Item, MarketBuy, MarketSell
from phoenixtools_app.services.pathing import shortest_path
from phoenixtools_app.services.phoenix_order import PhoenixOrder


@dataclass(frozen=True)
class TradeRouteCandidate:
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
    total_profit: float


def generate_trade_routes(session: Session, *, limit: int = 500) -> list[TradeRouteCandidate]:
    """
    Simple first-pass route generation:
    - For each item, pick the best (lowest) sell and best (highest) buy across all bases.
    - If profitable, emit a candidate.

    This intentionally ignores pathing/keys/travel-time for now; those come later.
    """
    buys = session.exec(select(MarketBuy)).all()
    sells = session.exec(select(MarketSell)).all()

    best_buy: dict[int, MarketBuy] = {}
    for b in buys:
        cur = best_buy.get(b.item_id)
        if cur is None or b.price > cur.price:
            best_buy[b.item_id] = b

    best_sell: dict[int, MarketSell] = {}
    for s in sells:
        cur = best_sell.get(s.item_id)
        if cur is None or s.price < cur.price:
            best_sell[s.item_id] = s

    candidates: list[TradeRouteCandidate] = []
    for item_id, buy in best_buy.items():
        sell = best_sell.get(item_id)
        if sell is None:
            continue
        if buy.price <= sell.price:
            continue
        volume = min(int(buy.quantity), int(sell.quantity))
        if volume <= 0:
            continue

        item = session.get(Item, item_id) or Item(id=item_id, name=f"Item {item_id}")
        from_base = session.get(Base, int(sell.base_id)) or Base(id=int(sell.base_id))
        to_base = session.get(Base, int(buy.base_id)) or Base(id=int(buy.base_id))

        profit_per_unit = float(buy.price - sell.price)
        total_profit = float(profit_per_unit * volume)

        candidates.append(
            TradeRouteCandidate(
                item_id=item_id,
                item_name=item.name,
                from_base_id=int(sell.base_id),
                from_base_name=from_base.name or f"Base {sell.base_id}",
                to_base_id=int(buy.base_id),
                to_base_name=to_base.name or f"Base {buy.base_id}",
                sell_price=float(sell.price),
                buy_price=float(buy.price),
                volume=volume,
                profit_per_unit=profit_per_unit,
                total_profit=total_profit,
            )
        )

    candidates.sort(key=lambda c: c.total_profit, reverse=True)
    return candidates[:limit]


def orders_for_candidate(c: TradeRouteCandidate) -> str:
    # This function is called by the UI without passing a session.
    # Keep it pure text by emitting a header only; the UI will call the
    # session-backed variant below.
    return "\n".join(
        [
            f"; TradeRoute: {c.item_name}",
            "; (orders require database session)",
        ]
    )


def orders_for_candidate_with_session(session: Session, c: TradeRouteCandidate) -> str:
    from_base = session.get(Base, c.from_base_id)
    to_base = session.get(Base, c.to_base_id)
    if from_base is None or to_base is None:
        return "; ERROR: missing base(s) in database"
    if from_base.star_system_id is None or to_base.star_system_id is None:
        return "; ERROR: base missing star_system_id"

    from_cbody_id = None
    if from_base.celestial_body_id is not None:
        cb = session.get(CelestialBody, int(from_base.celestial_body_id))
        from_cbody_id = cb.cbody_id if cb is not None else None
    to_cbody_id = None
    if to_base.celestial_body_id is not None:
        cb = session.get(CelestialBody, int(to_base.celestial_body_id))
        to_cbody_id = cb.cbody_id if cb is not None else None

    lines: list[str] = []
    lines.append(f"; TradeRoute: {c.item_name}")
    lines.append(f"; Buy at: {c.from_base_name} (sell {c.sell_price})")
    lines.append(f"; Sell at: {c.to_base_name} (buy {c.buy_price})")
    lines.append(f"; Volume: {c.volume}")
    lines.append("")

    orders: list[PhoenixOrder] = []
    orders.append(PhoenixOrder.navigation_hazard_status(True))

    # Move to source base planet (if known), then buy.
    if from_cbody_id is not None:
        orders.append(PhoenixOrder.move_to_planet(int(from_base.star_system_id), int(from_cbody_id)))
    orders.append(PhoenixOrder.buy(int(from_base.id), int(c.item_id), int(c.volume)))

    # Travel (jump links) between systems.
    path = shortest_path(session, int(from_base.star_system_id), int(to_base.star_system_id))
    if path is None:
        lines.append("; ERROR: no path between systems")
    else:
        if len(path.system_ids) > 1:
            orders.append(PhoenixOrder.move_to_random_jump_quad())
        for sys_id in path.system_ids[1:]:
            orders.append(PhoenixOrder.jump(int(sys_id)))

    # Move to destination base planet (if known), then sell.
    if to_cbody_id is not None:
        orders.append(PhoenixOrder.move_to_planet(int(to_base.star_system_id), int(to_cbody_id)))
    orders.append(PhoenixOrder.sell(int(to_base.id), int(c.item_id), int(c.volume)))
    orders.append(PhoenixOrder.wait_for_tus(240))

    lines.extend(str(o) for o in orders)
    return "\n".join(lines)

