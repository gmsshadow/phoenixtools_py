from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.services.trade_routes import (
    TradeRouteCandidate,
    generate_trade_routes,
    orders_for_candidate_with_session,
)


class TradeRoutesPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()
        self._routes: list[TradeRouteCandidate] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.refresh_btn = QPushButton("Refresh routes")
        self.copy_orders_btn = QPushButton("Copy orders for selection")

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Item", "From", "To", "Sell", "Buy", "Volume", "Total profit"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)

        left_layout.addWidget(self.refresh_btn)
        left_layout.addWidget(self.copy_orders_btn)
        left_layout.addWidget(self.table, 1)

        self.orders = QTextEdit()
        self.orders.setReadOnly(True)

        root.addWidget(left, 3)
        root.addWidget(self.orders, 2)

        self.refresh_btn.clicked.connect(self._refresh)
        self.copy_orders_btn.clicked.connect(self._copy_orders)
        self.table.itemSelectionChanged.connect(self._show_orders_preview)

        self._refresh()

    def _refresh(self) -> None:
        with make_session(self._engine) as session:
            self._routes = generate_trade_routes(session)

        self.table.setRowCount(len(self._routes))
        for r, tr in enumerate(self._routes):
            self.table.setItem(r, 0, _cell(tr.item_name))
            self.table.setItem(r, 1, _cell(tr.from_base_name))
            self.table.setItem(r, 2, _cell(tr.to_base_name))
            self.table.setItem(r, 3, _cell(f"{tr.sell_price:.2f}", align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 4, _cell(f"{tr.buy_price:.2f}", align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 5, _cell(str(tr.volume), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 6, _cell(f"{tr.total_profit:.0f}", align=Qt.AlignmentFlag.AlignRight))

        if self._routes:
            self.table.selectRow(0)

    def _selected(self) -> TradeRouteCandidate | None:
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
            text = orders_for_candidate_with_session(session, tr)
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copied", "Orders copied to clipboard.")


def _cell(text: str, *, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align is not None:
        item.setTextAlignment(int(align))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item

