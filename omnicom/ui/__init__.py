"""Native Qt user interface for OmniCOM."""

from __future__ import annotations

import argparse
import sys


def run(argv: list[str] | None = None, demo: bool = False) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .. import __version__, paths
    from .main_window import OmniWindow
    from .theme import app_stylesheet

    argv = list(sys.argv if argv is None else argv)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("OmniCOM")
    app.setOrganizationName("OmniCOM")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())

    icon_path = paths.resource_file("assets", "omnicom.ico")
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    window = OmniWindow(demo=demo)
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    from .. import __version__

    parser = argparse.ArgumentParser(prog="omnicom",
                                     description="OmniCOM — All-in-One Embedded Serial Debugger")
    parser.add_argument("--demo", action="store_true",
                        help="add a virtual DEMO port (AT loopback + telemetry)")
    parser.add_argument("--selftest", action="store_true",
                        help="run a headless smoke check and exit (used to verify builds)")
    parser.add_argument("--version", action="version", version=f"OmniCOM {__version__}")
    options = parser.parse_args(argv)

    if options.selftest:
        from .smoke import run as run_smoke
        return run_smoke()

    return run([sys.argv[0]] + (["--demo"] if options.demo else []), demo=options.demo)
