from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlmodel import Session, select

from phoenixtools_app.db.models import AppState
from phoenixtools_app.services.import_market import MarketImportResult, run_market_import
from phoenixtools_app.services.import_setup import SetupResult, run_setup_import
from phoenixtools_app.services.import_jump_map import JumpMapImportResult, run_jump_map_import
from phoenixtools_app.services.import_cbodies import CbodiesImportResult, run_cbodies_import


ProgressCb = Callable[[str], None]


@dataclass(frozen=True)
class FullRefreshResult:
    setup: SetupResult
    jump_map: JumpMapImportResult
    cbodies: CbodiesImportResult
    market: MarketImportResult


def run_full_refresh(session: Session, *, progress: ProgressCb | None = None) -> FullRefreshResult:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    log("Full refresh started …")
    setup_result = run_setup_import(session, progress=progress)
    jump_map_result = run_jump_map_import(session, progress=progress)
    cbodies_result = run_cbodies_import(session, progress=progress)
    market_result = run_market_import(session, progress=progress)

    app_state = session.exec(select(AppState).where(AppState.id == 1)).first()
    if app_state:
        app_state.last_full_refresh_at = datetime.utcnow()
        app_state.updated_at = datetime.utcnow()
        session.add(app_state)
        session.commit()

    log("Full refresh complete.")
    return FullRefreshResult(
        setup=setup_result,
        jump_map=jump_map_result,
        cbodies=cbodies_result,
        market=market_result,
    )

