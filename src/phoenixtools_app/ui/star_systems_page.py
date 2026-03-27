from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import JumpLink, StarSystem
from phoenixtools_app.services.pathing import shortest_path


class StarSystemsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()
        self._systems: list[StarSystem] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by system name / ID …")
        self.refresh_btn = QPushButton("Refresh")

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["ID", "System"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)

        left_layout.addWidget(QLabel("<b>Star systems</b>"))
        left_layout.addWidget(self.filter)
        left_layout.addWidget(self.refresh_btn)
        left_layout.addWidget(self.table, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        detail = QWidget()
        detail_layout = QFormLayout(detail)
        detail_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.sys_id = QLabel("—")
        self.sys_name = QLabel("—")
        detail_layout.addRow("ID", self.sys_id)
        detail_layout.addRow("Name", self.sys_name)

        self.links = QTextEdit()
        self.links.setReadOnly(True)

        pf = QWidget()
        pf_layout = QFormLayout(pf)
        pf_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.from_id = QLineEdit()
        self.to_id = QLineEdit()
        self.path_btn = QPushButton("Compute path")
        self.path_out = QTextEdit()
        self.path_out.setReadOnly(True)
        pf_layout.addRow("From system ID", self.from_id)
        pf_layout.addRow("To system ID", self.to_id)
        pf_layout.addRow("", self.path_btn)

        right_layout.addWidget(detail)
        right_layout.addWidget(QLabel("<b>Jump links (outgoing)</b>"))
        right_layout.addWidget(self.links, 1)
        right_layout.addWidget(QLabel("<b>Path finder</b>"))
        right_layout.addWidget(pf)
        right_layout.addWidget(self.path_out, 1)

        root.addWidget(left, 2)
        root.addWidget(right, 3)

        self.refresh_btn.clicked.connect(self._refresh)
        self.filter.textChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.path_btn.clicked.connect(self._compute_path)

        self._refresh()

    def _refresh(self) -> None:
        with make_session(self._engine) as session:
            self._systems = session.exec(select(StarSystem).order_by(StarSystem.name)).all()
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self.filter.text().strip().lower()
        rows = self._systems
        if q:
            rows = [s for s in self._systems if q in s.name.lower() or q == str(s.id)]

        self.table.setRowCount(len(rows))
        for i, s in enumerate(rows):
            self.table.setItem(i, 0, _cell(str(s.id), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(i, 1, _cell(s.name))

        if rows:
            self.table.selectRow(0)
        else:
            self._set_detail(None)

    def _selected_system_id(self) -> int | None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return None
        row = min(rows)
        cell = self.table.item(row, 0)
        if cell is None:
            return None
        try:
            return int(cell.text())
        except ValueError:
            return None

    def _show_detail(self) -> None:
        sys_id = self._selected_system_id()
        with make_session(self._engine) as session:
            ss = session.get(StarSystem, sys_id) if sys_id is not None else None
            links = []
            if ss is not None:
                links = session.exec(select(JumpLink).where(JumpLink.from_id == ss.id).order_by(JumpLink.jumps)).all()
                name_by_id = {int(s.id): s.name for s in session.exec(select(StarSystem)).all()}
                text = "\n".join(
                    f"{ss.name} -> {name_by_id.get(int(l.to_id), l.to_id)}  ({l.jumps} jumps, tu={l.tu_cost})"
                    for l in links
                )
            else:
                text = ""
        self._set_detail(ss)
        self.links.setPlainText(text)

    def _set_detail(self, ss: StarSystem | None) -> None:
        if ss is None:
            self.sys_id.setText("—")
            self.sys_name.setText("—")
            return
        self.sys_id.setText(str(ss.id))
        self.sys_name.setText(ss.name)

    def _compute_path(self) -> None:
        try:
            a = int(self.from_id.text().strip())
            b = int(self.to_id.text().strip())
        except ValueError:
            self.path_out.setPlainText("Enter valid numeric system IDs.")
            return
        with make_session(self._engine) as session:
            result = shortest_path(session, a, b)
            if result is None:
                self.path_out.setPlainText("No path found.")
                return
            names = {int(s.id): s.name for s in session.exec(select(StarSystem)).all()}
            pretty = " -> ".join(names.get(i, str(i)) for i in result.system_ids)
            self.path_out.setPlainText(f"TU cost: {result.tu_cost}\n{pretty}")


def _cell(text: str, *, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align is not None:
        item.setTextAlignment(int(align))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item

