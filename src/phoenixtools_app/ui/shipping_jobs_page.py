from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sqlmodel import select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import StarSystem
from phoenixtools_app.services.shipping_jobs import ShippingJobsReport, compute_shipping_jobs


class ShippingJobsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Nearest to:"))
        self.nearest = QComboBox()
        self.nearest.setEditable(True)
        self.nearest.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.nearest.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        controls.addWidget(self.nearest, 1)

        self.cargo = QCheckBox("Cargo")
        self.cargo.setChecked(True)
        self.life = QCheckBox("Life")
        self.life.setChecked(True)
        self.ores = QCheckBox("Ores")
        self.ores.setChecked(True)
        controls.addWidget(self.cargo)
        controls.addWidget(self.life)
        controls.addWidget(self.ores)

        self.refresh_btn = QPushButton("Sort / refresh")
        controls.addWidget(self.refresh_btn)
        root.addLayout(controls)

        self.summary = QLabel("")
        root.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["Base / group", "Travel", "Total mass", "Cargo", "Life", "Ores"])
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 320)
        root.addWidget(self.tree, 1)

        self.refresh_btn.clicked.connect(self._refresh)
        self.cargo.toggled.connect(self._refresh)
        self.life.toggled.connect(self._refresh)
        self.ores.toggled.connect(self._refresh)

        self._load_systems()
        self._refresh()

    def _load_systems(self) -> None:
        self.nearest.clear()
        self.nearest.addItem("(Any — sort by name)", None)
        with make_session(self._engine) as session:
            for ss in session.exec(select(StarSystem).order_by(StarSystem.name)).all():
                self.nearest.addItem(f"{ss.name} ({ss.id})", int(ss.id))

    def _refresh(self) -> None:
        nearest_id = self.nearest.currentData()
        with make_session(self._engine) as session:
            report = compute_shipping_jobs(
                session,
                nearest_system_id=int(nearest_id) if nearest_id is not None else None,
                show_cargo=self.cargo.isChecked(),
                show_life=self.life.isChecked(),
                show_ores=self.ores.isChecked(),
            )
        self._populate(report)

    def _populate(self, report: ShippingJobsReport) -> None:
        self.tree.clear()
        total_groups = sum(len(r.groups) for r in report.rows)
        if report.nearest_system_id is not None:
            self.summary.setText(
                f"<b>{len(report.rows)}</b> base(s) with shippable groups, sorted by travel time "
                f"&mdash; <b>{total_groups}</b> group(s)."
            )
        else:
            self.summary.setText(
                f"<b>{len(report.rows)}</b> base(s) with shippable groups (alphabetical) "
                f"&mdash; <b>{total_groups}</b> group(s). Pick a 'Nearest to' system to sort by travel time."
            )

        for r in report.rows:
            travel = "—" if r.travel_time is None else f"{r.travel_time} TU"
            base_item = QTreeWidgetItem([f"{r.base_name}  ({r.system_name})", travel, "", "", "", ""])
            base_item.setData(0, Qt.ItemDataRole.UserRole, r.base_id)
            font = base_item.font(0)
            font.setBold(True)
            base_item.setFont(0, font)
            self.tree.addTopLevelItem(base_item)
            for g in r.groups:
                child = QTreeWidgetItem(
                    [
                        f"{g.group_name} (id {g.group_id}, {g.lines} item(s))",
                        "",
                        _mass(g.total_mass),
                        _mass(g.total_cargo),
                        _mass(g.total_life),
                        _mass(g.total_ores),
                    ]
                )
                for col in range(2, 6):
                    child.setTextAlignment(col, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                base_item.addChild(child)
            base_item.setExpanded(True)


def _mass(value: int) -> str:
    return f"{value:,}" if value else "—"
