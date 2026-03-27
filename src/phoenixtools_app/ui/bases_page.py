from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import Base, BaseItem, CelestialBody, Item, ItemGroup, StarSystem
from phoenixtools_app.services.import_turn import run_turn_import
from phoenixtools_app.services.shipping_jobs import group_summaries_for_base, squadron_move_group_orders


class BasesPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()
        self._rows: list[tuple[Base, StarSystem | None, CelestialBody | None]] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by base name / system …")

        self.refresh_btn = QPushButton("Refresh")

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "Base", "System", "CBody", "Docks", "Hiports"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)

        left_layout.addWidget(QLabel("<b>Bases</b>"))
        left_layout.addWidget(self.filter)
        left_layout.addWidget(self.refresh_btn)
        left_layout.addWidget(self.table, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        detail = QWidget()
        detail_layout = QFormLayout(detail)
        detail_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.base_id = QLabel("—")
        self.base_name = QLabel("—")
        self.location = QLabel("—")
        self.facilities = QLabel("—")

        self.copy_id_btn = QPushButton("Copy base ID")
        self.fetch_turn_btn = QPushButton("Fetch turn (inventory + item groups)")

        self.inventory = QTableWidget(0, 3)
        self.inventory.setHorizontalHeaderLabels(["Qty", "Item", "Item ID"])
        self.inventory.setAlternatingRowColors(True)
        self.inventory.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        self.groups = QTableWidget(0, 4)
        self.groups.setHorizontalHeaderLabels(["Group ID", "Group name", "Item", "Qty"])
        self.groups.setAlternatingRowColors(True)
        self.groups.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        shipping = QWidget()
        shipping_layout = QFormLayout(shipping)
        shipping_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.ship_group = QComboBox()
        self.ship_destination = QComboBox()
        self.ship_generate_btn = QPushButton("Generate shipping orders")
        self.ship_copy_btn = QPushButton("Copy shipping orders")
        self.ship_orders = QLineEdit()
        self.ship_orders.setReadOnly(True)
        self.ship_orders.setPlaceholderText("Generated shipping orders preview (first line).")
        self._ship_full_text = ""

        shipping_layout.addRow("Item group", self.ship_group)
        shipping_layout.addRow("Destination", self.ship_destination)
        shipping_layout.addRow("", self.ship_generate_btn)
        shipping_layout.addRow("", self.ship_copy_btn)
        shipping_layout.addRow("Preview", self.ship_orders)

        detail_layout.addRow("ID", self.base_id)
        detail_layout.addRow("Name", self.base_name)
        detail_layout.addRow("Location", self.location)
        detail_layout.addRow("Facilities", self.facilities)
        detail_layout.addRow("", self.copy_id_btn)
        detail_layout.addRow("", self.fetch_turn_btn)

        right_layout.addWidget(detail)
        right_layout.addWidget(QLabel("<b>Inventory (from turn)</b>"))
        right_layout.addWidget(self.inventory, 1)
        right_layout.addWidget(QLabel("<b>Item groups (from turn)</b>"))
        right_layout.addWidget(self.groups, 1)
        right_layout.addWidget(QLabel("<b>Item group shipping</b>"))
        right_layout.addWidget(shipping)

        root.addWidget(left, 3)
        root.addWidget(right, 2)

        self.refresh_btn.clicked.connect(self._refresh)
        self.filter.textChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.copy_id_btn.clicked.connect(self._copy_id)
        self.fetch_turn_btn.clicked.connect(self._fetch_turn)
        self.ship_generate_btn.clicked.connect(self._generate_shipping_orders)
        self.ship_copy_btn.clicked.connect(self._copy_shipping_orders)

        self._refresh()

    def _refresh(self) -> None:
        with make_session(self._engine) as session:
            self._rows = _load_bases(session)
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self.filter.text().strip().lower()
        filtered = self._rows
        if q:
            filtered = [
                r
                for r in self._rows
                if (r[0].name or "").lower().find(q) >= 0
                or ((r[1].name if r[1] else "") or "").lower().find(q) >= 0
            ]

        self.table.setRowCount(len(filtered))
        for row_idx, (b, ss, cb) in enumerate(filtered):
            self.table.setItem(row_idx, 0, _cell(str(b.id), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(row_idx, 1, _cell(b.name or f"Base {b.id}"))
            self.table.setItem(row_idx, 2, _cell(ss.name if ss else "—"))
            self.table.setItem(row_idx, 3, _cell(cb.name if cb and cb.name else "—"))
            self.table.setItem(row_idx, 4, _cell("" if b.docks is None else str(b.docks), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(
                row_idx, 5, _cell("" if b.hiports is None else str(b.hiports), align=Qt.AlignmentFlag.AlignRight)
            )

        if filtered:
            self.table.selectRow(0)
        else:
            self._set_detail(None)

    def _selected_row(self) -> tuple[Base, StarSystem | None, CelestialBody | None] | None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return None
        row = min(rows)
        # We can't reliably map back to the original list after filtering, so rebuild from the table ID.
        base_id_item = self.table.item(row, 0)
        if base_id_item is None:
            return None
        try:
            base_id = int(base_id_item.text())
        except ValueError:
            return None
        for b, ss, cb in self._rows:
            if int(b.id) == base_id:
                return (b, ss, cb)
        return None

    def _show_detail(self) -> None:
        self._set_detail(self._selected_row())

    def _set_detail(self, row: tuple[Base, StarSystem | None, CelestialBody | None] | None) -> None:
        if row is None:
            self.base_id.setText("—")
            self.base_name.setText("—")
            self.location.setText("—")
            self.facilities.setText("—")
            return

        b, ss, cb = row
        self.base_id.setText(str(b.id))
        self.base_name.setText(b.name or "—")
        self.location.setText(
            f"{ss.name if ss else '—'}"
            + (f" / {cb.name} ({cb.cbody_type or '—'})" if cb and cb.name else "")
        )
        self.facilities.setText(
            ", ".join(
                [
                    f"docks={b.docks}" if b.docks is not None else "docks=?",
                    f"hiports={b.hiports}" if b.hiports is not None else "hiports=?",
                    f"maintenance={b.maintenance}" if b.maintenance is not None else "maintenance=?",
                ]
            )
        )
        self._load_turn_data(int(b.id))
        self._refresh_shipping_controls(int(b.id))

    def _copy_id(self) -> None:
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "No selection", "Select a base first.")
            return
        b, _, _ = row
        self.window().clipboard().setText(str(b.id))
        QMessageBox.information(self, "Copied", "Base ID copied to clipboard.")

    def _fetch_turn(self) -> None:
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "No selection", "Select a base first.")
            return
        b, _, _ = row
        try:
            with make_session(self._engine) as session:
                result = run_turn_import(session, int(b.id))
            QMessageBox.information(
                self,
                "Turn imported",
                f"Imported base {result.base_id}: inventory={result.inventory_items}, "
                f"item groups={result.item_groups} ({result.item_group_rows} rows).",
            )
            self._load_turn_data(int(b.id))
        except Exception as e:
            QMessageBox.critical(self, "Turn import failed", str(e))

    def _load_turn_data(self, base_id: int) -> None:
        with make_session(self._engine) as session:
            inv = session.exec(
                select(BaseItem, Item)
                .where(BaseItem.base_id == base_id)
                .where(BaseItem.item_id == Item.id)
                .order_by(BaseItem.quantity.desc())
            ).all()
            groups = session.exec(
                select(ItemGroup, Item)
                .where(ItemGroup.base_id == base_id)
                .where(ItemGroup.item_id == Item.id)
                .order_by(ItemGroup.group_id, Item.name)
            ).all()

        self.inventory.setRowCount(len(inv))
        for r, (bi, item) in enumerate(inv):
            self.inventory.setItem(r, 0, _cell(str(bi.quantity), align=Qt.AlignmentFlag.AlignRight))
            self.inventory.setItem(r, 1, _cell(item.name))
            self.inventory.setItem(r, 2, _cell(str(item.id), align=Qt.AlignmentFlag.AlignRight))

        self.groups.setRowCount(len(groups))
        for r, (ig, item) in enumerate(groups):
            self.groups.setItem(r, 0, _cell(str(ig.group_id), align=Qt.AlignmentFlag.AlignRight))
            self.groups.setItem(r, 1, _cell(ig.name))
            self.groups.setItem(r, 2, _cell(item.name))
            self.groups.setItem(r, 3, _cell(str(ig.quantity), align=Qt.AlignmentFlag.AlignRight))

    def _refresh_shipping_controls(self, source_base_id: int) -> None:
        self.ship_group.clear()
        self.ship_destination.clear()
        self.ship_orders.setText("")
        self._ship_full_text = ""

        with make_session(self._engine) as session:
            for g in group_summaries_for_base(session, source_base_id):
                self.ship_group.addItem(f"{g.group_id}: {g.name} (qty {g.total_quantity})", (g.group_id, g.total_quantity))
            bases = session.exec(select(Base).order_by(Base.name)).all()
            for b in bases:
                if int(b.id) == int(source_base_id):
                    continue
                self.ship_destination.addItem(f"{b.name or f'Base {b.id}'} ({b.id})", int(b.id))

    def _generate_shipping_orders(self) -> None:
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "No selection", "Select a source base first.")
            return
        source_base, _, _ = row
        if self.ship_group.count() == 0:
            QMessageBox.information(self, "No item groups", "This base has no imported item groups yet.")
            return
        if self.ship_destination.count() == 0:
            QMessageBox.information(self, "No destination", "No destination bases available.")
            return

        group_payload = self.ship_group.currentData()
        if not isinstance(group_payload, tuple) or len(group_payload) != 2:
            QMessageBox.information(self, "Invalid group", "Could not parse selected item group.")
            return
        group_id, total_qty = int(group_payload[0]), int(group_payload[1])
        dest_id = int(self.ship_destination.currentData())

        try:
            with make_session(self._engine) as session:
                text = squadron_move_group_orders(
                    session,
                    source_base_id=int(source_base.id),
                    destination_base_id=dest_id,
                    group_id=group_id,
                    pickup_quantity=total_qty,
                )
            self._ship_full_text = text
            first = text.splitlines()[0] if text else ""
            self.ship_orders.setText(first)
        except Exception as e:
            QMessageBox.critical(self, "Shipping orders failed", str(e))

    def _copy_shipping_orders(self) -> None:
        if not self._ship_full_text:
            QMessageBox.information(self, "Nothing to copy", "Generate shipping orders first.")
            return
        QApplication.clipboard().setText(self._ship_full_text)
        QMessageBox.information(self, "Copied", "Shipping orders copied to clipboard.")


def _load_bases(session: Session) -> list[tuple[Base, StarSystem | None, CelestialBody | None]]:
    bases = session.exec(select(Base).order_by(Base.id)).all()
    system_ids = {b.star_system_id for b in bases if b.star_system_id is not None}
    cbody_ids = {b.celestial_body_id for b in bases if b.celestial_body_id is not None}

    systems = {}
    if system_ids:
        for ss in session.exec(select(StarSystem).where(StarSystem.id.in_(system_ids))).all():
            systems[int(ss.id)] = ss
    cbodies = {}
    if cbody_ids:
        for cb in session.exec(select(CelestialBody).where(CelestialBody.id.in_(cbody_ids))).all():
            cbodies[int(cb.id)] = cb

    out: list[tuple[Base, StarSystem | None, CelestialBody | None]] = []
    for b in bases:
        ss = systems.get(int(b.star_system_id)) if b.star_system_id is not None else None
        cb = cbodies.get(int(b.celestial_body_id)) if b.celestial_body_id is not None else None
        out.append((b, ss, cb))
    return out


def _cell(text: str, *, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align is not None:
        item.setTextAlignment(int(align))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item

