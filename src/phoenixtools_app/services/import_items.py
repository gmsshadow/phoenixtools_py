from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session, delete, select

from phoenixtools_app.db.models import Item, ItemAttribute, ItemType, NexusConfig
from phoenixtools_app.importer.nexus_html import NexusHtmlClient, NexusHtmlConfig
from phoenixtools_app.importer.parsers import parse_item_attributes_html


ProgressCb = Callable[[str], None]


@dataclass(frozen=True)
class ItemsFetchResult:
    attempted: int
    fetched: int
    failed: int


def _parse_mass_value(val: str) -> int | None:
    digits = "".join(ch for ch in val.split("mus")[0] if ch.isdigit())
    try:
        return int(digits)
    except ValueError:
        return None


def _apply_attribute_side_effects(session: Session, item: Item, key: str, val: str) -> None:
    """Rails `set_item_attr_value!`: mass / name / item_type. The site has renamed
    the mass key from 'Mus' (Rails-era) to 'Mass Units (Mus)'."""
    if key == "Mus" or key.startswith("Mass Units"):
        mass = _parse_mass_value(val)
        if mass is not None:
            item.mass = mass
    elif key == "Name" and val:
        item.name = val
    elif key == "Type":
        it = session.exec(select(ItemType).where(ItemType.name == val)).first()
        item.item_type_id = int(it.id) if it else item.item_type_id


def fetch_item_attributes(session: Session, client: NexusHtmlClient, item_id: int) -> bool:
    """
    Fetch one item's attribute page (Rails `Item#fetch_item_attributes!`).
    Returns False when the page yields no attributes (caller may re-login and retry once).
    """
    item = session.get(Item, int(item_id))
    if item is None:
        return False

    html_text = client.get("game", "items", id=int(item_id))
    values = parse_item_attributes_html(html_text)
    if not values:
        return False

    session.exec(delete(ItemAttribute).where(ItemAttribute.item_id == int(item_id)))
    for key, val in values.items():
        session.add(ItemAttribute(item_id=int(item_id), attr_key=key, attr_value=val))
        _apply_attribute_side_effects(session, item, key, val)

    item.attributes_fetched = True
    session.add(item)
    session.commit()
    return True


def run_items_fetch_missing(
    session: Session, *, progress: ProgressCb | None = None, limit: int | None = None
) -> ItemsFetchResult:
    """Rails `Item.fetch_missing!` / `Nexus#update_items!`."""

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    if not cfg or not cfg.nexus_user or not cfg.nexus_password:
        raise RuntimeError("Missing Nexus configuration (nexus_user/nexus_password).")

    missing = session.exec(select(Item).where(Item.attributes_fetched == False).order_by(Item.id)).all()  # noqa: E712
    if limit is not None:
        missing = missing[: int(limit)]
    if not missing:
        log("No items missing attributes.")
        return ItemsFetchResult(attempted=0, fetched=0, failed=0)

    client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
    fetched = 0
    failed = 0
    try:
        client.login()
        for i, item in enumerate(missing, start=1):
            log(f"[{i}/{len(missing)}] Fetching item {item.name} ({item.id}) …")
            try:
                ok = fetch_item_attributes(session, client, int(item.id))
                if not ok:
                    # Rails re-logins and treats it as a failure for this pass.
                    client.login()
                    ok = fetch_item_attributes(session, client, int(item.id))
                if ok:
                    fetched += 1
                else:
                    failed += 1
                    log(f"  -> no attributes returned for item {item.id}.")
            except Exception as e:  # keep batch going; report at the end
                failed += 1
                log(f"  -> ERROR fetching item {item.id}: {e}")
    finally:
        client.close()

    log(f"Item attributes fetch complete: {fetched} fetched, {failed} failed.")
    return ItemsFetchResult(attempted=len(missing), fetched=fetched, failed=failed)


def fetch_single_item(session: Session, item_id: int, *, progress: ProgressCb | None = None) -> bool:
    """Refetch one item's attributes regardless of `attributes_fetched` (Rails POST /items/:id/fetch)."""
    cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
    if not cfg or not cfg.nexus_user or not cfg.nexus_password:
        raise RuntimeError("Missing Nexus configuration (nexus_user/nexus_password).")
    client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
    try:
        client.login()
        ok = fetch_item_attributes(session, client, int(item_id))
        if not ok:
            client.login()
            ok = fetch_item_attributes(session, client, int(item_id))
        return ok
    finally:
        client.close()


def reapply_attribute_side_effects(session: Session) -> int:
    """
    Recompute mass / item_type / name for all items from already-stored attributes
    (repair pass; no network). Returns number of items updated.
    """
    updated = 0
    items = {int(i.id): i for i in session.exec(select(Item)).all()}
    rows = session.exec(select(ItemAttribute)).all()
    touched: set[int] = set()
    for row in rows:
        item = items.get(int(row.item_id))
        if item is None:
            continue
        before = (item.mass, item.name, item.item_type_id)
        _apply_attribute_side_effects(session, item, row.attr_key, row.attr_value)
        if (item.mass, item.name, item.item_type_id) != before:
            session.add(item)
            touched.add(int(item.id))
    session.commit()
    updated = len(touched)
    return updated


def item_attr_value(session: Session, item_id: int, key: str) -> str | None:
    row = session.exec(
        select(ItemAttribute).where(ItemAttribute.item_id == int(item_id)).where(ItemAttribute.attr_key == key)
    ).first()
    return row.attr_value if row else None
