from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from phoenixtools_app.db.models import Base, BaseItem, Item, MarketBuy, MarketDatum, MarketSell
from phoenixtools_app.services.phoenix_order import PhoenixOrder


@dataclass(frozen=True)
class CompetitiveBuyRow:
    item_id: int
    item_name: str
    recommended_buy_price: float
    recommended_buy_volume: int
    best_sell_base_id: int
    best_sell_price: float
    best_buy_base_id: int
    best_buy_price: float
    profit: float


def _latest_market_datum_id(session: Session) -> int | None:
    md = session.exec(select(MarketDatum).order_by(MarketDatum.market_time.desc())).first()
    return int(md.id) if md and md.id is not None else None


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
    """
    Approximates Rails `competitive_buyable_goods` using latest market snapshot.
    Full parity needs item types + planetary market columns on Base.
    """
    md_id = _latest_market_datum_id(session)
    if md_id is None:
        return []

    base = session.get(Base, int(base_id))
    if base is None:
        return []

    buys = session.exec(select(MarketBuy).where(MarketBuy.market_datum_id == md_id)).all()
    sells = session.exec(select(MarketSell).where(MarketSell.market_datum_id == md_id)).all()

    best_buy: dict[int, MarketBuy] = {}
    for b in buys:
        cur = best_buy.get(int(b.item_id))
        if cur is None or float(b.price) > float(cur.price):
            best_buy[int(b.item_id)] = b

    best_sell: dict[int, MarketSell] = {}
    for s in sells:
        cur = best_sell.get(int(s.item_id))
        if cur is None or float(s.price) < float(cur.price):
            best_sell[int(s.item_id)] = s

    out: list[CompetitiveBuyRow] = []
    for item_id in set(best_buy.keys()) & set(best_sell.keys()):
        bb = best_buy[item_id]
        bs = best_sell[item_id]
        if int(bb.base_id) == int(base_id) or int(bs.base_id) == int(base_id):
            continue
        local_sell = session.exec(
            select(MarketSell).where(MarketSell.base_id == int(base_id), MarketSell.item_id == int(item_id))
        ).first()
        if local_sell is not None:
            continue

        profit = float(bb.price) - float(bs.price)
        if profit <= 0:
            continue

        # Rails uses planetary local_price * tier; without that, interpolate between best sell and best buy.
        sell_p = float(bs.price)
        buy_p = float(bb.price)
        rbp = round(sell_p + profit * 0.35, 2)
        if rbp <= sell_p or rbp >= buy_p:
            continue
        rbv = int(min(100_000, max(1, 50_000 / rbp))) if rbp > 0 else 0

        item = session.get(Item, int(item_id))
        name = item.name if item else f"Item {item_id}"

        out.append(
            CompetitiveBuyRow(
                item_id=int(item_id),
                item_name=name,
                recommended_buy_price=rbp,
                recommended_buy_volume=rbv,
                best_sell_base_id=int(bs.base_id),
                best_sell_price=float(bs.price),
                best_buy_base_id=int(bb.base_id),
                best_buy_price=float(bb.price),
                profit=profit,
            )
        )

    out.sort(key=lambda r: r.profit, reverse=True)
    return out


def competitive_buy_orders(session: Session, base_id: int) -> list[PhoenixOrder]:
    rows = competitive_buy_rows(session, base_id)
    orders: list[PhoenixOrder] = []
    for r in rows:
        if r.recommended_buy_volume < 1 or r.recommended_buy_price <= 0:
            continue
        orders.append(
            PhoenixOrder.market_buy(
                r.item_id,
                r.recommended_buy_volume,
                r.recommended_buy_price,
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
    """Items that have both buys and sells in the latest market (for middleman picker)."""
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
        return "; No competitive buy orders generated (market may be empty or no eligible items)."
    lines = [f"; Competitive market buy orders for base {base_id} ({len(orders)} orders)", ""]
    lines.extend(str(o) for o in orders)
    return "\n".join(lines)
