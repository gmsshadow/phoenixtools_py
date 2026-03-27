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
from phoenixtools_app.services.import_market import run_market_import
from phoenixtools_app.services.full_refresh import run_full_refresh
from phoenixtools_app.ui.trade_routes_page import TradeRoutesPage
from phoenixtools_app.ui.data_browser_page import DataBrowserPage
from phoenixtools_app.ui.bases_page import BasesPage
from phoenixtools_app.ui.items_page import ItemsPage
from phoenixtools_app.ui.star_systems_page import StarSystemsPage
from phoenixtools_app.ui.celestial_bodies_page import CelestialBodiesPage


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
            ("Bases", "Base reports, shipping/mining jobs."),
            ("Star systems", "Systems + pathing."),
            ("Items", "Items + opportunities."),
            ("Celestial bodies", "Search + GPI planner."),
            ("Data browser", "Raw data inspection."),
        ]

        for title, subtitle in items:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, subtitle)
            self.nav.addItem(item)
            if title == "Home":
                self.pages.addWidget(HomePage())
            elif title == "Configuration":
                self.pages.addWidget(ConfigurationPage())
            elif title == "Trade routes":
                self.pages.addWidget(TradeRoutesPage())
            elif title == "Bases":
                self.pages.addWidget(BasesPage())
            elif title == "Items":
                self.pages.addWidget(ItemsPage())
            elif title == "Star systems":
                self.pages.addWidget(StarSystemsPage())
            elif title == "Celestial bodies":
                self.pages.addWidget(CelestialBodiesPage())
            elif title == "Data browser":
                self.pages.addWidget(DataBrowserPage())
            else:
                self.pages.addWidget(_PlaceholderPage(title, subtitle))

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)


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

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QFormLayout(left)
        left_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)

        self.daily_btn = QPushButton("Run daily refresh (market)")
        self.full_btn = QPushButton("Run full refresh")

        left_layout.addRow("Status", self.status)
        left_layout.addRow("", self.daily_btn)
        left_layout.addRow("", self.full_btn)

        self.log = QTextEdit()
        self.log.setReadOnly(True)

        root.addWidget(left, 1)
        root.addWidget(self.log, 2)

        self.daily_btn.clicked.connect(self._daily_refresh)
        self.full_btn.clicked.connect(self._full_refresh)

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

    def _daily_refresh(self) -> None:
        self._append("Starting daily refresh (market) …")
        try:
            with make_session(self._engine) as session:
                result = run_market_import(session, progress=self._append)
            self._append(
                f"Imported market: {result.bases} bases, {result.items_touched} items touched, "
                f"{result.buys} buys, {result.sells} sells."
            )
        except Exception as e:
            QMessageBox.critical(self, "Daily refresh failed", str(e))
            self._append(f"ERROR: {e}")
        finally:
            self._refresh_status()

    def _full_refresh(self) -> None:
        self._append("Starting full refresh …")
        try:
            with make_session(self._engine) as session:
                result = run_full_refresh(session, progress=self._append)
            self._append(
                f"Setup: {result.setup.item_types} item types, {result.setup.items} items, "
                f"{result.setup.systems} systems, {result.setup.affiliations} affiliations, {result.setup.positions} positions."
            )
            self._append(f"Jump map: {result.jump_map.systems_touched} systems touched, {result.jump_map.links} links.")
            self._append(
                f"Cbodies: {result.cbodies.systems_processed} systems processed, {result.cbodies.cbodies_upserted} cbodies."
            )
            self._append(
                f"Market: {result.market.bases} bases, {result.market.items_touched} items touched, "
                f"{result.market.buys} buys, {result.market.sells} sells."
            )
        except Exception as e:
            QMessageBox.critical(self, "Full refresh failed", str(e))
            self._append(f"ERROR: {e}")
        finally:
            self._refresh_status()


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

        left_layout.addRow("Nexus user", self.nexus_user)
        left_layout.addRow("Nexus password", self.nexus_password)
        left_layout.addRow("User ID", self.user_id)
        left_layout.addRow("XML code", self.xml_code)

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

    def _save(self) -> None:
        with make_session(self._engine) as session:
            cfg = session.exec(select(NexusConfig).where(NexusConfig.id == 1)).first()
            cfg = cfg or NexusConfig(id=1)
            cfg.nexus_user = self.nexus_user.text().strip() or None
            cfg.nexus_password = self.nexus_password.text() or None
            cfg.user_id = int(self.user_id.text()) if self.user_id.text().strip() else None
            cfg.xml_code = self.xml_code.text().strip() or None
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

