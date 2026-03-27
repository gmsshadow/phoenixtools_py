from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import Base, Item, MarketBuy, MarketSell


@dataclass(frozen=True)
class ItemRow:
    item_id: int
    name: str
    best_sell: tuple[int, float] | None  # (base_id, price) - cheapest sell (you can buy)
    best_buy: tuple[int, float] | None  # (base_id, price) - highest buy (you can sell)
    spread: float | None


class ItemsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()
        self._rows: list[ItemRow] = []
        self._base_name: dict[int, str] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by item name / ID …")
        self.refresh_btn = QPushButton("Refresh")

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "Item", "Best sell @", "Sell", "Best buy @", "Buy", "Spread"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)

        left_layout.addWidget(QLabel("<b>Items</b>"))
        left_layout.addWidget(self.filter)
        left_layout.addWidget(self.refresh_btn)
        left_layout.addWidget(self.table, 1)

        root.addWidget(left, 1)

        self.refresh_btn.clicked.connect(self._refresh)
        self.filter.textChanged.connect(self._apply_filter)

        self._refresh()

    def _refresh(self) -> None:
        with make_session(self._engine) as session:
            self._base_name = _base_names(session)
            self._rows = _load_item_rows(session)
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self.filter.text().strip().lower()
        rows = self._rows
        if q:
            rows = [
                r
                for r in self._rows
                if q in r.name.lower() or q == str(r.item_id)
            ]

        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            sell_base = self._base_name.get(r.best_sell[0], f"Base {r.best_sell[0]}") if r.best_sell else "—"
            buy_base = self._base_name.get(r.best_buy[0], f"Base {r.best_buy[0]}") if r.best_buy else "—"
            sell_price = "" if r.best_sell is None else f"{r.best_sell[1]:.2f}"
            buy_price = "" if r.best_buy is None else f"{r.best_buy[1]:.2f}"
            spread = "" if r.spread is None else f"{r.spread:.2f}"

            self.table.setItem(i, 0, _cell(str(r.item_id), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(i, 1, _cell(r.name))
            self.table.setItem(i, 2, _cell(sell_base))
            self.table.setItem(i, 3, _cell(sell_price, align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(i, 4, _cell(buy_base))
            self.table.setItem(i, 5, _cell(buy_price, align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(i, 6, _cell(spread, align=Qt.AlignmentFlag.AlignRight))


def _load_item_rows(session: Session) -> list[ItemRow]:
    items = session.exec(select(Item).order_by(Item.name)).all()
    buys = session.exec(select(MarketBuy)).all()
    sells = session.exec(select(MarketSell)).all()

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
        out.append(ItemRow(item_id=int(it.id), name=it.name, best_sell=sell, best_buy=buy, spread=spread))

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

