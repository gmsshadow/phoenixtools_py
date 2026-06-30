from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QWidget,
)

from sqlmodel import select

from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.db.models import AppState, NexusConfig
from phoenixtools_app.services.import_setup import run_setup_import
from phoenixtools_app.services.import_items import run_items_fetch_missing
from phoenixtools_app.services.import_market import run_market_import
from phoenixtools_app.services.import_turn import list_nexus_turns, run_turn_import_for_my_bases
from phoenixtools_app.ui.nexus_turns_dialog import NexusTurnsDialog
from phoenixtools_app.services.full_refresh import run_full_refresh
from phoenixtools_app.ui.background import run_job
from phoenixtools_app.ui.trade_routes_page import TradeRoutesPage
from phoenixtools_app.ui.data_browser_page import DataBrowserPage
from phoenixtools_app.ui.bases_page import BasesPage
from phoenixtools_app.ui.items_page import ItemsPage
from phoenixtools_app.ui.star_systems_page import StarSystemsPage
from phoenixtools_app.ui.celestial_bodies_page import CelestialBodiesPage
from phoenixtools_app.ui.mining_jobs_page import MiningJobsPage
from phoenixtools_app.ui.path_to_base_page import PathToBasePage
from phoenixtools_app.ui.shipping_jobs_page import ShippingJobsPage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Phoenix Tools (Desktop)")
        self.setMinimumSize(1100, 700)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.nav = QListWidget()
        self.nav.setFixedWidth(260)
        self.nav.setSpacing(4)
        self.nav.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        items = [
            ("Home", "Home dashboard (status + refresh)."),
            ("Configuration", "Nexus credentials + import."),
            ("Trade routes", "Find routes + generate orders."),
            ("Bases", "Base reports, shipping jobs."),
            ("Mining jobs", "Depleting resources + rare ores."),
            ("Shipping jobs", "Item groups by travel time."),
            ("Path to base", "Shortest path + move/sell orders."),
            ("Star systems", "Systems + pathing."),
            ("Items", "Items + opportunities."),
            ("Celestial bodies", "Search + GPI planner."),
            ("Data browser", "Raw data inspection."),
        ]

        self._page_index: dict[str, int] = {}
        self.path_to_base_page = PathToBasePage()
        self.items_page = ItemsPage()

        for title, subtitle in items:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, subtitle)
            self.nav.addItem(item)
            self._page_index[title] = self.pages.count()
            if title == "Home":
                self.pages.addWidget(HomePage())
            elif title == "Configuration":
                self.pages.addWidget(ConfigurationPage())
            elif title == "Trade routes":
                self.pages.addWidget(TradeRoutesPage())
            elif title == "Bases":
                self.pages.addWidget(BasesPage())
            elif title == "Mining jobs":
                self.pages.addWidget(MiningJobsPage())
            elif title == "Shipping jobs":
                self.pages.addWidget(ShippingJobsPage())
            elif title == "Path to base":
                self.pages.addWidget(self.path_to_base_page)
            elif title == "Items":
                self.pages.addWidget(self.items_page)
            elif title == "Star systems":
                self.pages.addWidget(StarSystemsPage())
            elif title == "Celestial bodies":
                self.pages.addWidget(CelestialBodiesPage())
            elif title == "Data browser":
                self.pages.addWidget(DataBrowserPage())
            else:
                self.pages.addWidget(_PlaceholderPage(title, subtitle))

        self.items_page.request_path_to_base.connect(self._open_path_to_base)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

    def _open_path_to_base(self, destination_base_id: int, sell_item_id: int) -> None:
        self.path_to_base_page.prefill(
            destination_base_id=int(destination_base_id),
            sell_item_id=int(sell_item_id) if sell_item_id else None,
        )
        idx = self._page_index.get("Path to base")
        if idx is not None:
            self.nav.setCurrentRow(idx)


class _PlaceholderPage(QWidget):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        label = QLabel(f"<h2>{title}</h2><p>{subtitle}</p>")
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(label)


class HomePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()
        self._job = None
        self._turns_cache = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QFormLayout(left)
        left_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)

        self.daily_btn = QPushButton("Run daily refresh (market)")
        self.full_btn = QPushButton("Run full refresh")
        self.items_btn = QPushButton("Fetch item data (missing attributes)")
        self.turn_btn = QPushButton("Fetch turn data (my bases)")
        self.turns_dialog_btn = QPushButton("Import Nexus turns (incl. shared)…")

        left_layout.addRow("Status", self.status)
        left_layout.addRow("", self.daily_btn)
        left_layout.addRow("", self.full_btn)
        left_layout.addRow("", self.items_btn)
        left_layout.addRow("", self.turn_btn)
        left_layout.addRow("", self.turns_dialog_btn)

        self.log = QTextEdit()
        self.log.setReadOnly(True)

        root.addWidget(left, 1)
        root.addWidget(self.log, 2)

        self.daily_btn.clicked.connect(self._daily_refresh)
        self.full_btn.clicked.connect(self._full_refresh)
        self.items_btn.clicked.connect(self._fetch_items)
        self.turn_btn.clicked.connect(self._fetch_turns)
        self.turns_dialog_btn.clicked.connect(self._open_turns_dialog)

        self._refresh_status()

    def _append(self, msg: str) -> None:
        self.log.append(msg)

    def _refresh_status(self) -> None:
        with make_session(self._engine) as session:
            st = session.exec(select(AppState).where(AppState.id == 1)).first()
        if not st:
            self.status.setText("No state yet.")
            return
        self.status.setText(
            "<p>"
            f"<b>Last daily refresh:</b> {st.last_daily_refresh_at or 'never'}<br/>"
            f"<b>Last full refresh:</b> {st.last_full_refresh_at or 'never'}"
            "</p>"
        )

    def _set_busy(self, busy: bool) -> None:
        for btn in (self.daily_btn, self.full_btn, self.items_btn, self.turn_btn, self.turns_dialog_btn):
            btn.setEnabled(not busy)

    def _start_job(self, start_msg: str, fn) -> None:
        if self._job is not None:
            QMessageBox.information(self, "Busy", "Another refresh is still running.")
            return
        self._append(start_msg)
        self._set_busy(True)

        def on_done(summary: str) -> None:
            self._append(summary)
            self._job = None
            self._set_busy(False)
            self._refresh_status()

        def on_failed(err: str) -> None:
            self._append(f"ERROR: {err}")
            self._job = None
            self._set_busy(False)
            self._refresh_status()

        self._job = run_job(self, fn, on_progress=self._append, on_done=on_done, on_failed=on_failed)

    def _daily_refresh(self) -> None:
        def job(progress) -> str:
            engine = make_engine()
            with make_session(engine) as session:
                result = run_market_import(session, progress=progress)
            return (
                f"Imported market: {result.bases} bases, {result.items_touched} items touched, "
                f"{result.buys} buys, {result.sells} sells, {result.trade_routes} trade routes."
            )

        self._start_job("Starting daily refresh (market) …", job)

    def _fetch_items(self) -> None:
        def job(progress) -> str:
            engine = make_engine()
            with make_session(engine) as session:
                result = run_items_fetch_missing(session, progress=progress)
            return f"Item fetch: attempted {result.attempted}, fetched {result.fetched}, failed {result.failed}."

        self._start_job("Starting item attributes fetch (missing only) …", job)

    def _fetch_turns(self) -> None:
        def job(progress) -> str:
            engine = make_engine()
            with make_session(engine) as session:
                result = run_turn_import_for_my_bases(session, progress=progress)
            summary = (
                f"Turn data: {result.bases_ok}/{result.bases_total} bases imported, "
                f"{result.bases_failed} failed. "
                f"{result.item_group_rows} item-group rows, {result.inventory_items} inventory items."
            )
            if result.errors:
                summary += "\nErrors:\n" + "\n".join(f"  - {e}" for e in result.errors[:20])
                if len(result.errors) > 20:
                    summary += f"\n  … and {len(result.errors) - 20} more."
            return summary

        self._start_job("Starting turn data fetch for my bases …", job)

    def _open_turns_dialog(self) -> None:
        if self._job is not None:
            QMessageBox.information(self, "Busy", "Another refresh is still running.")
            return
        self._append("Fetching Nexus turns list (own + shared) …")
        self._set_busy(True)

        def job(progress) -> str:
            engine = make_engine()
            with make_session(engine) as session:
                entries = list_nexus_turns(session, progress=progress)
            self._turns_cache = entries
            shared = sum(1 for e in entries if not e.owned)
            return f"Found {len(entries)} turns ({shared} shared)."

        def on_done(summary: str) -> None:
            self._append(summary)
            self._job = None
            self._set_busy(False)
            if not self._turns_cache:
                QMessageBox.information(self, "No turns", "No turns were found in your Nexus turns list.")
                return
            NexusTurnsDialog(self._turns_cache, self).exec()
            self._refresh_status()

        def on_failed(err: str) -> None:
            self._append(f"ERROR: {err}")
            self._job = None
            self._set_busy(False)
            QMessageBox.critical(self, "Could not list turns", err)

        self._job = run_job(self, job, on_progress=self._append, on_done=on_done, on_failed=on_failed)

    def _full_refresh(self) -> None:
        def job(progress) -> str:
            engine = make_engine()
            with make_session(engine) as session:
                result = run_full_refresh(session, progress=progress)
            return (
                f"Setup: {result.setup.item_types} item types, {result.setup.items} items, "
                f"{result.setup.systems} systems, {result.setup.affiliations} affiliations, "
                f"{result.setup.positions} positions.\n"
                f"Jump map: {result.jump_map.systems_touched} systems touched, {result.jump_map.links} links.\n"
                f"Cbodies: {result.cbodies.systems_processed} systems processed, "
                f"{result.cbodies.cbodies_upserted} cbodies.\n"
                f"Market: {result.market.bases} bases, {result.market.items_touched} items touched, "
                f"{result.market.buys} buys, {result.market.sells} sells, "
                f"{result.market.trade_routes} trade routes."
            )

        self._start_job("Starting full refresh …", job)


class ConfigurationPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = make_engine()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QFormLayout(left)
        left_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.nexus_user = QLineEdit()
        self.nexus_password = QLineEdit()
        self.nexus_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.user_id = QLineEdit()
        self.xml_code = QLineEdit()
        self.affiliation_id = QLineEdit()
        self.affiliation_id.setPlaceholderText("Optional — your affiliation id (Rails Nexus.config.affiliation)")

        left_layout.addRow("Nexus user", self.nexus_user)
        left_layout.addRow("Nexus password", self.nexus_password)
        left_layout.addRow("User ID", self.user_id)
        left_layout.addRow("XML code", self.xml_code)
        left_layout.addRow("Affiliation ID", self.affiliation_id)

        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        self.save_btn = QPushButton("Save")
        self.setup_btn = QPushButton("Run setup import")
        buttons_layout.addWidget(self.save_btn)
        buttons_layout.addWidget(self.setup_btn)
        left_layout.addRow("", buttons)

        self.log = QTextEdit()
        self.log.setReadOnly(True)

        root.addWidget(left, 1)
        root.addWidget(self.log, 2)

        self.save_btn.clicked.connect(self._save)
        self.setup_btn.clicked.connect(self._run_setup)

        self._load()

    def _append(self, msg: str) -> None:
        self.log.append(msg)

    def _load(self) -> None:
        with make_session(self._engine) as session:
            cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
            if not cfg:
                return
            self.nexus_user.setText(cfg.nexus_user or "")
            self.nexus_password.setText(cfg.nexus_password or "")
            self.user_id.setText("" if cfg.user_id is None else str(cfg.user_id))
            self.xml_code.setText(cfg.xml_code or "")
            self.affiliation_id.setText("" if cfg.affiliation_id is None else str(cfg.affiliation_id))

    def _save(self) -> None:
        with make_session(self._engine) as session:
            cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
            cfg = cfg or NexusConfig(id=1)
            cfg.nexus_user = self.nexus_user.text().strip() or None
            cfg.nexus_password = self.nexus_password.text() or None
            cfg.user_id = int(self.user_id.text()) if self.user_id.text().strip() else None
            cfg.xml_code = self.xml_code.text().strip() or None
            cfg.affiliation_id = int(self.affiliation_id.text()) if self.affiliation_id.text().strip() else None
            session.add(cfg)
            session.commit()
        QMessageBox.information(self, "Saved", "Configuration saved.")

    def _run_setup(self) -> None:
        self._append("Starting setup import …")
        try:
            with make_session(self._engine) as session:
                result = run_setup_import(session, progress=self._append)
            self._append(
                f"Imported: {result.item_types} item types, {result.items} items, "
                f"{result.systems} systems, {result.affiliations} affiliations, {result.positions} positions."
            )
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))
            self._append(f"ERROR: {e}")

