"""Render the README screenshots from the real window (no hardware needed).

    python tools/make_screenshots.py            # writes docs/images/*.png

Every image is produced by the actual application (DEMO port, real fonts), so a
README screenshot can never show a mock-up that the program does not render.
Run `python tools/make_readme.py` afterwards to refresh the two READMEs.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OMNICOM_SETTINGS_SUFFIX", "-shots")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicom.ui.dialogs import DocsDialog, PreferencesDialog  # noqa: E402
from omnicom.ui.main_window import OmniWindow  # noqa: E402
from omnicom.ui.theme import app_stylesheet  # noqa: E402
from omnicom.ui.update_dialog import UpdateDialog  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "images")
ERRORS: list[str] = []

INFO = {
    "available": True,
    "version": "1.1.3",
    "current": "1.1.2",
    "url": "https://github.com/Fatmear/OmniCOM/releases/latest",
    "asset_url": "",
    "notes": (
        "### 本次更新\n"
        "- 多窗口多串口：CLI 按串口绑定窗口，窗口关了串口 CLI 立即解绑\n"
        "- DTR / RTS 默认关闭，打开串口不再复位板子\n"
        "- 支持用本地升级包离线更新\n"
    ),
}


def guard(label: str, func) -> None:
    try:
        func()
    except Exception:
        ERRORS.append(f"{label}: {traceback.format_exc()}")


def save(widget, name: str) -> None:
    path = os.path.join(OUT_DIR, name)
    pixmap = widget.grab()
    pixmap.save(path)
    print(f"  {name:22} {pixmap.width()}x{pixmap.height()}")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)

    app = QApplication(sys.argv)
    for filename in ("segoeui.ttf", "msyh.ttc", "consola.ttf"):
        font_path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts",
                                 filename)
        if os.path.isfile(font_path):
            QFontDatabase.addApplicationFont(font_path)
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())

    scratch = tempfile.TemporaryDirectory(prefix="omnicom-shots-")
    window = OmniWindow(demo=True,
                        command_path=os.path.join(scratch.name, "commands.json"))
    window.resize(1440, 900)
    window.show()
    window.reset_layout()
    window._saved_send_h = 0
    window._apply_main_split()

    def connect_demo() -> None:
        index = window.port_combo.findData("DEMO")
        if index < 0:
            ERRORS.append("DEMO port missing")
            return
        window.port_combo.setCurrentIndex(index)
        guard("toggle_connection", window.toggle_connection)

    def talk() -> None:
        for command in ("AT", "AT+CGMR", "AT+CSQ"):
            guard("send", lambda c=command: window.send_payload(
                (c + "\r\n").encode(), c))

    def collect() -> None:
        guard("search", lambda: window.search_panel.add_keyword("temp"))
        guard("search2", lambda: window.search_panel.add_keyword("AT"))

    def shots() -> None:
        try:
            window.resize(1440, 900)
            window.reset_layout()
            app.processEvents()
            print("saving:")
            save(window, "main.png")
            window.stack.setCurrentIndex(1)          # terminal page
            app.processEvents()
            save(window, "terminal.png")
            window.stack.setCurrentIndex(2)          # transfer page
            app.processEvents()
            save(window, "transfer.png")
            window.stack.setCurrentIndex(0)
            app.processEvents()

            dialog = PreferencesDialog(window, "zh")
            dialog.show()
            app.processEvents()
            dialog.adjustSize()
            app.processEvents()
            save(dialog, "preferences.png")
            dialog.close()

            updater = UpdateDialog(window, INFO)
            updater.show()
            app.processEvents()
            save(updater, "update.png")
            updater.close()

            help_dialog = DocsDialog(window)
            help_dialog.show()
            app.processEvents()
            save(help_dialog, "manual.png")
            help_dialog.close()
        except Exception:
            ERRORS.append("shots: " + traceback.format_exc())
        finally:
            window.close()
            app.quit()

    QTimer.singleShot(300, connect_demo)
    QTimer.singleShot(700, talk)
    QTimer.singleShot(1400, collect)
    QTimer.singleShot(2600, talk)
    QTimer.singleShot(4200, shots)

    app.exec()
    scratch.cleanup()

    if ERRORS:
        print("\n".join(ERRORS))
        return 1
    print("all screenshots written to docs/images/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
