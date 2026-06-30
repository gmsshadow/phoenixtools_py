from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
        self.resize(640, 480)
        self._entries = entries
        self._job = None

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Tick the turns to import. <b>Shared</b> turns come from other players "
                "(read-only data). Imported turns are tracked and counted like your own bases."
            )
        )

        self.table = QTableWidget(len(entries), 4)
        self.table.setHorizontalHeaderLabels(["Import", "Pos", "Name", "Owner / type"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        for r, e in enumerate(entries):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, _ro(str(e.pos_id)))
            self.table.setItem(r, 2, _ro(e.name))
            kind = "Own" if e.owned else "Shared"
            self.table.setItem(r, 3, _ro(f"{e.owner_name} — {kind}"))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        sel_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select all")
        self.select_shared_btn = QPushButton("Select shared only")
        self.clear_btn = QPushButton("Clear")
        sel_row.addWidget(self.select_all_btn)
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
        self.select_shared_btn.clicked.connect(self._select_shared)
        self.import_btn.clicked.connect(self._import_selected)
        self.close_btn.clicked.connect(self.reject)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(state)

    def _select_shared(self) -> None:
        for r, e in enumerate(self._entries):
            self.table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if not e.owned else Qt.CheckState.Unchecked
            )

    def _checked_ids(self) -> list[int]:
        out: list[int] = []
        for r, e in enumerate(self._entries):
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


def _ro(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    return item
