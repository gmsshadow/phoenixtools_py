from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from dateutil.parser import isoparse
from sqlmodel import Session, delete, select

from phoenixtools_app.db.models import (
    AppState,
    Base,
    CelestialBody,
    Item,
    MarketBuy,
    MarketDatum,
    MarketSell,
    NexusConfig,
    StarSystem,
)
from phoenixtools_app.importer.market_xml import MarketXmlClient
from phoenixtools_app.importer.parsers import parse_market_xml
from phoenixtools_app.services.hub_link import (
    link_outposts_to_hub,
    sync_base_starbase_from_positions,
    upsert_bases_from_positions,
)
from phoenixtools_app.services.trade_routes import run_trade_route_generation


ProgressCb = Callable[[str], None]


@dataclass(frozen=True)
class MarketImportResult:
    bases: int
    items_touched: int
    buys: int
    sells: int
    trade_routes: int


def run_market_import(session: Session, *, progress: ProgressCb | None = None) -> MarketImportResult:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    log("Fetching market XML …")
    xml_text = MarketXmlClient().fetch()
    data = parse_market_xml(xml_text)

    market_time = _parse_market_time(data.market_time)
    datum = MarketDatum(market_time=market_time, stardate=data.stardate)
    session.add(datum)
    session.commit()
    session.refresh(datum)

    # Keep it simple for now: rebuild buys/sells for each new market datum.
    log("Clearing previous market buys/sells …")
    session.exec(delete(MarketBuy))
    session.exec(delete(MarketSell))
    session.commit()

    bases_count = 0
    buys_count = 0
    sells_count = 0
    items_touched: set[int] = set()

    log(f"Importing starbases ({len(data.starbases)}) …")
    for sb in data.starbases:
        base_id = int(sb["id"])

        ss_id = sb["system"]["id"] if isinstance(sb.get("system"), dict) else None
        ss_name = sb["system"]["name"] if isinstance(sb.get("system"), dict) else None
        if ss_id is not None:
            star_system = session.get(StarSystem, int(ss_id))
            if star_system is None:
                star_system = StarSystem(id=int(ss_id), name=str(ss_name or f"System {ss_id}"))
                session.add(star_system)
            elif ss_name:
                star_system.name = str(ss_name)
        else:
            star_system = None

        cb_id = sb["cbody"]["id"] if isinstance(sb.get("cbody"), dict) else None
        cb_name = sb["cbody"]["name"] if isinstance(sb.get("cbody"), dict) else None
        if star_system and cb_id is not None:
            cbody = (
                session.exec(
                    select(CelestialBody).where(
                        CelestialBody.star_system_id == star_system.id, CelestialBody.cbody_id == int(cb_id)
                    )
                ).first()
                or CelestialBody(star_system_id=star_system.id, cbody_id=int(cb_id), name=str(cb_name or ""))
            )
            if cb_name:
                cbody.name = str(cb_name)
            session.add(cbody)
            session.commit()
            session.refresh(cbody)
        else:
            cbody = None

        base = session.get(Base, base_id) or Base(id=base_id)
        base.name = sb.get("name") if isinstance(sb.get("name"), str) else base.name
        base.docks = sb.get("docks") if isinstance(sb.get("docks"), int) else base.docks
        base.hiports = sb.get("hiports") if isinstance(sb.get("hiports"), int) else base.hiports
        base.maintenance = sb.get("maintenance") if isinstance(sb.get("maintenance"), int) else base.maintenance
        base.patches = sb.get("patches") if isinstance(sb.get("patches"), float) else base.patches
        if star_system:
            base.star_system_id = star_system.id
        if cbody:
            base.celestial_body_id = cbody.id

        # Affiliation tag mapping is not implemented yet in the new schema
        # (old Rails used Affiliation.tag). We'll attach affiliation later.
        session.add(base)
        bases_count += 1

        for it in sb.get("items", []):
            if not isinstance(it, dict):
                continue
            item_id = int(it["id"])
            items_touched.add(item_id)
            item = session.get(Item, item_id) or Item(id=item_id, name=str(it.get("name") or f"Item {item_id}"))
            if it.get("name"):
                item.name = str(it["name"])
            session.add(item)

            buy = it.get("buy")
            if isinstance(buy, dict):
                session.add(
                    MarketBuy(
                        market_datum_id=datum.id,
                        base_id=base_id,
                        item_id=item_id,
                        quantity=int(buy["quantity"]),
                        price=float(buy["price"]),
                    )
                )
                buys_count += 1
            sell = it.get("sell")
            if isinstance(sell, dict):
                session.add(
                    MarketSell(
                        market_datum_id=datum.id,
                        base_id=base_id,
                        item_id=item_id,
                        quantity=int(sell["quantity"]),
                        price=float(sell["price"]),
                    )
                )
                sells_count += 1

    session.commit()

    app_state = session.exec(select(AppState).where(AppState.id == 1)).first()
    if app_state:
        app_state.last_daily_refresh_at = datetime.utcnow()
        app_state.updated_at = datetime.utcnow()
        session.add(app_state)
        session.commit()

    log("Syncing bases from positions (create/update) + starbase flags + outpost hubs …")
    ncfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    aff_id = int(ncfg.affiliation_id) if ncfg and ncfg.affiliation_id is not None else None
    upsert_bases_from_positions(session, default_affiliation_id=aff_id)
    sync_base_starbase_from_positions(session)
    link_outposts_to_hub(session)

    log("Generating trade routes …")
    n_routes = run_trade_route_generation(session)
    log(f"Trade routes generated: {n_routes}.")

    log("Market import complete.")
    return MarketImportResult(
        bases=bases_count,
        items_touched=len(items_touched),
        buys=buys_count,
        sells=sells_count,
        trade_routes=n_routes,
    )


def _parse_market_time(s: str | None) -> datetime:
    if not s:
        return datetime.utcnow()
    try:
        return isoparse(s)
    except Exception:
        return datetime.utcnow()

