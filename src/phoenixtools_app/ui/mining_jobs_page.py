from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.services.mining_jobs import (
    MiningJobsReport,
    ResourceBalanceRow,
    ResourceCandidate,
    compute_mining_jobs,
    compute_resource_balance,
)


class MiningJobsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()

        root = QVBoxLayout(self)

        header = QHBoxLayout()
        self.title = QLabel()
        self.refresh_btn = QPushButton("Refresh")
        self.status = QLabel("")
        header.addWidget(self.title, 1)
        header.addWidget(self.status)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        controls = QHBoxLayout()
        self.show_all = QCheckBox("Show full resource balance (incl. healthy items)")
        self.threshold = QSpinBox()
        self.threshold.setRange(1, 9999)
        self.threshold.setValue(26)
        self.threshold.setSuffix(" weeks")
        controls.addWidget(self.show_all)
        controls.addStretch(1)
        controls.addWidget(QLabel("Warn under:"))
        controls.addWidget(self.threshold)
        root.addLayout(controls)

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
        self.show_all.toggled.connect(self.refresh)
        self.threshold.valueChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        threshold = int(self.threshold.value())
        with make_session(self._engine) as session:
            report = compute_mining_jobs(session, weeks_threshold=threshold)
            balance = compute_resource_balance(session) if self.show_all.isChecked() else None
        self._populate(report, balance)

    def _populate(self, report: MiningJobsReport, balance: list[ResourceBalanceRow] | None) -> None:
        threshold = int(self.threshold.value())
        if balance is not None:
            self.title.setText(
                "<b>Mining jobs</b> — full resource balance across your hub bases "
                f"(items running out under {threshold} weeks are highlighted)."
            )
            depleting = sum(1 for b in balance if b.weeks_remaining is not None)
            self.status.setText(
                f"{report.hub_count} hub(s) · {len(balance)} material(s) · {depleting} net-depleting"
            )
            self._fill_jobs_table_from_balance(balance, threshold)
        else:
            self.title.setText(
                f"<b>Mining jobs</b> — resources running out within {threshold} weeks across your hub bases."
            )
            self.status.setText(
                f"{report.hub_count} hub(s) · {len(report.jobs)} job(s) · {len(report.rare_ores)} rare ore(s)"
            )
            self._fill_jobs_table_from_jobs(report)

        self._fill_rare_table(report)

    def _fill_jobs_table_from_jobs(self, report: MiningJobsReport) -> None:
        self.jobs_table.setSortingEnabled(False)
        self.jobs_table.setRowCount(len(report.jobs))
        for r, j in enumerate(report.jobs):
            self.jobs_table.setItem(r, 0, _weeks_cell(j.weeks_remaining))
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

    def _fill_jobs_table_from_balance(self, balance: list[ResourceBalanceRow], threshold: int) -> None:
        self.jobs_table.setSortingEnabled(False)
        self.jobs_table.setRowCount(len(balance))
        for r, b in enumerate(balance):
            forever = b.weeks_remaining is None
            self.jobs_table.setItem(r, 0, _weeks_cell(b.weeks_remaining))
            self.jobs_table.setItem(r, 1, _cell(b.base_name))
            self.jobs_table.setItem(r, 2, _cell(b.item_name))
            self.jobs_table.setItem(r, 3, _num_cell(b.available))
            self.jobs_table.setItem(r, 4, _num_cell(b.production))
            self.jobs_table.setItem(r, 5, _num_cell(b.consumption))
            self.jobs_table.setItem(r, 6, _num_cell(b.weekly_burn))
            is_warning = not forever and b.weeks_remaining is not None and b.weeks_remaining < threshold
            self.jobs_table.setItem(r, 7, _cell(_balance_deposit_text(b.best_resource, is_warning)))
            self.jobs_table.setItem(
                r, 8, _num_cell(b.best_resource.next_complex_output if b.best_resource else None)
            )
            if is_warning:
                _highlight_row(self.jobs_table, r)
        self.jobs_table.setSortingEnabled(True)

    def _fill_rare_table(self, report: MiningJobsReport) -> None:
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


def _balance_deposit_text(c: ResourceCandidate | None, is_warning: bool) -> str:
    """Deposit label for the full balance view.

    Only items that are actually running low appear in the Rare ores table, so
    'see below' is reserved for those; healthy items just note there is no
    minable deposit in this hub's cluster.
    """
    if c is not None:
        return f"{c.base_name} · res#{c.resource_id} (yield {c.resource_yield:g}, drop {c.resource_drop})"
    return "— (rare ore, see below)" if is_warning else "— (no deposit in cluster)"


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


class _WeeksItem(QTableWidgetItem):
    """Weeks-remaining cell: shows '∞' for non-depleting items but sorts as +inf."""

    def __init__(self, weeks: int | None) -> None:
        super().__init__()
        self._key = float("inf") if weeks is None else float(weeks)
        self.setText("∞" if weeks is None else str(weeks))
        self.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
        self.setFlags(self.flags() ^ Qt.ItemFlag.ItemIsEditable)

    def __lt__(self, other: QTableWidgetItem) -> bool:  # type: ignore[override]
        if isinstance(other, _WeeksItem):
            return self._key < other._key
        return super().__lt__(other)


def _weeks_cell(weeks: int | None) -> _WeeksItem:
    return _WeeksItem(weeks)


_WARN_BG = QBrush(QColor(176, 96, 0, 90))  # translucent amber, readable on light + dark


def _highlight_row(table: QTableWidget, row: int) -> None:
    bold = QFont()
    bold.setBold(True)
    for col in range(table.columnCount()):
        cell = table.item(row, col)
        if cell is not None:
            cell.setBackground(_WARN_BG)
            cell.setFont(bold)
