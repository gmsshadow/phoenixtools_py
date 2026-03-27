import sys

from PySide6.QtWidgets import QApplication

from phoenixtools_app.services.app_bootstrap import bootstrap
from phoenixtools_app.ui.main_window import MainWindow


def main() -> int:
    bootstrap()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

