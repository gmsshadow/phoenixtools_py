from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sqlmodel import select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import ItemType, NexusConfig, StarSystem
from phoenixtools_app.services.trade_routes import (
    TradeRouteFilter,
    TradeRouteRow,
    assign_barge,
    orders_for_candidate_with_session,
    orders_for_trade_route,
    query_trade_routes,
    run_trade_route_generation,
)


class TradeRoutesPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()
        self._routes: list[TradeRouteRow] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        filters = QWidget()
        fl = QFormLayout(filters)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.start_system = QComboBox()
        self.start_system.addItem("(Any start system)", None)
        self.max_tus = QSpinBox()
        self.max_tus.setRange(50, 5000)
        self.max_tus.setValue(300)
        self.item_type = QComboBox()
        self.item_type.addItem("(Any item type)", None)
        self.no_keys = QCheckBox("No stargate keys (route + path to start)")
        self.exclude_my_aff = QCheckBox("Exclude destinations in my affiliation")
        self.lifeform_filter = QComboBox()
        self.lifeform_filter.addItem("All items", None)
        self.lifeform_filter.addItem("Life-type items only", "only")
        self.lifeform_filter.addItem("Exclude life-type items", "exclude")

        fl.addRow("Start system", self.start_system)
        fl.addRow("Max TUs (travel + to start)", self.max_tus)
        fl.addRow("Item type", self.item_type)
        fl.addRow("", self.no_keys)
        fl.addRow("", self.exclude_my_aff)
        fl.addRow("Life item types", self.lifeform_filter)

        self.refresh_btn = QPushButton("Apply filters / refresh list")
        self.regen_btn = QPushButton("Regenerate routes (latest market)")
        self.assign_barge_btn = QPushButton("Assign barge to selection")
        self.copy_orders_btn = QPushButton("Copy orders for selection")

        self.table = QTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels(
            [
                "Item",
                "From",
                "To",
                "Sell",
                "Buy",
                "Vol",
                "Path TU",
                "Travel",
                "Barge $/wk",
                "Brg",
                "Max",
                "Keys",
                "Profit",
            ]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)

        left_layout.addWidget(filters)
        row_btns = QHBoxLayout()
        row_btns.addWidget(self.refresh_btn)
        row_btns.addWidget(self.regen_btn)
        left_layout.addLayout(row_btns)
        left_layout.addWidget(self.assign_barge_btn)
        left_layout.addWidget(self.copy_orders_btn)
        left_layout.addWidget(self.table, 1)

        self.orders = QTextEdit()
        self.orders.setReadOnly(True)

        root.addWidget(left, 3)
        root.addWidget(self.orders, 2)

        self.refresh_btn.clicked.connect(self._refresh)
        self.regen_btn.clicked.connect(self._regenerate)
        self.assign_barge_btn.clicked.connect(self._assign_barge)
        self.copy_orders_btn.clicked.connect(self._copy_orders)
        self.table.itemSelectionChanged.connect(self._show_orders_preview)

        self._load_filter_options()
        self._refresh()

    def _load_filter_options(self) -> None:
        self.start_system.clear()
        self.start_system.addItem("(Any start system)", None)
        self.item_type.clear()
        self.item_type.addItem("(Any item type)", None)
        with make_session(self._engine) as session:
            for ss in session.exec(select(StarSystem).order_by(StarSystem.name)).all():
                self.start_system.addItem(f"{ss.name} ({ss.id})", int(ss.id))
            for it in session.exec(select(ItemType).order_by(ItemType.name)).all():
                self.item_type.addItem(f"{it.name} ({it.id})", int(it.id))

    def _build_filter(self) -> TradeRouteFilter:
        start_data = self.start_system.currentData()
        it_data = self.item_type.currentData()
        lf = self.lifeform_filter.currentData()
        excl_aff = None
        if self.exclude_my_aff.isChecked():
            with make_session(self._engine) as session:
                cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
                if cfg and cfg.affiliation_id is not None:
                    excl_aff = int(cfg.affiliation_id)
        return TradeRouteFilter(
            start_system_id=int(start_data) if start_data is not None else None,
            max_tus=int(self.max_tus.value()),
            item_type_id=int(it_data) if it_data is not None else None,
            no_keys=self.no_keys.isChecked(),
            exclude_dest_affiliation_id=excl_aff,
            lifeform_mode=str(lf) if lf is not None else None,
        )

    def _refresh(self) -> None:
        with make_session(self._engine) as session:
            self._routes = query_trade_routes(session, self._build_filter())

        self.table.setRowCount(len(self._routes))
        for r, tr in enumerate(self._routes):
            self.table.setItem(r, 0, _cell(tr.item_name))
            self.table.setItem(r, 1, _cell(tr.from_base_name))
            self.table.setItem(r, 2, _cell(tr.to_base_name))
            self.table.setItem(r, 3, _cell(f"{tr.sell_price:.2f}", align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 4, _cell(f"{tr.buy_price:.2f}", align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 5, _cell(str(tr.volume), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 6, _cell(str(tr.path_tu), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 7, _cell(str(tr.travel_time), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 8, _cell(str(tr.barge_weekly_profit), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 9, _cell(str(tr.barges_assigned), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 10, _cell(str(tr.barges_max), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 11, _cell("Y" if tr.requires_gate_keys else ""))
            self.table.setItem(r, 12, _cell(str(tr.total_profit), align=Qt.AlignmentFlag.AlignRight))

        if self._routes:
            self.table.selectRow(0)

    def _regenerate(self) -> None:
        try:
            with make_session(self._engine) as session:
                n = run_trade_route_generation(session)
            QMessageBox.information(self, "Regenerated", f"Generated {n} trade routes from the latest market snapshot.")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Regenerate failed", str(e))

    def _selected(self) -> TradeRouteRow | None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return None
        row = min(rows)
        if row < 0 or row >= len(self._routes):
            return None
        return self._routes[row]

    def _show_orders_preview(self) -> None:
        tr = self._selected()
        if tr is None:
            self.orders.setPlainText("")
            return
        with make_session(self._engine) as session:
            self.orders.setPlainText(orders_for_candidate_with_session(session, tr))

    def _copy_orders(self) -> None:
        tr = self._selected()
        if tr is None:
            QMessageBox.information(self, "No selection", "Select a route first.")
            return
        with make_session(self._engine) as session:
            text = orders_for_trade_route(session, int(tr.id))
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copied", "Orders copied to clipboard.")

    def _assign_barge(self) -> None:
        tr = self._selected()
        if tr is None:
            QMessageBox.information(self, "No selection", "Select a route first.")
            return
        try:
            with make_session(self._engine) as session:
                ok = assign_barge(session, int(tr.id))
            if ok:
                QMessageBox.information(self, "Barge assigned", "barges_assigned incremented.")
                self._refresh()
            else:
                QMessageBox.information(self, "No assignment", "No free barge slots or route invalid.")
        except Exception as e:
            QMessageBox.critical(self, "Assign failed", str(e))


def _cell(text: str, *, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align is not None:
        item.setTextAlignment(int(align))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item
