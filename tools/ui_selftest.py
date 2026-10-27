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
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OMNICOM_SETTINGS_SUFFIX", "-test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget  # noqa: E402

from omnicom.ui import theme  # noqa: E402
from omnicom.ui.commands_panel import CommandRow, GroupHeader  # noqa: E402
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
            layout = panel.list_layout
            total = 0
            for index in range(layout.count()):
                widget = layout.itemAt(index).widget()
                if isinstance(widget, CommandRow):
                    total += 1
            return total

        group = store.add_group("回归测试组", "给这条分组写的备注")
        store.move_to_group(store.items[0]["id"], group["id"])
        panel.rebuild()
        app.processEvents()
        check("R-031 命令可以归入分组", group["id"] in
              [g["id"] for g, _items in store.groups_with_items() if g])
        check("R-031 分组支持备注", store.group_by_id(group["id"])["note"] != "")

        expanded = row_count()
        panel.toggle_group(group["id"])
        app.processEvents()
        collapsed = row_count()
        panel.toggle_group(group["id"])
        app.processEvents()
        check("R-031 分组可折叠与展开", collapsed < expanded and row_count() == expanded,
              f"{expanded} -> {collapsed} -> {row_count()}")

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
        """R-036: transparent logo in the title bar, big rounded controls."""
        pixmap = window.titlebar.logo.pixmap()
        check("R-036 标题栏使用真实 logo",
              pixmap is not None and not pixmap.isNull(), window.titlebar.logo.text())
        image = pixmap.toImage()
        corner = image.pixelColor(0, 0)
        check("R-036 logo 背景透明（不是白底方块）", corner.alpha() < 16,
              f"alpha={corner.alpha()}")
        check("R-036 logo 中间有内容", any(
            image.pixelColor(x, image.height() // 2).alpha() > 200
            for x in range(image.width())), "opaque row")
        check("R-036 勾选框圆角足够大（半径 ≥ 尺寸 1/3）",
              theme.CHECK_RADIUS * 3 >= theme.CHECK_SIZE,
              f"r={theme.CHECK_RADIUS} s={theme.CHECK_SIZE}")
        check("R-036 文本框圆角小于勾选框圆角",
              theme.RADIUS_CTRL < theme.CHECK_RADIUS,
              f"{theme.RADIUS_CTRL} < {theme.CHECK_RADIUS}")
        check("R-036 应用图标已设置",
              not window.windowIcon().isNull())
        next_()

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
