from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlmodel import Session, select

from phoenixtools_app.db.models import (
    Affiliation,
    AppState,
    Item,
    ItemType,
    NexusConfig,
    Position,
    StarSystem,
)
from phoenixtools_app.importer.nexus_xml import NexusXmlClient, NexusXmlConfig
from phoenixtools_app.importer.parsers import parse_info_data, parse_pos_list


ProgressCb = Callable[[str], None]


@dataclass(frozen=True)
class SetupResult:
    items: int
    systems: int
    affiliations: int
    item_types: int
    positions: int


def run_setup_import(session: Session, *, progress: ProgressCb | None = None) -> SetupResult:
    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    if not cfg or not cfg.user_id or not cfg.xml_code:
        raise RuntimeError("Missing Nexus configuration (user_id/xml_code).")

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    log("Fetching info_data …")
    client = NexusXmlClient(NexusXmlConfig(user_id=int(cfg.user_id), xml_code=str(cfg.xml_code)))
    try:
        info_xml = client.fetch("info_data")
        info = parse_info_data(info_xml)

        log(f"Importing item types ({len(info.item_types)}) …")
        _upsert_pairs(session, ItemType, info.item_types)

        log(f"Importing items ({len(info.items)}) …")
        _upsert_pairs(session, Item, info.items)

        log(f"Importing star systems ({len(info.systems)}) …")
        _upsert_pairs(session, StarSystem, info.systems)

        log(f"Importing affiliations ({len(info.affiliations)}) …")
        _upsert_pairs(session, Affiliation, info.affiliations)

        log("Fetching pos_list …")
        pos_xml = client.fetch("pos_list")
        pos = parse_pos_list(pos_xml)

        log(f"Importing positions ({len(pos.positions)}) …")
        positions_count = 0
        for p in pos.positions:
            existing = session.get(Position, int(p["id"]))
            if existing is None:
                session.add(Position(**p))  # type: ignore[arg-type]
            else:
                for k, v in p.items():
                    setattr(existing, k, v)
            positions_count += 1

        session.commit()

        app_state = session.exec(select(AppState).where(AppState.id == 1)).first()
        if app_state:
            app_state.updated_at = datetime.utcnow()
            session.add(app_state)
            session.commit()

        log("Setup import complete.")
        return SetupResult(
            items=len(info.items),
            systems=len(info.systems),
            affiliations=len(info.affiliations),
            item_types=len(info.item_types),
            positions=positions_count,
        )
    finally:
        client.close()


def _upsert_pairs(session: Session, model, pairs: list[tuple[int, str]]) -> None:
    for id_, name in pairs:
        existing = session.get(model, id_)
        if existing is None:
            session.add(model(id=id_, name=name))
        else:
            existing.name = name
    session.commit()

