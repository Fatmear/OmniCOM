"""Offscreen UI smoke test: builds the window, drives the DEMO port, saves a PNG.

    python tools/ui_snapshot.py [--out .artifacts/ui.png]

Exit code is non-zero when the UI raises, which makes this usable in CI.
"""

from __future__ import annotations

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OMNICOM_SETTINGS_SUFFIX", "-snapshot")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicom.ui.main_window import OmniWindow  # noqa: E402
from omnicom.ui.theme import app_stylesheet  # noqa: E402

ERRORS: list[str] = []


def guard(label: str, func) -> None:
    try:
        func()
    except Exception:
        ERRORS.append(f"{label}: {traceback.format_exc()}")


def main() -> int:
    out = os.path.join(ROOT, ".artifacts", "ui.png")
    args = sys.argv[1:]
    if "--out" in args:
        out = args[args.index("--out") + 1]
    os.makedirs(os.path.dirname(out), exist_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())
    window = OmniWindow(demo=True)
    window.resize(1440, 900)
    window.show()

    def connect_demo() -> None:
        index = window.port_combo.findData("DEMO")
        if index < 0:
            ERRORS.append("DEMO port missing from the port list")
            return
        window.port_combo.setCurrentIndex(index)
        guard("toggle_connection", window.toggle_connection)

    def exercise() -> None:
        guard("search.add", lambda: window.search_panel.add_keyword("temp"))
        guard("search.add2", lambda: window.search_panel.add_keyword("vbat"))
        guard("command.send", lambda: window.commands_panel.send_item(
            window.command_store.items[0]))
        guard("console.tab", lambda: window.switch_page("console"))
        guard("console.back", lambda: window.switch_page("terminal"))
        guard("transfer.tab", lambda: window.stack.setCurrentIndex(2))
        guard("transfer.back", lambda: window.stack.setCurrentIndex(0))
        guard("autoscroll.off", lambda: window.row_scroll.setChecked(False))

    def snapshot() -> None:
        try:
            pixmap = window.grab()
            pixmap.save(out)
            print("saved", out, pixmap.width(), "x", pixmap.height())
            print("rx_bytes", window.bridge.manager.rx_bytes,
                  "tx_bytes", window.bridge.manager.tx_bytes)
            print("log blocks", window.log.blockCount(), "appended", window._appended)
            print("search hits", window.search_panel.hits)
            print("commands", len(window.command_store.items))
            print("status", window._status.get("connected"), window._status.get("port"))
            print("log lines sample:")
            for line in window.log.plain_text().splitlines()[:4]:
                print("   ", line)
            print("hits sample:")
            for row in range(min(3, window.search_panel.results.count())):
                print("   ", window.search_panel.results.item(row).text())
            print("geometry:")
            for name, widget in (("rail", window.rail),
                                 ("stack", window.stack),
                                 ("sidebar", window._sidebar_widget),
                                 ("cmd_block", window.cmd_block),
                                 ("search_block", window.search_block),
                                 ("cmd_list", window.commands_panel.scroll),
                                 ("hits", window.search_panel.results),
                                 ("send", window.send_panel),
                                 ("log", window.log)):
                rect = widget.geometry()
                print(f"    {name:12} {rect.width():4d}x{rect.height():3d} "
                      f"visible={widget.isVisible()}")
        except Exception:
            ERRORS.append("snapshot: " + traceback.format_exc())
        finally:
            window.close()
            app.quit()

    QTimer.singleShot(300, connect_demo)
    QTimer.singleShot(1400, lambda: guard("send", lambda: window.send_payload(b"AT\r\n", "AT")))
    QTimer.singleShot(2600, exercise)
    QTimer.singleShot(3600, lambda: guard("autoscroll.on", lambda: window.row_scroll.setChecked(True)))
    QTimer.singleShot(6200, snapshot)

    app.exec()

    if ERRORS:
        print("\n".join(ERRORS))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
