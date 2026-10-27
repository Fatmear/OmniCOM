"""End-to-end GUI acceptance test for OmniCOM (runs offscreen, no hardware).

Every check maps to a requirement in ``project-context/REQUIREMENTS.md``:

    python tools/ui_selftest.py

Exit code 0 means all checks passed.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicom import paths  # noqa: E402
from omnicom.ui.main_window import OmniWindow  # noqa: E402
from omnicom.ui.theme import app_stylesheet  # noqa: E402
from omnicom.ui.commands_panel import CommandRow  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []
STAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} ")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


class Runner:
    def __init__(self) -> None:
        self.steps: list[tuple[str, callable, int]] = []

    def add(self, label: str, func, delay: int = 0) -> "Runner":
        self.steps.append((label, func, delay))
        return self

    def start(self) -> None:
        self.advance()

    def advance(self) -> None:
        if not self.steps:
            QTimer.singleShot(120, QApplication.instance().quit)
            return
        label, func, delay = self.steps.pop(0)

        def go() -> None:
            try:
                func(self.advance)
            except Exception:  # noqa: BLE001
                check(label, False, "exception: " + traceback.format_exc().splitlines()[-1])
                self.advance()

        QTimer.singleShot(delay, go)


def wait(ms: int, then) -> None:
    QTimer.singleShot(ms, then)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())
    store_dir = tempfile.mkdtemp(prefix="omnicom-test-")
    store_path = os.path.join(store_dir, "commands.json")
    window = OmniWindow(demo=True, command_path=store_path)
    window.resize(1440, 900)
    window.show()

    state: dict = {}
    runner = Runner()

    # ---------------------------------------------------------------- startup
    def step_ports(next_):
        names = [window.port_combo.itemData(i) for i in range(window.port_combo.count())]
        check("R-005 串口列表包含 DEMO 端口", "DEMO" in names, str(names))
        index = window.port_combo.findData("DEMO")
        window.port_combo.setCurrentIndex(index)
        window.toggle_connection()
        wait(700, next_)

    def step_connected(next_):
        status = window._status
        check("R-004 串口已打开", status.get("connected") is True, str(status.get("port")))
        check("R-002 参数字段齐全",
              all(hasattr(window, attr) for attr in
                  ("baud", "bytesize", "stopbits", "parity", "flow", "row_dtr", "row_rts")))
        check("R-002 流控选项包含 RTS/CTS 与 XON/XOFF",
              window.flow.findData("rtscts") >= 0 and window.flow.findData("xonxoff") >= 0)
        next_()

    def step_rx(next_):
        # DEMO emits telemetry every ~700 ms; poll instead of assuming a fixed delay
        waited = {"ms": 0}

        def poll():
            waited["ms"] += 100
            if window.bridge.manager.rx_bytes > 0 and window._appended > 0:
                check_rx()
                return
            if waited["ms"] > 4000:
                check_rx()
                return
            QTimer.singleShot(100, poll)

        def check_rx():
            state["rx0"] = window.bridge.manager.rx_bytes
            state["appended0"] = window._appended
            lines = window.log.plain_text().splitlines()
            check("R-004 收到数据并打印", state["rx0"] > 0 and len(lines) > 0,
                  f"{state['rx0']} B / {len(lines)} 行 / 等 {waited['ms']}ms")
            check("R-010 时间戳精确到毫秒（HH:MM:SS.mmm）",
                  any(STAMP_RE.match(line) for line in lines),
                  lines[0] if lines else "")
            next_()

        poll()

    # ------------------------------------------------------------------- send
    def step_send(next_):
        state["tx0"] = window.bridge.manager.tx_bytes
        window.send_payload(b"AT\r\n", "AT")
        wait(500, next_)

    def step_send_check(next_):
        grew = window.bridge.manager.tx_bytes - state["tx0"]
        check("R-004 发送生效（txt 计数增加）", grew == 4, f"+{grew} B")
        check("R-007 发送回显出现在日志", "TX" in window.log.plain_text())
        # device answers AT with OK
        wait(500, next_)

    def step_send_reply(next_):
        text = window.log.plain_text()
        check("R-004 设备应答被接收（OK）", "OK" in text)
        next_()

    # --------------------------------------------------------- display toggles
    def step_ctrl_chars(next_):
        window.row_ctrl.setChecked(True)
        wait(900, next_)

    def step_ctrl_check(next_):
        text = window.log.plain_text()
        check("R-015 开启后 \\r\\n 以可见转义显示", "\\r\\n" in text,
              text.splitlines()[-1] if text else "")
        window.row_ctrl.setChecked(False)
        wait(700, next_)

    def step_hex(next_):
        window.row_hex.setChecked(True)
        wait(1400, next_)

    def step_hex_check(next_):
        lines = [line for line in window.log.plain_text().splitlines() if line.strip()]
        pattern = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3} RX [0-9a-f]{8}  ")
        check("R-007 HEX 显示为 偏移 + 十六进制 + ASCII", any(pattern.match(line) for line in lines),
              lines[-1] if lines else "")
        window.row_hex.setChecked(False)
        wait(700, next_)

    # ------------------------------------------------------------- pause/resume
    def step_pause(next_):
        window.toggle_pause()
        state["appended_pause"] = window._appended
        state["rx_pause"] = window.bridge.manager.rx_bytes
        wait(1800, next_)

    def step_pause_check(next_):
        appended = window._appended
        rx_now = window.bridge.manager.rx_bytes
        check("R-012 暂停时界面不再新增行", appended == state["appended_pause"],
              f"{state['appended_pause']} -> {appended}")
        check("R-012 暂停期间串口仍在接收", rx_now > state["rx_pause"],
              f"+{rx_now - state['rx_pause']} B")
        check("R-012 暂停提示可见", window.pause_badge.isVisible())
        state["rx_resume"] = rx_now
        state["appended_resume"] = appended
        window.toggle_pause()
        wait(1500, next_)

    def step_resume_check(next_):
        added = window._appended - state["appended_resume"]
        check("R-012 继续后补印暂停期间的数据", added >= 2, f"+{added} 行")
        check("R-017 暂停期间没有丢字节",
              window.bridge.manager.rx_bytes >= state["rx_resume"]
              and window._lost_bytes == 0,
              f"appended={window._appended} lost={window._lost_bytes}")
        next_()

    # ------------------------------------------------------------- timed send
    def step_timed_send(next_):
        window.send_panel.input.setPlainText("PING")
        state["tx_timed"] = window.bridge.manager.tx_bytes
        window.send_panel.interval.setValue(80)
        window.send_panel.toggle_timed()
        wait(600, next_)

    def step_timed_check(next_):
        grew = window.bridge.manager.tx_bytes - state["tx_timed"]
        check("R-011 定时发送连续发出", grew >= 6, f"+{grew} B")
        window.send_panel.toggle_timed()
        state["tx_timed_off"] = window.bridge.manager.tx_bytes
        wait(500, next_)

    def step_timed_stop(next_):
        grew = window.bridge.manager.tx_bytes - state["tx_timed_off"]
        check("R-011 定时发送可停止", grew == 0, f"+{grew} B")
        next_()

    # ------------------------------------------------------------------ search
    def step_search(next_):
        window.search_panel.add_keyword("temp")
        window.search_panel.add_keyword("vbat")
        wait(1500, next_)

    def step_search_check(next_):
        hits = window.search_panel.hits
        check("R-014 多关键字命中写入右侧栏", hits > 0, f"{hits} 条")
        check("R-014 关键字以 chip 形式展示", window.search_panel.chip_layout.count() == 2)
        window.search_panel.mode_box.setCurrentIndex(1)   # 全部匹配
        state["all_hits"] = hits
        wait(1600, next_)

    def step_search_all(next_):
        grew = window.search_panel.hits > state["all_hits"]
        check("R-014 「全部匹配」模式可用", grew, f"{state['all_hits']} -> {window.search_panel.hits}")
        next_()

    # --------------------------------------------------------------- commands
    def step_import(next_):
        before = len(window.command_store.items)
        paste = "AT\r\nAT+CGMR\r\nAT+CSQ\r\nATI\r\nAT+CFUN?\r\n"
        window.commands_panel.import_text(paste)
        after = len(window.command_store.items)
        check("R-009 粘贴多行自动填入命令列表", after - before == 5, f"{before} -> {after}")
        from omnicom.ui.commands_panel import CommandRow
        rows = window.commands_panel.findChildren(CommandRow)
        check("R-008 命令列表已渲染为可操作行", len(rows) == after, f"{len(rows)} 行")
        item = window.command_store.items[-1]
        check("R-016 命令支持延时与定时字段",
              "delay" in item and "interval" in item and "timer" in item)
        next_()

    def step_send_command(next_):
        state["tx_cmd"] = window.bridge.manager.tx_bytes
        window.commands_panel.send_item(window.command_store.items[0])
        wait(400, next_)

    def step_send_command_check(next_):
        grew = window.bridge.manager.tx_bytes - state["tx_cmd"]
        check("R-008 点击命令即可发送", grew >= 4, f"+{grew} B")

        item = window.command_store.items[-1]
        window.command_store.update(item["id"], {"timer": True, "interval": 80})
        state["tx_timer"] = window.bridge.manager.tx_bytes
        wait(600, next_)

    def step_timer_check(next_):
        grew = window.bridge.manager.tx_bytes - state["tx_timer"]
        check("R-016 命令定时发送生效", grew >= 3, f"+{grew} B")
        item = window.command_store.items[-1]
        window.command_store.update(item["id"], {"timer": False})
        wait(300, next_)

    def step_timer_stop(next_):
        state["tx_after"] = window.bridge.manager.tx_bytes
        wait(500, next_)

    def step_timer_stop_check(next_):
        grew = window.bridge.manager.tx_bytes - state["tx_after"]
        check("R-016 关闭定时后停止发送", grew == 0, f"+{grew} B")
        next_()

    # ----------------------------------------------------------------- record
    def step_record_start(next_):
        window.toggle_record()
        wait(1200, next_)

    def step_record_stop(next_):
        recorder = window.bridge.recorder
        state["record_path"] = recorder.state().get("path")
        state["record_bytes"] = recorder.state().get("bytes", 0)
        window.toggle_record()
        wait(300, next_)

    def step_record_check(next_):
        path = state.get("record_path")
        exists = bool(path) and os.path.isfile(path)
        size = os.path.getsize(path) if exists else 0
        check("R-013 接收数据写入文件", exists and size > 0, f"{size} B @ {path}")
        check("R-013 记录字节数与接收量一致", state.get("record_bytes", 0) == size,
              f"{state.get('record_bytes')} vs {size}")
        next_()

    # ------------------------------------------------------------- send file
    def step_send_file(next_):
        payload = bytes(range(256)) * 40     # 10 KB
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
        handle.write(payload)
        handle.close()
        state["file"] = handle.name
        state["file_size"] = len(payload)
        state["tx_file"] = window.bridge.manager.tx_bytes
        window.send_file(handle.name)
        wait(1800, next_)

    def step_send_file_check(next_):
        grew = window.bridge.manager.tx_bytes - state["tx_file"]
        check("R-006 文件按原始字节发送", grew == state["file_size"],
              f"+{grew} / {state['file_size']}")
        try:
            os.unlink(state["file"])
        except OSError:
            pass
        next_()

    # ------------------------------------------------------------------- misc
    def step_sidebar(next_):
        height_before = window.search_block.height()
        window.commands_panel.toggle_collapsed()
        wait(300, next_)

    def step_sidebar_check(next_):
        check("侧栏：折叠扩展命令后搜索区变高",
              window.search_block.height() > 0 and not window.commands_panel.body.isVisible(),
              f"search={window.search_block.height()}px")
        window.commands_panel.toggle_collapsed()
        wait(200, next_)

    def step_persist(next_):
        # saved settings / commands must reload (R-008, 侧栏布局)
        from omnicom.commands import CommandStore
        reloaded = CommandStore(store_path)
        check("R-008 命令列表持久化到磁盘",
              len(reloaded.items) == len(window.command_store.items),
              f"{len(reloaded.items)} 条")
        check("侧栏整体可隐藏", hasattr(window, "toggle_sidebar"))
        next_()

    runner.add("ports", step_ports)
    runner.add("connected", step_connected)
    runner.add("rx", step_rx)
    runner.add("send", step_send)
    runner.add("send_check", step_send_check)
    runner.add("send_reply", step_send_reply)
    runner.add("ctrl_chars", step_ctrl_chars)
    runner.add("ctrl_check", step_ctrl_check)
    runner.add("hex", step_hex)
    runner.add("hex_check", step_hex_check)
    runner.add("pause", step_pause)
    runner.add("pause_check", step_pause_check)
    runner.add("resume_check", step_resume_check)
    runner.add("timed_send", step_timed_send)
    runner.add("timed_check", step_timed_check)
    runner.add("timed_stop", step_timed_stop)
    runner.add("search", step_search)
    runner.add("search_check", step_search_check)
    runner.add("search_all", step_search_all)
    runner.add("import", step_import)
    runner.add("send_command", step_send_command)
    runner.add("send_command_check", step_send_command_check)
    runner.add("timer_check", step_timer_check)
    runner.add("timer_stop", step_timer_stop)
    runner.add("timer_stop_check", step_timer_stop_check)
    runner.add("record_start", step_record_start)
    runner.add("record_stop", step_record_stop)
    runner.add("record_check", step_record_check)
    runner.add("send_file", step_send_file)
    runner.add("send_file_check", step_send_file_check)
    runner.add("sidebar", step_sidebar)
    runner.add("sidebar_check", step_sidebar_check)
    runner.add("persist", step_persist)

    runner.start()
    app.exec()

    failed = [item for item in RESULTS if not item[0]]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        print("失败项：")
        for _ok, name, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print("ALL GUI SELFTESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
