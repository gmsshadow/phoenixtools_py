from __future__ import annotations

from sqlmodel import select

from phoenixtools_app.db.engine import init_db, make_engine, make_session
from phoenixtools_app.db.models import AppState, NexusConfig


def bootstrap():
    engine = make_engine()
    init_db(engine)
    with make_session(engine) as session:
        existing = session.exec(select(AppState).where(AppState.id == 1)).first()
        if existing is None:
            session.add(AppState(id=1))
            session.commit()
        cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
        if cfg is None:
            session.add(NexusConfig(id=1))
            session.commit()
    return engine

