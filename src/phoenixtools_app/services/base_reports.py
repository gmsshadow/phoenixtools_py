from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from phoenixtools_app.db.models import Base, BaseItem, Item, MarketBuy, MarketDatum, MarketSell
from phoenixtools_app.services.phoenix_order import PhoenixOrder
from phoenixtools_app.services.planetary_market import (
    competitive_buy_price_ok,
    local_price,
    market_bracket,
    recommended_buy_price,
    recommended_buy_volume,
    sellable_items,
    worth_buying,
    weeks_supply_of_same_category,
    _best_buy_sell,
    _latest_market_datum_id,
    _selling_item,
)


@dataclass(frozen=True)
class CompetitiveBuyRow:
    item_id: int
    item_name: str
    recommended_buy_price: float
    recommended_buy_volume: int
    local_value: float
    market_bracket: str
    weeks_supply: str | int
    worth_buying: bool
    best_sell_base_id: int | None
    best_sell_price: float | None
    best_buy_base_id: int | None
    best_buy_price: float | None


def trade_items_for_base(session: Session, base_id: int) -> list[tuple[BaseItem, Item]]:
    rows = session.exec(
        select(BaseItem, Item)
        .where(BaseItem.base_id == int(base_id))
        .where(BaseItem.category == "Trade Items")
        .where(BaseItem.item_id == Item.id)
        .order_by(Item.name)
    ).all()
    return [(bi, it) for bi, it in rows]


def raw_materials_for_base(session: Session, base_id: int) -> list[tuple[BaseItem, Item]]:
    rows = session.exec(
        select(BaseItem, Item)
        .where(BaseItem.base_id == int(base_id))
        .where(BaseItem.category == "Raw Materials")
        .where(BaseItem.item_id == Item.id)
        .order_by(Item.name)
    ).all()
    return [(bi, it) for bi, it in rows]


def competitive_buy_rows(session: Session, base_id: int) -> list[CompetitiveBuyRow]:
    """Rails Base#competitive_buyable_goods with planetary market pricing."""
    base = session.get(Base, int(base_id))
    if base is None or base.trade_good_value_per_mu is None:
        return []

    md_id = _latest_market_datum_id(session)
    if md_id is None:
        return []

    out: list[CompetitiveBuyRow] = []
    for item, profile in sellable_items(session):
        if _selling_item(session, int(base_id), int(item.id), md_id):
            continue
        best_buy, best_sell = _best_buy_sell(session, int(item.id), md_id)
        if not competitive_buy_price_ok(
            session, base, profile, md_id=md_id, best_buy=best_buy, best_sell=best_sell
        ):
            continue
        rbp = recommended_buy_price(session, profile, base)
        rbv = recommended_buy_volume(session, profile, base)
        out.append(
            CompetitiveBuyRow(
                item_id=int(item.id),
                item_name=item.name,
                recommended_buy_price=rbp,
                recommended_buy_volume=rbv,
                local_value=local_price(session, profile, base),
                market_bracket=market_bracket(session, profile, base),
                weeks_supply=weeks_supply_of_same_category(session, base, profile) or "—",
                worth_buying=worth_buying(session, base, profile),
                best_sell_base_id=int(best_sell.base_id) if best_sell else None,
                best_sell_price=float(best_sell.price) if best_sell else None,
                best_buy_base_id=int(best_buy.base_id) if best_buy else None,
                best_buy_price=float(best_buy.price) if best_buy else None,
            )
        )
    out.sort(key=lambda r: (not r.worth_buying, r.item_name.lower()))
    return out


def competitive_buy_orders(session: Session, base_id: int) -> list[PhoenixOrder]:
    base = session.get(Base, int(base_id))
    if base is None:
        return []
    orders: list[PhoenixOrder] = []
    for row in competitive_buy_rows(session, base_id):
        if not row.worth_buying:
            continue
        if row.recommended_buy_volume < 1 or row.recommended_buy_price <= 0:
            continue
        orders.append(
            PhoenixOrder.market_buy(
                row.item_id,
                row.recommended_buy_volume,
                row.recommended_buy_price,
                False,
                False,
                0,
            )
        )
    return orders


@dataclass(frozen=True)
class MiddlemanInfo:
    item_id: int
    item_name: str
    middleman_buy_price: float
    middleman_sell_price: float
    quantity: int
    best_sell_price: float
    best_buy_price: float
    profit: float


def middleman_info(session: Session, item_id: int) -> MiddlemanInfo | None:
    md_id = _latest_market_datum_id(session)
    if md_id is None:
        return None

    buys = session.exec(select(MarketBuy).where(MarketBuy.market_datum_id == md_id, MarketBuy.item_id == int(item_id))).all()
    sells = session.exec(select(MarketSell).where(MarketSell.market_datum_id == md_id, MarketSell.item_id == int(item_id))).all()
    if not buys or not sells:
        return None

    best_buy = max(buys, key=lambda b: float(b.price))
    best_sell = min(sells, key=lambda s: float(s.price))
    profit = round(float(best_buy.price) - float(best_sell.price), 2)
    if profit <= 0:
        return None

    m_buy = round(float(best_sell.price) + (profit * 0.4), 2)
    m_sell = round(m_buy + (profit * 0.2), 2)
    if m_sell <= m_buy or m_buy >= 25:
        return None

    qty = int(50000 / m_buy) if m_buy > 0 else 0
    item = session.get(Item, int(item_id))
    return MiddlemanInfo(
        item_id=int(item_id),
        item_name=item.name if item else f"Item {item_id}",
        middleman_buy_price=m_buy,
        middleman_sell_price=m_sell,
        quantity=qty,
        best_sell_price=float(best_sell.price),
        best_buy_price=float(best_buy.price),
        profit=profit,
    )


def middleman_candidate_items(session: Session) -> list[tuple[int, str]]:
    md_id = _latest_market_datum_id(session)
    if md_id is None:
        return []

    buy_ids = {int(b.item_id) for b in session.exec(select(MarketBuy).where(MarketBuy.market_datum_id == md_id)).all()}
    sell_ids = {int(s.item_id) for s in session.exec(select(MarketSell).where(MarketSell.market_datum_id == md_id)).all()}
    ids = sorted(buy_ids & sell_ids)
    out: list[tuple[int, str]] = []
    for iid in ids:
        info = middleman_info(session, iid)
        if info is None:
            continue
        out.append((iid, f"{info.item_name} ({iid})"))
    out.sort(key=lambda t: t[1].lower())
    return out


def middleman_orders_text(session: Session, item_id: int) -> str:
    info = middleman_info(session, item_id)
    if info is None or info.quantity < 1:
        return "; No middleman opportunity for this item (market data / thresholds)."

    orders = [
        PhoenixOrder.market_buy(info.item_id, info.quantity, info.middleman_buy_price, False, False, 3),
        PhoenixOrder.market_sell(info.item_id, 50_000, info.middleman_sell_price, False, False, 3),
    ]
    lines = [
        f"; Middleman: {info.item_name}",
        f"; Buy @ {info.middleman_buy_price}, Sell @ {info.middleman_sell_price}, qty {info.quantity}",
        "",
    ]
    lines.extend(str(o) for o in orders)
    return "\n".join(lines)


def competitive_buy_orders_text(session: Session, base_id: int) -> str:
    orders = competitive_buy_orders(session, base_id)
    if not orders:
        return "; No competitive buy orders generated (import a turn with planetary market data + refresh market)."
    lines = [f"; Competitive market buy orders for base {base_id} ({len(orders)} orders)", ""]
    lines.extend(str(o) for o in orders)
    return "\n".join(lines)
