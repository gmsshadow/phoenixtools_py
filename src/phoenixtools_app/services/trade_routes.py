from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from phoenixtools_app.db.models import Base, Item, MarketBuy, MarketSell


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
    # Placeholder until we port `PhoenixOrder` and pathing.
    return "\n".join(
        [
            f"; TradeRoute: {c.item_name}",
            f"; Buy at: {c.from_base_name} (sell price {c.sell_price})",
            f"; Sell at: {c.to_base_name} (buy price {c.buy_price})",
            f"; Volume: {c.volume}",
        ]
    )

