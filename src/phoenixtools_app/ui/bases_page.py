from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
from phoenixtools_app.db.models import Base, CelestialBody, StarSystem


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
        right_layout = QFormLayout(right)
        right_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.base_id = QLabel("—")
        self.base_name = QLabel("—")
        self.location = QLabel("—")
        self.facilities = QLabel("—")

        self.copy_id_btn = QPushButton("Copy base ID")

        right_layout.addRow("ID", self.base_id)
        right_layout.addRow("Name", self.base_name)
        right_layout.addRow("Location", self.location)
        right_layout.addRow("Facilities", self.facilities)
        right_layout.addRow("", self.copy_id_btn)

        root.addWidget(left, 3)
        root.addWidget(right, 2)

        self.refresh_btn.clicked.connect(self._refresh)
        self.filter.textChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.copy_id_btn.clicked.connect(self._copy_id)

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

    def _copy_id(self) -> None:
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "No selection", "Select a base first.")
            return
        b, _, _ = row
        self.window().clipboard().setText(str(b.id))
        QMessageBox.information(self, "Copied", "Base ID copied to clipboard.")


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

