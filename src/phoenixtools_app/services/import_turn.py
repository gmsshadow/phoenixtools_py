from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session, delete, select

from phoenixtools_app.db.models import Base, BaseItem, Item, ItemGroup, NexusConfig
from phoenixtools_app.importer.nexus_html import NexusHtmlClient, NexusHtmlConfig
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
    if not cfg or not cfg.nexus_user or not cfg.nexus_password:
        raise RuntimeError("Missing Nexus configuration (nexus_user/nexus_password).")

    client = NexusHtmlClient(NexusHtmlConfig(nexus_user=cfg.nexus_user, nexus_password=cfg.nexus_password))
    try:
        log(f"Fetching turn report for base {base.id} …")
        html_text = client.get_turn_html(int(base.id))
        parsed = parse_turn_html(html_text)

        # Clear old.
        session.exec(delete(BaseItem).where(BaseItem.base_id == int(base.id)))
        session.exec(delete(ItemGroup).where(ItemGroup.base_id == int(base.id)))
        session.commit()

        inv_count = 0
        for item_id, qty in parsed.inventory.items():
            item = session.get(Item, int(item_id))
            if item is None:
                item = Item(id=int(item_id), name=f"Item {item_id}")
                session.add(item)
                session.commit()
            session.add(BaseItem(base_id=int(base.id), item_id=int(item_id), quantity=int(qty), category="Inventory"))
            inv_count += 1

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
    finally:
        client.close()

