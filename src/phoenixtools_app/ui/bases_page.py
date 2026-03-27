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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import Base, BaseItem, BaseResource, CelestialBody, Item, ItemGroup, MassProduction, StarSystem
from phoenixtools_app.services.base_reports import (
    competitive_buy_orders_text,
    competitive_buy_rows,
    middleman_candidate_items,
    middleman_orders_text,
    raw_materials_for_base,
    trade_items_for_base,
)
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

        self.tabs = QTabWidget()

        # --- Overview tab ---
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)

        detail = QWidget()
        detail_layout = QFormLayout(detail)
        detail_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.base_id = QLabel("—")
        self.base_name = QLabel("—")
        self.location = QLabel("—")
        self.facilities = QLabel("—")
        self.base_role = QLabel("—")

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
        detail_layout.addRow("Role / hub", self.base_role)
        detail_layout.addRow("", self.copy_id_btn)
        detail_layout.addRow("", self.fetch_turn_btn)

        overview_layout.addWidget(detail)
        overview_layout.addWidget(QLabel("<b>Inventory report</b> <small>(category: Inventory)</small>"))
        overview_layout.addWidget(self.inventory, 1)
        overview_layout.addWidget(QLabel("<b>Item groups</b>"))
        overview_layout.addWidget(self.groups, 1)
        overview_layout.addWidget(QLabel("<b>Item group shipping</b>"))
        overview_layout.addWidget(shipping)

        self.tabs.addTab(overview, "Overview")

        # --- Trade / raw tab (Rails: trade_items_report + raw materials) ---
        trade_tab = QWidget()
        trade_layout = QVBoxLayout(trade_tab)
        trade_layout.addWidget(
            QLabel(
                "<b>Trade items</b> and <b>raw materials</b> from the last fetched turn "
                "(Trade Item Report / Raw Material Report)."
            )
        )
        self.trade_table = QTableWidget(0, 3)
        self.trade_table.setHorizontalHeaderLabels(["Qty", "Item", "Item ID"])
        self.trade_table.setAlternatingRowColors(True)
        self.raw_table = QTableWidget(0, 3)
        self.raw_table.setHorizontalHeaderLabels(["Qty", "Item", "Item ID"])
        self.raw_table.setAlternatingRowColors(True)
        trade_layout.addWidget(QLabel("<b>Trade Item Report</b>"))
        trade_layout.addWidget(self.trade_table, 1)
        trade_layout.addWidget(QLabel("<b>Raw Material Report</b>"))
        trade_layout.addWidget(self.raw_table, 1)
        self.tabs.addTab(trade_tab, "Trade / raw")

        # --- Competitive buys tab ---
        comp_tab = QWidget()
        comp_layout = QVBoxLayout(comp_tab)
        comp_layout.addWidget(
            QLabel(
                "<b>Competitive market buys</b> — approximate vs Rails "
                "(full parity needs planetary market fields on the base + item classifications)."
            )
        )
        self.comp_table = QTableWidget(0, 8)
        self.comp_table.setHorizontalHeaderLabels(
            ["Item", "Rec. buy", "Volume", "Profit", "Best sell @", "Best buy @", "Sell base", "Buy base"]
        )
        self.comp_copy_btn = QPushButton("Copy competitive buy orders")
        self._comp_text = ""
        comp_layout.addWidget(self.comp_table, 1)
        comp_layout.addWidget(self.comp_copy_btn)
        self.tabs.addTab(comp_tab, "Competitive buys")

        # --- Resource production (Rails resource_report / base_resources) ---
        res_tab = QWidget()
        res_layout = QVBoxLayout(res_tab)
        res_layout.addWidget(
            QLabel(
                "<b>Resource production</b> — merged Mineral/Mining/Resource reports from the last fetched turn."
            )
        )
        self.resource_table = QTableWidget(0, 8)
        self.resource_table.setHorizontalHeaderLabels(
            ["Item", "Res#", "Yield", "Drop", "Size", "Ore mines", "R.complex", "Output"]
        )
        self.resource_table.setAlternatingRowColors(True)
        res_layout.addWidget(self.resource_table, 1)
        self.tabs.addTab(res_tab, "Resources")

        # --- Mass production ---
        mass_tab = QWidget()
        mass_layout = QVBoxLayout(mass_tab)
        mass_layout.addWidget(QLabel("<b>Mass production</b> — Production Report from the last fetched turn."))
        self.mass_table = QTableWidget(0, 4)
        self.mass_table.setHorizontalHeaderLabels(["Item", "Factories", "Carry", "Status"])
        self.mass_table.setAlternatingRowColors(True)
        mass_layout.addWidget(self.mass_table, 1)
        self.tabs.addTab(mass_tab, "Mass production")

        # --- Outposts ---
        out_tab = QWidget()
        out_layout = QVBoxLayout(out_tab)
        out_layout.addWidget(
            QLabel(
                "<b>Outposts</b> assigned to this hub (hub_id = selected base). "
                "Choose a new starbase hub and click Save."
            )
        )
        self.outpost_table = QTableWidget(0, 4)
        self.outpost_table.setHorizontalHeaderLabels(["ID", "Name", "System / body", "New hub"])
        self.outpost_table.setAlternatingRowColors(True)
        self.outpost_save_btn = QPushButton("Save hub assignments")
        self._outpost_hub_combos: list[tuple[int, QComboBox]] = []
        out_layout.addWidget(self.outpost_table, 1)
        out_layout.addWidget(self.outpost_save_btn)
        self.tabs.addTab(out_tab, "Outposts")

        # --- Middleman tab ---
        mid_tab = QWidget()
        mid_layout = QVBoxLayout(mid_tab)
        mid_layout.addWidget(
            QLabel(
                "<b>Middleman</b> — market buy/sell pair from latest market snapshot "
                "(Rails formulas; item must pass spread / price thresholds)."
            )
        )
        mid_form = QFormLayout()
        mid_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.mid_item = QComboBox()
        self.mid_gen_btn = QPushButton("Generate middleman orders")
        self.mid_copy_btn = QPushButton("Copy middleman orders")
        self.mid_orders = QTextEdit()
        self.mid_orders.setReadOnly(True)
        self.mid_orders.setPlaceholderText("Order=… lines appear here.")
        self._mid_full_text = ""
        mid_form.addRow("Item", self.mid_item)
        mid_form.addRow("", self.mid_gen_btn)
        mid_form.addRow("", self.mid_copy_btn)
        mid_layout.addLayout(mid_form)
        mid_layout.addWidget(self.mid_orders, 1)
        self.tabs.addTab(mid_tab, "Middleman")

        root.addWidget(left, 3)
        root.addWidget(self.tabs, 2)

        self.refresh_btn.clicked.connect(self._refresh)
        self.filter.textChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.copy_id_btn.clicked.connect(self._copy_id)
        self.fetch_turn_btn.clicked.connect(self._fetch_turn)
        self.ship_generate_btn.clicked.connect(self._generate_shipping_orders)
        self.ship_copy_btn.clicked.connect(self._copy_shipping_orders)
        self.comp_copy_btn.clicked.connect(self._copy_competitive_orders)
        self.mid_gen_btn.clicked.connect(self._generate_middleman)
        self.mid_copy_btn.clicked.connect(self._copy_middleman)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.outpost_save_btn.clicked.connect(self._save_outpost_hubs)

        self._refresh()
        self._refresh_middleman_items()

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.tabText(index) == "Middleman":
            self._refresh_middleman_items()

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
            self.base_role.setText("—")
            self._clear_report_tables()
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
        bid = int(b.id)
        with make_session(self._engine) as session:
            bb = session.get(Base, bid)
            if bb:
                role = "Starbase" if bb.starbase else "Outpost"
                hub_txt = "—"
                if bb.hub_id is not None:
                    hb = session.get(Base, int(bb.hub_id))
                    hub_txt = f"{hb.name if hb else ''} ({bb.hub_id})" if hb or bb.hub_id else str(bb.hub_id)
                self.base_role.setText(f"{role} · hub_id={hub_txt}")
        self._load_turn_data(bid)
        self._load_trade_raw_tables(bid)
        self._load_competitive_table(bid)
        self._load_resource_mass_outposts_tables(bid)
        self._refresh_shipping_controls(bid)

    def _clear_report_tables(self) -> None:
        self.trade_table.setRowCount(0)
        self.raw_table.setRowCount(0)
        self.comp_table.setRowCount(0)
        self._comp_text = ""
        self.resource_table.setRowCount(0)
        self.mass_table.setRowCount(0)
        self._clear_outpost_widgets()

    def _clear_outpost_widgets(self) -> None:
        for r in range(self.outpost_table.rowCount()):
            if self.outpost_table.cellWidget(r, 3) is not None:
                self.outpost_table.removeCellWidget(r, 3)
        self.outpost_table.setRowCount(0)
        self._outpost_hub_combos = []

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
            bid = int(b.id)
            self._load_turn_data(bid)
            self._load_trade_raw_tables(bid)
            self._load_resource_mass_outposts_tables(bid)
        except Exception as e:
            QMessageBox.critical(self, "Turn import failed", str(e))

    def _load_turn_data(self, base_id: int) -> None:
        with make_session(self._engine) as session:
            inv = session.exec(
                select(BaseItem, Item)
                .where(BaseItem.base_id == base_id)
                .where(BaseItem.category == "Inventory")
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

    def _load_trade_raw_tables(self, base_id: int) -> None:
        with make_session(self._engine) as session:
            trade = trade_items_for_base(session, base_id)
            raw = raw_materials_for_base(session, base_id)

        self.trade_table.setRowCount(len(trade))
        for r, (bi, item) in enumerate(trade):
            self.trade_table.setItem(r, 0, _cell(str(bi.quantity), align=Qt.AlignmentFlag.AlignRight))
            self.trade_table.setItem(r, 1, _cell(item.name))
            self.trade_table.setItem(r, 2, _cell(str(item.id), align=Qt.AlignmentFlag.AlignRight))

        self.raw_table.setRowCount(len(raw))
        for r, (bi, item) in enumerate(raw):
            self.raw_table.setItem(r, 0, _cell(str(bi.quantity), align=Qt.AlignmentFlag.AlignRight))
            self.raw_table.setItem(r, 1, _cell(item.name))
            self.raw_table.setItem(r, 2, _cell(str(item.id), align=Qt.AlignmentFlag.AlignRight))

    def _load_competitive_table(self, base_id: int) -> None:
        with make_session(self._engine) as session:
            rows = competitive_buy_rows(session, base_id)
            names = {int(b.id): (b.name or f"Base {b.id}") for b in session.exec(select(Base)).all()}
            self._comp_text = competitive_buy_orders_text(session, base_id)

        self.comp_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.comp_table.setItem(r, 0, _cell(row.item_name))
            self.comp_table.setItem(r, 1, _cell(f"{row.recommended_buy_price:.2f}", align=Qt.AlignmentFlag.AlignRight))
            self.comp_table.setItem(r, 2, _cell(str(row.recommended_buy_volume), align=Qt.AlignmentFlag.AlignRight))
            self.comp_table.setItem(r, 3, _cell(f"{row.profit:.2f}", align=Qt.AlignmentFlag.AlignRight))
            self.comp_table.setItem(r, 4, _cell(f"{row.best_sell_price:.2f}", align=Qt.AlignmentFlag.AlignRight))
            self.comp_table.setItem(r, 5, _cell(f"{row.best_buy_price:.2f}", align=Qt.AlignmentFlag.AlignRight))
            self.comp_table.setItem(r, 6, _cell(names.get(row.best_sell_base_id, str(row.best_sell_base_id))))
            self.comp_table.setItem(r, 7, _cell(names.get(int(row.best_buy_base_id), str(row.best_buy_base_id))))

    def _load_resource_mass_outposts_tables(self, base_id: int) -> None:
        self._load_resource_tab(base_id)
        self._load_mass_tab(base_id)
        self._load_outposts_tab(base_id)

    def _load_resource_tab(self, base_id: int) -> None:
        with make_session(self._engine) as session:
            rows = session.exec(
                select(BaseResource, Item)
                .where(BaseResource.base_id == base_id)
                .where(BaseResource.item_id == Item.id)
                .order_by(BaseResource.resource_id)
            ).all()

        self.resource_table.setRowCount(len(rows))
        for r, (br, item) in enumerate(rows):
            self.resource_table.setItem(r, 0, _cell(item.name))
            self.resource_table.setItem(r, 1, _cell(str(br.resource_id), align=Qt.AlignmentFlag.AlignRight))
            self.resource_table.setItem(r, 2, _cell(str(br.resource_yield), align=Qt.AlignmentFlag.AlignRight))
            self.resource_table.setItem(r, 3, _cell(str(br.resource_drop), align=Qt.AlignmentFlag.AlignRight))
            sz = "∞" if br.resource_size == -999 else str(br.resource_size)
            self.resource_table.setItem(r, 4, _cell(sz, align=Qt.AlignmentFlag.AlignRight))
            self.resource_table.setItem(r, 5, _cell(str(br.ore_mines), align=Qt.AlignmentFlag.AlignRight))
            self.resource_table.setItem(r, 6, _cell(str(br.resource_complexes), align=Qt.AlignmentFlag.AlignRight))
            self.resource_table.setItem(
                r, 7, _cell("" if br.output is None else f"{br.output:.2f}", align=Qt.AlignmentFlag.AlignRight)
            )

    def _load_mass_tab(self, base_id: int) -> None:
        with make_session(self._engine) as session:
            rows = session.exec(
                select(MassProduction, Item)
                .where(MassProduction.base_id == base_id)
                .where(MassProduction.item_id == Item.id)
                .order_by(Item.name)
            ).all()

        self.mass_table.setRowCount(len(rows))
        for r, (mp, item) in enumerate(rows):
            self.mass_table.setItem(r, 0, _cell(item.name))
            self.mass_table.setItem(r, 1, _cell(str(mp.factories), align=Qt.AlignmentFlag.AlignRight))
            self.mass_table.setItem(r, 2, _cell(str(mp.carry), align=Qt.AlignmentFlag.AlignRight))
            self.mass_table.setItem(r, 3, _cell(mp.status or "—"))

    def _load_outposts_tab(self, hub_base_id: int) -> None:
        self._clear_outpost_widgets()
        with make_session(self._engine) as session:
            outposts = session.exec(select(Base).where(Base.hub_id == int(hub_base_id)).order_by(Base.name)).all()
            starbases = session.exec(select(Base).where(Base.starbase == True).order_by(Base.name)).all()
            systems = {int(s.id): s.name for s in session.exec(select(StarSystem)).all()}
            cbodies = {int(c.id): c for c in session.exec(select(CelestialBody)).all()}

        self.outpost_table.setRowCount(len(outposts))
        for r, o in enumerate(outposts):
            self.outpost_table.setItem(r, 0, _cell(str(o.id), align=Qt.AlignmentFlag.AlignRight))
            self.outpost_table.setItem(r, 1, _cell(o.name or "—"))
            loc = "—"
            if o.star_system_id is not None:
                loc = systems.get(int(o.star_system_id), str(o.star_system_id))
            if o.celestial_body_id is not None and int(o.celestial_body_id) in cbodies:
                cb = cbodies[int(o.celestial_body_id)]
                loc = f"{loc} / {cb.name or '—'}"
            self.outpost_table.setItem(r, 2, _cell(loc))
            combo = QComboBox()
            for sb in starbases:
                combo.addItem(f"{sb.name or sb.id} ({sb.id})", int(sb.id))
            ix = combo.findData(int(hub_base_id))
            if ix >= 0:
                combo.setCurrentIndex(ix)
            self.outpost_table.setCellWidget(r, 3, combo)
            self._outpost_hub_combos.append((int(o.id), combo))

    def _save_outpost_hubs(self) -> None:
        if not self._outpost_hub_combos:
            QMessageBox.information(self, "Nothing to save", "No outposts listed for this hub.")
            return
        try:
            with make_session(self._engine) as session:
                for oid, combo in self._outpost_hub_combos:
                    b = session.get(Base, int(oid))
                    if b is not None:
                        b.hub_id = int(combo.currentData())
                        session.add(b)
                session.commit()
            QMessageBox.information(self, "Saved", "Hub assignments updated.")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def _copy_competitive_orders(self) -> None:
        if not self._comp_text.strip() or self._comp_text.startswith("; No competitive"):
            QMessageBox.information(self, "Nothing to copy", "No competitive orders to copy (run market refresh + select a base).")
            return
        QApplication.clipboard().setText(self._comp_text)
        QMessageBox.information(self, "Copied", "Competitive buy orders copied to clipboard.")

    def _refresh_middleman_items(self) -> None:
        self.mid_item.clear()
        with make_session(self._engine) as session:
            items = middleman_candidate_items(session)
        for iid, label in items:
            self.mid_item.addItem(label, int(iid))
        if self.mid_item.count() == 0:
            self.mid_item.addItem("(No middleman candidates — refresh market)", None)

    def _generate_middleman(self) -> None:
        data = self.mid_item.currentData()
        if data is None:
            QMessageBox.information(self, "No item", "Select an item.")
            return
        try:
            with make_session(self._engine) as session:
                text = middleman_orders_text(session, int(data))
            self._mid_full_text = text
            self.mid_orders.setPlainText(text)
        except Exception as e:
            QMessageBox.critical(self, "Middleman failed", str(e))

    def _copy_middleman(self) -> None:
        if not self._mid_full_text.strip():
            QMessageBox.information(self, "Nothing to copy", "Generate middleman orders first.")
            return
        QApplication.clipboard().setText(self._mid_full_text)
        QMessageBox.information(self, "Copied", "Middleman orders copied to clipboard.")

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
