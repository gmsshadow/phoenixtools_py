from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session, delete, select

from phoenixtools_app.db.models import Base, BaseItem, Item, ItemGroup, NexusConfig
from phoenixtools_app.importer.nexus_html import NexusHtmlClient, NexusHtmlConfig
from phoenixtools_app.importer.nexus_xml import NexusXmlClient, NexusXmlConfig
from phoenixtools_app.importer.parsers import parse_turn_html


ProgressCb = Callable[[str], None]


@dataclass(frozen=True)
class TurnImportResult:
    base_id: int
    inventory_items: int
    item_groups: int
    item_group_rows: int


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

    # Prefer XML API: a=xml&uid=<id>&code=<code>&sa=turn_data&tid=<pos>
    parsed = None
    if cfg.user_id and cfg.xml_code:
        xml_client = NexusXmlClient(NexusXmlConfig(user_id=int(cfg.user_id), xml_code=str(cfg.xml_code)))
        try:
            log(f"Fetching turn report via XML for base {base.id} …")
            html_text = xml_client.fetch("turn_data", tid=int(base.id))
            parsed = parse_turn_html(html_text)
        finally:
            xml_client.close()

    # Fallback to HTML scrape (username/password) for accounts that don't have XML configured.
    if parsed is None:
        if not cfg.nexus_user or not cfg.nexus_password:
            raise RuntimeError("Missing Nexus configuration (user_id/xml_code or nexus_user/nexus_password).")
        html_client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
        try:
            log(f"Fetching turn report via HTML scrape for base {base.id} …")
            html_text = html_client.get_turn_html(int(base.id))
            parsed = parse_turn_html(html_text)
        finally:
            html_client.close()

    if parsed is None:
        raise RuntimeError("Failed to parse turn data from both XML and HTML sources.")

    # Clear old.
    session.exec(delete(BaseItem).where(BaseItem.base_id == int(base.id)))
    session.exec(delete(ItemGroup).where(ItemGroup.base_id == int(base.id)))
    session.commit()

    def _add_base_items(qty_map: dict[int, int], category: str) -> int:
        n = 0
        for item_id, qty in qty_map.items():
            item = session.get(Item, int(item_id))
            if item is None:
                item = Item(id=int(item_id), name=f"Item {item_id}")
                session.add(item)
                session.commit()
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
            item = session.get(Item, int(item_id))
            if item is None:
                item = Item(id=int(item_id), name=f"Item {item_id}")
                session.add(item)
                session.commit()
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

    session.commit()
    log("Turn import complete.")
    return TurnImportResult(
        base_id=int(base.id),
        inventory_items=inv_count,
        item_groups=group_count,
        item_group_rows=group_rows,
    )
    

