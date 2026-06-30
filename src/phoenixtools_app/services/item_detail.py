"""
Item detail report (Rails ItemsController#show + Item model accessors).

Pulls together everything we know about a single item: parsed attributes,
origin, raw materials / ammo, current market sellers & buyers, where it is
produced across your bases, and the best resource deposits for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from phoenixtools_app.db.models import (
    Base,
    CelestialBody,
    Item,
    ItemAttribute,
    ItemType,
    MarketBuy,
    MarketDatum,
    MarketSell,
    MassProduction,
    BaseResource,
    Periphery,
    StarSystem,
)
from phoenixtools_app.services.mining_jobs import (
    ResourceCandidate,
    _min_week_yield_ok,
    _mp_item_output,
    _to_candidate,
    current_output,
    next_complex_output,
)

# "1 Metals (1)2.5 Foo (45)" -> [(qty, name, id), ...]
_ITEM_HASH_RE = re.compile(r"([\d.]+)\s+(.+?)\s*\((\d+)\)")
# "Some Name (1234)" -> name, id
_NAME_ID_RE = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")


@dataclass(frozen=True)
class ItemRef:
    item_id: int
    name: str


@dataclass(frozen=True)
class RawMaterial:
    item_id: int
    name: str
    quantity: float


@dataclass(frozen=True)
class ItemMarketRow:
    base_id: int
    base_name: str
    location: str
    quantity: int
    price: float


@dataclass(frozen=True)
class ProductionRow:
    base_id: int
    base_name: str
    output: int
    source: str  # 'Mass production' or 'Resource'


@dataclass(frozen=True)
class ItemDetail:
    item_id: int
    name: str
    mass: int
    type_name: str | None
    attributes_fetched: bool

    tech_manual: str | None
    tech_level: int | None
    race: str | None
    subtype: str | None
    type_attribute: str | None
    lifeform: bool
    infrastructure_type: str | None
    source_value: float | None
    origin_system: ItemRef | None
    origin_cbody_name: str | None
    origin_periphery: str | None
    substitute: ItemRef | None
    substitute_ratio: float | None
    production: float | None
    production_limit: float | None
    blueprint: ItemRef | None

    raw_materials: list[RawMaterial] = field(default_factory=list)
    ammo: list[RawMaterial] = field(default_factory=list)
    attributes: list[tuple[str, str]] = field(default_factory=list)
    sellers: list[ItemMarketRow] = field(default_factory=list)
    buyers: list[ItemMarketRow] = field(default_factory=list)
    starbase_production: list[ProductionRow] = field(default_factory=list)
    total_production: int = 0
    best_resources: list[ResourceCandidate] = field(default_factory=list)


def _latest_market_datum_id(session: Session) -> int | None:
    md = session.exec(select(MarketDatum).order_by(MarketDatum.market_time.desc())).first()
    return int(md.id) if md and md.id is not None else None


def _attrs(session: Session, item_id: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in session.exec(select(ItemAttribute).where(ItemAttribute.item_id == int(item_id))).all():
        out[a.attr_key] = a.attr_value
    return out


def _as_float(attrs: dict[str, str], key: str) -> float | None:
    raw = attrs.get(key)
    if raw is None:
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return None


def _as_int(attrs: dict[str, str], key: str) -> int | None:
    raw = attrs.get(key)
    if raw is None:
        return None
    try:
        return int(float(raw.strip()))
    except ValueError:
        return None


def _name_id(attrs: dict[str, str], key: str) -> tuple[str, int] | None:
    raw = attrs.get(key)
    if not raw:
        return None
    m = _NAME_ID_RE.match(raw.strip())
    if not m:
        return None
    return m.group(1).strip(), int(m.group(2))


def _item_ref(session: Session, attrs: dict[str, str], key: str) -> ItemRef | None:
    ni = _name_id(attrs, key)
    if ni is None:
        return None
    name, iid = ni
    it = session.get(Item, int(iid))
    return ItemRef(item_id=int(iid), name=it.name if it else name)


def _item_hash(session: Session, attrs: dict[str, str], key: str) -> list[RawMaterial]:
    raw = attrs.get(key)
    if not raw:
        return []
    out: list[RawMaterial] = []
    for qty, name, iid in _ITEM_HASH_RE.findall(raw):
        it = session.get(Item, int(iid))
        out.append(RawMaterial(item_id=int(iid), name=it.name if it else name.strip(), quantity=float(qty)))
    return out


def _source_value(attrs: dict[str, str]) -> float | None:
    raw = attrs.get("Value at Source")
    if not raw:
        return None
    head = raw.split("(", 1)[0].strip()
    try:
        return float(head)
    except ValueError:
        return None


def _location_text(base: Base | None, systems: dict[int, str], cbodies: dict[int, CelestialBody]) -> str:
    if base is None:
        return "—"
    parts: list[str] = []
    if base.celestial_body_id is not None and int(base.celestial_body_id) in cbodies:
        cb = cbodies[int(base.celestial_body_id)]
        if cb.name:
            parts.append(cb.name)
    if base.star_system_id is not None:
        parts.append(systems.get(int(base.star_system_id), str(base.star_system_id)))
    return ", ".join(parts) if parts else "—"


def compute_item_detail(session: Session, item_id: int) -> ItemDetail | None:
    item = session.get(Item, int(item_id))
    if item is None:
        return None

    attrs = _attrs(session, int(item_id))
    type_name = None
    if item.item_type_id is not None:
        it_type = session.get(ItemType, int(item.item_type_id))
        type_name = it_type.name if it_type else None

    # Origin system / cbody / periphery.
    origin_system: ItemRef | None = None
    origin_cbody_name: str | None = None
    origin_periphery: str | None = None
    sys_ni = _name_id(attrs, "Origin System")
    if sys_ni is not None:
        ss = session.get(StarSystem, int(sys_ni[1]))
        if ss is not None:
            origin_system = ItemRef(item_id=int(ss.id), name=ss.name)
            if ss.periphery_id is not None:
                per = session.get(Periphery, int(ss.periphery_id))
                origin_periphery = per.name if per else None
            cb_ni = _name_id(attrs, "Origin Celestial Body")
            if cb_ni is not None:
                cb = session.exec(
                    select(CelestialBody).where(
                        CelestialBody.star_system_id == int(ss.id),
                        CelestialBody.cbody_id == int(cb_ni[1]),
                    )
                ).first()
                origin_cbody_name = (cb.name if cb and cb.name else cb_ni[0]) if cb_ni else None

    # Market sellers / buyers (latest snapshot).
    sellers: list[ItemMarketRow] = []
    buyers: list[ItemMarketRow] = []
    md_id = _latest_market_datum_id(session)
    if md_id is not None:
        base_by_id = {int(b.id): b for b in session.exec(select(Base)).all()}
        systems = {int(s.id): s.name for s in session.exec(select(StarSystem)).all()}
        cbodies = {int(c.id): c for c in session.exec(select(CelestialBody)).all()}

        def _rows(records) -> list[ItemMarketRow]:
            rows: list[ItemMarketRow] = []
            for m in records:
                b = base_by_id.get(int(m.base_id))
                rows.append(
                    ItemMarketRow(
                        base_id=int(m.base_id),
                        base_name=(b.name if b and b.name else f"Base {m.base_id}"),
                        location=_location_text(b, systems, cbodies),
                        quantity=int(m.quantity),
                        price=float(m.price),
                    )
                )
            return rows

        sells = session.exec(
            select(MarketSell).where(MarketSell.market_datum_id == md_id, MarketSell.item_id == int(item_id))
        ).all()
        buys = session.exec(
            select(MarketBuy).where(MarketBuy.market_datum_id == md_id, MarketBuy.item_id == int(item_id))
        ).all()
        sellers = sorted(_rows(sells), key=lambda r: r.price)
        buyers = sorted(_rows(buys), key=lambda r: r.price, reverse=True)

    # Starbase production: running mass-production lines + mining output.
    base_names = {int(b.id): (b.name or f"Base {b.id}") for b in session.exec(select(Base)).all()}
    starbase_production: list[ProductionRow] = []
    total = 0
    for mp in session.exec(select(MassProduction).where(MassProduction.item_id == int(item_id))).all():
        if (mp.status or "") != "Running":
            continue
        out = int(_mp_item_output(session, mp))
        if out <= 0:
            continue
        total += out
        starbase_production.append(
            ProductionRow(int(mp.base_id), base_names.get(int(mp.base_id), f"Base {mp.base_id}"), out, "Mass production")
        )
    for br in session.exec(select(BaseResource).where(BaseResource.item_id == int(item_id))).all():
        out = int(current_output(br))
        if out <= 0:
            continue
        total += out
        starbase_production.append(
            ProductionRow(int(br.base_id), base_names.get(int(br.base_id), f"Base {br.base_id}"), out, "Resource")
        )
    starbase_production.sort(key=lambda r: r.output, reverse=True)

    # Best resource deposits for this item (Rails resources_for_item).
    eligible = [
        br
        for br in session.exec(select(BaseResource).where(BaseResource.item_id == int(item_id))).all()
        if _min_week_yield_ok(br)
    ]
    seen: set[int] = set()
    unique: list[BaseResource] = []
    for br in sorted(eligible, key=next_complex_output, reverse=True):
        if int(br.base_id) in seen:
            continue
        seen.add(int(br.base_id))
        unique.append(br)
    best_resources = [_to_candidate(br, base_names) for br in unique[:21]]

    return ItemDetail(
        item_id=int(item.id),
        name=item.name,
        mass=int(item.mass or 0),
        type_name=type_name,
        attributes_fetched=bool(item.attributes_fetched),
        tech_manual=attrs.get("Tech Manual"),
        tech_level=_as_int(attrs, "Tech level"),
        race=attrs.get("Race"),
        subtype=attrs.get("Subtype"),
        type_attribute=attrs.get("Type"),
        lifeform=attrs.get("Lifeform") == "1",
        infrastructure_type=attrs.get("Infrastructure Type"),
        source_value=_source_value(attrs),
        origin_system=origin_system,
        origin_cbody_name=origin_cbody_name,
        origin_periphery=origin_periphery,
        substitute=_item_ref(session, attrs, "Substitute Item"),
        substitute_ratio=_as_float(attrs, "Substitute Ratio"),
        production=_as_float(attrs, "Production"),
        production_limit=_as_float(attrs, "Production Limit"),
        blueprint=_item_ref(session, attrs, "Blueprint"),
        raw_materials=_item_hash(session, attrs, "Raw Materials"),
        ammo=_item_hash(session, attrs, "Ammo"),
        attributes=sorted(attrs.items(), key=lambda kv: kv[0].lower()),
        sellers=sellers,
        buyers=buyers,
        starbase_production=starbase_production,
        total_production=total,
        best_resources=best_resources,
    )
