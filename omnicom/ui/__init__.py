"""Native Qt user interface for OmniCOM."""

from __future__ import annotations

import argparse
import sys


def _console_is_ours(process_count: int, handle: int) -> bool:
    """Hide the console only when this process is its *only* client.

    A prompt that also hosts the shell (or a script runner) must keep its
    window, otherwise the terminal the user started us from would vanish.
    """
    return bool(handle) and process_count <= 1


def _hide_own_console() -> bool:
    """Hide the console window Windows gave us when the GUI was double-clicked.

    Running from source (or a console build) can attach a black ``cmd`` window to
    the process; the user asked for the window only (R-076).  A real terminal
    that hosts other processes is left untouched, so ``python run.py`` from an
    existing prompt and ``cli.cmd`` keep their output.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetConsoleWindow()
        if not handle:
            return False
        pids = (ctypes.c_ulong * 4)()
        count = int(kernel32.GetConsoleProcessList(pids, 4))
        if not _console_is_ours(count, handle):
            return False            # a real terminal hosts us: keep it
        ctypes.windll.user32.ShowWindow(handle, 0)
        return True
    except Exception:
        return False


def run(argv: list[str] | None = None, demo: bool = False) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .. import __version__, paths
    from .main_window import OmniWindow
    from .theme import app_stylesheet

    argv = list(sys.argv if argv is None else argv)

    # A release downloaded from the update dialog is unpacked but not applied
    # while the program runs (Windows locks the files).  It is copied over the
    # bundle *here*, before any module is imported by the window and before
    # anything can hold a handle - and it never touches omnicom-data, so the
    # command library, captures and settings survive the upgrade (R-108).
    try:
        from .. import update as update_module

        applied = update_module.pending()
        if applied is not None:
            upgraded = str(applied.get("version") or "")
            update_module.apply_pending()
            print(f"[OmniCOM] update {upgraded} applied")
    except Exception as error:            # a failed update must not block startup
        print(f"[OmniCOM] update skipped: {error}")

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    if "--keep-console" not in argv:
        _hide_own_console()
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
    # Lowest priority of anything the program does: the first release check
    # waits until the window is up and idle (R-109).
    window.schedule_update_check()
    window.raise_()
    window.activateWindow()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    from .. import __version__

    argv = list(sys.argv[1:] if argv is None else argv)
    # Agents get the JSON CLI through the very same entry point: the shipped
    # archive needs no second program to be explained to them (R-021/R-086).
    if argv and argv[0] == "cli":
        from ..cli import main as cli_main

        return cli_main(argv[1:])

    # ``shortcut`` is the other agent-friendly sub-command: the portable folder
    # ships a one-click .cmd that ends up calling this (R-097).
    if argv and argv[0] == "shortcut":
        from ..shortcut import main as shortcut_main

        return shortcut_main(argv[1:])

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
