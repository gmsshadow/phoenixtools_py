from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine


def default_db_path() -> Path:
    data_dir = Path(user_data_dir(appname="phoenixtools", appauthor="phoenixtools"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "phoenixtools.sqlite"


def make_engine(db_path: Path | None = None):
    path = db_path or default_db_path()
    return create_engine(f"sqlite:///{path}", echo=False)


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)
    _migrate_sqlite(engine)


def make_session(engine) -> Session:
    return Session(engine)


def _migrate_sqlite(engine) -> None:
    """
    Minimal forward-only SQLite migrations for early development.
    This lets us evolve the schema without asking users to delete their DB each time.
    """
    insp = inspect(engine)
    if "celestialbody" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("celestialbody")}
        with engine.begin() as conn:
            if "cbody_type" not in cols:
                conn.execute(text("ALTER TABLE celestialbody ADD COLUMN cbody_type VARCHAR"))
            if "ring" not in cols:
                conn.execute(text("ALTER TABLE celestialbody ADD COLUMN ring INTEGER"))
            if "quad" not in cols:
                conn.execute(text("ALTER TABLE celestialbody ADD COLUMN quad INTEGER"))


