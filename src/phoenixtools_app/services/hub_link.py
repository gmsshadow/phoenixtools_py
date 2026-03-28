from __future__ import annotations

from sqlmodel import Session, select

from phoenixtools_app.db.models import Base, CelestialBody, Position
from phoenixtools_app.importer.parsers import parse_position_loc_text

# Rails Position::BASE_CLASSES
_BASE_POSITION_CLASSES = frozenset({"starbase", "outpost"})


def _position_is_base_facility(position_class: str | None) -> bool:
    c = (position_class or "").strip().lower()
    return c in _BASE_POSITION_CLASSES


def upsert_bases_from_positions(session: Session, *, default_affiliation_id: int | None = None) -> int:
    """
    Create/update `Base` rows from owned positions (Rails `Position#create_base` / `after_save`).
    Only rows whose class is Starbase or Outpost are touched.
    """
    n = 0
    for pos in session.exec(select(Position)).all():
        if not _position_is_base_facility(pos.position_class):
            continue
        base = session.get(Base, int(pos.id))
        created = base is None
        if base is None:
            base = Base(id=int(pos.id))
        if pos.name:
            base.name = pos.name
        pc = (pos.position_class or "").strip().lower()
        base.starbase = pc == "starbase"

        # Rails `parse_location!`: system suffix sets `star_system`; Docked only copies `celestial_body`.
        loc = parse_position_loc_text(pos.loc_text)
        if loc.star_system_id is not None:
            base.star_system_id = int(loc.star_system_id)
        if loc.docked_base_id is not None:
            dock = session.get(Base, int(loc.docked_base_id))
            if dock is not None and dock.celestial_body_id is not None:
                base.celestial_body_id = int(dock.celestial_body_id)
        elif loc.cbody_game_id is not None and base.star_system_id is not None:
            cb_row = session.exec(
                select(CelestialBody).where(
                    CelestialBody.star_system_id == int(base.star_system_id),
                    CelestialBody.cbody_id == int(loc.cbody_game_id),
                )
            ).first()
            if cb_row is not None and cb_row.id is not None:
                base.celestial_body_id = int(cb_row.id)

        if created and default_affiliation_id is not None:
            base.affiliation_id = int(default_affiliation_id)

        session.add(base)
        n += 1
    session.commit()
    return n


def sync_base_starbase_from_positions(session: Session) -> None:
    """Set `Base.starbase` from matching `Position` class (Outpost vs Starbase)."""
    for pos in session.exec(select(Position)).all():
        if not _position_is_base_facility(pos.position_class):
            continue
        b = session.get(Base, int(pos.id))
        if b is None:
            continue
        pc = (pos.position_class or "").strip().lower()
        b.starbase = pc != "outpost"
        session.add(b)
    session.commit()


def link_outposts_to_hub(session: Session) -> None:
    """
    Assign `hub_id` for outposts in a system to a starbase in the same system
    (Rails `Base.link_outposts_to_hub!` / `has_star_system` excludes system id 0).
    """
    bases = session.exec(select(Base)).all()
    by_sys: dict[int, list[Base]] = {}
    for b in bases:
        if b.star_system_id is None:
            continue
        sid = int(b.star_system_id)
        if sid == 0:
            continue
        by_sys.setdefault(sid, []).append(b)

    for lst in by_sys.values():
        hubs = [x for x in lst if x.starbase]
        if not hubs:
            continue
        hub = sorted(hubs, key=lambda x: int(x.id))[0]
        for o in lst:
            if not o.starbase and (o.hub_id is None or o.hub_id == 0):
                o.hub_id = int(hub.id)
                session.add(o)
    session.commit()
