"""End-to-end GUI acceptance test for OmniCOM (runs offscreen, no hardware).

Every check maps to a requirement in ``project-context/REQUIREMENTS.md``:

    python tools/ui_selftest.py

Exit code 0 means all checks passed.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OMNICOM_SETTINGS_SUFFIX", "-test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QContextMenuEvent, QFontDatabase, QImage, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QWidget  # noqa: E402

from omnicom.ui import theme  # noqa: E402
from omnicom.ui.commands_panel import CommandRow, GroupDialog, GroupHeader  # noqa: E402
from omnicom.ui.main_window import OmniWindow  # noqa: E402
from omnicom.ui.theme import app_stylesheet  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []
STAMP_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\]")

def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


class Runner:
    def __init__(self) -> None:
        self.steps: list[tuple[str, callable, int]] = []

    def add(self, label: str, func, delay: int = 0) -> Runner:
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
            except Exception:
                check(label, False, "exception: " + traceback.format_exc().splitlines()[-1])
                self.advance()

        QTimer.singleShot(delay, go)


def wait(ms: int, then) -> None:
    QTimer.singleShot(ms, then)


def main() -> int:
    _utf8_console()
    app = QApplication(sys.argv)
    for filename in ("segoeui.ttf", "msyh.ttc", "consola.ttf"):
        font_path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", filename)
        if os.path.isfile(font_path):
            QFontDatabase.addApplicationFont(font_path)
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
        check("R-007 发送回显出现在日志", "发→" in window.log.plain_text())
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
        pattern = re.compile(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\]收←◇[0-9a-f]{8}  ")
        check("R-007 HEX 显示为 偏移 + 十六进制 + ASCII", any(pattern.match(line) for line in lines),
              lines[-1] if lines else "")
        window.row_hex.setChecked(False)
        wait(700, next_)

    # ------------------------------------------------- follow / scroll behaviour
    def step_follow(next_):
        # R-012 was redefined by the user: the pause button is gone and the
        # "自动滚动到最新" switch is the only print control.
        check("R-012 暂停打印已移除", not hasattr(window, "toggle_pause")
              and not hasattr(window, "btn_pause"))
        check("R-012 自动滚动开关存在", window.row_scroll.isChecked())
        window.row_scroll.setChecked(True)
        state["appended_follow"] = window._appended
        state["rx_follow"] = window.bridge.manager.rx_bytes
        wait(1500, next_)

    def step_follow_check(next_):
        added = window._appended - state["appended_follow"]
        rx_now = window.bridge.manager.rx_bytes
        check("R-012 自动滚动打开时始终跟随最新行",
              window.log._at_bottom() and added > 0,
              f"+{added} 行 bottom={window.log._at_bottom()}")
        check("R-017 接收端没有丢字节（无暂停缓冲）",
              rx_now >= state["rx_follow"] and not hasattr(window, "_lost_bytes"),
              f"+{rx_now - state['rx_follow']} B")
        # make sure the view can actually scroll, then scroll up while following
        for _ in range(200):
            window.log.append_line(0.0, "RX", "filler line " + "z" * 60, "rx")
        app.processEvents()
        bar = window.log.verticalScrollBar()
        bar.setValue(0)
        window.log.append_line(0.0, "RX", "follow probe", "rx")
        app.processEvents()
        check("R-012 手动上滑后新数据仍滚到底部", window.log._at_bottom(),
              f"max={bar.maximum()} value={bar.value()}")
        window.row_scroll.setChecked(False)
        app.processEvents()
        bar.setValue(0)
        app.processEvents()
        check("R-012 关闭后浮出「回到最新」按钮", window.scroll_pill.isVisible(),
              f"max={bar.maximum()} value={bar.value()}")
        window.jump_to_latest()
        check("R-012 「回到最新」可滚到底部", window.log._at_bottom())
        window.row_scroll.setChecked(True)
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
        # Notepad++-style "find all" over the *whole* log, including history.
        window.log.append_line(0.0, "RX", "AT+CSQ", "rx")
        window.log.append_line(0.0, "RX", "AT+QSSLCLOSE=0", "rx")
        window.log.append_line(0.0, "RX", "unrelated telemetry", "rx")
        window.search_panel.add_keyword("AT")
        wait(400, next_)

    def step_search_check(next_):
        panel = window.search_panel
        check("R-014 关键字以 chip 形式展示", panel.chip_layout.count() == 1,
              f"{panel.chip_layout.count()} chip")
        check("R-014 命中写入结果列表", panel.hits > 0, f"{panel.hits} 条")
        check("R-027 搜索结果覆盖历史行（不只是新数据）",
              panel.results.count() == panel.hits and panel.hits >= 3,
              f"rows={panel.results.count()} hits={panel.hits}")

        # every listed row must carry a jump target
        item = panel.results.item(panel.results.count() - 1)
        block = item.data(Qt.UserRole)
        panel._activate(item)
        check("R-027 双击结果可跳到日志原文",
              isinstance(block, int) and block >= 0
              and window.log.textCursor().blockNumber() == block,
              f"block={block} cursor={window.log.textCursor().blockNumber()}")
        state["search_hits"] = panel.hits

        panel.set_fullscreen(True)
        app.processEvents()
        check("R-027 搜索结果支持全屏显示",
              window._search_overlay is not None
              and panel.width() > window.sidebar.width(),
              f"panel={panel.width()}px sidebar={window.sidebar.width()}px")
        panel.set_fullscreen(False)
        app.processEvents()
        check("R-027 退出全屏后回到右侧栏",
              window._search_overlay is None
              and panel.parentWidget() is not None,
              type(panel.parentWidget()).__name__)
        next_()

    def step_search_all(next_):
        grown = window.search_panel.hits >= state["search_hits"]
        check("R-014 结果列表随新数据增长", grown, f"{state['search_hits']} -> {window.search_panel.hits}")
        window.search_panel.remove_keyword("AT")
        check("R-014 移除关键字后结果清空", window.search_panel.hits == 0)
        next_()

    # --------------------------------------------------------------- commands
    def step_import(next_):
        before = len(window.command_store.items)
        paste = "AT\r\nAT+CGMR\r\nAT+CSQ\r\nATI\r\nAT+CFUN?\r\n"
        window.commands_panel.import_text(paste)
        after = len(window.command_store.items)
        check("R-009 粘贴多行自动填入命令列表", after - before == 5, f"{before} -> {after}")
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
        # The file must outlive this call, so delete=False + explicit path.
        sample = os.path.join(tempfile.mkdtemp(prefix="omnicom-file-"), "payload.bin")
        with open(sample, "wb") as handle:
            handle.write(payload)
        state["file"] = sample
        state["file_size"] = len(payload)
        state["tx_file"] = window.bridge.manager.tx_bytes
        window.send_file(sample)
        wait(1800, next_)

    def step_send_file_check(next_):
        grew = window.bridge.manager.tx_bytes - state["tx_file"]
        check("R-006 文件按原始字节发送", grew == state["file_size"],
              f"+{grew} / {state['file_size']}")
        with contextlib.suppress(OSError):
            os.unlink(state["file"])
        next_()

    # ------------------------------------------------------------------- misc
    def step_sidebar(next_):
        window.commands_panel.toggle_collapsed()
        wait(300, next_)

    def step_sidebar_check(next_):
        check("R-008 侧栏：折叠扩展命令后搜索区变高",
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
        # -- 侧栏显隐必须是真实行为，不能只查有没有这个方法
        side = window._sidebar_widget
        window.toggle_sidebar()
        app.processEvents()
        hid = side.isHidden() and not side.isVisible()
        window.toggle_sidebar()
        app.processEvents()
        back = not side.isHidden() and side.isVisible()
        check("R-008 侧栏整体可隐藏并可恢复", hid and back, f"hide={hid} show={back}")

        # -- 测试隔离：UI 状态不得写进用户真实配置
        check("R-008 测试运行使用独立配置（不污染用户设置）",
              window.settings.applicationName() == "OmniCOM-test",
              window.settings.applicationName())

        # -- isHidden 语义：窗口自身隐藏不能被误判成"侧栏被关掉"
        window.hide()
        app.processEvents()
        semantics = not side.isVisible() and not side.isHidden()
        window.show()
        app.processEvents()
        check("R-008 侧栏显隐按 isHidden 判定（窗口隐藏不误判）", semantics,
              f"visible={side.isVisible()} hidden={side.isHidden()}")
        next_()

    def step_ui_polish(next_):
        # -- 窗口按钮必须是"画"出来的，不依赖字体里有没有这个符号
        missing = []
        for name in ("btn_min", "btn_max", "btn_close", "btn_sidebar"):
            button = getattr(window.titlebar, name)
            shot = button.grab().toImage()
            corner = shot.pixelColor(1, 1).name()
            ink = sum(
                1
                for y in range(shot.height())
                for x in range(shot.width())
                if shot.pixelColor(x, y).name() != corner
            )
            if ink < 12:
                missing.append(f"{name}={ink}px")
        check("R-001 右上角窗口按钮图标可见（自绘，不依赖字体）", not missing, str(missing))

        # -- 白色主题：背景色亮度必须很高
        bg = QColor(theme.BG)
        check("R-020 浅色（白色）主题生效", bg.lightness() > 220,
              f"{theme.BG} lightness={bg.lightness()}")

        # -- 日志格式 [hh:mm:ss.mmm]发→◇ / 收←◇
        window.log.clear_all()
        window.log.append_line(0.0, "TX", "AT+QSSLCLOSE=0", "tx")
        window.log.append_line(0.0, "RX", "OK", "rx")
        lines = window.log.plain_text().splitlines()
        sent = next((line for line in lines if "QSSLCLOSE" in line), "")
        check("R-024 日志行为「[时间]发→◇内容」",
              bool(re.match(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\]发→◇AT\+QSSLCLOSE=0$", sent)),
              sent)
        got = next((line for line in lines if line.endswith("OK")), "")
        check("R-024 日志行为「[时间]收←◇内容」",
              bool(re.match(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\]收←◇OK$", got)), got)

        # -- 开关本体可点击（曾经点开关无效，只有点文字才行）
        row = window.row_scroll
        before = row.isChecked()
        QTest.mouseClick(row.switch, Qt.LeftButton,
                         pos=QPoint(row.switch.width() // 2, row.switch.height() // 2))
        app.processEvents()
        flipped = row.isChecked() != before
        row.setChecked(before)
        check("R-002 点击开关本体即可切换（不限于点文字）", flipped,
              f"{before} -> {not before}")

        # -- 打开控制字符显示后，发送内容也要显示不可见字符
        was = window.row_ctrl.isChecked()
        window.row_ctrl.setChecked(True)
        window.log.clear_all()
        window.send_payload(b"AT+QSSLCLOSE=0\r\n", "AT+QSSLCLOSE=0")
        app.processEvents()
        text = window.log.plain_text()
        window.row_ctrl.setChecked(was)
        check("R-025 开启后发送内容也显示不可见字符",
              "发→" in text and "\\r\\n" in text, text.splitlines()[-1] if text else "")
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
    runner.add("follow", step_follow)
    runner.add("follow_check", step_follow_check)
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
    # -------------------------------------------------------------- combo boxes
    def step_combo(next_):
        box = window.baud
        shot = box.grab().toImage()
        corner = shot.pixelColor(1, 1).name()
        ink = sum(1 for y in range(shot.height()) for x in range(shot.width())
                  if shot.pixelColor(x, y).name() != corner)
        check("R-028 下拉框自绘箭头可见", ink > 20, f"ink={ink}px")
        check("R-028 波特率可手动输入", box.isEditable())
        box.showPopup()
        app.processEvents()
        opened = box.view().isVisible()
        box.hidePopup()
        app.processEvents()
        check("R-028 点击输入框即弹出候选列表", opened)
        # typing must take over: the open list closes so Enter keeps the text
        box.showPopup()
        box.lineEdit().selectAll()
        app.processEvents()
        QTest.keyClicks(box.lineEdit(), "768000")
        app.processEvents()
        check("R-028 手动输入时候选列表自动收起", not box.view().isVisible(),
              f"visible={box.view().isVisible()}")
        check("R-028 自定义输入不被列表覆盖",
              box.lineEdit().text() == "768000", box.lineEdit().text())
        box.setCurrentText("115200")
        next_()

    def step_font(next_):
        metrics = window.log.fontMetrics()
        check("R-029 日志使用等宽字体",
              metrics.horizontalAdvance("i") == metrics.horizontalAdvance("W"),
              f"{metrics.horizontalAdvance('i')} vs {metrics.horizontalAdvance('W')}")
        next_()

    def step_terminal(next_):
        window.switch_page("console")
        app.processEvents()
        check("R-026 存在独立的终端模式页",
              "console" in window._page_keys and window.console.isVisible())
        state["tx_term"] = window.bridge.manager.tx_bytes
        window.console.feed(b"login: ")
        window.console.feed(b"root\r\n")
        window.console.feed(b"\x1b[32mOK\x1b[0m\r\n")
        text = window.console.view.toPlainText()
        check("R-026 终端解释 CR 与 ANSI 颜色序列",
              "login: root" in text and "OK" in text and "\x1b" not in text,
              text.replace("\n", "|")[:60])
        window._on_terminal_keys(b"AT\r")
        grew = window.bridge.manager.tx_bytes - state["tx_term"]
        check("R-026 键盘输入直达串口", grew == 3, f"+{grew} B")
        window.console.view.reset()
        window.switch_page("terminal")
        app.processEvents()
        next_()

    def step_search_modes(next_):
        """R-030: plain / fuzzy / whole-word / regex, plus a match-case switch."""
        from omnicom.ui.search_panel import fuzzy_match

        panel = window.search_panel
        panel.attach_log(window.log)
        panel.clear_all()
        for line in ("AT+CGMR", "at+cgmr", "AT+VERSION", "hello world", "abcXYZ"):
            window.log.append_line(8 * 3600 * 1000.0, "RX", line, "rx")
        app.processEvents()

        def rows(mode: str, needle: str, case: bool) -> int:
            panel.match_box.setCurrentIndex(panel.match_box.findData(mode))
            panel.case_box.setChecked(case)
            panel.input.setText(needle)
            panel.run_search()
            app.processEvents()
            return panel.results.count()

        sensitive = rows("plain", "CGMR", False)
        exact = rows("plain", "CGMR", True)
        check("R-030 普通匹配默认忽略大小写", sensitive == 2, f"{sensitive} 行")
        check("R-030 区分大小写开关生效", exact == 1, f"{exact} 行")
        check("R-030 模糊匹配按顺序命中", fuzzy_match("cgr", "AT+CGMR", False)
              and rows("fuzzy", "cgr", False) == 2)
        check("R-030 全词匹配只命中整词", rows("word", "worl", False) == 0
              and rows("word", "world", False) == 1,
              f"{rows('word', 'worl', False)} / {rows('word', 'world', False)}")
        hits = rows("regex", r"AT\+CG\w+", False)
        check("R-030 正则表达式匹配", hits == 2, f"{hits} 行")
        rows("regex", r"*broken", False)
        check("R-030 非法正则给出提示而不是崩溃", bool(panel.regex_hint.text()))
        panel.match_box.setCurrentIndex(panel.match_box.findData("plain"))
        panel.case_box.setChecked(False)
        panel.input.setText("")
        panel.run_search()
        panel.clear_all()
        next_()

    def step_command_groups(next_):
        """R-031: groups with notes, collapse, and per-item / per-group export."""
        import json
        import tempfile

        from omnicom.commands import CommandStore

        panel = window.commands_panel
        store = window.command_store

        def row_count() -> int:
            """Visible command rows.

            Rows inside a group sit in an indenting holder, so they are found by
            walking the widget tree instead of the layout items.
            """
            return sum(1 for row in panel.list_host.findChildren(CommandRow)
                       if row.isVisible())

        group = store.add_group("回归测试组", "给这条分组写的备注")
        store.move_to_group(store.items[0]["id"], group["id"])
        panel.rebuild()
        app.processEvents()
        check("R-031 命令可以归入分组", group["id"] in
              [g["id"] for g, _items in store.groups_with_items() if g])
        check("R-031 分组支持备注", store.group_by_id(group["id"])["note"] != "")

        expanded = row_count()
        start = time.perf_counter()
        panel.toggle_group(group["id"])
        app.processEvents()
        elapsed = (time.perf_counter() - start) * 1000
        collapsed = row_count()
        group_rows = panel._group_rows.get(group["id"], [])
        hidden_ok = bool(group_rows) and not any(row.isVisible() for row in group_rows)
        panel.toggle_group(group["id"])
        app.processEvents()
        check("R-031 分组可折叠与展开", collapsed < expanded and row_count() == expanded,
              f"{expanded} -> {collapsed} -> {row_count()}")
        check("R-041 折叠不重建列表（不卡顿）", elapsed < 40, f"{elapsed:.1f} ms")
        check("R-041 折叠只隐藏该分组的行（其他分组不受影响）",
              hidden_ok, f"{len(group_rows)} 行在该分组内")

        expected_ids = {item["id"] for item in store.items if item.get("group") == group["id"]}
        panel.toggle_group(group["id"])
        added_group = store.add_group("新建分组可见性检查", "")
        check("R-053 新建分组保留旧组折叠状态", store.group_by_id(group["id"])["collapsed"])
        panel.toggle_group(group["id"])
        app.processEvents()
        restored = [row for row in panel.list_host.findChildren(CommandRow)
                  if row.item.get("group") == group["id"]]
        check("R-053 新建分组后旧组可展开且所有命令仍可见",
              {row.item["id"] for row in restored} == expected_ids
              and all(row.isVisible() for row in restored))
        store.remove_group(added_group["id"])

        # a command can be created straight inside a group
        before_add = len(store.items)
        page = panel.store.group_by_id(group["id"])
        check("R-031 分组标题带「新增命令」入口",
              hasattr(panel, "add_to_group") and page is not None)
        store.add(["AT+INGROUP"], {"group": group["id"]})
        panel.rebuild()
        app.processEvents()
        check("R-031 新建的命令直接落在分组里",
              len(store.items) == before_add + 1
              and store.items[-1]["group"] == group["id"])

        # and moved between groups
        other = store.add_group("第二组", "")
        panel.move_item(store.items[-1]["id"], other["id"])
        app.processEvents()
        check("R-031 命令可移动到别的分组",
              store.items[-1]["group"] == other["id"])
        store.remove_group(other["id"])

        single = store.export_payload([store.items[0]])
        check("R-031 支持按条导出", len(single["items"]) == 1 and
              single["format"] == "omnicom.commands")
        grouped = store.export_payload(
            [item for item in store.items if item.get("group") == group["id"]])
        check("R-031 支持按组导出并携带分组信息",
              len(grouped["groups"]) == 1 and grouped["items"][0]["group"] == group["id"])

        target = os.path.join(tempfile.mkdtemp(), "group-export.json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(grouped, handle, ensure_ascii=False)
        fresh = CommandStore(os.path.join(tempfile.mkdtemp(), "commands.json"))
        added = fresh.import_from(target)
        os.unlink(target)
        check("R-031 导出的文件可以导入回来",
              added == len(grouped["items"]) and
              "回归测试组" in [g["name"] for g in fresh.groups],
              f"{added} 条")
        store.remove_group(group["id"])
        panel.rebuild()
        app.processEvents()
        next_()

    def step_plugins(next_):
        """R-032: plugin folders on disk become top-bar tabs and can be reloaded."""
        loaded = window.plugins.loaded()
        check("R-032 插件从独立目录自动发现", bool(loaded),
              ",".join(info.id for info in loaded))
        if not loaded:
            next_()
            return
        info = loaded[0]
        key = f"plugin:{info.id}"
        check("R-032 插件在顶部栏拥有独立标签页", key in window.titlebar.tab_buttons)
        check("R-032 插件标签页是真实控件（不是占位）",
              info.widget is not None and window.stack.indexOf(info.widget) >= 0)
        window.switch_page(key)
        app.processEvents()
        check("R-032 点击插件标签可切换页面",
              window.stack.currentWidget() is info.widget)
        window.switch_page("terminal")
        window.reload_plugins()
        app.processEvents()
        check("R-032 刷新插件后仍可用", f"plugin:{info.id}" in window.titlebar.tab_buttons)
        check("R-032 插件的数据放在自己的目录",
              os.path.isdir(info.api.data_dir), info.api.data_dir)
        yes = window.plugins.search_paths()
        check("R-032 插件搜索路径包含可移植目录", any("plugin" in p for p in yes),
              str(len(yes)))
        next_()

    def step_language(next_):
        """R-033: zh-only in Chinese, en-only in English - menus included."""
        cjk = re.compile(r"[\u3000-\u9fff]")

        def user_data(widget) -> bool:
            """True for labels carrying user data rather than UI copy."""
            node = widget.parent()
            while node is not None:
                if isinstance(node, (CommandRow, GroupHeader)):
                    return True
                node = node.parent()
            return False

        def texts(root) -> list[str]:
            # Rebuilt widgets are only removed on the next event-loop pass, so
            # flush deferred deletions or the stale labels still match.
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            out = []
            for child in root.findChildren(QWidget):
                if not isinstance(child, (QLabel, QPushButton)) or not child.isVisible():
                    continue
                # Command names and group names are *user data*, not UI copy, so
                # they legitimately stay Chinese in the English interface.
                if user_data(child):
                    continue
                out.append(child.text() or "")
            return out

        areas = (window.titlebar, window.rail, window.send_panel, window.sidebar)
        window.change_language("zh")
        app.processEvents()
        chinese = [text for area in areas for text in texts(area)]
        check("R-033 中文界面显示中文",
              any(cjk.search(text) for text in chinese),
              f"{sum(1 for t in chinese if cjk.search(t))}/{len(chinese)} 条")
        check("R-033 设置与帮助菜单为中文入口",
              "首选项" in window.titlebar.btn_menu.toolTip() or
              "菜单" in window.titlebar.btn_menu.toolTip(),
              window.titlebar.btn_menu.toolTip())

        window.change_language("en")
        app.processEvents()
        english = [text for area in areas for text in texts(area)]
        leftovers = [text for text in english if cjk.search(text)]
        check("R-033 英文界面没有中文残留", not leftovers, leftovers[:3])
        from omnicom.i18n import tr

        check("R-033 日志右键菜单也跟随语言", tr("log.menu.copy") == "Copy"
              and tr("log.menu.find") == "Find…", tr("log.menu.copy"))
        check("R-033 日志方向标记随语言切换",
              window.log.direction_labels()["RX"] == "RX←",
              window.log.direction_labels()["RX"])

        window.change_language("zh")
        app.processEvents()
        check("R-033 切回中文后恢复中文",
              "收" in window.log.direction_labels()["RX"],
              window.log.direction_labels()["RX"])
        next_()

    def step_help(next_):
        """R-034: Help → documentation / about, both real dialogs."""
        from omnicom import __version__ as build_version
        from omnicom import docs as manual_module
        from omnicom.ui.dialogs import AboutDialog, DocsDialog

        docs = DocsDialog(window)
        docs.render()
        body = docs.view.toPlainText()
        check("R-034 文档弹窗有实际内容", len(body) > 500, f"{len(body)} 字符")
        docs.close()

        about = AboutDialog(window)
        about.render()
        check("R-034 关于弹窗显示版本号", build_version in about.app_title.text(),
              build_version)
        about.close()
        check("R-034 使用说明覆盖 10 个章节",
              len(manual_module.MANUAL["zh"]) >= 10,
              f"{len(manual_module.MANUAL['zh'])} 章")
        check("R-034 帮助入口挂在顶部菜单上",
              hasattr(window, "open_docs") and hasattr(window, "open_about"))
        for name in ("README.md", "README.en.md"):
            path = os.path.join(ROOT, name)
            check(f"R-034 仓库带 {name}", os.path.exists(path), path)
        next_()

    def step_responsive(next_):
        """R-035: no control is clipped at any supported window width."""
        problems: list[str] = []
        for width in (1000, 1120, 1280, 1440, 1680, 1920):
            window.resize(width, 880)
            app.processEvents()
            # the rail and the sidebar must both fit inside the window
            used = window.rail.width() + window.sidebar.width() + 40
            if used > width:
                problems.append(f"w={width} 用宽 {used}")
            for area in (window.titlebar, window.rail, window.sidebar):
                for child in area.findChildren(QPushButton):
                    if not child.isVisible():
                        continue
                    need = child.sizeHint().width()
                    if need > child.width() + 2:
                        problems.append(
                            f"w={width} 按钮「{child.text()[:8]}」要 {need} 实际 "
                            f"{child.width()}")
        check("R-035 窗口变窄时控件不被裁剪", not problems, problems[:3])
        narrow = window.titlebar.subtitle
        window.resize(1000, 880)
        app.processEvents()
        hidden = not narrow.isVisible()
        window.resize(1440, 880)
        app.processEvents()
        check("R-035 窄窗口自动收起副标题", hidden and narrow.isVisible())
        next_()

    def step_logo(next_):
        """R-036: the brand tile and the radius scale."""
        mark = window.titlebar.logo
        # widget.grab() bakes the parent's backdrop into the pixmap, so render
        # into a transparent image instead - only the tile's own paint shows.
        image = QImage(mark.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        mark.render(image)
        corner = image.pixelColor(0, 0)
        check("R-036 品牌标记四角透明（圆角而非方块）", corner.alpha() < 40,
              f"alpha={corner.alpha()}")
        check("R-036 品牌标记中间有内容",
              image.pixelColor(image.width() // 2, image.height() // 2).alpha() > 200)
        check("R-036 勾选框圆角足够大（半径 ≥ 尺寸 1/3）",
              theme.CHECK_RADIUS * 3 >= theme.CHECK_SIZE,
              f"r={theme.CHECK_RADIUS} s={theme.CHECK_SIZE}")
        check("R-045 勾选框尺寸与 13px 正文协调（≤ 16px）",
              theme.CHECK_SIZE <= 16, f"{theme.CHECK_SIZE}px")
        check("R-036 文本框圆角小于勾选框圆角",
              theme.RADIUS_CTRL < theme.CHECK_RADIUS,
              f"{theme.RADIUS_CTRL} < {theme.CHECK_RADIUS}")
        check("R-036 应用图标已设置",
              not window.windowIcon().isNull())
        check("R-036 标题栏使用自绘标记而不是图片",
              type(window.titlebar.logo).__name__ == "BrandMark")
        next_()

    def step_menus(next_):
        """R-043 / R-044 / R-046: context menus wired, composer resizable, no dupes."""
        log = window.log
        panel = window.search_panel

        # Every entry of the log menu must reach a slot.  Emitting proves
        # nothing (an unconnected signal is a no-op), so each pair is probed
        # with disconnect(), which Qt raises on when nothing is connected, and
        # the connection is then restored.
        wired = [
            ("save", log.saveRequested, window.save_view),
            ("clear", log.clearRequested, window.clear_log),
            ("find", log.findRequested, window.focus_search),
            ("latest", log.latestRequested, window.jump_to_latest),
        ]
        dead: list[str] = []
        for name, signal, slot in wired:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                dead.append(name)
            else:
                signal.connect(slot)
        check("R-046 日志右键每个菜单项都接了槽", not dead, str(dead))

        # behavioural proof for the one that is safe to trigger headless
        log.clear_all()
        log.append_line(0.0, "RX", "probe-line", "rx")
        app.processEvents()
        log.clearRequested.emit()
        check("R-046 右键「清空」真的清空日志",
              log.plain_text().strip() == "", log.plain_text()[:24])

        log.append_line(0.0, "RX", "probe-line", "rx")
        log.latestRequested.emit()
        app.processEvents()
        check("R-046 右键「跳到最新」把视图拉到底部", log._at_bottom())

        if panel._collapsed:
            panel.toggle_collapsed()
        log.findRequested.emit("")
        app.processEvents()
        check("R-046 右键「查找」展开搜索面板", not panel._collapsed)

        # composer resizable
        sizes = window.main_split.sizes()
        check("R-043 日志与发送框之间有可拖动的分隔条",
              len(sizes) == 2 and window.main_split.handleWidth() > 0, str(sizes))
        before = sizes[1]
        editor_before = window.send_panel.input.height()
        # Move the handle *up* (smaller composer).  Pushing it down can be
        # clamped by the splitter's own height, which made this a flaky check.
        window.main_split.moveSplitter(max(1, sizes[0] - 60), 1)
        app.processEvents()
        after = window.main_split.sizes()[1]
        # Either direction is a valid drag; the point is that the handle is
        # live.  (Moving it down gets clamped by the splitter height, so only
        # "did it change" is a stable assertion.)
        check("R-043 拖动后发送框高度真的变化", after != before,
              f"{before} -> {after}")

        editor_after = window.send_panel.input.height()
        check("R-043 增加的高度分配给实际输入框而不是空白",
              after != before and abs((editor_after - editor_before) - (after - before)) <= 2,
              f"panel {before}->{after}, editor {editor_before}->{editor_after}")

        # duplicated controls are gone
        from omnicom.ui.dialogs import AboutDialog, DocsDialog

        about = AboutDialog(window)
        docs = DocsDialog(window)
        check("R-044 关于/文档对话框不再重复「关闭」按钮",
              not hasattr(about, "close_btn") and not hasattr(docs, "close_btn"))
        about.deleteLater()
        docs.deleteLater()
        check("R-044 右栏不再重复「保存视图」按钮", not hasattr(window, "btn_save"))
        launcher = next((info.widget for info in window.plugins.loaded()
                         if info.widget is not None), None)
        if launcher is not None:
            check("R-044 快捷启动不再有手动的「刷新状态」按钮",
                  not hasattr(launcher, "btn_refresh"))

        # a group indents its rows
        store = window.command_store
        marker = store.add_group("缩进检查组", "")
        store.add(["AT+INDENT"], {"group": marker["id"]})
        window.commands_panel.rebuild()
        app.processEvents()
        holder = window.commands_panel._group_bodies.get(marker["id"])
        check("R-042 分组内的命令有缩进",
              holder is not None and holder.layout().contentsMargins().left() >= 12,
              str(holder.layout().contentsMargins().left()) if holder is not None else "-")
        store.remove_group(marker["id"])
        window.commands_panel.rebuild()
        app.processEvents()
        next_()

    def step_visual_interactions(next_):
        from PySide6.QtCore import QAbstractAnimation, QEvent, QPointF
        from PySide6.QtGui import QEnterEvent
        from PySide6.QtWidgets import QMenu

        from omnicom.i18n import tr
        from omnicom.ui.commands_panel import CommandDialog
        from omnicom.ui.widgets import GlyphButton

        panel = window.search_panel
        log = window.log
        log.setPlainText("AT+FIND\nAT+OTHER\nAT+FIND")
        panel.add_keyword("stale-filter")
        panel.match_box.setCurrentIndex(panel.match_box.findData("regex"))
        cursor = log.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(7, QTextCursor.KeepAnchor)
        log.setTextCursor(cursor)

        def pick_find():
            menu = QApplication.activePopupWidget()
            if not isinstance(menu, QMenu):
                return
            action = next(action for action in menu.actions()
                          if action.text() == tr("log.menu.find"))
            QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())

        QTimer.singleShot(0, pick_find)
        log.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(10, 10),
                                              log.mapToGlobal(QPoint(10, 10))))
        check("R-050 选中日志后右键查找使用选区并立即命中历史",
              panel.input.text() == "AT+FIND" and panel.hits == 2
              and not panel.keywords and panel.match_mode() == "plain",
              f"query={panel.input.text()} hits={panel.hits}")
        panel.search_selection("AT+FIND\nAT+OTHER")
        check("R-050 多行选区按行查找", panel.hits == 3, str(panel.hits))
        log.findRequested.emit("")
        check("R-050 无选区查找保留已有查询", panel.hits == 3)

        dialog = CommandDialog(window, groups=window.command_store.groups)
        check("R-047 命令编辑对话框正常构建", dialog.text is not None)
        dialog.deleteLater()

        problems = []
        for lang in ("zh", "en"):
            window.change_language(lang)
            for width in (1000, 1440, 1920):
                window.resize(width, 900)
                window.reset_layout()
                app.processEvents()
                for control in (window.btn_refresh, window.row_auto.switch,
                                window.row_ctrl.switch, window.row_scroll.switch):
                    edge = control.mapTo(window.rail, control.rect().topRight()).x()
                    if edge >= window.rail.width():
                        problems.append(f"{lang}/{width}: {edge}>={window.rail.width()}")
                title = window.commands_panel.title
                if title.width() < title.sizeHint().width():
                    problems.append(f"{lang}/{width}: command title clipped")
                editor = window.baud.lineEdit()
                if editor.width() < editor.fontMetrics().horizontalAdvance(editor.text()) + 4:
                    problems.append(f"{lang}/{width}: baud text clipped")
        check("R-052 中英文各宽度下刷新和显示开关没有横向溢出",
              not problems, str(problems))
        window.change_language("zh")
        window.resize(1440, 900)
        window.reset_layout()

        commands = window.commands_panel
        row = commands.list_host.findChildren(CommandRow)[0]
        original = row.check.isChecked()
        row.check.setChecked(not original)
        app.processEvents()
        centre = row.label.mapTo(row, row.label.rect().center()).y()
        check("R-049 命令和所有操作图标始终在同一行",
              row.height() == (60 if row.item.get("note", "").strip() else 40)
              and row.label.width() > 0
              and all(abs(button.mapTo(row, button.rect().center()).y() - centre) <= 1
                      for button in row.findChildren(GlyphButton)))
        if row.item.get("note", "").strip():
            check("R-049 备注在命令下方以较小文字可见",
                row.note_label.isVisible() and row.note_label.y() > row.label.y()
                and row.note_label.font().pixelSize() < row.label.font().pixelSize())
        check("R-051 勾选命令不重建列表，当前行仍存在",
              row in commands.list_host.findChildren(CommandRow))
        button = window.btn_refresh
        app.sendEvent(button, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))

        def settled():
            check("R-049 选择动画停在正确状态",
                  row.selection == float(not original)
                  and row._selection_anim.state() == QAbstractAnimation.Stopped)
            check("R-051 图标悬停动画结束后停止",
                  button.feedback == 1.0
                  and button._feedback_anim.state() == QAbstractAnimation.Stopped)
            app.sendEvent(button, QEvent(QEvent.Leave))
            row.check.setChecked(original)
            wait(320, restored)

        def restored():
            check("R-051 取消悬停及恢复勾选后没有常驻动画",
                  button.feedback == 0.0 and row.selection == float(original)
                  and button._feedback_anim.state() == QAbstractAnimation.Stopped
                  and row._selection_anim.state() == QAbstractAnimation.Stopped)
            import json

            with open(store_path, encoding="utf-8") as handle:
                saved = json.load(handle)
            saved_item = next(item for item in saved["items"] if item["id"] == row.item["id"])
            check("R-049 勾选状态延后保存且没有丢失", saved_item["enabled"] == original)
            next_()

        wait(180, settled)

    def step_performance(next_):
        import json
        import statistics

        panel = window.commands_panel
        store = window.command_store
        group = store.add_group("Performance", "")
        store.add([f"AT+PERF={index}" for index in range(200)], {"group": group["id"]})
        app.processEvents()
        timings = {name: [] for name in ("group_action", "group_frame", "log_scroll", "rx_batch")}
        for _index in range(12):
            start = time.perf_counter()
            panel.toggle_group(group["id"])
            timings["group_action"].append((time.perf_counter() - start) * 1000)
            app.processEvents()
            timings["group_frame"].append((time.perf_counter() - start) * 1000)
        rows = [row for row in panel.list_host.findChildren(CommandRow)
                if row.item.get("group") == group["id"]]
        check("R-054 压力折叠后200条命令仍完整可见",
              len(rows) == 200 and all(row.isVisible() for row in rows))

        log = window.log
        window.clear_log()
        log.autoscroll = False
        log.setPlainText("\n".join(f"PERF {index:05d} payload=" + "x" * 80
                                    for index in range(20000)))
        window._appended = 20000
        app.processEvents()
        bar = log.verticalScrollBar()
        for index in range(40):
            start = time.perf_counter()
            bar.setValue(max(0, bar.maximum() - (index + 1) * 3))
            app.processEvents()
            timings["log_scroll"].append((time.perf_counter() - start) * 1000)
        expected = window._appended
        for index in range(12):
            payload = (f"PERF_RX_{index}\r\n" * 100).encode("ascii")
            start = time.perf_counter()
            window._on_rx(time.time() * 1000, payload)
            window._flush_rx()
            app.processEvents()
            timings["rx_batch"].append((time.perf_counter() - start) * 1000)
            expected += 100
        check("R-054 压力接收逐行计数准确且日志上限损失提示可见",
              window._appended >= expected and log.blockCount() == 20000
              and window.trim_badge.isVisible())
        received = [line.rsplit("◇", 1)[-1] for line in log.line_texts()]
        check("R-054 每一批100条接收内容全部保留",
              all(received.count(f"PERF_RX_{index}") == 100 for index in range(12)))
        report = {}
        for name, samples in timings.items():
            ordered = sorted(samples)
            report[name] = {"median_ms": round(statistics.median(samples), 3),
                            "p95_ms": round(ordered[int((len(samples) - 1) * 0.95)], 3),
                            "max_ms": round(max(samples), 3)}
        destination = os.path.join(ROOT, ".artifacts", "ui-perf.json")
        if "--perf-out" in sys.argv:
            destination = sys.argv[sys.argv.index("--perf-out") + 1]
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print("UI_PERF " + json.dumps(report))
        check("R-054 滚动、200条命令折叠和100行接收批次的P95耗时低于40ms",
              all(report[name]["p95_ms"] < 40 for name in ("log_scroll", "group_frame", "rx_batch")),
              str(report))
        store.remove_group(group["id"], drop_commands=True)
        next_()

    def step_shell(next_):
        """R-037..R-040: square window, draggable columns, readable flow labels."""
        corner = window.centralWidget().grab().toImage()
        bottom = corner.pixelColor(2, corner.height() - 3)
        check("R-037 窗口外框四角都是直角", bottom.alpha() > 200,
              f"alpha={bottom.alpha()}")
        check("R-038 左右侧栏可通过分隔条拖动", window.body_split.count() == 3
              and window.body_split.handleWidth() > 0,
              f"count={window.body_split.count()}")
        before = window.rail.width()
        # Move the handle the way a real drag does (setSizes can be overruled by
        # the centre column's minimum width).
        window.body_split.moveSplitter(before + 90, 1)
        app.processEvents()
        check("R-038 拖动分隔条能改变左栏宽度",
              window.rail.width() > before, f"{before} -> {window.rail.width()}")
        window.reset_layout()
        app.processEvents()

        from omnicom.ui.widgets import GlyphButton as _Glyph

        commands = window.commands_panel
        row_glyphs: list[str] = []
        for row in commands.list_host.findChildren(CommandRow):
            row_glyphs.extend(button.glyph for button in row.findChildren(_Glyph))
        check("R-040 命令行里没有单独的导出按钮",
              _Glyph.SAVE not in row_glyphs, str(sorted(set(row_glyphs))))
        check("R-040 导出只有一个入口（顶部按钮打开选择框）",
              hasattr(commands, "export_dialog"), type(commands).__name__)

        labels = [window.flow.itemText(i) for i in range(window.flow.count())]
        check("R-039 流控显示可读文案而不是变量名",
              not any("flow." in text for text in labels), str(labels))
        check("R-039 DTR/RTS 与开关靠在一起",
              window.row_dtr.switch.x() - (window.row_dtr.label.x()
                                           + window.row_dtr.label.width()) < 40,
              str(window.row_dtr.switch.x()))
        window.baud.setCurrentText("768000")
        check("R-039 波特率可自定义", window._serial_opts()["baudrate"] == 768000)
        window.baud.setCurrentText("999")
        check("R-039 非法波特率被夹到合法范围",
              window._serial_opts()["baudrate"] >= 50,
              str(window._serial_opts()["baudrate"]))
        window.baud.setCurrentText("115200")

        # No processEvents(): the DEMO manager would immediately report its own
        # status again and overwrite the value under test.
        window._on_status({"connected": True, "port": "COM_TEST",
                           "reconnecting": False})
        check("R-048 串口框显示真正连上的端口",
              window.port_combo.currentData() == "COM_TEST",
              str(window.port_combo.currentData()))
        next_()

    def step_serial_lifecycle(next_):
        manager = window.bridge.manager
        manager.close()
        window._on_status(manager.status())
        window._sync_port_combo("DEMO")
        original_open = manager.open
        original_close = manager.close
        delay = threading.Event()
        timings = []
        gaps = []
        gap_cycles = []
        blocked_stacks = []
        watchdog_stop = threading.Event()
        previous = [time.perf_counter()]
        cycles = [0]
        deadline = [time.monotonic() + 4]

        def slow_open(*args, **kwargs):
            delay.wait(0.4)
            original_open(*args, **kwargs)

        def slow_close():
            delay.wait(0.4)
            original_close()

        open_patch = patch.object(manager, "open", slow_open)
        close_patch = patch.object(manager, "close", slow_close)
        open_patch.start()
        close_patch.start()
        heartbeat = QTimer(window)
        heartbeat.setInterval(10)

        def tick():
            now = time.perf_counter()
            gaps.append((now - previous[0]) * 1000)
            gap_cycles.append(cycles[0])
            previous[0] = now

        heartbeat.timeout.connect(tick)
        heartbeat.start()

        def watch_main_thread():
            while not watchdog_stop.wait(0.04):
                if time.perf_counter() - previous[0] > 0.08 and len(blocked_stacks) < 3:
                    frame = sys._current_frames().get(threading.main_thread().ident)
                    blocked_stacks.append(" > ".join(
                        f"{item.name}:{item.lineno}" for item in traceback.extract_stack(frame)[-6:]))

        watchdog = threading.Thread(target=watch_main_thread, daemon=True)
        watchdog.start()

        def finish():
            heartbeat.stop()
            watchdog_stop.set()
            heartbeat.deleteLater()
            open_patch.stop()
            close_patch.stop()
            check("R-055 连续12次慢驱动开关不阻塞按钮回调",
                  len(timings) == 12 and max(timings, default=999) < 40,
                  f"{len(timings)} calls, max {max(timings, default=999):.1f} ms")
            check("R-055 慢驱动开关期间界面心跳无超过80ms的停顿",
                  len(gaps) > 20 and max(gaps, default=999) < 80,
                f"{len(gaps)} ticks, max {max(gaps, default=999):.1f} ms; "
                  f"stalls {[(gap_cycles[index], round(gap, 1)) for index, gap in enumerate(gaps) if gap >= 80]}; "
                  f"stacks {blocked_stacks}")
            check("R-055 连续开关后串口确实关闭且未进入重连",
                  not manager.status()["connected"] and not manager.status()["want"]
                  and not manager.status()["reconnecting"])
            next_()

        def toggle():
            started = time.perf_counter()
            window.btn_connect.click()
            timings.append((time.perf_counter() - started) * 1000)
            cycles[0] += 1
            deadline[0] = time.monotonic() + 4
            QTimer.singleShot(10, poll)

        def poll():
            expected = bool(cycles[0] % 2)
            if (manager.status()["connected"] == expected
                    and not window._status.get("busy")
                    and window._status.get("connected") == expected):
                if cycles[0] == 12:
                    finish()
                else:
                    QTimer.singleShot(10, toggle)
                return
            if time.monotonic() > deadline[0]:
                check("R-055 串口开关在超时前完成", False, str(manager.status()))
                finish()
                return
            QTimer.singleShot(10, poll)

        QTimer.singleShot(20, toggle)

    def step_serial_races(next_):
        from omnicom import serial_port

        manager = serial_port.PortManager(lambda event: None)
        entered = threading.Event()
        release = threading.Event()

        class DelayedDevice:
            in_waiting = 0

            def reset_input_buffer(self):
                pass

            def read(self, size):
                entered.set()
                release.wait(2)
                raise OSError("retired reader")

            def close(self):
                pass

        try:
            with patch.object(serial_port.serial, "Serial", return_value=DelayedDevice()):
                manager.open("TEST_DELAYED", {"autoReconnect": False})
            entered.wait(1)
            old_reader = manager._reader
            manager.close()
            manager.open("DEMO", {"autoReconnect": False})
            release.set()
            old_reader.join(1)
            check("R-055 旧读线程的延迟错误不会关闭新连接",
                  manager.status()["connected"] and manager.status()["port"] == "DEMO")
            current_handle = manager._ser
            current_stop = manager._reader_stop
            with patch.object(serial_port, "DemoDevice", wraps=serial_port.DemoDevice) as factory:
                manager._open_now("DEMO", manager.status()["opts"], announce=False,
                                  generation=manager._generation)
                check("R-055 延迟到达的重连不会重复打开已有连接",
                      factory.call_count == 0 and manager._ser is current_handle)
            if manager._ser is not current_handle:
                current_stop.set()
                current_handle.close()
            manager._handle_loss(OSError("retired write"), object())
            check("R-055 旧写入错误不会关闭当前连接", manager.status()["connected"])
            manager.close()

            entered.clear()
            release.clear()
            discarded = []

            def delayed_open(**kwargs):
                entered.set()
                release.wait(2)
                handle = serial_port.DemoDevice()
                discarded.append(handle)
                return handle

            with patch.object(serial_port.serial, "Serial", side_effect=delayed_open):
                opener = threading.Thread(target=manager.open,
                                          args=("TEST_CANCELLED", {"autoReconnect": False}))
                opener.start()
                entered.wait(1)
                release_timer = threading.Timer(0.05, release.set)
                release_timer.start()
                manager.close()
                opener.join(2)
                release_timer.join()
            check("R-055 打开完成前关闭不会重新发布已取消连接",
                  not opener.is_alive() and not manager.status()["connected"]
                  and manager.status()["want"] is None)
            check("R-055 被取消的驱动句柄已释放",
                  len(discarded) == 1 and not discarded[0].is_open)
        finally:
            release.set()
            manager.shutdown()
        next_()

    def step_group_unique(next_):
        import json
        from pathlib import Path

        from omnicom.commands import CommandStore
        from omnicom.i18n import language, set_language, tr

        def rejected(operation):
            try:
                operation()
            except ValueError:
                return True
            return False

        with tempfile.TemporaryDirectory(prefix="omnicom-groups-") as directory:
            path = Path(directory) / "commands.json"
            store = CommandStore(str(path))
            first = store.add_group("Unique A", "first")
            other = store.add_group("Unique B", "second")
            snapshot = path.read_bytes()
            before = json.dumps(store.groups, sort_keys=True)
            with patch.object(store, "_commit", wraps=store._commit) as commit:
                add_rejected = rejected(lambda: store.add_group("  Unique A  ", "duplicate"))
                rename_rejected = rejected(lambda: store.update_group(
                    other["id"], {"name": " Unique A ", "note": "must not change"}))
                check("R-056 同名创建和改名都拒绝且不写盘、不发重建通知",
                      add_rejected and rename_rejected and commit.call_count == 0
                      and json.dumps(store.groups, sort_keys=True) == before
                      and path.read_bytes() == snapshot)
            store.update_group(first["id"], {"name": " Unique A ", "note": "updated"})
            check("R-056 保持自身名称可保存且自动去除首尾空格",
                  first["name"] == "Unique A" and first["note"] == "updated")
            command = store.add(["AT+UNIQUE"], {"group": first["id"]})[0]
            bundle = store.export_payload([command])
            bundle["groups"][0]["name"] = "  Unique A  "
            export_path = Path(directory) / "import.json"
            export_path.write_text(json.dumps(bundle), encoding="utf-8")
            count = len(store.groups)
            added = store.import_from(str(export_path))
            check("R-056 导入同名分组合并而不再创建",
                  added == 1 and len(store.groups) == count
                  and store.items[-1]["group"] == first["id"])

        panel = window.commands_panel
        store = panel.store
        original_ids = {group["id"] for group in store.groups}
        existing = store.groups[0]
        other = store.add_group("R056_OTHER", "")
        before = json.dumps(store.groups, sort_keys=True)
        with patch.object(GroupDialog, "exec", return_value=QDialog.Accepted), \
                patch.object(GroupDialog, "result_group", return_value={
                    "name": existing["name"], "note": "duplicate"}), \
                patch.object(panel, "notify") as notify:
            panel.new_group()
            panel.edit_group(other["id"])
            check("R-056 界面创建和改名时显示重名提示且不改变原数据",
                  notify.call_count == 2 and json.dumps(store.groups, sort_keys=True) == before)
        for group in list(store.groups):
            if group["id"] not in original_ids:
                store.remove_group(group["id"])
        previous_language = language()
        set_language("zh")
        chinese = tr("cmd.group.duplicate", name="Unique A")
        set_language("en")
        english = tr("cmd.group.duplicate", name="Unique A")
        set_language(previous_language)
        check("R-033 重名提示中英文隔离且保留用户分组名称",
              bool(re.search(r"[\u4e00-\u9fff]", chinese))
              and not re.search(r"[\u4e00-\u9fff]", english)
              and "Unique A" in chinese and "Unique A" in english)
        next_()

    def step_connection_colours(next_):
        states = [
            ("关闭", {}, "Ghost"),
            ("打开中", {"busy": True}, "Ghost"),
            ("已连接", {"connected": True, "port": "DEMO"}, "Primary"),
            ("重连中", {"reconnecting": True}, "Ghost"),
            ("打开失败", {"error": "test error"}, "Ghost"),
        ]
        for name, status, expected in states:
            window._on_status(status)
            check(f"R-057 {name}按钮颜色与实际连接状态一致",
                  window.btn_connect.objectName() == expected
                  and window.btn_connect.isEnabled() == (not status.get("busy", False)),
                  window.btn_connect.objectName())
        window._on_status(window.bridge.manager.status())
        next_()

    def step_motion_buttons(next_):
        from PySide6.QtCore import QAbstractAnimation, QPointF
        from PySide6.QtGui import QEnterEvent, QPainter
        from PySide6.QtWidgets import QHBoxLayout

        from omnicom.ui.widgets import Button, GlyphButton

        host = QWidget(window)
        host.setGeometry(400, 200, 320, 70)
        layout = QHBoxLayout(host)
        primary = Button("Send", host)
        primary.setObjectName("Primary")
        secondary = Button("Cancel", host)
        secondary.setObjectName("Ghost")
        icon = GlyphButton(GlyphButton.SEND, host)
        controls = [primary, secondary, icon]
        for control in controls:
            layout.addWidget(control)
        clicks = []
        primary.clicked.connect(lambda: clicks.append(time.perf_counter()))
        host.show()
        app.processEvents()
        geometry = [control.geometry() for control in controls]
        frames = [host.grab().toImage()]
        for control in controls:
            app.sendEvent(control, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))

        def hover_frame():
            frames.append(host.grab().toImage())
            check("R-058 悬停具有实际可见的中间帧而非瞬间切换",
                  all(0 < control.hover < 1 for control in controls)
                  and frames[-1] != frames[0])
            QTest.mousePress(primary, Qt.LeftButton)
            wait(40, press_frame)

        def press_frame():
            frames.append(host.grab().toImage())
            check("R-058 文字按钮按压有动画且布局尺寸不变",
                0 < primary.press <= 1
                  and [control.geometry() for control in controls] == geometry
                  and frames[-1] != frames[-2])
            QTest.mouseRelease(primary, Qt.LeftButton)
            check("R-058 松开立即执行一次动作并继续回弹",
                len(clicks) == 1 and primary.press > 0,
                f"clicks={len(clicks)}, press={primary.press:.3f}")
            wait(240, release_frame)

        def release_frame():
            frames.append(host.grab().toImage())
            check("R-058 松开后回到原尺寸且按压动画停止",
                  all(control.press == 0 and control._press_anim.state() == QAbstractAnimation.Stopped
                      for control in controls)
                  and [control.geometry() for control in controls] == geometry)
            primary.setEnabled(False)
            QTest.mouseClick(primary, Qt.LeftButton)
            check("R-058 禁用按钮不触发动作也不继续动画",
                  len(clicks) == 1 and primary.press == 0 and primary.hover == 0
                  and primary._hover_anim.state() == QAbstractAnimation.Stopped)
            primary.setEnabled(True)
            primary.setFocus()
            QTest.keyClick(primary, Qt.Key_Space)
            check("R-058 键盘空格也立即触发一次并有按压反馈",
                  len(clicks) == 2 and primary.press > 0)
            for control in controls:
                app.sendEvent(control, QEvent(QEvent.Leave))
            wait(260, finish)

        def finish():
            check("R-058 空闲时全部反馈动画停止且不会改变布局",
                  all(control.hover == 0 and control.press == 0
                      and control._hover_anim.state() == QAbstractAnimation.Stopped
                      and control._press_anim.state() == QAbstractAnimation.Stopped
                      for control in controls)
                  and [control.geometry() for control in controls] == geometry)
            QTest.mousePress(icon, Qt.LeftButton)
            wait(40, glyph_frame)

        def glyph_frame():
            frames.append(host.grab().toImage())
            check("R-058 图标按钮真实按压也有反馈且不移动控件",
                  0 < icon.press <= 1 and icon.geometry() == geometry[-1])
            QTest.mouseRelease(icon, Qt.LeftButton)
            wait(240, complete)

        def complete():
            dialog = GroupDialog(window)
            actual = [window.btn_connect, window.btn_record, window.send_panel.btn_send,
                      *window.titlebar.tab_buttons.values(), *dialog.findChildren(QPushButton)]
            check("R-058 主操作、标签页和对话框均使用共用动画按钮",
                  all(isinstance(control, Button) for control in actual))
            dialog.deleteLater()
            strip = QImage(host.width() * len(frames), host.height(), QImage.Format_ARGB32)
            strip.fill(QColor(theme.BG))
            painter = QPainter(strip)
            for index, frame in enumerate(frames):
                painter.drawImage(index * host.width(), 0, frame)
            painter.end()
            os.makedirs(os.path.join(ROOT, ".artifacts"), exist_ok=True)
            strip.save(os.path.join(ROOT, ".artifacts", "button-motion.png"))
            host.hide()
            host.deleteLater()
            next_()

        wait(45, hover_frame)

    def step_launcher_remove(next_):
        import json
        from pathlib import Path
        from types import SimpleNamespace

        module = next(info.module for info in window.plugins.plugins if info.id == "launcher")
        with tempfile.TemporaryDirectory(prefix="omnicom-launcher-") as directory:
            path = Path(directory) / "apps.json"
            entries = [{"id": "first", "name": "First", "path": sys.executable},
                       {"id": "second", "name": "Second", "path": sys.executable}]
            path.write_text(json.dumps(entries), encoding="utf-8")
            api = SimpleNamespace(data_dir=directory, t=lambda zh, en: zh,
                                  notify=lambda *args: None, on_language=lambda callback: None)
            page = module.LauncherPage(api)
            page._timer.stop()
            page.show()
            app.processEvents()
            tile = page.tiles[0]

            def choose_remove():
                menu = QApplication.activePopupWidget()
                QTest.mouseClick(menu, Qt.LeftButton,
                                 pos=menu.actionGeometry(menu.actions()[-1]).center())

            QTimer.singleShot(30, choose_remove)
            app.sendEvent(tile, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(2, 2),
                                                 tile.mapToGlobal(QPoint(2, 2))))
            check("R-059 真实图标右键删除只移除选中的快捷方式",
                  [item["id"] for item in page.apps] == ["second"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            check("R-059 删除结果写入配置且重新载入不会复活",
                  [item["id"] for item in saved] == ["second"]
                  and [item["id"] for item in page._load()] == ["second"])
            page.hide()
            page.deleteLater()
        next_()

    def step_launcher_instances(next_):
        import json
        from pathlib import Path
        from types import SimpleNamespace

        module = next(info.module for info in window.plugins.plugins if info.id == "launcher")
        if sys.platform.startswith("win") and app.platformName() == "windows":
            native = [QWidget(), QWidget()]
            for index, widget in enumerate(native):
                widget.setWindowTitle(f"OmniCOM instance test {index}")
                widget.resize(280, 100)
                widget.show()
            app.processEvents()
            actual = module._windows_windows({os.getpid(): os.path.basename(sys.executable)})
            actual = [item for item in actual if item["title"].startswith("OmniCOM instance test")]
            check("R-060 Windows原生枚举保留同进程两个真实窗口句柄",
                  {item["hwnd"] for item in actual} == {int(widget.winId()) for widget in native})
            activated = []
            for widget in native:
                activated.append(module._focus_window({"hwnd": int(widget.winId()), "pid": os.getpid()}))
                app.processEvents()
            check("R-060 Windows原生API分别激活两个真实窗口", all(activated))
            check("R-060 Windows原生API拒绝PID不符的过期绑定",
                  not module._focus_window({"hwnd": int(native[0].winId()), "pid": os.getpid() + 1}))
            for widget in native:
                widget.close()
                widget.deleteLater()
        with tempfile.TemporaryDirectory(prefix="omnicom-windows-") as directory:
            path = Path(directory) / "apps.json"
            path.write_text(json.dumps([{"id": "editor", "name": "Editor",
                                         "path": sys.executable}]), encoding="utf-8")
            api = SimpleNamespace(data_dir=directory, t=lambda zh, en: zh,
                                  notify=lambda *args: None, on_language=lambda callback: None)
            page = module.LauncherPage(api)
            page._timer.stop()
            handles = [
                {"hwnd": 101, "pid": 999, "path": sys.executable, "title": "Older document"},
                {"hwnd": 202, "pid": 999, "path": sys.executable, "title": "Newer document"},
            ]
            page._apply_processes({"processes": {999: os.path.basename(sys.executable)},
                                   "windows": handles})
            tiles = [tile for tile in page.tiles if isinstance(tile, module.AppTile)]
            check("R-060 同一进程的两个窗口显示两个独立图标",
                  len(tiles) == 2 and {tile.instance["hwnd"] for tile in tiles} == {101, 202})
            with patch.object(module, "_focus_window", return_value=True) as focus, \
                    patch.object(page, "_spawn") as spawn:
                for tile in tiles:
                    tile.click()
                check("R-060 新旧窗口图标分别切换到自己的句柄而不是首个窗口",
                      [call.args[0]["hwnd"] for call in focus.call_args_list] == [101, 202]
                      and spawn.call_count == 0)
            page._apply_processes({"processes": {999: os.path.basename(sys.executable)},
                                   "windows": handles[1:]})
            tiles = [tile for tile in page.tiles if isinstance(tile, module.AppTile)]
            check("R-060 已关闭窗口消失且仍存活的窗口保留绑定",
                  len(tiles) == 1 and tiles[0].instance["hwnd"] == 202)
            stale = {**page.apps[0], "_window": handles[0]}
            with patch.object(module, "_focus_window", return_value=False), \
                    patch.object(page, "_spawn") as spawn:
                page.launch(stale)
                check("R-060 点击过期窗口不会擅自创建新实例", spawn.call_count == 0)
            page._apply_processes({"processes": {}, "windows": []})
            check("R-060 所有窗口退出后保留一个可启动的快捷方式",
                  len(page.tiles) == 1 and page.tiles[0].instance is None
                  and len(page.apps) == 1)
            page.deleteLater()
        next_()

    def step_custom_baud(next_):
        from PySide6.QtCore import QSignalBlocker

        original = window.baud.currentText()
        window.baud.setCurrentText("115200")
        window._committed_baud = "115200"
        window.activateWindow()
        app.processEvents()
        with QSignalBlocker(window.bridge), patch.object(window.bridge, "update_opts") as update:
            window._on_status({"connected": True, "port": "DEMO"})
            editor = window.baud.lineEdit()
            QTest.mouseClick(editor, Qt.LeftButton)
            app.processEvents()
            for character in "123457":
                QTest.keyClick(QApplication.focusWidget(), Qt.Key(ord(character)))
            check("R-061 鼠标进入后真实键盘输入非预设波特率",
                  window.baud.currentText() == "123457", window.baud.currentText())
            check("R-061 输入期间不按每位数字重配串口", update.call_count == 0)
            QTest.keyClick(QApplication.focusWidget(), Qt.Key_Return)
            check("R-061 Enter保留自定义值并仅提交一次完整参数",
                  window.baud.currentText() == "123457" and update.call_count == 1
                  and update.call_args.args[0]["baudrate"] == 123457,
                  f"text={window.baud.currentText()}, calls={update.call_count}")
            window.baud.showPopup()
            QTest.keyClick(window.baud.view(), Qt.Key_Escape)
            window.send_panel.input.setFocus()
            check("R-061 候选列表退出和失焦不覆盖自定义值",
                  window.baud.currentText() == "123457", window.baud.currentText())
            window._save_settings()
            window.baud.setCurrentText("115200")
            window._restore_settings()
            check("R-061 设置重载仍保留已提交的自定义波特率",
                  window.baud.currentText() == "123457" and window._serial_opts()["baudrate"] == 123457)
        window.baud.setCurrentText(original)
        window._save_settings()
        window._on_status(window.bridge.manager.status())
        next_()

    runner.add("R-061 custom_baud", step_custom_baud)
    runner.add("R-059 launcher_remove", step_launcher_remove)
    runner.add("R-060 launcher_instances", step_launcher_instances)
    runner.add("persist", step_persist)
    runner.add("combo", step_combo)
    runner.add("font", step_font)
    runner.add("terminal", step_terminal)
    runner.add("ui_polish", step_ui_polish)
    runner.add("search_modes", step_search_modes)
    runner.add("command_groups", step_command_groups)
    runner.add("plugins", step_plugins)
    runner.add("language", step_language)
    runner.add("help", step_help)
    runner.add("responsive", step_responsive)
    runner.add("logo", step_logo)
    runner.add("shell", step_shell)
    runner.add("menus", step_menus)
    runner.add("R-049 视觉与选区交互", step_visual_interactions)
    runner.add("R-054 performance", step_performance)
    runner.add("R-055 serial_lifecycle", step_serial_lifecycle)
    runner.add("R-055 serial_races", step_serial_races)
    runner.add("R-056 group_unique", step_group_unique)
    runner.add("R-057 connection_colours", step_connection_colours)
    runner.add("R-058 motion_buttons", step_motion_buttons)

    if "--only" in sys.argv:
        target = sys.argv[sys.argv.index("--only") + 1]
        runner.steps = [step for step in runner.steps if target in step[0]]
        if not runner.steps:
            raise ValueError(f"Unknown test step: {target}")

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
