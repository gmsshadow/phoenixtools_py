from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session, delete, select

from phoenixtools_app.db.models import JumpLink, NexusConfig, Periphery, StarSystem
from phoenixtools_app.importer.nexus_html import NexusHtmlClient, NexusHtmlConfig
from phoenixtools_app.importer.parsers import parse_jump_map_html


ProgressCb = Callable[[str], None]


PERIPHERY_IDS: dict[str, int] = {
    "Coreward Arm": 14,
    "Cluster": 3,
    "Darkfold": 2,
    "Detinus Republic": 6,
    "Dewiek Home": 4,
    "Dewiek Pocket": 5,
    "Felini Home": 11,
    "Flagritz Empire": 10,
    "Halo": 13,
    "Inner Capellan": 1,
    "Caliphate": 9,
    "Inner Empire": 8,
    "None": 0,
    "Outer Capellan": 12,
    "Orion Spur": 16,
    "Perfidion Reach": 17,
    "Transpiral": 15,
    "Twilight": 7,
}


@dataclass(frozen=True)
class JumpMapImportResult:
    systems_touched: int
    links: int


def run_jump_map_import(session: Session, *, progress: ProgressCb | None = None) -> JumpMapImportResult:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    if not cfg or not cfg.nexus_user or not cfg.nexus_password:
        raise RuntimeError("Missing Nexus configuration (nexus_user/nexus_password).")

    # Ensure periphery table exists (seed from Rails constants)
    for name, pid in PERIPHERY_IDS.items():
        if session.get(Periphery, pid) is None:
            session.add(Periphery(id=pid, name=name))
    session.commit()

    log("Clearing previous jump links …")
    session.exec(delete(JumpLink))
    session.commit()

    client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
    systems_touched: set[int] = set()
    links_added = 0
    try:
        client.login()
        for name, pid in sorted(PERIPHERY_IDS.items(), key=lambda kv: kv[1]):
            log(f"Fetching jump map for {name} ({pid}) …")
            html_text = client.get("game", "jump", id=pid)
            parsed = parse_jump_map_html(html_text)

            for system_id, system_name in parsed.systems:
                systems_touched.add(system_id)
                ss = session.get(StarSystem, system_id) or StarSystem(id=system_id, name=system_name)
                ss.name = system_name
                ss.periphery_id = pid
                session.add(ss)

            session.commit()

            for a_id, b_id, jumps in parsed.links:
                _ensure_link(session, a_id, b_id, jumps)
                _ensure_link(session, b_id, a_id, jumps)
                links_added += 2
            session.commit()

        log("Jump map import complete.")
        return JumpMapImportResult(systems_touched=len(systems_touched), links=links_added)
    finally:
        client.close()


def _ensure_link(session: Session, from_id: int, to_id: int, jumps: int) -> None:
    existing = session.exec(
        select(JumpLink).where(JumpLink.from_id == from_id, JumpLink.to_id == to_id, JumpLink.jumps == jumps)
    ).first()
    if existing is None:
        session.add(JumpLink(from_id=from_id, to_id=to_id, jumps=jumps, hidden=False, tu_cost=50))

