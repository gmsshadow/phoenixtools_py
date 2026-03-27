from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class AppState(SQLModel, table=True):
    id: int | None = Field(default=1, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    last_daily_refresh_at: datetime | None = None
    last_full_refresh_at: datetime | None = None


class NexusConfig(SQLModel, table=True):
    id: int | None = Field(default=1, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    nexus_user: str | None = None
    nexus_password: str | None = None
    user_id: int | None = None
    xml_code: str | None = None


class ItemType(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str


class Item(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str
    item_type_id: int | None = Field(default=None, foreign_key="itemtype.id")


class Periphery(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str


class StarSystem(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str
    periphery_id: int | None = Field(default=None, foreign_key="periphery.id")


class Affiliation(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str


class Position(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str | None = None
    position_class: str | None = None
    design: str | None = None
    size: int | None = None
    size_type: str | None = None


class CelestialBody(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    star_system_id: int = Field(foreign_key="starsystem.id")
    cbody_id: int
    name: str | None = None
    cbody_type: str | None = None
    ring: int | None = None
    quad: int | None = None


class Base(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str | None = None
    docks: int | None = None
    hiports: int | None = None
    maintenance: int | None = None
    patches: float | None = None
    affiliation_id: int | None = Field(default=None, foreign_key="affiliation.id")
    star_system_id: int | None = Field(default=None, foreign_key="starsystem.id")
    celestial_body_id: int | None = Field(default=None, foreign_key="celestialbody.id")


class MarketDatum(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    market_time: datetime
    stardate: str | None = None


class MarketBuy(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    market_datum_id: int | None = Field(default=None, foreign_key="marketdatum.id")
    base_id: int = Field(foreign_key="base.id")
    item_id: int = Field(foreign_key="item.id")
    quantity: int
    price: float


class MarketSell(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    market_datum_id: int | None = Field(default=None, foreign_key="marketdatum.id")
    base_id: int = Field(foreign_key="base.id")
    item_id: int = Field(foreign_key="item.id")
    quantity: int
    price: float


class JumpLink(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    from_id: int = Field(foreign_key="starsystem.id")
    to_id: int = Field(foreign_key="starsystem.id")
    jumps: int = 1
    hidden: bool = False
    tu_cost: int = 50


class Path(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    from_id: int = Field(foreign_key="starsystem.id")
    to_id: int = Field(foreign_key="starsystem.id")
    tu_cost: int


