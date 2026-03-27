from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session, select

from phoenixtools_app.db.models import CelestialBody, NexusConfig, StarSystem
from phoenixtools_app.importer.nexus_html import NexusHtmlClient, NexusHtmlConfig
from phoenixtools_app.importer.parsers import parse_system_cbodies_html


ProgressCb = Callable[[str], None]

QUAD_NAMES: dict[str, int] = {"Alpha": 1, "Beta": 2, "Gamma": 3, "Delta": 4}


@dataclass(frozen=True)
class CbodiesImportResult:
    systems_processed: int
    cbodies_upserted: int


def run_cbodies_import(session: Session, *, progress: ProgressCb | None = None, max_systems: int | None = None) -> CbodiesImportResult:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    if not cfg or not cfg.nexus_user or not cfg.nexus_password:
        raise RuntimeError("Missing Nexus configuration (nexus_user/nexus_password).")

    systems = session.exec(select(StarSystem).order_by(StarSystem.id)).all()
    if max_systems is not None:
        systems = systems[:max_systems]

    client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
    processed = 0
    upserted = 0
    try:
        client.login()
        for ss in systems:
            processed += 1
            log(f"Fetching cbodies for system {ss.name} ({ss.id}) …")
            html_text = client.get("game", "system", id=ss.id)
            parsed = parse_system_cbodies_html(html_text)
            for c in parsed.cbodies:
                cbody_id = int(c["cbody_id"])
                existing = session.exec(
                    select(CelestialBody).where(
                        CelestialBody.star_system_id == ss.id, CelestialBody.cbody_id == cbody_id
                    )
                ).first()
                if existing is None:
                    existing = CelestialBody(star_system_id=ss.id, cbody_id=cbody_id)
                existing.name = str(c.get("name") or "") or None
                existing.cbody_type = str(c.get("cbody_type") or "") or None
                existing.ring = int(c.get("ring")) if str(c.get("ring") or "").strip().isdigit() else existing.ring
                quad = str(c.get("quad") or "").strip()
                existing.quad = QUAD_NAMES.get(quad, existing.quad)
                session.add(existing)
                upserted += 1
            session.commit()

        log("Celestial bodies import complete.")
        return CbodiesImportResult(systems_processed=processed, cbodies_upserted=upserted)
    finally:
        client.close()

