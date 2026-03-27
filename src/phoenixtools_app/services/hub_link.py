from __future__ import annotations

from sqlmodel import Session, select

from phoenixtools_app.db.models import Base, Position


def sync_base_starbase_from_positions(session: Session) -> None:
    """Set `Base.starbase` from matching `Position` class (Outpost vs Starbase)."""
    for pos in session.exec(select(Position)).all():
        b = session.get(Base, int(pos.id))
        if b is None:
            continue
        pc = (pos.position_class or "").lower()
        if "outpost" in pc:
            b.starbase = False
        else:
            b.starbase = True
        session.add(b)
    session.commit()


def link_outposts_to_hub(session: Session) -> None:
    """
    Assign `hub_id` for outposts in a system to a starbase in the same system
    (Rails `Base.link_outposts_to_hub!`).
    """
    bases = session.exec(select(Base)).all()
    by_sys: dict[int, list[Base]] = {}
    for b in bases:
        if b.star_system_id is not None:
            by_sys.setdefault(int(b.star_system_id), []).append(b)

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
