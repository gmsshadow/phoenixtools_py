from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlmodel import Session, select

from phoenixtools_app.db.models import (
    Base,
    BaseItem,
    CelestialBody,
    Item,
    ItemAttribute,
    MarketBuy,
    MarketDatum,
    MarketSell,
    StarSystem,
)
from phoenixtools_app.importer.parsers import PlanetaryMarket
from phoenixtools_app.services.periphery_distances import trade_distance_modifier

MAXIMUM_WEEKS_TRADE_RESERVES = 26
SELLABLE_TYPES = frozenset({"Trade Good", "Life", "Drug"})


def apply_planetary_market(base: Base, market: PlanetaryMarket | None) -> None:
    if market is None or not market.has_trade_data:
        return
    for field in (
        "trade_good_value_per_mu",
        "life_good_value_per_mu",
        "drug_value_per_mu",
        "trade_good_low_value",
        "trade_good_high_value",
        "life_good_low_value",
        "life_good_high_value",
        "drug_low_value",
        "drug_high_value",
        "trade_good_max_income",
        "life_good_max_income",
        "drug_max_income",
    ):
        val = getattr(market, field)
        if val is not None:
            setattr(base, field, float(val))


def _attr_map(session: Session, item_id: int) -> dict[str, str]:
    rows = session.exec(select(ItemAttribute).where(ItemAttribute.item_id == int(item_id))).all()
    return {r.attr_key: r.attr_value for r in rows}


def _parse_id_from_parens(text: str | None) -> int | None:
    if not text or "(" not in text:
        return None
    try:
        return int(text.split("(", 1)[1].split(")", 1)[0].strip())
    except (ValueError, IndexError):
        return None


def _source_value(attrs: dict[str, str]) -> float | None:
    raw = attrs.get("Value at Source")
    if not raw:
        return None
    i = raw.find("(")
    part = raw[:i].strip() if i >= 0 else raw.strip()
    try:
        return float(part)
    except ValueError:
        return None


@dataclass
class ItemTradeProfile:
    item_id: int
    item_name: str
    item_type: str | None
    source_value: float | None
    origin_system_id: int | None
    origin_cbody_id: int | None
    origin_periphery_id: int | None
    race: str | None

    def trade_good(self) -> bool:
        return self.item_type == "Trade Good"

    def life_good(self) -> bool:
        return self.item_type == "Life"

    def drug(self) -> bool:
        return self.item_type == "Drug"

    def sellable(self) -> bool:
        return self.item_type in SELLABLE_TYPES

    def civilian(self) -> bool:
        return self.life_good() and (
            self.item_name == "Hive Egg" or "Civilian" in self.item_name
        )


def item_trade_profile(session: Session, item: Item) -> ItemTradeProfile:
    attrs = _attr_map(session, int(item.id))
    return _profile_from_attrs(item, attrs, session)


def _profile_from_attrs(
    item: Item,
    attrs: dict[str, str],
    star_systems: dict[int, StarSystem] | Session,
) -> ItemTradeProfile:
    origin_system_id = _parse_id_from_parens(attrs.get("Origin System"))
    origin_cbody_id = _parse_id_from_parens(attrs.get("Origin Celestial Body"))
    origin_periphery_id: int | None = None
    if origin_system_id is not None:
        if isinstance(star_systems, Session):
            ss = star_systems.get(StarSystem, int(origin_system_id))
        else:
            ss = star_systems.get(int(origin_system_id))
        if ss and ss.periphery_id is not None:
            origin_periphery_id = int(ss.periphery_id)
    return ItemTradeProfile(
        item_id=int(item.id),
        item_name=item.name,
        item_type=attrs.get("Type"),
        source_value=_source_value(attrs),
        origin_system_id=origin_system_id,
        origin_cbody_id=origin_cbody_id,
        origin_periphery_id=origin_periphery_id,
        race=attrs.get("Race"),
    )


def _distance_multiplier(
    session: Session,
    profile: ItemTradeProfile,
    base: Base,
) -> int:
    if profile.origin_system_id is None or base.star_system_id is None:
        return 0
    if int(profile.origin_system_id) == int(base.star_system_id):
        if profile.origin_cbody_id is not None and base.celestial_body_id is not None:
            cb = session.get(CelestialBody, int(base.celestial_body_id))
            if cb is not None and int(profile.origin_cbody_id) != int(cb.cbody_id):
                return 3
        return 1
    from_ss = session.get(StarSystem, int(base.star_system_id))
    to_ss = session.get(StarSystem, int(profile.origin_system_id))
    if from_ss is None or to_ss is None:
        return 0
    return trade_distance_modifier(from_ss.periphery_id, to_ss.periphery_id)


def local_price(session: Session, profile: ItemTradeProfile, base: Base) -> float:
    if base.trade_good_value_per_mu is None:
        return 0.0
    if profile.source_value is None:
        return 0.0
    race_multiplier = 1.0
    if (
        base.race
        and profile.race
        and base.race not in ("Sentient",)
        and profile.race not in ("Sentient",)
        and base.race == profile.race
    ):
        race_multiplier = 2.0
    if profile.trade_good():
        planetary = float(base.trade_good_value_per_mu or 0)
    elif profile.life_good():
        planetary = float(base.life_good_value_per_mu or 0)
    elif profile.drug():
        planetary = float(base.drug_value_per_mu or 0)
    else:
        return 0.0
    dm = _distance_multiplier(session, profile, base)
    if dm <= 0 or planetary <= 0:
        return 0.0
    return round(dm * float(profile.source_value) * planetary * race_multiplier, 2)


def _tier_thresholds(profile: ItemTradeProfile, base: Base) -> tuple[float | None, float | None]:
    if profile.trade_good():
        return base.trade_good_low_value, base.trade_good_high_value
    if profile.life_good():
        return base.life_good_low_value, base.life_good_high_value
    if profile.drug():
        return base.drug_low_value, base.drug_high_value
    return None, None


def high_value_good(session: Session, profile: ItemTradeProfile, base: Base) -> bool:
    price = local_price(session, profile, base)
    if price <= 0:
        return False
    low, high = _tier_thresholds(profile, base)
    return high is not None and price >= float(high)


def low_value_good(session: Session, profile: ItemTradeProfile, base: Base) -> bool:
    price = local_price(session, profile, base)
    if price <= 0:
        return False
    low, high = _tier_thresholds(profile, base)
    return low is not None and price <= float(low)


def medium_value_good(session: Session, profile: ItemTradeProfile, base: Base) -> bool:
    price = local_price(session, profile, base)
    if price <= 0:
        return False
    return not (high_value_good(session, profile, base) or low_value_good(session, profile, base))


def market_bracket(session: Session, profile: ItemTradeProfile, base: Base) -> str:
    if high_value_good(session, profile, base):
        return "H"
    if low_value_good(session, profile, base):
        return "L"
    return "M"


def recommended_buy_price(session: Session, profile: ItemTradeProfile, base: Base) -> float:
    lp = local_price(session, profile, base)
    if lp <= 0:
        return 0.0
    if high_value_good(session, profile, base):
        return round(lp * 0.6, 2)
    if medium_value_good(session, profile, base):
        return round(lp * 0.8, 2)
    return round(lp * 0.7, 2)


def recommended_buy_volume(session: Session, profile: ItemTradeProfile, base: Base) -> int:
    if not (profile.life_good() or profile.trade_good()):
        return 0
    if local_price(session, profile, base) <= 0:
        return 0
    if profile.trade_good():
        if high_value_good(session, profile, base):
            return 5000
        if medium_value_good(session, profile, base):
            return 25000
        return 100000
    if profile.life_good():
        return 10000
    return 0


def _latest_market_datum_id(session: Session) -> int | None:
    md = session.exec(select(MarketDatum).order_by(MarketDatum.market_time.desc())).first()
    return int(md.id) if md and md.id is not None else None


def _best_buy_sell(session: Session, item_id: int, md_id: int) -> tuple[MarketBuy | None, MarketSell | None]:
    buys = session.exec(
        select(MarketBuy).where(MarketBuy.market_datum_id == md_id, MarketBuy.item_id == int(item_id))
    ).all()
    sells = session.exec(
        select(MarketSell).where(MarketSell.market_datum_id == md_id, MarketSell.item_id == int(item_id))
    ).all()
    best_buy = max(buys, key=lambda b: float(b.price), default=None)
    best_sell = min(sells, key=lambda s: float(s.price), default=None)
    return best_buy, best_sell


def _selling_item(session: Session, base_id: int, item_id: int, md_id: int) -> bool:
    return (
        session.exec(
            select(MarketSell).where(
                MarketSell.market_datum_id == md_id,
                MarketSell.base_id == int(base_id),
                MarketSell.item_id == int(item_id),
            )
        ).first()
        is not None
    )


def _non_local_trade_items(session: Session, base: Base) -> list[tuple[BaseItem, Item, ItemTradeProfile]]:
    rows = session.exec(
        select(BaseItem, Item)
        .where(BaseItem.base_id == int(base.id))
        .where(BaseItem.category == "Trade Items")
        .where(BaseItem.item_id == Item.id)
    ).all()
    out: list[tuple[BaseItem, Item, ItemTradeProfile]] = []
    for bi, item in rows:
        profile = item_trade_profile(session, item)
        if profile.trade_good() and not _item_local(session, profile, base):
            out.append((bi, item, profile))
        elif profile.life_good() and not profile.civilian() and not _item_local(session, profile, base):
            out.append((bi, item, profile))
        elif profile.drug() and not _item_local(session, profile, base):
            out.append((bi, item, profile))
    return out


def _item_local(session: Session, profile: ItemTradeProfile, base: Base) -> bool:
    if base.star_system_id is None or profile.origin_system_id is None:
        return False
    return int(base.star_system_id) == int(profile.origin_system_id)


def weeks_supply_of_same_category(
    session: Session,
    base: Base,
    profile: ItemTradeProfile,
    *,
    trade_rows: list[tuple[BaseItem, Item, ItemTradeProfile]] | None = None,
) -> str | int | None:
    if trade_rows is None:
        trade_rows = _non_local_trade_items(session, base)

    def _filter(kind: str) -> list[tuple[BaseItem, Item, ItemTradeProfile]]:
        if kind == "trade_goods":
            return [(bi, it, p) for bi, it, p in trade_rows if p.trade_good()]
        if kind == "life_goods":
            return [(bi, it, p) for bi, it, p in trade_rows if p.life_good() and not p.civilian()]
        return [(bi, it, p) for bi, it, p in trade_rows if p.drug()]

    if profile.trade_good():
        kind = "trade_goods"
        max_income = base.trade_good_max_income
    elif profile.life_good():
        kind = "life_goods"
        max_income = base.life_good_max_income
    elif profile.drug():
        kind = "drugs"
        max_income = base.drug_max_income
    else:
        return None

    max_sales = round(float(max_income or 0) / 4) if max_income else 0
    if max_sales <= 0:
        return "N/A"

    tier_rows = _filter(kind)
    if profile.trade_good() or profile.life_good() or profile.drug():
        if high_value_good(session, profile, base):
            tier_rows = [
                (bi, it, p)
                for bi, it, p in tier_rows
                if high_value_good(session, p, base)
            ]
        elif medium_value_good(session, profile, base):
            tier_rows = [
                (bi, it, p)
                for bi, it, p in tier_rows
                if medium_value_good(session, p, base)
            ]
        elif low_value_good(session, profile, base):
            tier_rows = [
                (bi, it, p) for bi, it, p in tier_rows if low_value_good(session, p, base)
            ]

    total = 0.0
    for bi, _it, p in tier_rows:
        total += float(bi.quantity) * local_price(session, p, base)
    total = round(total)
    if total < max_sales:
        return "< 1"
    return int(round(total / max_sales))


def worth_buying(session: Session, base: Base, profile: ItemTradeProfile) -> bool:
    supply = weeks_supply_of_same_category(session, base, profile)
    if supply is None:
        return False
    if supply == "< 1":
        return True
    if isinstance(supply, int) and supply < MAXIMUM_WEEKS_TRADE_RESERVES:
        return True
    return False


def competitive_buy_price_ok(
    session: Session,
    base: Base,
    profile: ItemTradeProfile,
    *,
    md_id: int,
    best_buy: MarketBuy | None,
    best_sell: MarketSell | None,
) -> bool:
    rbp = recommended_buy_price(session, profile, base)
    rbv = recommended_buy_volume(session, profile, base)
    if rbp <= 0 or rbv < 1:
        return False
    if profile.origin_periphery_id is not None and base.star_system_id is not None:
        ss = session.get(StarSystem, int(base.star_system_id))
        if ss and ss.periphery_id is not None and int(ss.periphery_id) == int(profile.origin_periphery_id):
            return False
    if best_sell is not None and int(best_sell.base_id) == int(base.id):
        return False
    if best_buy is not None and int(best_buy.base_id) == int(base.id):
        return False
    return True


def sellable_items(session: Session) -> list[tuple[Item, ItemTradeProfile]]:
    return _load_sellable_items(session)


def _load_sellable_items(session: Session) -> list[tuple[Item, ItemTradeProfile]]:
    type_rows = session.exec(select(ItemAttribute).where(ItemAttribute.attr_key == "Type")).all()
    sellable_ids = sorted(
        {
            int(r.item_id)
            for r in type_rows
            if r.attr_value in SELLABLE_TYPES and r.item_id is not None
        }
    )
    if not sellable_ids:
        return []

    items = {
        int(item.id): item
        for item in session.exec(select(Item).where(Item.id.in_(sellable_ids))).all()
        if item.id is not None
    }
    attr_rows = session.exec(
        select(ItemAttribute).where(ItemAttribute.item_id.in_(sellable_ids))
    ).all()
    attrs_by_item: dict[int, dict[str, str]] = defaultdict(dict)
    system_ids: set[int] = set()
    for row in attr_rows:
        if row.item_id is None:
            continue
        iid = int(row.item_id)
        attrs_by_item[iid][row.attr_key] = row.attr_value
        if row.attr_key == "Origin System":
            sid = _parse_id_from_parens(row.attr_value)
            if sid is not None:
                system_ids.add(sid)

    star_systems: dict[int, StarSystem] = {}
    if system_ids:
        star_systems = {
            int(ss.id): ss
            for ss in session.exec(select(StarSystem).where(StarSystem.id.in_(system_ids))).all()
            if ss.id is not None
        }

    out: list[tuple[Item, ItemTradeProfile]] = []
    for item_id in sellable_ids:
        item = items.get(item_id)
        if item is None:
            continue
        profile = _profile_from_attrs(item, attrs_by_item.get(item_id, {}), star_systems)
        if profile.sellable():
            out.append((item, profile))
    return out


@dataclass
class _MarketSnapshot:
    md_id: int
    best_buys: dict[int, MarketBuy]
    best_sells: dict[int, MarketSell]
    base_selling: set[int]

    @classmethod
    def load(cls, session: Session, md_id: int, base_id: int) -> _MarketSnapshot:
        buys = session.exec(select(MarketBuy).where(MarketBuy.market_datum_id == md_id)).all()
        sells = session.exec(select(MarketSell).where(MarketSell.market_datum_id == md_id)).all()
        best_buys: dict[int, MarketBuy] = {}
        for buy in buys:
            if buy.item_id is None:
                continue
            iid = int(buy.item_id)
            prev = best_buys.get(iid)
            if prev is None or float(buy.price) > float(prev.price):
                best_buys[iid] = buy
        best_sells: dict[int, MarketSell] = {}
        for sell in sells:
            if sell.item_id is None:
                continue
            iid = int(sell.item_id)
            prev = best_sells.get(iid)
            if prev is None or float(sell.price) < float(prev.price):
                best_sells[iid] = sell
        base_selling = {
            int(sell.item_id)
            for sell in sells
            if sell.item_id is not None and int(sell.base_id) == int(base_id)
        }
        return cls(md_id=md_id, best_buys=best_buys, best_sells=best_sells, base_selling=base_selling)

    def best_for_item(self, item_id: int) -> tuple[MarketBuy | None, MarketSell | None]:
        iid = int(item_id)
        return self.best_buys.get(iid), self.best_sells.get(iid)

    def is_selling(self, item_id: int) -> bool:
        return int(item_id) in self.base_selling


@dataclass
class CompetitiveCalc:
    """Preloaded context for competitive-buy calculations (avoids per-item DB queries)."""

    base: Base
    snapshot: _MarketSnapshot
    sellable: list[tuple[Item, ItemTradeProfile]]
    base_periphery_id: int | None
    base_cbody_game_id: int | None
    _local_prices: dict[int, float] = field(default_factory=dict)
    _brackets: dict[int, str] = field(default_factory=dict)
    _tier_totals: dict[tuple[str, str], float] = field(default_factory=dict)

    def local_price(self, profile: ItemTradeProfile) -> float:
        cached = self._local_prices.get(profile.item_id)
        if cached is not None:
            return cached
        if self.base.trade_good_value_per_mu is None or profile.source_value is None:
            self._local_prices[profile.item_id] = 0.0
            return 0.0
        race_multiplier = 1.0
        if (
            self.base.race
            and profile.race
            and self.base.race not in ("Sentient",)
            and profile.race not in ("Sentient",)
            and self.base.race == profile.race
        ):
            race_multiplier = 2.0
        if profile.trade_good():
            planetary = float(self.base.trade_good_value_per_mu or 0)
        elif profile.life_good():
            planetary = float(self.base.life_good_value_per_mu or 0)
        elif profile.drug():
            planetary = float(self.base.drug_value_per_mu or 0)
        else:
            self._local_prices[profile.item_id] = 0.0
            return 0.0
        dm = self._distance_multiplier(profile)
        if dm <= 0 or planetary <= 0:
            self._local_prices[profile.item_id] = 0.0
            return 0.0
        price = round(dm * float(profile.source_value) * planetary * race_multiplier, 2)
        self._local_prices[profile.item_id] = price
        return price

    def _distance_multiplier(self, profile: ItemTradeProfile) -> int:
        if profile.origin_system_id is None or self.base.star_system_id is None:
            return 0
        if int(profile.origin_system_id) == int(self.base.star_system_id):
            if profile.origin_cbody_id is not None and self.base_cbody_game_id is not None:
                if int(profile.origin_cbody_id) != int(self.base_cbody_game_id):
                    return 3
            return 1
        # Cross-system: profile carries origin periphery; base side uses cached periphery.
        if profile.origin_periphery_id is None or self.base_periphery_id is None:
            return 0
        return trade_distance_modifier(self.base_periphery_id, profile.origin_periphery_id)

    def _tier_thresholds(self, profile: ItemTradeProfile) -> tuple[float | None, float | None]:
        if profile.trade_good():
            return self.base.trade_good_low_value, self.base.trade_good_high_value
        if profile.life_good():
            return self.base.life_good_low_value, self.base.life_good_high_value
        if profile.drug():
            return self.base.drug_low_value, self.base.drug_high_value
        return None, None

    def market_bracket(self, profile: ItemTradeProfile) -> str:
        cached = self._brackets.get(profile.item_id)
        if cached is not None:
            return cached
        price = self.local_price(profile)
        if price <= 0:
            bracket = "M"
        else:
            low, high = self._tier_thresholds(profile)
            if high is not None and price >= float(high):
                bracket = "H"
            elif low is not None and price <= float(low):
                bracket = "L"
            else:
                bracket = "M"
        self._brackets[profile.item_id] = bracket
        return bracket

    def recommended_buy_price(self, profile: ItemTradeProfile) -> float:
        lp = self.local_price(profile)
        if lp <= 0:
            return 0.0
        bracket = self.market_bracket(profile)
        if bracket == "H":
            return round(lp * 0.6, 2)
        if bracket == "M":
            return round(lp * 0.8, 2)
        return round(lp * 0.7, 2)

    def recommended_buy_volume(self, profile: ItemTradeProfile) -> int:
        if not (profile.life_good() or profile.trade_good()):
            return 0
        if self.local_price(profile) <= 0:
            return 0
        bracket = self.market_bracket(profile)
        if profile.trade_good():
            if bracket == "H":
                return 5000
            if bracket == "M":
                return 25000
            return 100000
        if profile.life_good():
            return 10000
        return 0

    def weeks_supply(self, profile: ItemTradeProfile) -> str | int | None:
        if profile.trade_good():
            kind = "trade_goods"
            max_income = self.base.trade_good_max_income
        elif profile.life_good():
            kind = "life_goods"
            max_income = self.base.life_good_max_income
        elif profile.drug():
            kind = "drugs"
            max_income = self.base.drug_max_income
        else:
            return None
        max_sales = round(float(max_income or 0) / 4) if max_income else 0
        if max_sales <= 0:
            return "N/A"
        bracket = self.market_bracket(profile)
        total = round(self._tier_totals.get((kind, bracket), 0.0))
        if total < max_sales:
            return "< 1"
        return int(round(total / max_sales))

    def worth_buying(self, profile: ItemTradeProfile) -> bool:
        supply = self.weeks_supply(profile)
        if supply is None:
            return False
        if supply == "< 1":
            return True
        if isinstance(supply, int) and supply < MAXIMUM_WEEKS_TRADE_RESERVES:
            return True
        return False

    def price_ok(
        self,
        profile: ItemTradeProfile,
        best_buy: MarketBuy | None,
        best_sell: MarketSell | None,
    ) -> bool:
        if self.recommended_buy_price(profile) <= 0 or self.recommended_buy_volume(profile) < 1:
            return False
        if (
            profile.origin_periphery_id is not None
            and self.base_periphery_id is not None
            and int(profile.origin_periphery_id) == int(self.base_periphery_id)
        ):
            return False
        base_id = int(self.base.id)
        if best_sell is not None and int(best_sell.base_id) == base_id:
            return False
        if best_buy is not None and int(best_buy.base_id) == base_id:
            return False
        return True


def build_competitive_calc(session: Session, base_id: int) -> CompetitiveCalc | None:
    base = session.get(Base, int(base_id))
    if base is None or base.trade_good_value_per_mu is None:
        return None
    md_id = _latest_market_datum_id(session)
    if md_id is None:
        return None

    snapshot = _MarketSnapshot.load(session, md_id, int(base_id))
    sellable = _load_sellable_items(session)

    base_periphery_id: int | None = None
    if base.star_system_id is not None:
        ss = session.get(StarSystem, int(base.star_system_id))
        if ss and ss.periphery_id is not None:
            base_periphery_id = int(ss.periphery_id)

    base_cbody_game_id: int | None = None
    if base.celestial_body_id is not None:
        cb = session.get(CelestialBody, int(base.celestial_body_id))
        if cb and cb.cbody_id is not None:
            base_cbody_game_id = int(cb.cbody_id)

    profile_by_id = {profile.item_id: profile for _item, profile in sellable}
    trade_rows = session.exec(
        select(BaseItem, Item)
        .where(BaseItem.base_id == int(base.id))
        .where(BaseItem.category == "Trade Items")
        .where(BaseItem.item_id == Item.id)
    ).all()
    non_local: list[tuple[BaseItem, Item, ItemTradeProfile]] = []
    for bi, item in trade_rows:
        profile = profile_by_id.get(int(item.id))
        if profile is None:
            profile = item_trade_profile(session, item)
        if profile.trade_good() and not _item_local(session, profile, base):
            non_local.append((bi, item, profile))
        elif profile.life_good() and not profile.civilian() and not _item_local(session, profile, base):
            non_local.append((bi, item, profile))
        elif profile.drug() and not _item_local(session, profile, base):
            non_local.append((bi, item, profile))

    calc = CompetitiveCalc(
        base=base,
        snapshot=snapshot,
        sellable=sellable,
        base_periphery_id=base_periphery_id,
        base_cbody_game_id=base_cbody_game_id,
    )
    calc._tier_totals = _compute_tier_totals(calc, non_local)
    return calc


def _compute_tier_totals(
    calc: CompetitiveCalc,
    trade_rows: list[tuple[BaseItem, Item, ItemTradeProfile]],
) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for bi, _item, profile in trade_rows:
        if profile.trade_good():
            kind = "trade_goods"
        elif profile.life_good() and not profile.civilian():
            kind = "life_goods"
        elif profile.drug():
            kind = "drugs"
        else:
            continue
        bracket = calc.market_bracket(profile)
        totals[(kind, bracket)] += float(bi.quantity) * calc.local_price(profile)
    return dict(totals)
