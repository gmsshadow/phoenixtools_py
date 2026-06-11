from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.services.mining_jobs import (
    MiningJobsReport,
    ResourceCandidate,
    compute_mining_jobs,
)


class MiningJobsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()

        root = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("<b>Mining jobs</b> — resources running out within 26 weeks across your hub bases.")
        self.refresh_btn = QPushButton("Refresh")
        self.status = QLabel("")
        header.addWidget(title, 1)
        header.addWidget(self.status)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        self.jobs_table = QTableWidget(0, 9)
        self.jobs_table.setHorizontalHeaderLabels(
            [
                "Weeks left",
                "Base",
                "Item",
                "Available",
                "Production",
                "Consumption",
                "Weekly burn",
                "Best deposit @",
                "Next complex +/wk",
            ]
        )
        self.jobs_table.setAlternatingRowColors(True)
        self.jobs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.jobs_table.setSortingEnabled(True)
        self.jobs_table.horizontalHeader().setSortIndicatorShown(True)

        self.rare_table = QTableWidget(0, 7)
        self.rare_table.setHorizontalHeaderLabels(
            ["Ore", "Found at", "Res#", "Yield", "Drop", "Size", "Next complex +/wk"]
        )
        self.rare_table.setAlternatingRowColors(True)
        self.rare_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.rare_table.setSortingEnabled(True)
        self.rare_table.horizontalHeader().setSortIndicatorShown(True)

        root.addWidget(self.jobs_table, 3)
        root.addWidget(
            QLabel(
                "<b>Rare ores</b> — depleting ores with no usable deposit at the hub or its outposts; "
                "best known deposits anywhere are listed below."
            )
        )
        root.addWidget(self.rare_table, 2)

        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        with make_session(self._engine) as session:
            report = compute_mining_jobs(session)
        self._populate(report)

    def _populate(self, report: MiningJobsReport) -> None:
        self.status.setText(
            f"{report.hub_count} hub(s) · {len(report.jobs)} job(s) · {len(report.rare_ores)} rare ore(s)"
        )

        self.jobs_table.setSortingEnabled(False)
        self.jobs_table.setRowCount(len(report.jobs))
        for r, j in enumerate(report.jobs):
            self.jobs_table.setItem(r, 0, _num_cell(j.weeks_remaining))
            self.jobs_table.setItem(r, 1, _cell(j.base_name))
            self.jobs_table.setItem(r, 2, _cell(j.item_name))
            self.jobs_table.setItem(r, 3, _num_cell(j.available))
            self.jobs_table.setItem(r, 4, _num_cell(j.production))
            self.jobs_table.setItem(r, 5, _num_cell(j.consumption))
            self.jobs_table.setItem(r, 6, _num_cell(j.weekly_burn))
            self.jobs_table.setItem(r, 7, _cell(_deposit_text(j.best_resource)))
            self.jobs_table.setItem(
                r, 8, _num_cell(j.best_resource.next_complex_output if j.best_resource else None)
            )
        self.jobs_table.setSortingEnabled(True)

        rare_rows = [(ore, c) for ore in report.rare_ores for c in (ore.candidates or [None])]
        self.rare_table.setSortingEnabled(False)
        self.rare_table.setRowCount(len(rare_rows))
        for r, (ore, c) in enumerate(rare_rows):
            self.rare_table.setItem(r, 0, _cell(ore.item_name))
            if c is None:
                self.rare_table.setItem(r, 1, _cell("(no known deposits)"))
                for col in range(2, 7):
                    self.rare_table.setItem(r, col, _num_cell(None))
            else:
                self.rare_table.setItem(r, 1, _cell(c.base_name))
                self.rare_table.setItem(r, 2, _num_cell(c.resource_id))
                self.rare_table.setItem(r, 3, _num_cell(c.resource_yield))
                self.rare_table.setItem(r, 4, _num_cell(c.resource_drop))
                self.rare_table.setItem(r, 5, _num_cell(c.resource_size))
                self.rare_table.setItem(r, 6, _num_cell(c.next_complex_output))
        self.rare_table.setSortingEnabled(True)


def _deposit_text(c: ResourceCandidate | None) -> str:
    if c is None:
        return "— (rare ore, see below)"
    return f"{c.base_name} · res#{c.resource_id} (yield {c.resource_yield:g}, drop {c.resource_drop})"


def _cell(text: str, *, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align is not None:
        item.setTextAlignment(int(align))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item


def _num_cell(value: int | float | None) -> QTableWidgetItem:
    item = QTableWidgetItem()
    if value is not None:
        item.setData(Qt.ItemDataRole.EditRole, value)
    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    return item
