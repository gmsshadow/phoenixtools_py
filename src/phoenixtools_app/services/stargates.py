from __future__ import annotations

from typing import Callable

from sqlmodel import Session, delete, select

from phoenixtools_app.db.models import CelestialBody, Path, Stargate, StargateRoute, Wormhole


ProgressCb = Callable[[str], None]

# Rails Nexus#setup_known_stargates_and_wormholes!
KNOWN_STARGATES: list[tuple[int, int, int, int]] = [
    # (system_a, system_b, cbody_a, cbody_b)
    (61, 103, 5069, 1600),
    (103, 186, 1600, 564),
    (103, 121, 1600, 8434),
    (103, 163, 1600, 3186),
    (103, 23, 1600, 1253),
    (23, 186, 1253, 564),
    (163, 186, 3186, 564),
    (121, 127, 8434, 4962),
    (161, 103, 7230, 1600),
    (163, 161, 3186, 7230),
    (121, 182, 8434, 5114),
    (66, 182, 5114, 8434),
]

KNOWN_WORMHOLES: list[tuple[int, int, int, int]] = [
    # (system_a, system_b, cbody_a, cbody_b); cbody 0 -> unknown entry (unusable from that side)
    (99, 41, 2231, 565),
    (6, 9, 3318, 7637),
    (17, 104, 0, 8422),
    (10, 11, 4562, 2197),
    (198, 146, 9890, 3102),
    (29, 79, 3236, 6863),
    (55, 209, 6735, 2324),
]


def _get_or_create_cbody(session: Session, star_system_id: int, cbody_game_id: int, kind: str) -> CelestialBody:
    cb = session.exec(
        select(CelestialBody).where(
            CelestialBody.star_system_id == int(star_system_id),
            CelestialBody.cbody_id == int(cbody_game_id),
        )
    ).first()
    if cb is None:
        cb = CelestialBody(
            star_system_id=int(star_system_id),
            cbody_id=int(cbody_game_id),
            name=kind,
            cbody_type=kind,
        )
        session.add(cb)
        session.commit()
        session.refresh(cb)
    return cb


def _get_or_create_stargate(session: Session, star_system_id: int, cbody_game_id: int) -> Stargate:
    gate = session.exec(select(Stargate).where(Stargate.star_system_id == int(star_system_id))).first()
    if gate is None:
        gate = Stargate(star_system_id=int(star_system_id))
        session.add(gate)
        session.commit()
        session.refresh(gate)
    cb = _get_or_create_cbody(session, star_system_id, cbody_game_id, "Stargate")
    gate.celestial_body_id = int(cb.id) if cb.id is not None else None
    session.add(gate)
    session.commit()
    session.refresh(gate)
    return gate


def setup_known_stargates_and_wormholes(session: Session, *, progress: ProgressCb | None = None) -> None:
    """Rails `Nexus#setup_known_stargates_and_wormholes!` (clears + reseeds, invalidates Path cache)."""

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    log("Adding known stargates …")
    session.exec(delete(StargateRoute))
    session.exec(delete(Stargate))
    session.commit()
    for a_id, b_id, cbody_a, cbody_b in KNOWN_STARGATES:
        ga = _get_or_create_stargate(session, a_id, cbody_a)
        gb = _get_or_create_stargate(session, b_id, cbody_b)
        session.add(StargateRoute(from_id=int(ga.id), to_id=int(gb.id)))
        session.add(StargateRoute(from_id=int(gb.id), to_id=int(ga.id)))
    session.commit()

    log("Adding known wormholes …")
    session.exec(delete(Wormhole))
    session.commit()
    for a_id, b_id, cbody_a, cbody_b in KNOWN_WORMHOLES:
        cb_a_id = None
        if cbody_a > 0:
            cb_a = _get_or_create_cbody(session, a_id, cbody_a, "Wormhole")
            cb_a_id = int(cb_a.id) if cb_a.id is not None else None
        cb_b_id = None
        if cbody_b > 0:
            cb_b = _get_or_create_cbody(session, b_id, cbody_b, "Wormhole")
            cb_b_id = int(cb_b.id) if cb_b.id is not None else None
        session.add(Wormhole(star_system_id=int(a_id), to_id=int(b_id), celestial_body_id=cb_a_id))
        session.add(Wormhole(star_system_id=int(b_id), to_id=int(a_id), celestial_body_id=cb_b_id))
    session.commit()

    # Cached Path rows were computed without (or with stale) gate/wormhole edges.
    log("Clearing cached paths …")
    session.exec(delete(Path))
    session.commit()
