from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from sqlmodel import Session, delete, select

from phoenixtools_app.db.models import (
    Base,
    BaseItem,
    BaseResource,
    CelestialBody,
    Item,
    ItemGroup,
    MassProduction,
    NexusConfig,
    Position,
    StarSystem,
)
from phoenixtools_app.importer.nexus_html import NexusHtmlClient, NexusHtmlConfig
from phoenixtools_app.importer.nexus_xml import NexusXmlClient, NexusXmlConfig
from phoenixtools_app.importer.parsers import (
    TurnListEntry,
    merge_turn_catalog,
    parse_pos_types,
    parse_turn_html,
    parse_turn_location_ids,
    parse_turn_position_type,
)
from phoenixtools_app.services.import_setup import run_positions_refresh


ProgressCb = Callable[[str], None]

_BASE_POSITION_CLASSES = frozenset({"Starbase", "Outpost"})


@dataclass(frozen=True)
class TurnImportResult:
    base_id: int
    inventory_items: int
    item_groups: int
    item_group_rows: int


@dataclass(frozen=True)
class SelectedTurnsResult:
    requested: int
    imported: int
    failed: int
    item_group_rows: int
    inventory_items: int
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BulkTurnImportResult:
    bases_total: int
    bases_ok: int
    bases_failed: int
    inventory_items: int
    item_group_rows: int
    errors: list[str]


def _owned_positions(session: Session, *, starbases_only: bool = False) -> list[Position]:
    """Owned base facilities from the last pos_list import (excludes ships/platforms)."""
    out: list[Position] = []
    for pos in session.exec(select(Position)).all():
        pc = (pos.position_class or "").strip()
        if pc not in _BASE_POSITION_CLASSES:
            continue
        if starbases_only and pc != "Starbase":
            continue
        out.append(pos)
    return sorted(out, key=lambda p: (p.name or f"Position {p.id}").lower())


def _my_owned_bases(session: Session) -> list[Base]:
    """Owned bases for reports — affiliation match plus any tracked shared turns."""
    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    my_aff = int(cfg.affiliation_id) if cfg and cfg.affiliation_id is not None else None
    bases = session.exec(select(Base)).all()
    if my_aff is not None:
        owned = [
            b
            for b in bases
            if bool(b.tracked) or (b.affiliation_id is not None and int(b.affiliation_id) == my_aff)
        ]
    else:
        owned = [b for b in bases if bool(b.tracked) or b.affiliation_id is not None]
    return sorted(owned, key=lambda b: (b.name or f"Base {b.id}").lower())


def run_turn_import_for_my_bases(
    session: Session,
    *,
    progress: ProgressCb | None = None,
    starbases_only: bool = False,
    refresh_positions: bool = True,
) -> BulkTurnImportResult:
    """
    Bulk-import turn data for every owned base facility (Rails `phoenixtools:turns` / `:all_turns`).

    Refreshes pos_list first by default so new outposts/starbases are picked up, then fetches each
    turn via the XML/HTML APIs. Does not import tracked external/shared turns.
    """
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    if refresh_positions:
        try:
            pos = run_positions_refresh(session, progress=progress)
            log(f"Positions refreshed: {pos.positions} positions, {pos.bases_upserted} bases updated.")
        except Exception as e:  # noqa: BLE001 - continue with cached positions if refresh fails
            log(f"Position refresh failed ({e}); using cached owned positions …")

    owned = _owned_positions(session, starbases_only=starbases_only)
    if not owned:
        scope = "starbases" if starbases_only else "owned bases"
        log(f"No {scope} found (run setup import or check Nexus XML credentials).")
        return BulkTurnImportResult(0, 0, 0, 0, 0, [])

    scope = "starbase" if starbases_only else "owned base"
    log(f"Importing turn data for {len(owned)} {scope}(s) …")
    ok = failed = inv = rows = 0
    errors: list[str] = []
    for idx, pos in enumerate(owned, start=1):
        label = pos.name or f"Base {pos.id}"
        log(f"[{idx}/{len(owned)}] {label} ({pos.id}) …")
        try:
            r = run_turn_import(session, int(pos.id), progress=progress)
            ok += 1
            inv += r.inventory_items
            rows += r.item_group_rows
        except Exception as e:  # noqa: BLE001 - report per-base and keep going
            failed += 1
            errors.append(f"{label} ({pos.id}): {e}")
            log(f"  ERROR: {e}")

    log(f"Turn data import finished: {ok} ok, {failed} failed.")
    return BulkTurnImportResult(
        bases_total=len(owned),
        bases_ok=ok,
        bases_failed=failed,
        inventory_items=inv,
        item_group_rows=rows,
        errors=errors,
    )


def run_turn_import(session: Session, base_id: int, *, progress: ProgressCb | None = None) -> TurnImportResult:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    base = session.get(Base, int(base_id))
    if base is None:
        raise RuntimeError(f"Base {base_id} not found in database.")

    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    if not cfg:
        raise RuntimeError("Missing Nexus configuration.")

    # Prefer XML API (own positions); fall back to the HTML turns list (works for shared turns too).
    html_text: str | None = None
    if cfg.user_id and cfg.xml_code:
        xml_client = NexusXmlClient(NexusXmlConfig(user_id=int(cfg.user_id), xml_code=str(cfg.xml_code)))
        try:
            log(f"Fetching turn report via XML for base {base.id} …")
            html_text = xml_client.fetch("turn_data", tid=int(base.id))
        except Exception as e:  # noqa: BLE001 - XML fails for shared turns; try HTML next
            log(f"  XML fetch failed ({e}); trying HTML turns list …")
            html_text = None
        finally:
            xml_client.close()

    if html_text is None:
        if not cfg.nexus_user or not cfg.nexus_password:
            raise RuntimeError("Missing Nexus configuration (user_id/xml_code or nexus_user/nexus_password).")
        html_client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
        try:
            log(f"Fetching turn report via HTML for base {base.id} …")
            html_text = html_client.get_turn_html(int(base.id))
        finally:
            html_client.close()

    parsed = parse_turn_html(html_text)
    _resolve_and_set_location(session, base, html_text)
    result = _store_turn(session, base, parsed)
    log("Turn import complete.")
    return result


def _store_turn(session: Session, base: Base, parsed) -> TurnImportResult:
    """Replace a base's turn-derived rows (inventory, item groups, mass production, resources)."""
    session.exec(delete(BaseItem).where(BaseItem.base_id == int(base.id)))
    session.exec(delete(ItemGroup).where(ItemGroup.base_id == int(base.id)))
    session.exec(delete(MassProduction).where(MassProduction.base_id == int(base.id)))
    session.exec(delete(BaseResource).where(BaseResource.base_id == int(base.id)))
    session.commit()

    def _ensure_item(item_id: int, name: str | None = None) -> None:
        if session.get(Item, int(item_id)) is None:
            session.add(Item(id=int(item_id), name=name or f"Item {item_id}"))
            session.commit()

    def _add_base_items(qty_map: dict[int, int], category: str) -> int:
        n = 0
        for item_id, qty in qty_map.items():
            _ensure_item(int(item_id))
            session.add(BaseItem(base_id=int(base.id), item_id=int(item_id), quantity=int(qty), category=category))
            n += 1
        return n

    inv_count = _add_base_items(parsed.inventory, "Inventory")
    _add_base_items(parsed.trade_items, "Trade Items")
    _add_base_items(parsed.raw_materials, "Raw Materials")

    group_count = 0
    group_rows = 0
    for group_id, meta in parsed.item_groups.items():
        group_count += 1
        name = str(meta.get("name") or f"Group {group_id}")
        items = meta.get("items") if isinstance(meta.get("items"), dict) else {}
        for item_id, qty in items.items():
            _ensure_item(int(item_id))
            session.add(
                ItemGroup(
                    base_id=int(base.id),
                    group_id=int(group_id),
                    name=name,
                    item_id=int(item_id),
                    quantity=int(qty),
                )
            )
            group_rows += 1

    for mp in parsed.mass_production:
        _ensure_item(int(mp["item_id"]))
        session.add(
            MassProduction(
                base_id=int(base.id),
                item_id=int(mp["item_id"]),
                factories=int(mp["factories"]),
                carry=int(mp["carry"]),
                status=str(mp.get("status") or "") or None,
            )
        )

    for br in parsed.base_resources:
        iid = int(br["item_id"])
        _ensure_item(iid, str(br.get("item_name") or ""))
        out_v = br.get("output")
        session.add(
            BaseResource(
                base_id=int(base.id),
                item_id=iid,
                resource_id=int(br["resource_id"]),
                resource_yield=float(br["resource_yield"]),
                resource_drop=int(br["resource_drop"]),
                resource_size=int(br["resource_size"]),
                ore_mines=int(br.get("ore_mines") or 0),
                resource_complexes=int(br.get("resource_complexes") or 0),
                output=float(out_v) if out_v is not None else None,
            )
        )

    session.commit()
    return TurnImportResult(
        base_id=int(base.id),
        inventory_items=inv_count,
        item_groups=group_count,
        item_group_rows=group_rows,
    )


def _resolve_and_set_location(session: Session, base: Base, html_text: str) -> None:
    """Best-effort: set a base's star system / celestial body from the turn report header."""
    if base.star_system_id is not None and base.celestial_body_id is not None:
        return
    ids = parse_turn_location_ids(html_text)
    if not ids:
        return
    system_id = next((i for i in ids if session.get(StarSystem, int(i)) is not None), None)
    if system_id is None:
        return
    if base.star_system_id is None:
        base.star_system_id = int(system_id)
    if base.celestial_body_id is None:
        for i in ids:
            if int(i) == int(system_id):
                continue
            cb = session.exec(
                select(CelestialBody).where(
                    CelestialBody.star_system_id == int(system_id),
                    CelestialBody.cbody_id == int(i),
                )
            ).first()
            if cb is not None:
                base.celestial_body_id = int(cb.id)
                break
    session.add(base)
    session.commit()


def list_nexus_turns(session: Session, *, progress: ProgressCb | None = None) -> list[TurnListEntry]:
    """List Nexus turns: personal list plus Find → External affiliation directory."""
    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    if not cfg or not cfg.nexus_user or not cfg.nexus_password:
        raise RuntimeError("Shared turns require a Nexus username/password in Configuration.")
    if progress:
        progress("Fetching personal turns list from the Nexus …")
    client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
    try:
        personal = client.list_turns()
        if progress:
            progress("Fetching Find → External turn directory …")
        external = client.list_external_turns()
        entries = merge_turn_catalog(personal, external)
    finally:
        client.close()

    # Enrich personal-list rows with position type from pos_list when missing.
    types = _position_types(cfg, progress)
    if types:
        entries = [
            replace(e, position_type=e.position_type or types.get(e.pos_id)) for e in entries
        ]
    return entries


def _position_types(cfg: NexusConfig, progress: ProgressCb | None) -> dict[int, str]:
    if not cfg.user_id or not cfg.xml_code:
        return {}
    if progress:
        progress("Fetching position types (pos_list) …")
    client = NexusXmlClient(NexusXmlConfig(user_id=int(cfg.user_id), xml_code=str(cfg.xml_code)))
    try:
        return parse_pos_types(client.fetch("pos_list"))
    except Exception:  # noqa: BLE001 - type info is best-effort
        return {}
    finally:
        client.close()


def run_turn_import_selected(
    session: Session, pos_ids: list[int], *, progress: ProgressCb | None = None
) -> SelectedTurnsResult:
    """
    Import the chosen turns (own or shared) via the HTML turns list. Creates a tracked Base row
    for turns we don't have yet, so they participate in the rest of the app like owned bases.
    """
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    wanted = [int(p) for p in pos_ids]
    if not wanted:
        return SelectedTurnsResult(0, 0, 0, 0, 0, [])

    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    if not cfg or not cfg.nexus_user or not cfg.nexus_password:
        raise RuntimeError("Shared turns require a Nexus username/password in Configuration.")

    client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
    imported = failed = inv = rows = 0
    errors: list[str] = []
    try:
        log("Fetching turn catalog from the Nexus …")
        personal = client.list_turns()
        external = client.list_external_turns()
        by_id = {e.pos_id: e for e in merge_turn_catalog(personal, external)}
        for idx, pid in enumerate(wanted, start=1):
            entry = by_id.get(pid)
            if entry is None:
                failed += 1
                errors.append(f"Turn {pid}: not found in Nexus turn catalog.")
                continue
            tag = ""
            if entry.source == "external" and not entry.on_personal_list:
                tag = " [external]"
            elif not entry.owned:
                tag = " [shared]"
            log(f"[{idx}/{len(wanted)}] {entry.name} ({pid}){tag} …")
            try:
                token = entry.token
                if not token:
                    resolved = client.resolve_turn_token(pid)
                    token = resolved.token
                html_text = client.get_turn_report(token)
                parsed = parse_turn_html(html_text)
                header_type = parse_turn_position_type(html_text, pid) or entry.position_type
                base = _ensure_tracked_base(session, entry, position_type=header_type)
                if base.star_system_id is None and entry.system_id is not None:
                    if session.get(StarSystem, int(entry.system_id)) is not None:
                        base.star_system_id = int(entry.system_id)
                        session.add(base)
                        session.commit()
                _resolve_and_set_location(session, base, html_text)
                r = _store_turn(session, base, parsed)
                imported += 1
                inv += r.inventory_items
                rows += r.item_group_rows
            except Exception as e:  # noqa: BLE001 - report per-turn and keep going
                failed += 1
                errors.append(f"{entry.name} ({pid}): {e}")
                log(f"  ERROR: {e}")
    finally:
        client.close()

    log(f"Turn import finished: {imported} imported, {failed} failed.")
    return SelectedTurnsResult(
        requested=len(wanted),
        imported=imported,
        failed=failed,
        item_group_rows=rows,
        inventory_items=inv,
        errors=errors,
    )


def _ensure_tracked_base(session: Session, entry: TurnListEntry, *, position_type: str | None = None) -> Base:
    # position_type (from the report header) overrides the list type, and covers shared turns.
    is_base = position_type in TurnListEntry.BASE_TYPES if position_type else entry.is_base
    base = session.get(Base, int(entry.pos_id))
    if base is None:
        base = Base(id=int(entry.pos_id), name=entry.name, starbase=is_base, tracked=True)
        session.add(base)
    else:
        base.tracked = True
        base.starbase = is_base
        if not base.name and entry.name:
            base.name = entry.name
        session.add(base)
    session.commit()
    session.refresh(base)
    return base

