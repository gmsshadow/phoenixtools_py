from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.importer.parsers import TurnListEntry
from phoenixtools_app.services.import_turn import run_turn_import_selected
from phoenixtools_app.ui.background import run_job


class NexusTurnsDialog(QDialog):
    def __init__(self, entries: list[TurnListEntry], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Nexus turns")
        self.resize(900, 560)
        self._entries = entries
        self._job = None

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Tick turns to import. Includes your <b>personal turns list</b> and the full "
                "<b>Find → External</b> affiliation directory (no need to bookmark each turn first). "
                "Imported turns are tracked in the app."
            )
        )

        filter_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name or system …")
        filter_row.addWidget(self.filter_edit, 1)
        self.bases_only = QCheckBox("Bases only (Outpost / Starbase)")
        self.bases_only.setChecked(True)
        filter_row.addWidget(self.bases_only)
        layout.addLayout(filter_row)

        self.table = QTableWidget(len(entries), 7)
        self.table.setHorizontalHeaderLabels(
            ["Import", "Pos", "Name", "Type", "System", "Source", "Owner"]
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        for r, e in enumerate(entries):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, _ro(str(e.pos_id)))
            self.table.setItem(r, 2, _ro(e.name))
            self.table.setItem(r, 3, _ro(e.position_type or "—"))
            sys_text = e.system_name or "—"
            if e.system_id is not None and e.system_name:
                sys_text = f"{e.system_name} ({e.system_id})"
            self.table.setItem(r, 4, _ro(sys_text))
            self.table.setItem(r, 5, _ro(_source_label(e)))
            owner = e.owner_name or "—"
            if e.owned:
                owner = f"{owner} — Own"
            elif e.source == "external" and not e.on_personal_list:
                owner = "Affiliation external"
            elif not e.owned:
                owner = f"{owner} — Shared"
            self.table.setItem(r, 6, _ro(owner))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        self.filter_edit.textChanged.connect(self._apply_filters)
        self.bases_only.toggled.connect(self._apply_filters)
        self._apply_filters()

        sel_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select all visible")
        self.select_external_btn = QPushButton("Select external only")
        self.select_shared_btn = QPushButton("Select shared (personal list)")
        self.clear_btn = QPushButton("Clear")
        sel_row.addWidget(self.select_all_btn)
        sel_row.addWidget(self.select_external_btn)
        sel_row.addWidget(self.select_shared_btn)
        sel_row.addWidget(self.clear_btn)
        sel_row.addStretch(1)
        layout.addLayout(sel_row)

        self.status = QLabel("")
        layout.addWidget(self.status)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.import_btn = QPushButton("Import selected")
        self.close_btn = QPushButton("Close")
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        self.select_all_btn.clicked.connect(lambda: self._set_all(True))
        self.clear_btn.clicked.connect(lambda: self._set_all(False))
        self.select_external_btn.clicked.connect(self._select_external)
        self.select_shared_btn.clicked.connect(self._select_shared)
        self.import_btn.clicked.connect(self._import_selected)
        self.close_btn.clicked.connect(self.reject)

    def _apply_filters(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        bases_only = self.bases_only.isChecked()
        for r, e in enumerate(self._entries):
            hidden = False
            if bases_only and not e.is_base:
                hidden = True
            if needle:
                hay = " ".join(
                    x
                    for x in (
                        e.name,
                        e.system_name or "",
                        str(e.system_id or ""),
                        e.position_type or "",
                        _source_label(e),
                    )
                    if x
                ).lower()
                if needle not in hay:
                    hidden = True
            self.table.setRowHidden(r, hidden)
            if hidden:
                self.table.item(r, 0).setCheckState(Qt.CheckState.Unchecked)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            self.table.item(r, 0).setCheckState(state)

    def _select_external(self) -> None:
        for r, e in enumerate(self._entries):
            if self.table.isRowHidden(r):
                continue
            external = e.source == "external" and not e.on_personal_list
            self.table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if external else Qt.CheckState.Unchecked
            )

    def _select_shared(self) -> None:
        for r, e in enumerate(self._entries):
            if self.table.isRowHidden(r):
                continue
            shared = e.on_personal_list and not e.owned
            self.table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if shared else Qt.CheckState.Unchecked
            )

    def _checked_ids(self) -> list[int]:
        out: list[int] = []
        for r, e in enumerate(self._entries):
            if self.table.isRowHidden(r):
                continue
            if self.table.item(r, 0).checkState() == Qt.CheckState.Checked:
                out.append(int(e.pos_id))
        return out

    def _import_selected(self) -> None:
        if self._job is not None:
            return
        ids = self._checked_ids()
        if not ids:
            QMessageBox.information(self, "Nothing selected", "Tick at least one turn to import.")
            return
        self.import_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.status.setText("Importing …")

        def job(progress) -> str:
            engine = make_engine()
            with make_session(engine) as session:
                res = run_turn_import_selected(session, ids, progress=progress)
            summary = (
                f"Imported {res.imported}/{res.requested} turns "
                f"({res.failed} failed). {res.item_group_rows} item-group rows, "
                f"{res.inventory_items} inventory items."
            )
            if res.errors:
                summary += "\n" + "\n".join(f"• {e}" for e in res.errors[:15])
            return summary

        def on_progress(msg: str) -> None:
            self.status.setText(msg if len(msg) < 80 else msg[:77] + "…")

        def on_done(summary: str) -> None:
            self._job = None
            self.import_btn.setEnabled(True)
            self.close_btn.setEnabled(True)
            self.status.setText("Done.")
            QMessageBox.information(self, "Import complete", summary)

        def on_failed(err: str) -> None:
            self._job = None
            self.import_btn.setEnabled(True)
            self.close_btn.setEnabled(True)
            self.status.setText("Failed.")
            QMessageBox.critical(self, "Import failed", err)

        self._job = run_job(self, job, on_progress=on_progress, on_done=on_done, on_failed=on_failed)


def _source_label(e: TurnListEntry) -> str:
    if e.on_personal_list and e.source == "external":
        return "Personal + External"
    if e.on_personal_list:
        return "Personal list"
    return "External"


def _ro(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    return item
