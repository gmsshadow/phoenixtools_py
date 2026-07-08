from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import Base, Item, ItemType, MarketBuy, MarketSell
from phoenixtools_app.services.import_items import fetch_single_item, run_items_fetch_missing
from phoenixtools_app.services.item_detail import ItemDetail, compute_item_detail
from phoenixtools_app.services.item_opportunities import (
    filter_profitable_no_route,
    item_ids_with_trade_routes,
    list_known_races,
    list_periphery_choices,
    middleman_orders_text_for_items,
    sellable_item_ids_for_periphery,
    sellable_item_ids_for_race,
)
from phoenixtools_app.ui.background import run_job

_VIEW_ALL = "all"
_VIEW_PROFITABLE = "profitable_no_route"
_VIEW_PERIPHERY = "periphery"
_VIEW_RACE = "race"


@dataclass(frozen=True)
class ItemRow:
    item_id: int
    name: str
    mass: int
    type_name: str | None
    attributes_fetched: bool
    best_sell: tuple[int, float] | None  # (base_id, price) - cheapest sell (you can buy)
    best_buy: tuple[int, float] | None  # (base_id, price) - highest buy (you can sell)
    spread: float | None


class ItemsPage(QWidget):
    # (destination_base_id, sell_item_id) -> open Path to base for the closest best buyer.
    request_path_to_base = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()
        self._rows: list[ItemRow] = []
        self._all_rows: list[ItemRow] = []
        self._routed_item_ids: set[int] = set()
        self._base_name: dict[int, str] = {}
        self._current_detail: ItemDetail | None = None
        self._job = None
        self._middleman_text = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by item name / ID …")

        self.view_mode = QComboBox()
        self.view_mode.addItem("All items", _VIEW_ALL)
        self.view_mode.addItem("Profitable but no trade route", _VIEW_PROFITABLE)
        self.view_mode.addItem("Periphery goods", _VIEW_PERIPHERY)
        self.view_mode.addItem("Race preferred goods", _VIEW_RACE)

        self.periphery_combo = QComboBox()
        self.periphery_combo.addItem("(Select periphery)", None)

        self.race_combo = QComboBox()
        self.race_combo.addItem("(Select race)", None)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)

        self.show_all = QCheckBox("Show all items (incl. unknown / not on any market)")
        self.refresh_btn = QPushButton("Refresh")
        self.fetch_one_btn = QPushButton("Fetch attributes for selection")
        self.fetch_missing_btn = QPushButton("Fetch all missing attributes")
        self.copy_middleman_btn = QPushButton("Copy middleman orders")
        self.middleman_orders = QTextEdit()
        self.middleman_orders.setReadOnly(True)
        self.middleman_orders.setMaximumHeight(100)
        self.middleman_orders.setPlaceholderText("Middleman orders for periphery / race lists appear here.")
        self.middleman_orders.setVisible(False)
        self.copy_middleman_btn.setVisible(False)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Item", "Mass", "Type", "Attrs", "Best sell @", "Sell", "Best buy @", "Buy", "Spread"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)

        left_layout.addWidget(QLabel("<b>Items</b>"))
        left_layout.addWidget(self.filter)
        left_layout.addWidget(self.view_mode)
        left_layout.addWidget(self.periphery_combo)
        left_layout.addWidget(self.race_combo)
        left_layout.addWidget(self.summary)
        left_layout.addWidget(self.show_all)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.fetch_one_btn)
        btn_row.addWidget(self.fetch_missing_btn)
        left_layout.addLayout(btn_row)
        mid_row = QHBoxLayout()
        mid_row.addWidget(self.copy_middleman_btn)
        left_layout.addLayout(mid_row)
        left_layout.addWidget(self.middleman_orders)
        left_layout.addWidget(self.table, 1)

        root.addWidget(left, 3)
        root.addWidget(self._build_detail_pane(), 2)

        self.refresh_btn.clicked.connect(self._refresh)
        self.filter.textChanged.connect(self._apply_filter)
        self.show_all.toggled.connect(self._apply_filter)
        self.view_mode.currentIndexChanged.connect(self._on_view_mode_changed)
        self.periphery_combo.currentIndexChanged.connect(self._on_opportunity_param_changed)
        self.race_combo.currentIndexChanged.connect(self._on_opportunity_param_changed)
        self.copy_middleman_btn.clicked.connect(self._copy_middleman_orders)
        self.fetch_one_btn.clicked.connect(self._fetch_selected)
        self.fetch_missing_btn.clicked.connect(self._fetch_missing)
        self.table.itemSelectionChanged.connect(self._show_detail)

        self._on_view_mode_changed()
        self._refresh()

    def _current_view(self) -> str:
        mode = self.view_mode.currentData()
        return mode if isinstance(mode, str) else _VIEW_ALL

    def _on_view_mode_changed(self) -> None:
        view = self._current_view()
        self.periphery_combo.setVisible(view == _VIEW_PERIPHERY)
        self.race_combo.setVisible(view == _VIEW_RACE)
        show_mm = view in (_VIEW_PERIPHERY, _VIEW_RACE)
        self.middleman_orders.setVisible(show_mm)
        self.copy_middleman_btn.setVisible(show_mm)
        self.show_all.setEnabled(view == _VIEW_ALL)
        self._apply_view_rows()

    def _on_opportunity_param_changed(self) -> None:
        if self._current_view() in (_VIEW_PERIPHERY, _VIEW_RACE):
            self._apply_view_rows()

    def _apply_view_rows(self) -> None:
        view = self._current_view()
        self._middleman_text = ""
        self.middleman_orders.clear()

        if view == _VIEW_ALL:
            self._rows = list(self._all_rows)
            self.summary.setText(f"{len(self._all_rows)} items loaded from database.")
        elif view == _VIEW_PROFITABLE:
            self._rows = filter_profitable_no_route(
                self._all_rows, routed_item_ids=self._routed_item_ids
            )
            self.summary.setText(
                f"{len(self._rows)} items with market profit but no generated trade route "
                f"({len(self._routed_item_ids)} items have routes)."
            )
        elif view == _VIEW_PERIPHERY:
            periphery = self.periphery_combo.currentData()
            if periphery is None:
                self._rows = []
                self.summary.setText("Select a periphery to list sellable trade goods from that periphery.")
            else:
                with make_session(self._engine) as session:
                    ids = sellable_item_ids_for_periphery(session, periphery)
                    self._rows = [r for r in self._all_rows if r.item_id in ids]
                    name = self.periphery_combo.currentText()
                    self._middleman_text = middleman_orders_text_for_items(
                        session, [r.item_id for r in self._rows]
                    )
                self.summary.setText(f"{len(self._rows)} sellable goods from {name}.")
                self.middleman_orders.setPlainText(self._middleman_text)
        elif view == _VIEW_RACE:
            race = self.race_combo.currentData()
            if not race:
                self._rows = []
                self.summary.setText("Select a race to list preferred sellable trade goods.")
            else:
                with make_session(self._engine) as session:
                    ids = sellable_item_ids_for_race(session, str(race))
                    self._rows = [r for r in self._all_rows if r.item_id in ids]
                    self._middleman_text = middleman_orders_text_for_items(
                        session, [r.item_id for r in self._rows]
                    )
                self.summary.setText(f'{len(self._rows)} sellable goods for race "{race}".')
                self.middleman_orders.setPlainText(self._middleman_text)
        else:
            self._rows = list(self._all_rows)
            self.summary.setText("")

        self._apply_filter()

    def _copy_middleman_orders(self) -> None:
        if not self._middleman_text:
            QMessageBox.information(self, "Nothing to copy", "No middleman orders for this list.")
            return
        QApplication.clipboard().setText(self._middleman_text)
        QMessageBox.information(self, "Copied", "Middleman orders copied to clipboard.")

    def _build_detail_pane(self) -> QWidget:
        right = QWidget()
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)

        self.detail_title = QLabel("<i>Select an item</i>")
        self.detail_title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.detail_title)

        tabs = QTabWidget()

        # --- Details tab ---
        details = QWidget()
        details_layout = QVBoxLayout(details)
        form_host = QWidget()
        self.detail_form = QFormLayout(form_host)
        self.detail_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.d_mass = QLabel("—")
        self.d_type = QLabel("—")
        self.d_tech = QLabel("—")
        self.d_race = QLabel("—")
        self.d_origin = QLabel("—")
        self.d_periphery = QLabel("—")
        self.d_source_value = QLabel("—")
        self.d_substitute = QLabel("—")
        self.d_production = QLabel("—")
        self.detail_form.addRow("Mass", self.d_mass)
        self.detail_form.addRow("Type", self.d_type)
        self.detail_form.addRow("Tech level", self.d_tech)
        self.detail_form.addRow("Race", self.d_race)
        self.detail_form.addRow("Origin", self.d_origin)
        self.detail_form.addRow("Periphery", self.d_periphery)
        self.detail_form.addRow("Value at source", self.d_source_value)
        self.detail_form.addRow("Substitute", self.d_substitute)
        self.detail_form.addRow("Production", self.d_production)
        details_layout.addWidget(form_host)
        self.d_tech_manual = QTextEdit()
        self.d_tech_manual.setReadOnly(True)
        self.d_tech_manual.setPlaceholderText("Tech manual / description")
        self.d_tech_manual.setMaximumHeight(90)
        details_layout.addWidget(self.d_tech_manual)
        details_layout.addWidget(QLabel("<b>Raw materials</b>"))
        self.d_raw = QTableWidget(0, 3)
        self.d_raw.setHorizontalHeaderLabels(["Qty", "Item", "ID"])
        self.d_raw.setAlternatingRowColors(True)
        details_layout.addWidget(self.d_raw, 1)
        details_layout.addWidget(QLabel("<b>All attributes</b>"))
        self.d_attrs = QTableWidget(0, 2)
        self.d_attrs.setHorizontalHeaderLabels(["Attribute", "Value"])
        self.d_attrs.setAlternatingRowColors(True)
        details_layout.addWidget(self.d_attrs, 2)
        tabs.addTab(details, "Details")

        # --- Market tab ---
        market = QWidget()
        market_layout = QVBoxLayout(market)
        market_layout.addWidget(QLabel("<b>Sellers</b> <small>(cheapest first — you can buy)</small>"))
        self.d_sellers = QTableWidget(0, 4)
        self.d_sellers.setHorizontalHeaderLabels(["Base", "Location", "Qty", "Price"])
        self.d_sellers.setAlternatingRowColors(True)
        market_layout.addWidget(self.d_sellers, 1)
        market_layout.addWidget(QLabel("<b>Buyers</b> <small>(highest first — you can sell)</small>"))
        self.d_buyers = QTableWidget(0, 4)
        self.d_buyers.setHorizontalHeaderLabels(["Base", "Location", "Qty", "Price"])
        self.d_buyers.setAlternatingRowColors(True)
        market_layout.addWidget(self.d_buyers, 1)
        self.path_to_buyer_btn = QPushButton("Path to best buyer →")
        self.path_to_buyer_btn.setEnabled(False)
        self.path_to_buyer_btn.clicked.connect(self._open_path_to_best_buyer)
        market_layout.addWidget(self.path_to_buyer_btn)
        tabs.addTab(market, "Market")

        # --- Production tab ---
        production = QWidget()
        production_layout = QVBoxLayout(production)
        self.d_prod_total = QLabel("Total production: —")
        production_layout.addWidget(self.d_prod_total)
        self.d_production_tbl = QTableWidget(0, 3)
        self.d_production_tbl.setHorizontalHeaderLabels(["Base", "Output/wk", "Source"])
        self.d_production_tbl.setAlternatingRowColors(True)
        production_layout.addWidget(self.d_production_tbl, 1)
        production_layout.addWidget(QLabel("<b>Best resource deposits</b>"))
        self.d_best = QTableWidget(0, 5)
        self.d_best.setHorizontalHeaderLabels(["Base", "Res#", "Yield", "Drop", "Next +/wk"])
        self.d_best.setAlternatingRowColors(True)
        production_layout.addWidget(self.d_best, 1)
        tabs.addTab(production, "Production")

        layout.addWidget(tabs, 1)
        return right

    def _refresh(self) -> None:
        with make_session(self._engine) as session:
            self._base_name = _base_names(session)
            self._all_rows = _load_item_rows(session)
            self._routed_item_ids = item_ids_with_trade_routes(session)
            peripheries = list_periphery_choices(session)
            races = list_known_races(session)

        self.periphery_combo.blockSignals(True)
        cur_per = self.periphery_combo.currentData()
        self.periphery_combo.clear()
        self.periphery_combo.addItem("(Select periphery)", None)
        for pid, name in peripheries:
            self.periphery_combo.addItem(name, pid)
        if cur_per is not None:
            ix = self.periphery_combo.findData(cur_per)
            if ix >= 0:
                self.periphery_combo.setCurrentIndex(ix)
        self.periphery_combo.blockSignals(False)

        self.race_combo.blockSignals(True)
        cur_race = self.race_combo.currentData()
        self.race_combo.clear()
        self.race_combo.addItem("(Select race)", None)
        for race in races:
            self.race_combo.addItem(race, race)
        if cur_race:
            ix = self.race_combo.findData(cur_race)
            if ix >= 0:
                self.race_combo.setCurrentIndex(ix)
        self.race_combo.blockSignals(False)

        self._apply_view_rows()

    def _apply_filter(self) -> None:
        q = self.filter.text().strip().lower()
        rows = self._rows
        if self._current_view() == _VIEW_ALL and not self.show_all.isChecked():
            rows = [r for r in rows if r.attributes_fetched or r.best_sell or r.best_buy]
        if q:
            rows = [
                r
                for r in rows
                if q in r.name.lower() or q == str(r.item_id)
            ]

        self._filtered = rows
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            sell_base = self._base_name.get(r.best_sell[0], f"Base {r.best_sell[0]}") if r.best_sell else "—"
            buy_base = self._base_name.get(r.best_buy[0], f"Base {r.best_buy[0]}") if r.best_buy else "—"

            self.table.setItem(i, 0, _num_cell(r.item_id))
            self.table.setItem(i, 1, _cell(r.name))
            self.table.setItem(i, 2, _num_cell(r.mass))
            self.table.setItem(i, 3, _cell(r.type_name or "—"))
            self.table.setItem(i, 4, _cell("✓" if r.attributes_fetched else ""))
            self.table.setItem(i, 5, _cell(sell_base))
            self.table.setItem(i, 6, _num_cell(None if r.best_sell is None else round(r.best_sell[1], 2)))
            self.table.setItem(i, 7, _cell(buy_base))
            self.table.setItem(i, 8, _num_cell(None if r.best_buy is None else round(r.best_buy[1], 2)))
            self.table.setItem(i, 9, _num_cell(None if r.spread is None else round(r.spread, 2)))
        self.table.setSortingEnabled(True)
        if rows:
            self.table.selectRow(0)
        self.table.blockSignals(False)
        self._show_detail()

    def _show_detail(self) -> None:
        item_id = self._selected_item_id()
        if item_id is None:
            self._populate_detail(None)
            return
        with make_session(self._engine) as session:
            detail = compute_item_detail(session, item_id)
        self._populate_detail(detail)

    def _populate_detail(self, d: ItemDetail | None) -> None:
        self._current_detail = d
        self.path_to_buyer_btn.setEnabled(bool(d and d.buyers))
        if d and d.buyers:
            self.path_to_buyer_btn.setText(f"Path to best buyer → {d.buyers[0].base_name}")
        else:
            self.path_to_buyer_btn.setText("Path to best buyer →")
        if d is None:
            self.detail_title.setText("<i>Select an item</i>")
            self.d_mass.setText("—")
            self.d_type.setText("—")
            self.d_tech.setText("—")
            self.d_race.setText("—")
            self.d_origin.setText("—")
            self.d_periphery.setText("—")
            self.d_source_value.setText("—")
            self.d_substitute.setText("—")
            self.d_production.setText("—")
            self.d_tech_manual.setPlainText("")
            for tbl in (self.d_raw, self.d_attrs, self.d_sellers, self.d_buyers, self.d_production_tbl, self.d_best):
                tbl.setRowCount(0)
            self.d_prod_total.setText("Total production: —")
            return

        flag = " · attributes not fetched" if not d.attributes_fetched else ""
        self.detail_title.setText(f"<b>{d.name}</b> <small>(ID {d.item_id}{flag})</small>")
        self.d_mass.setText(str(d.mass))
        self.d_type.setText(d.type_name or d.type_attribute or "—")
        self.d_tech.setText("—" if d.tech_level is None else str(d.tech_level))
        self.d_race.setText(d.race or "—")
        if d.origin_system is not None:
            origin = f"{d.origin_system.name} ({d.origin_system.item_id})"
            if d.origin_cbody_name:
                origin += f" / {d.origin_cbody_name}"
            self.d_origin.setText(origin)
        else:
            self.d_origin.setText("—")
        self.d_periphery.setText(d.origin_periphery or "—")
        self.d_source_value.setText("—" if d.source_value is None else f"{d.source_value:g}")
        if d.substitute is not None:
            ratio = "" if d.substitute_ratio is None else f"{d.substitute_ratio:g} × "
            self.d_substitute.setText(f"{ratio}{d.substitute.name} ({d.substitute.item_id})")
        else:
            self.d_substitute.setText("—")
        prod_txt = "—" if d.production is None else f"{d.production:g}"
        if d.production_limit is not None:
            prod_txt += f"  (limit {d.production_limit:g})"
        if d.blueprint is not None:
            prod_txt += f"  · blueprint: {d.blueprint.name} ({d.blueprint.item_id})"
        self.d_production.setText(prod_txt)
        self.d_tech_manual.setPlainText(d.tech_manual or "")

        self._fill_raw(self.d_raw, d.raw_materials)
        self._fill_attrs(d.attributes)
        self._fill_market(self.d_sellers, d.sellers)
        self._fill_market(self.d_buyers, d.buyers)
        self._fill_production(d)

    def _fill_raw(self, tbl: QTableWidget, mats) -> None:
        tbl.setRowCount(len(mats))
        for i, m in enumerate(mats):
            tbl.setItem(i, 0, _cell(f"{m.quantity:g}", align=Qt.AlignmentFlag.AlignRight))
            tbl.setItem(i, 1, _cell(m.name))
            tbl.setItem(i, 2, _num_cell(m.item_id))

    def _fill_attrs(self, attrs) -> None:
        self.d_attrs.setRowCount(len(attrs))
        for i, (k, v) in enumerate(attrs):
            self.d_attrs.setItem(i, 0, _cell(k))
            self.d_attrs.setItem(i, 1, _cell(v))

    def _fill_market(self, tbl: QTableWidget, rows) -> None:
        tbl.setSortingEnabled(False)
        tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            tbl.setItem(i, 0, _cell(r.base_name))
            tbl.setItem(i, 1, _cell(r.location))
            tbl.setItem(i, 2, _num_cell(r.quantity))
            tbl.setItem(i, 3, _num_cell(round(r.price, 2)))
        tbl.setSortingEnabled(True)

    def _fill_production(self, d: ItemDetail) -> None:
        self.d_prod_total.setText(f"Total production: {d.total_production} / week")
        self.d_production_tbl.setRowCount(len(d.starbase_production))
        for i, p in enumerate(d.starbase_production):
            self.d_production_tbl.setItem(i, 0, _cell(p.base_name))
            self.d_production_tbl.setItem(i, 1, _num_cell(p.output))
            self.d_production_tbl.setItem(i, 2, _cell(p.source))
        self.d_best.setRowCount(len(d.best_resources))
        for i, c in enumerate(d.best_resources):
            self.d_best.setItem(i, 0, _cell(c.base_name))
            self.d_best.setItem(i, 1, _num_cell(c.resource_id))
            self.d_best.setItem(i, 2, _num_cell(round(c.resource_yield, 3)))
            self.d_best.setItem(i, 3, _num_cell(c.resource_drop))
            self.d_best.setItem(i, 4, _num_cell(c.next_complex_output))

    def _open_path_to_best_buyer(self) -> None:
        d = self._current_detail
        if d is None or not d.buyers:
            QMessageBox.information(self, "No buyer", "This item has no current buyers on the market.")
            return
        self.request_path_to_base.emit(int(d.buyers[0].base_id), int(d.item_id))

    def _selected_item_id(self) -> int | None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return None
        row = min(rows)
        id_item = self.table.item(row, 0)
        if id_item is None:
            return None
        try:
            return int(id_item.text())
        except ValueError:
            return None

    def _fetch_selected(self) -> None:
        item_id = self._selected_item_id()
        if item_id is None:
            QMessageBox.information(self, "No selection", "Select an item first.")
            return
        try:
            with make_session(self._engine) as session:
                ok = fetch_single_item(session, item_id)
            if ok:
                QMessageBox.information(self, "Fetched", f"Attributes fetched for item {item_id}.")
                self._refresh()
            else:
                QMessageBox.warning(self, "No data", f"No attributes returned for item {item_id}.")
        except Exception as e:
            QMessageBox.critical(self, "Fetch failed", str(e))

    def _fetch_missing(self) -> None:
        if self._job is not None:
            QMessageBox.information(self, "Busy", "An item fetch is already running.")
            return

        self.fetch_missing_btn.setEnabled(False)
        self.fetch_missing_btn.setText("Fetching …")

        def job(progress) -> str:
            engine = make_engine()
            with make_session(engine) as session:
                result = run_items_fetch_missing(session, progress=progress)
            return f"Attempted {result.attempted}, fetched {result.fetched}, failed {result.failed}."

        def on_progress(msg: str) -> None:
            self.fetch_missing_btn.setText(msg if len(msg) < 60 else msg[:57] + "…")

        def on_done(summary: str) -> None:
            self._job = None
            self.fetch_missing_btn.setEnabled(True)
            self.fetch_missing_btn.setText("Fetch all missing attributes")
            QMessageBox.information(self, "Fetch complete", summary)
            self._refresh()

        def on_failed(err: str) -> None:
            self._job = None
            self.fetch_missing_btn.setEnabled(True)
            self.fetch_missing_btn.setText("Fetch all missing attributes")
            QMessageBox.critical(self, "Fetch failed", err)

        self._job = run_job(self, job, on_progress=on_progress, on_done=on_done, on_failed=on_failed)


def _load_item_rows(session: Session) -> list[ItemRow]:
    items = session.exec(select(Item).order_by(Item.name)).all()
    buys = session.exec(select(MarketBuy)).all()
    sells = session.exec(select(MarketSell)).all()
    type_names = {int(t.id): t.name for t in session.exec(select(ItemType)).all()}

    best_buy: dict[int, tuple[int, float]] = {}
    for b in buys:
        cur = best_buy.get(int(b.item_id))
        if cur is None or float(b.price) > cur[1]:
            best_buy[int(b.item_id)] = (int(b.base_id), float(b.price))

    best_sell: dict[int, tuple[int, float]] = {}
    for s in sells:
        cur = best_sell.get(int(s.item_id))
        if cur is None or float(s.price) < cur[1]:
            best_sell[int(s.item_id)] = (int(s.base_id), float(s.price))

    out: list[ItemRow] = []
    for it in items:
        sell = best_sell.get(int(it.id))
        buy = best_buy.get(int(it.id))
        spread = None
        if sell and buy:
            spread = float(buy[1] - sell[1])
        out.append(
            ItemRow(
                item_id=int(it.id),
                name=it.name,
                mass=int(it.mass or 0),
                type_name=type_names.get(int(it.item_type_id)) if it.item_type_id is not None else None,
                attributes_fetched=bool(it.attributes_fetched),
                best_sell=sell,
                best_buy=buy,
                spread=spread,
            )
        )

    out.sort(key=lambda r: (r.spread is None, -(r.spread or 0.0), r.name.lower()))
    return out


def _base_names(session: Session) -> dict[int, str]:
    out: dict[int, str] = {}
    for b in session.exec(select(Base)).all():
        out[int(b.id)] = b.name or f"Base {b.id}"
    return out


def _cell(text: str, *, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align is not None:
        item.setTextAlignment(int(align))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item


def _num_cell(value: int | float | None) -> QTableWidgetItem:
    """Right-aligned cell that sorts numerically (empty cells sort last ascending)."""
    item = QTableWidgetItem()
    if value is not None:
        item.setData(Qt.ItemDataRole.EditRole, value)
    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item

