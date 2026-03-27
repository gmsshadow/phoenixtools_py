from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import CelestialBody, StarSystem
from phoenixtools_app.services.phoenix_order import PhoenixOrder


class CelestialBodiesPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()
        self._rows: list[tuple[CelestialBody, StarSystem | None]] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name / type / system / cbody_id …")
        self.refresh_btn = QPushButton("Refresh")

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "System", "CBody ID", "Name", "Type", "Quad/Ring"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)

        left_layout.addWidget(QLabel("<b>Celestial bodies</b>"))
        left_layout.addWidget(self.filter)
        left_layout.addWidget(self.refresh_btn)
        left_layout.addWidget(self.table, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        detail = QWidget()
        detail_layout = QFormLayout(detail)
        detail_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.sys_name = QLabel("—")
        self.cbody_name = QLabel("—")
        self.cbody_type = QLabel("—")
        self.cbody_quad = QLabel("—")
        detail_layout.addRow("System", self.sys_name)
        detail_layout.addRow("Name", self.cbody_name)
        detail_layout.addRow("Type", self.cbody_type)
        detail_layout.addRow("Quad/Ring", self.cbody_quad)

        planner = QWidget()
        planner_layout = QFormLayout(planner)
        planner_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.ships = QSpinBox()
        self.ships.setRange(1, 50)
        self.ships.setValue(5)
        self.row = QSpinBox()
        self.row.setRange(0, 200)
        self.row.setValue(1)
        self.start_x = QSpinBox()
        self.start_x.setRange(0, 200)
        self.start_x.setValue(1)
        self.end_x = QSpinBox()
        self.end_x.setRange(0, 200)
        self.end_x.setValue(10)
        self.ore_type = QSpinBox()
        self.ore_type.setRange(0, 20)
        self.ore_type.setValue(0)
        self.gen_btn = QPushButton("Generate GPI orders")
        self.orders = QTextEdit()
        self.orders.setReadOnly(True)

        planner_layout.addRow("Ships", self.ships)
        planner_layout.addRow("Row", self.row)
        planner_layout.addRow("Start X", self.start_x)
        planner_layout.addRow("End X", self.end_x)
        planner_layout.addRow("Ore type", self.ore_type)
        planner_layout.addRow("", self.gen_btn)

        right_layout.addWidget(detail)
        right_layout.addWidget(QLabel("<b>GPI planner (basic)</b>"))
        right_layout.addWidget(planner)
        right_layout.addWidget(self.orders, 1)

        root.addWidget(left, 3)
        root.addWidget(right, 2)

        self.refresh_btn.clicked.connect(self._refresh)
        self.filter.textChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.gen_btn.clicked.connect(self._generate)

        self._refresh()

    def _refresh(self) -> None:
        with make_session(self._engine) as session:
            self._rows = _load_rows(session)
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self.filter.text().strip().lower()
        rows = self._rows
        if q:
            def matches(cb: CelestialBody, ss: StarSystem | None) -> bool:
                if q in str(cb.cbody_id).lower():
                    return True
                if q in str(cb.id or "").lower():
                    return True
                if cb.name and q in cb.name.lower():
                    return True
                if cb.cbody_type and q in cb.cbody_type.lower():
                    return True
                if ss and q in ss.name.lower():
                    return True
                return False

            rows = [(cb, ss) for (cb, ss) in self._rows if matches(cb, ss)]

        self.table.setRowCount(len(rows))
        for i, (cb, ss) in enumerate(rows):
            quad_ring = "—"
            if cb.quad is not None or cb.ring is not None:
                quad_ring = f"{cb.quad or '—'}/{cb.ring or '—'}"
            self.table.setItem(i, 0, _cell(str(cb.id or ""), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(i, 1, _cell(ss.name if ss else "—"))
            self.table.setItem(i, 2, _cell(str(cb.cbody_id), align=Qt.AlignmentFlag.AlignRight))
            self.table.setItem(i, 3, _cell(cb.name or "—"))
            self.table.setItem(i, 4, _cell(cb.cbody_type or "—"))
            self.table.setItem(i, 5, _cell(quad_ring, align=Qt.AlignmentFlag.AlignRight))

        if rows:
            self.table.selectRow(0)
        else:
            self._set_detail(None, None)

    def _selected(self) -> tuple[CelestialBody, StarSystem | None] | None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return None
        row = min(rows)
        cid_item = self.table.item(row, 2)  # cbody_id
        sys_item = self.table.item(row, 1)
        if cid_item is None:
            return None
        try:
            cbody_id = int(cid_item.text())
        except ValueError:
            return None
        sys_name = sys_item.text() if sys_item else ""
        for cb, ss in self._rows:
            if int(cb.cbody_id) == cbody_id and (ss is None or ss.name == sys_name):
                return (cb, ss)
        return None

    def _show_detail(self) -> None:
        sel = self._selected()
        if sel is None:
            self._set_detail(None, None)
            return
        cb, ss = sel
        self._set_detail(cb, ss)

    def _set_detail(self, cb: CelestialBody | None, ss: StarSystem | None) -> None:
        if cb is None:
            self.sys_name.setText("—")
            self.cbody_name.setText("—")
            self.cbody_type.setText("—")
            self.cbody_quad.setText("—")
            return
        self.sys_name.setText(ss.name if ss else "—")
        self.cbody_name.setText(cb.name or "—")
        self.cbody_type.setText(cb.cbody_type or "—")
        self.cbody_quad.setText(f"{cb.quad or '—'}/{cb.ring or '—'}")

    def _generate(self) -> None:
        sel = self._selected()
        if sel is None:
            QMessageBox.information(self, "No selection", "Select a celestial body first.")
            return
        cb, ss = sel
        if ss is None:
            QMessageBox.information(self, "Missing system", "This body has no linked system; re-run full refresh.")
            return

        ships = int(self.ships.value())
        row = int(self.row.value())
        start_x = int(self.start_x.value())
        end_x = int(self.end_x.value())
        ore_type = int(self.ore_type.value())

        orders = [PhoenixOrder.move_to_planet(int(ss.id), int(cb.cbody_id))]
        for _ in range(ships):
            orders.append(PhoenixOrder.gpi_row(row, start_x, end_x, ore_type=ore_type))
        text = "\n".join(str(o) for o in orders)
        self.orders.setPlainText(text)


def _load_rows(session: Session) -> list[tuple[CelestialBody, StarSystem | None]]:
    cbodies = session.exec(select(CelestialBody).order_by(CelestialBody.star_system_id, CelestialBody.cbody_id)).all()
    systems = {int(s.id): s for s in session.exec(select(StarSystem)).all()}
    return [(cb, systems.get(int(cb.star_system_id))) for cb in cbodies]


def _cell(text: str, *, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align is not None:
        item.setTextAlignment(int(align))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item

