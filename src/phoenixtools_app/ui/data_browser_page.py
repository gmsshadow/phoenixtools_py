from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from sqlalchemy import func
from sqlmodel import select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import Base, Item, MarketBuy, MarketSell, StarSystem


class DataBrowserPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        col = QWidget()
        col_layout = QVBoxLayout(col)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Table", "Rows", "Notes", "Sample"])
        self.table.setAlternatingRowColors(True)

        col_layout.addWidget(self.table, 1)
        root.addWidget(col, 1)

        self._load()

    def _load(self) -> None:
        with make_session(self._engine) as session:
            rows = [
                ("StarSystem", _count(session, StarSystem), "Imported via setup/market", "…"),
                ("Base", _count(session, Base), "Imported via market", "…"),
                ("Item", _count(session, Item), "Imported via setup/market", "…"),
                ("MarketBuy", _count(session, MarketBuy), "Imported via market", "…"),
                ("MarketSell", _count(session, MarketSell), "Imported via market", "…"),
            ]

        self.table.setRowCount(len(rows))
        for r, (name, count, notes, sample) in enumerate(rows):
            self.table.setItem(r, 0, _cell(name))
            self.table.setItem(r, 1, _cell(str(count), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(r, 2, _cell(notes))
            self.table.setItem(r, 3, _cell(sample))


def _cell(text: str, *, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align is not None:
        item.setTextAlignment(int(align))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item


def _count(session, model) -> int:
    return int(session.exec(select(func.count()).select_from(model)).one())

