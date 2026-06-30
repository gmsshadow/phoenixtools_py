from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session

from phoenixtools_app.db.models import Base, CelestialBody, StarSystem
from phoenixtools_app.services.pathing import (
    PathLeg,
    legs_to_orders,
    legs_to_squad_orders,
    shortest_path,
)
from phoenixtools_app.services.phoenix_order import PhoenixOrder

# Rails default when a sell quantity is not supplied (bases_controller#path_to_base).
DEFAULT_SELL_QUANTITY = 100_000
# Rails PhoenixOrder.wait_for_tus default at the tail of a sell.
SELL_WAIT_TUS = 300


@dataclass
class PathToBaseRequest:
    destination_base_id: int
    start_system_id: int | None = None
    start_base_id: int | None = None
    squadron: bool = False
    sell_item_id: int | None = None
    sell_quantity: int | None = None


@dataclass(frozen=True)
class PathStop:
    system_id: int
    system_name: str
    kind: str  # "start" | "jump" | "gate" | "wormhole"


@dataclass
class PathToBaseResult:
    start_system_id: int | None
    start_system_name: str
    end_base_id: int
    end_base_name: str
    end_system_id: int | None
    end_system_name: str
    same_system: bool
    path_found: bool
    tu_cost: int
    requires_gate_keys: bool
    stops: list[PathStop] = field(default_factory=list)
    orders: list[PhoenixOrder] = field(default_factory=list)
    error: str | None = None

    @property
    def orders_text(self) -> str:
        return "\n".join(str(o) for o in self.orders)


def _system_name(session: Session, system_id: int | None) -> str:
    if system_id is None:
        return "(unknown)"
    ss = session.get(StarSystem, int(system_id))
    return ss.name if ss and ss.name else f"System {system_id}"


def _base_cbody_game_id(session: Session, base: Base) -> int | None:
    if base.celestial_body_id is None:
        return None
    cb = session.get(CelestialBody, int(base.celestial_body_id))
    return int(cb.cbody_id) if cb is not None else None


def _base_move_to_orders(session: Session, base: Base, *, squadron: bool) -> list[PhoenixOrder]:
    """Rails `Base#move_to_order` / `Base#squadron_move_to_orders`."""
    if squadron:
        return [
            PhoenixOrder.squadron_start(),
            PhoenixOrder.move_to_base(int(base.id)),
            PhoenixOrder.squadron_stop(),
        ]
    cbody_game_id = _base_cbody_game_id(session, base)
    if cbody_game_id is not None and base.star_system_id is not None:
        return [PhoenixOrder.move_to_planet(int(base.star_system_id), int(cbody_game_id))]
    # Rails returns nil here; fall back to a dock-to-base move so the order list is still usable.
    return [PhoenixOrder.move_to_base(int(base.id))]


def _path_orders(legs: list[PathLeg], *, squadron: bool) -> list[PhoenixOrder]:
    """Rails `Path#to_orders` (squadron wraps the run with squadron start/stop)."""
    if not squadron:
        return legs_to_orders(legs)
    orders: list[PhoenixOrder] = [PhoenixOrder.wait_for_tus(240), PhoenixOrder.squadron_start()]
    orders = legs_to_squad_orders(legs, orders)
    last = legs[-1] if legs else None
    if last is not None and last.kind in ("gate", "wormhole"):
        # Rails trims the trailing stop emitted by the last gate/wormhole hop.
        orders = orders[:-1]
    else:
        orders.append(PhoenixOrder.squadron_stop())
    return orders


def _stops_from_legs(session: Session, start_system_id: int, legs: list[PathLeg]) -> list[PathStop]:
    stops = [PathStop(system_id=int(start_system_id), system_name=_system_name(session, start_system_id), kind="start")]
    for leg in legs:
        stops.append(
            PathStop(
                system_id=int(leg.to_system_id),
                system_name=_system_name(session, int(leg.to_system_id)),
                kind=leg.kind,
            )
        )
    return stops


def compute_path_to_base(session: Session, req: PathToBaseRequest) -> PathToBaseResult:
    """
    Rails `BasesController#path_to_base`: shortest path from a start system (or start base's
    system) to a destination base, plus the move/sell orders to run it.
    """
    end_base = session.get(Base, int(req.destination_base_id))
    if end_base is None:
        return PathToBaseResult(
            start_system_id=req.start_system_id,
            start_system_name="(unknown)",
            end_base_id=int(req.destination_base_id),
            end_base_name=f"Base {req.destination_base_id}",
            end_system_id=None,
            end_system_name="(unknown)",
            same_system=False,
            path_found=False,
            tu_cost=0,
            requires_gate_keys=False,
            error="Destination base not found.",
        )

    start_system_id = req.start_system_id
    if req.start_base_id is not None:
        start_base = session.get(Base, int(req.start_base_id))
        if start_base is not None and start_base.star_system_id is not None:
            start_system_id = int(start_base.star_system_id)

    end_system_id = int(end_base.star_system_id) if end_base.star_system_id is not None else None
    end_base_name = end_base.name or f"Base {end_base.id}"

    result = PathToBaseResult(
        start_system_id=start_system_id,
        start_system_name=_system_name(session, start_system_id),
        end_base_id=int(end_base.id),
        end_base_name=end_base_name,
        end_system_id=end_system_id,
        end_system_name=_system_name(session, end_system_id),
        same_system=False,
        path_found=False,
        tu_cost=0,
        requires_gate_keys=False,
    )

    if start_system_id is None:
        result.error = "No start system or start base supplied."
        return result
    if end_system_id is None:
        result.error = "Destination base has no star system."
        return result

    orders: list[PhoenixOrder] = []

    if int(start_system_id) == int(end_system_id):
        result.same_system = True
        result.path_found = True
        result.stops = [PathStop(int(start_system_id), _system_name(session, start_system_id), "start")]
        orders.extend(_base_move_to_orders(session, end_base, squadron=req.squadron))
    else:
        sp = shortest_path(session, int(start_system_id), int(end_system_id))
        if sp is None:
            result.path_found = False
        else:
            result.path_found = True
            result.tu_cost = int(sp.tu_cost)
            result.requires_gate_keys = sp.requires_gate_keys
            result.stops = _stops_from_legs(session, int(start_system_id), sp.legs)
            orders.extend(_path_orders(sp.legs, squadron=req.squadron))
            orders.extend(_base_move_to_orders(session, end_base, squadron=req.squadron))

    if req.sell_item_id is not None:
        quantity = int(req.sell_quantity) if req.sell_quantity else DEFAULT_SELL_QUANTITY
        orders.append(PhoenixOrder.sell(int(end_base.id), int(req.sell_item_id), quantity))
        orders.append(PhoenixOrder.wait_for_tus(SELL_WAIT_TUS))

    result.orders = orders
    return result


def orders_text_for_path_to_base(session: Session, req: PathToBaseRequest) -> str:
    """Annotated order block (comments + orders), mirroring the trade-route preview style."""
    res = compute_path_to_base(session, req)
    lines: list[str] = []
    lines.append(f"; Path to base: {res.start_system_name} -> {res.end_base_name} ({res.end_system_name})")
    if res.error:
        lines.append(f"; ERROR: {res.error}")
        return "\n".join(lines)
    if res.same_system:
        lines.append("; Destination is in the start system (no jumps required).")
    elif not res.path_found:
        lines.append("; ERROR: no path found between systems.")
        return "\n".join(lines)
    else:
        lines.append(f"; Total path cost: {res.tu_cost} TUs")
        if res.requires_gate_keys:
            lines.append("; NOTE: route uses stargates (gate keys required).")
    if req.sell_item_id is not None:
        qty = int(req.sell_quantity) if req.sell_quantity else DEFAULT_SELL_QUANTITY
        lines.append(f"; Sell item {req.sell_item_id} x{qty} on arrival.")
    lines.append("")
    lines.extend(str(o) for o in res.orders)
    return "\n".join(lines)
