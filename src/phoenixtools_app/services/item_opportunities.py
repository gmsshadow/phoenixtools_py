from __future__ import annotations

from sqlmodel import Session, select

from phoenixtools_app.db.models import ItemAttribute, Periphery, TradeRoute
from phoenixtools_app.services.base_reports import middleman_info
from phoenixtools_app.services.planetary_market import SELLABLE_TYPES, _load_sellable_items
from phoenixtools_app.services.phoenix_order import PhoenixOrder


def list_periphery_choices(session: Session) -> list[tuple[int, str]]:
    rows = session.exec(select(Periphery).order_by(Periphery.name)).all()
    return [(int(p.id), p.name) for p in rows if p.id is not None]


def list_known_races(session: Session) -> list[str]:
    rows = session.exec(select(ItemAttribute).where(ItemAttribute.attr_key == "Race")).all()
    return sorted({r.attr_value.strip() for r in rows if r.attr_value and r.attr_value.strip()})


def _resolve_periphery_id(session: Session, periphery: str | int | None) -> int | None:
    if periphery is None:
        return None
    if isinstance(periphery, int):
        return int(periphery)
    text = str(periphery).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    row = session.exec(select(Periphery).where(Periphery.name == text)).first()
    return int(row.id) if row and row.id is not None else None


def item_ids_with_trade_routes(session: Session) -> set[int]:
    return {
        int(tr.item_id)
        for tr in session.exec(select(TradeRoute)).all()
        if tr.item_id is not None
    }


def filter_profitable_no_route(rows: list, *, routed_item_ids: set[int]) -> list:
    out = []
    for r in rows:
        if r.spread is None or float(r.spread) <= 0:
            continue
        if int(r.item_id) in routed_item_ids:
            continue
        out.append(r)
    out.sort(key=lambda r: (-float(r.spread or 0), r.name.lower()))
    return out


def sellable_item_ids_for_periphery(session: Session, periphery: str | int) -> set[int]:
    pid = _resolve_periphery_id(session, periphery)
    if pid is None:
        return set()
    ids: set[int] = set()
    for _item, profile in _load_sellable_items(session):
        if profile.origin_periphery_id is not None and int(profile.origin_periphery_id) == int(pid):
            ids.add(int(profile.item_id))
    return ids


def sellable_item_ids_for_race(session: Session, race: str) -> set[int]:
    race = race.strip()
    if not race:
        return set()
    ids: set[int] = set()
    for _item, profile in _load_sellable_items(session):
        if profile.race and profile.race.strip() == race:
            ids.add(int(profile.item_id))
    return ids


def middleman_orders_text_for_items(session: Session, item_ids: list[int]) -> str:
    """Rails periphery_goods / race_preferred_goods combined middleman order blocks."""
    all_orders: list[PhoenixOrder] = []
    for iid in item_ids:
        info = middleman_info(session, int(iid))
        if info is None or info.quantity < 1:
            continue
        all_orders.extend(
            [
                PhoenixOrder.market_buy(info.item_id, info.quantity, info.middleman_buy_price, False, False, 3),
                PhoenixOrder.market_sell(info.item_id, 50_000, info.middleman_sell_price, False, False, 3),
            ]
        )
    if not all_orders:
        return "; No middleman orders generated (items need buy+sell spread and buy price < 25)."
    order_count = len(all_orders)
    lines = [f"; Middleman orders ({order_count} order lines)", ""]
    lines.extend(str(o) for o in all_orders)
    return "\n".join(lines)
