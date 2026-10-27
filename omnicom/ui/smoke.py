"""Headless smoke check used by ``--selftest`` (also inside the packaged exe).

Verifies the window builds, the serial layer opens, bytes flow, and the log is
populated — the minimum needed to prove a build is not broken.

A windowed exe has no stdout, so the verdict is also written to
``<data>/selftest.json`` and returned as the process exit code.
"""

from __future__ import annotations

import json
import os
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

RESULTS: list[dict] = []


def _emit(text: str) -> None:
    """Print when possible; a --windowed build has no stdout at all."""
    try:
        print(text)
    except Exception:
        pass


def _check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    _emit(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _write_result(ok: bool) -> None:
    from .. import paths
    payload = {
        "ok": ok,
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "frozen": bool(getattr(sys, "frozen", False)),
        "checks": RESULTS,
    }
    try:
        target = os.path.join(paths.data_dir(), "selftest.json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        _emit(f"result written to {target}")
    except Exception:
        pass


def run() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from .main_window import OmniWindow
    from .theme import app_stylesheet

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())
    window = OmniWindow(demo=True)
    window.resize(1280, 820)
    window.show()

    def open_port() -> None:
        index = window.port_combo.findData("DEMO")
        if index < 0:
            _check("DEMO 端口存在", False)
            app.quit()
            return
        window.port_combo.setCurrentIndex(index)
        window.toggle_connection()

    def verify() -> None:
        status = window._status
        _check("串口打开", bool(status.get("connected")), str(status.get("port")))
        _check("接收数据流入", window.bridge.manager.rx_bytes > 0,
               f"{window.bridge.manager.rx_bytes} B")
        _check("日志有内容", window._appended > 0, f"{window._appended} 行")
        window.send_payload(b"AT\r\n", "AT")
        QTimer.singleShot(400, verify_send)

    def verify_send() -> None:
        _check("发送计数", window.bridge.manager.tx_bytes >= 4,
               f"{window.bridge.manager.tx_bytes} B")
        _check("右侧栏命令库已加载", len(window.command_store.items) > 0)
        window.close()
        app.quit()

    QTimer.singleShot(300, open_port)
    QTimer.singleShot(1600, verify)
    app.exec()

    ok = bool(RESULTS) and all(item["ok"] for item in RESULTS)
    _write_result(ok)
    _emit("SELFTEST OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1
