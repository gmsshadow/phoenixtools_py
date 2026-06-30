from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sqlmodel import select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import Base, Item, StarSystem
from phoenixtools_app.services.path_to_base import (
    PathToBaseRequest,
    PathToBaseResult,
    compute_path_to_base,
    orders_text_for_path_to_base,
)

_KIND_LABEL = {
    "start": "",
    "jump": "(jump)",
    "gate": "(Stargate)",
    "wormhole": "(Wormhole)",
}


def _make_search_combo(placeholder: str) -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.lineEdit().setPlaceholderText(placeholder)
    combo.completer().setCompletionMode(combo.completer().CompletionMode.PopupCompletion)
    combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
    return combo


class PathToBasePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        form = QWidget()
        fl = QFormLayout(form)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.start_system = _make_search_combo("(Optional) start system")
        self.start_base = _make_search_combo("(Optional) start base — overrides system")
        self.destination = _make_search_combo("Destination base (required)")
        self.squadron = QCheckBox("Squadron move (jump fleet)")

        self.sell_item = _make_search_combo("(Optional) item to sell on arrival")
        self.sell_quantity = QSpinBox()
        self.sell_quantity.setRange(0, 100_000_000)
        self.sell_quantity.setValue(0)
        self.sell_quantity.setSpecialValueText("(default 100000)")

        fl.addRow("Start system", self.start_system)
        fl.addRow("Start base", self.start_base)
        fl.addRow("Destination base", self.destination)
        fl.addRow("", self.squadron)
        fl.addRow("Sell item", self.sell_item)
        fl.addRow("Sell quantity", self.sell_quantity)

        self.compute_btn = QPushButton("Find path + build orders")
        self.copy_btn = QPushButton("Copy orders to clipboard")

        self.summary = QLabel("Pick a destination base, then find the path.")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setWordWrap(True)

        self.stops = QListWidget()

        left_layout.addWidget(form)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.compute_btn)
        btn_row.addWidget(self.copy_btn)
        left_layout.addLayout(btn_row)
        left_layout.addWidget(self.summary)
        left_layout.addWidget(QLabel("Path stops:"))
        left_layout.addWidget(self.stops, 1)

        self.orders = QTextEdit()
        self.orders.setReadOnly(True)

        root.addWidget(left, 3)
        root.addWidget(self.orders, 2)

        self.compute_btn.clicked.connect(self._compute)
        self.copy_btn.clicked.connect(self._copy_orders)

        self._load_options()

    def _load_options(self) -> None:
        with make_session(self._engine) as session:
            systems = session.exec(select(StarSystem).order_by(StarSystem.name)).all()
            bases = session.exec(select(Base).order_by(Base.name)).all()
            items = session.exec(select(Item).order_by(Item.name)).all()

        self.start_system.clear()
        self.start_system.addItem("(None)", None)
        for ss in systems:
            self.start_system.addItem(f"{ss.name} ({ss.id})", int(ss.id))

        for combo in (self.start_base, self.destination):
            combo.clear()
        self.start_base.addItem("(None)", None)
        self.destination.addItem("(Select base)", None)
        for b in bases:
            label = f"{b.name or f'Base {b.id}'} ({b.id})"
            self.start_base.addItem(label, int(b.id))
            self.destination.addItem(label, int(b.id))

        self.sell_item.clear()
        self.sell_item.addItem("(None)", None)
        for it in items:
            self.sell_item.addItem(f"{it.name or f'Item {it.id}'} ({it.id})", int(it.id))

    def _build_request(self) -> PathToBaseRequest | None:
        dest = self.destination.currentData()
        if dest is None:
            return None
        start_system = self.start_system.currentData()
        start_base = self.start_base.currentData()
        sell_item = self.sell_item.currentData()
        sell_qty = int(self.sell_quantity.value()) or None
        return PathToBaseRequest(
            destination_base_id=int(dest),
            start_system_id=int(start_system) if start_system is not None else None,
            start_base_id=int(start_base) if start_base is not None else None,
            squadron=self.squadron.isChecked(),
            sell_item_id=int(sell_item) if sell_item is not None else None,
            sell_quantity=sell_qty,
        )

    def _compute(self) -> None:
        req = self._build_request()
        if req is None:
            QMessageBox.information(self, "No destination", "Select a destination base first.")
            return
        if req.start_system_id is None and req.start_base_id is None:
            QMessageBox.information(self, "No start", "Select a start system or a start base first.")
            return
        with make_session(self._engine) as session:
            res = compute_path_to_base(session, req)
            text = orders_text_for_path_to_base(session, req)
        self._render(res)
        self.orders.setPlainText(text)

    def _render(self, res: PathToBaseResult) -> None:
        self.stops.clear()
        if res.error:
            self.summary.setText(f"<span style='color:#c0392b'><b>Error:</b> {res.error}</span>")
            return
        if res.same_system:
            self.summary.setText(
                f"<b>{res.start_system_name}</b> &rarr; <b>{res.end_base_name}</b><br/>"
                "Destination is in the start system (no jumps required)."
            )
        elif not res.path_found:
            self.summary.setText(
                f"<span style='color:#c0392b'>No path found between "
                f"<b>{res.start_system_name}</b> and <b>{res.end_system_name}</b>.</span>"
            )
            return
        else:
            keys = " &mdash; <span style='color:#b9770e'>gate keys required</span>" if res.requires_gate_keys else ""
            self.summary.setText(
                f"<b>{res.start_system_name}</b> &rarr; <b>{res.end_base_name}</b> "
                f"({res.end_system_name})<br/><b>{res.tu_cost}</b> TUs{keys}"
            )
        for stop in res.stops:
            label = stop.system_name
            suffix = _KIND_LABEL.get(stop.kind, "")
            if suffix:
                label = f"{label}  {suffix}"
            self.stops.addItem(label)

    def _copy_orders(self) -> None:
        text = self.orders.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Nothing to copy", "Find a path first.")
            return
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copied", "Orders copied to clipboard.")
