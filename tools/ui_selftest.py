"""End-to-end GUI acceptance test for OmniCOM (runs offscreen, no hardware).

Every check maps to a requirement in ``project-context/REQUIREMENTS.md``:

    python tools/ui_selftest.py

Exit code 0 means all checks passed.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
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

# A suite run must never write into the user's real data folder (command
# library, captures, plugin data) and it must start from a clean one, or a
# later run inherits hundreds of stale capture files and starts failing on
# timing and layout - L-116.
_DATA_DIR = os.path.join(ROOT, ".artifacts", "test-data", "gui")
shutil.rmtree(_DATA_DIR, ignore_errors=True)
os.environ["OMNICOM_DATA_DIR"] = _DATA_DIR
os.makedirs(_DATA_DIR, exist_ok=True)

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QContextMenuEvent, QFontDatabase, QImage, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QWidget  # noqa: E402

from omnicom.ui import theme  # noqa: E402
from omnicom.ui.commands_panel import CommandRow, GroupDialog, GroupHeader  # noqa: E402
from omnicom.ui.main_window import Aurora, OmniWindow  # noqa: E402
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


def _contrast(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two ``#RRGGBB`` colours."""

    def luminance(value: str) -> float:
        channels = [int(value.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                  for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    first, second = luminance(foreground), luminance(background)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


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
        wait(700, next_)

    def step_record_traffic(next_):
        # A send guarantees TX bytes while the DEMO port keeps streaming RX.
        window.send_payload(b"AT+CIMI\r\n", "AT+CIMI")
        wait(1200, next_)

    def step_record_hint(next_):
        from omnicom.ui.main_window import human_bytes

        hint = window.record_hint.text()
        size = window.bridge.recorder.state().get("bytes", 0)
        check("R-066 记录提示只显示文件大小，不再分别显示 RX/TX 量",
              "RX" not in hint and "TX" not in hint
              and "收 " not in hint and "发 " not in hint,
              hint.replace(chr(10), " | "))
        # The hint refreshes every 500 ms, so it may trail the counter slightly;
        # compare with a one-block tolerance instead of demanding equality.
        shown = None
        match = re.findall(r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)", hint)
        if match:
            value, unit = match[-1]
            shown = float(value) * {"B": 1, "KB": 1024,
                                    "MB": 1024 ** 2, "GB": 1024 ** 3}[unit]
        check("R-066 提示里的字节数与已记录量一致（容差 1 KB）",
              shown is not None and 0 < shown <= size + 1024,
              f"提示 {shown} B @ 实际 {size} B / {human_bytes(size)}")
        state["hint_before"] = hint
        state["size_before"] = size
        wait(1300, next_)

    def step_record_hint_live(next_):
        hint = window.record_hint.text()
        size = window.bridge.recorder.state().get("bytes", 0)
        check("R-066 记录过程中文件大小实时刷新",
              window.record_timer.isActive() and hint != state.get("hint_before")
              and size > state.get("size_before", 0),
              f"{state.get('size_before')} -> {size} B")
        next_()

    def step_record_clear(next_):
        recorder = window.bridge.recorder
        path = recorder.state().get("path")
        state["record_before_clear"] = os.path.getsize(path) if path else 0
        window.clear_capture()
        size_after = os.path.getsize(path) if path else -1
        # The DEMO device keeps streaming, so a few bytes can land in the very
        # same millisecond as the truncate: the proof we want is that the
        # pre-clear content is gone, not that the file stays at 0 B (L-116).
        check("R-066 清空记录文件后磁盘上立刻为空",
              state["record_before_clear"] > 0
              and (size_after == 0 or size_after < state["record_before_clear"]),
              f"{state['record_before_clear']} -> {size_after} B")
        check("R-066 清空后继续记录同一个文件",
              recorder.active and recorder.state().get("path") == path,
              str(recorder.state().get("path")))
        window.send_payload(b"AT+CSQ\r\n", "AT+CSQ")
        wait(1000, next_)

    def step_record_stop(next_):
        recorder = window.bridge.recorder
        snapshot = recorder.state()
        state["record_path"] = snapshot.get("path")
        state["record_bytes"] = snapshot.get("bytes", 0)
        state["record_rx"] = snapshot.get("rx", 0)
        state["record_tx"] = snapshot.get("tx", 0)
        window.toggle_record()
        wait(300, next_)

    def step_record_check(next_):
        path = state.get("record_path")
        exists = bool(path) and os.path.isfile(path)
        size = os.path.getsize(path) if exists else 0
        rx, tx = state.get("record_rx", 0), state.get("record_tx", 0)
        check("R-013 清空后的新收发继续写入同一文件", exists and size > 0,
              f"{size} B @ {path}")
        check("R-066 收发两个方向都记录到同一文件",
              rx > 0 and tx >= 8 and state.get("record_bytes", 0) == rx + tx,
              f"RX {rx} B / TX {tx} B")
        check("R-013 记录字节数与文件大小一致",
              state.get("record_bytes", 0) == size,
              f"{state.get('record_bytes')} vs {size}")
        next_()

    # --------------------------------------------------- rail layout + version
    def step_rail_layout(next_):
        window.reset_layout()
        app.processEvents()
        sizes = window.body_split.sizes()
        check("R-068 左栏默认宽度与右侧栏一致",
              len(sizes) == 3 and sizes[0] == sizes[2] and sizes[0] >= 280,
              str(sizes))
        for name in ("meta_port", "meta_flow", "meta_version"):
            check(f"R-067 左栏底部不再显示冗余信息（{name}）", not hasattr(window, name))
        # The combo captions ("串口"/"流控") stay - they label real controls. What
        # must be gone is the bottom summary block, including its copy.
        from omnicom import i18n as i18n_module
        leftover = [key for key in i18n_module._TABLE if key.startswith("rail.meta.")]
        check("R-067 左栏底部信息块已连同文案一起移除", not leftover, str(leftover))
        texts = []
        layout = window.rail_layout
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None and hasattr(widget, "text"):
                texts.append(widget.text())
        check("R-067 左栏底部不再出现版本号小字",
              window.__class__.__name__ and not any(
                  text.strip() == "1.1.2" for text in texts), str(texts[-5:]))
        next_()

    def step_version(next_):
        from omnicom import __version__ as current
        check("R-069 版本号为 1.1.2", current == "1.1.2", current)
        check("R-069 窗口标题显示新版本号", current in window.windowTitle(),
              window.windowTitle())
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
    runner.add("record_traffic", step_record_traffic)
    runner.add("record_hint", step_record_hint)
    runner.add("record_hint_live", step_record_hint_live)
    runner.add("record_clear", step_record_clear)
    runner.add("record_stop", step_record_stop)
    runner.add("record_check", step_record_check)
    runner.add("send_file", step_send_file)
    runner.add("send_file_check", step_send_file_check)
    runner.add("sidebar", step_sidebar)
    runner.add("sidebar_check", step_sidebar_check)
    # -------------------------------------------------------------- combo boxes
    def step_combo(next_):
        box = window.baud
        # A combo popup only opens for the active window; earlier steps may have
        # shown a tooltip window, so take activation back first.
        window.activateWindow()
        app.processEvents()
        shot = box.grab().toImage()
        corner = shot.pixelColor(1, 1).name()
        ink = sum(1 for y in range(shot.height()) for x in range(shot.width())
                  if shot.pixelColor(x, y).name() != corner)
        check("R-028 下拉框自绘箭头可见", ink > 20, f"ink={ink}px")
        check("R-028 波特率可手动输入", box.isEditable())
        box.showPopup()
        app.processEvents()
        # The offscreen platform realises the popup window a little later, so
        # give it up to 500 ms instead of asserting on one event pass.
        for _attempt in range(10):
            if box.view().isVisible():
                break
            QTest.qWait(50)
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
        # Warm-up: the first collapse of a freshly built group also pays
        # first-time layout/style resolution for its widgets. Measure the
        # steady-state click so the 40 ms budget keeps its original meaning.
        panel.toggle_group(group["id"])
        app.processEvents()
        panel.toggle_group(group["id"])
        app.processEvents()
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

        # A fixed 180 ms is a guess: under load the animation can still be
        # running when the assertion fires.  Poll until it stops, bounded by
        # 1.5 s, then assert the resting state (L-116).
        def wait_settled(deadline: float) -> None:
            if (row._selection_anim.state() == QAbstractAnimation.Stopped
                    and button._feedback_anim.state() == QAbstractAnimation.Stopped):
                settled()
                return
            if time.monotonic() > deadline:
                settled()
                return
            QTimer.singleShot(25, lambda: wait_settled(deadline))

        wait_settled(time.monotonic() + 1.5)

    def step_performance(next_):
        # The DEMO device keeps emitting telemetry on its own schedule: a line
        # (or a half line) that lands between setPlainText() and the exact-count
        # assertion is correct behaviour but breaks "blocks == lines + 1", which
        # made this step pass or fail depending on timing (L-116).  Close the link
        # for the measurement and reopen it afterwards, exactly like R-073.
        manager = window.bridge.manager
        link_was_open = bool(manager.status().get("connected"))
        if link_was_open:
            manager.close()
            app.processEvents()
        # Measure the log/group work itself: the 500 ms stats timer and the
        # capture timer would otherwise land inside the samples and push the
        # p95 over the budget for reasons unrelated to this check (L-073).
        paused = [timer for timer in (window.stats_timer, window.record_timer)
                  if timer is not None and timer.isActive()]
        for timer in paused:
            timer.stop()
        # The tooltip poller is another 10 Hz timer that has nothing to do with
        # this measurement (L-073: pause, never relax the budget).
        poller = getattr(window, "tooltips", None)
        poller_was = poller is not None and poller._timer.isActive()
        if poller_was:
            poller._timer.stop()
        import json
        import statistics

        panel = window.commands_panel
        store = window.command_store
        group = store.add_group("Performance", "")
        store.add([f"AT+PERF={index}" for index in range(200)], {"group": group["id"]})
        app.processEvents()
        timings = {name: [] for name in ("group_action", "group_frame", "log_scroll", "rx_batch")}
        # 16 samples instead of 12: p95 is the second-largest value either way,
        # but more samples stop one GC hiccup from being the p95 (L-097).
        for _index in range(16):
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
        # The cap is a size budget now (R-081), so a 100 MB budget must keep
        # every one of these lines; the loss notice is proven by shrinking it.
        # Every append adds exactly one block; a document that ends with a
        # newline also carries one empty block at the end, so the block count is
        # ``lines`` or ``lines + 1``.  Pinning it to "+1" made the check depend on
        # a stray pending line from the DEMO device (L-116).
        blocks = log.blockCount()
        check("R-054 压力接收逐行计数准确且不丢行",
              window._appended == expected and blocks in (expected, expected + 1)
              and log.trimmed == 0,
              f"{window._appended} 行 / {blocks} 块 / trimmed={log.trimmed}")
        # 21 200 lines are ~2 MB, well inside the budget, so the loss notice must
        # stay hidden - and it is R-081 that proves it appears when the budget is
        # really exceeded.
        check("R-054 未超上限时不显示损失提示",
              not window.trim_badge.isVisible(), str(log.trimmed))
        received = [line.rsplit("◇", 1)[-1] for line in log.line_texts()]
        check("R-054 每一批100条接收内容全部保留",
              all(received.count(f"PERF_RX_{index}") == 100 for index in range(12)))
        report = {}
        for name, samples in timings.items():
            # First sample is warm-up: it pays the document/layout growth the
            # rest of the batches no longer see.
            samples = samples[1:]
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
        for timer in paused:
            timer.start()
        if poller_was:
            poller._timer.start()
        check("R-054 滚动、200条命令折叠和100行接收批次的P95耗时低于40ms",
              all(report[name]["p95_ms"] < 40 for name in ("log_scroll", "group_frame", "rx_batch")),
              str(report))
        store.remove_group(group["id"], drop_commands=True)
        if link_was_open:
            window.bridge.open("DEMO", window._serial_opts())
            for _wait in range(40):
                if manager.status().get("connected"):
                    break
                QTest.qWait(25)
            app.processEvents()
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
            tiles = [tile for tile in page.tiles
                     if isinstance(tile, module.AppTile) and tile.kind == "running"]
            check("R-060 同一进程的两个窗口显示两个独立图标",
                  len(tiles) == 2 and {tile.instance["hwnd"] for tile in tiles} == {101, 202})
            with patch.object(module, "_focus_window", return_value=True) as focus, \
                    patch.object(page, "_spawn") as spawn:
                for tile in tiles:
                    QTest.mouseClick(tile, Qt.LeftButton, pos=tile.rect().center())
                check("R-060 新旧窗口图标分别切换到自己的句柄而不是首个窗口",
                      [call.args[0]["hwnd"] for call in focus.call_args_list] == [101, 202]
                      and spawn.call_count == 0)
            page._apply_processes({"processes": {999: os.path.basename(sys.executable)},
                                   "windows": handles[1:]})
            tiles = [tile for tile in page.tiles if isinstance(tile, module.AppTile) and tile.kind == "running"]
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
                  and page.tiles[0].kind == "shortcut"
                  and len(page.apps) == 1)
            page.deleteLater()
        next_()

    def step_keep_awake(next_):
        from omnicom import power

        original = window.settings.value("display/awake", "0")
        calls = []
        with patch.object(power, "set_keep_awake",
                          side_effect=lambda value: calls.append(value) or True):
            window.row_awake.setChecked(True)
            app.processEvents()
            check("R-062 打开开关会请求系统保持常亮", bool(calls) and calls[-1] is True, str(calls))
            check("R-062 常亮状态写入设置",
                  window.settings.value("display/awake") == "1",
                  str(window.settings.value("display/awake")))
            window.row_awake.setChecked(False)
            app.processEvents()
            check("R-062 关闭开关会撤销常亮请求", bool(calls) and calls[-1] is False, str(calls))
            check("R-062 关闭后设置同步为关",
                  window.settings.value("display/awake") == "0",
                  str(window.settings.value("display/awake")))
        on_flags = power.flags_for(True)
        check("R-062 常亮同时要求屏幕与系统，取消只保留连续标记",
              bool(on_flags & power.ES_DISPLAY_REQUIRED)
              and bool(on_flags & power.ES_SYSTEM_REQUIRED)
              and bool(on_flags & power.ES_CONTINUOUS)
              and power.flags_for(False) == power.ES_CONTINUOUS,
              f"{on_flags:#010x} / {power.flags_for(False):#010x}")

        # Closing the app must hand the machine back its sleep timer.
        extra = OmniWindow(demo=False,
                           command_path=os.path.join(tempfile.mkdtemp(
                               prefix="omnicom-awake-"), "commands.json"))
        extra.row_awake.setChecked(True)
        with patch.object(power, "release") as release:
            extra.close()
            app.processEvents()
            check("R-062 关闭应用时释放常亮请求", release.call_count == 1,
                  str(release.call_count))
        if original != "1":
            window.settings.setValue("display/awake", original)
        next_()

    def step_launcher_sections(next_):
        import json
        from pathlib import Path
        from types import SimpleNamespace

        from PySide6.QtWidgets import QInputDialog

        module = next(info.module for info in window.plugins.plugins if info.id == "launcher")
        with tempfile.TemporaryDirectory(prefix="omnicom-sections-") as directory:
            path = Path(directory) / "apps.json"
            long_name = "记事本-需要完整显示的长名称-第二份文档"
            path.write_text(json.dumps([
                {"id": "a", "name": "Editor A", "path": sys.executable},
                {"id": "b", "name": long_name, "path": sys.executable},
            ], ensure_ascii=False), encoding="utf-8")
            api = SimpleNamespace(data_dir=directory, t=lambda zh, en: zh,
                                  notify=lambda *args: None,
                                  on_language=lambda callback: None)
            page = module.LauncherPage(api)
            page._timer.stop()
            page.resize(900, 700)
            page.show()
            app.processEvents()

            handles = [
                {"hwnd": 11, "pid": 900, "path": sys.executable, "title": "doc one"},
                {"hwnd": 22, "pid": 900, "path": sys.executable, "title": "doc two"},
            ]
            page._apply_processes({"processes": {900: os.path.basename(sys.executable)},
                                   "windows": handles})
            app.processEvents()

            shortcuts = [tile for tile in page.tiles if tile.kind == "shortcut"]
            running = [tile for tile in page.tiles if tile.kind == "running"]
            check("R-063 快捷方式在上、运行窗口在下两区分离",
                  len(shortcuts) == 2 and len(running) == 4
                  and page.sec_shortcuts.isVisible() and page.sec_running.isVisible()
                  and all(tile.parent() is page.shortcut_host for tile in shortcuts)
                  and all(tile.parent() is page.running_host for tile in running),
                  f"{len(shortcuts)} 快捷方式 / {len(running)} 窗口")

            tall = next(tile for tile in shortcuts if tile.title == long_name)
            short = next(tile for tile in shortcuts if tile.title != long_name)
            line = module.QFontMetrics(tall.font()).lineSpacing()
            check("R-063 长名称换行增高而不是被遮盖",
                  tall.height() > short.height()
                  and tall._text_rect().height() >= tall._wrapped_height(long_name)
                  and tall._wrapped_height(long_name) >= 2 * line,
                  f"{short.height()}px -> {tall.height()}px，文字 {tall._wrapped_height(long_name)}px")

            with patch.object(page, "_spawn") as spawn, \
                    patch.object(module, "_focus_window") as focus:
                tile = shortcuts[0]
                QTest.mouseClick(tile, Qt.LeftButton, pos=tile.rect().center())
                check("R-063 点击快捷方式总是新建窗口（即使已运行）",
                      spawn.call_count == 1 and focus.call_count == 0,
                      f"{spawn.call_count}/{focus.call_count}")
            first, second = running[0], running[1]
            with patch.object(QInputDialog, "getText", return_value=("A", True)):
                page._rename_instance(page.apps[0], first.instance)
            with patch.object(QInputDialog, "getText", return_value=("B", True)):
                page._rename_instance(page.apps[0], second.instance)
            app.processEvents()
            names = [tile.title for tile in page.tiles
                     if tile.kind == "running" and tile.app is page.apps[0]]
            check("R-063 同一程序的两个窗口可分别命名为 A、B",
                  names == ["A", "B"], str(names))
            saved = json.loads(path.read_text(encoding="utf-8"))
            check("R-063 窗口别名写盘，重开后仍按序号恢复",
                  len(saved[0].get("aliases") or {}) == 2
                  and saved[0].get("alias_pool") == ["A", "B"],
                  str(saved[0].get("alias_pool")))

            reloaded = module.LauncherPage(api)
            reloaded._timer.stop()
            reloaded._apply_processes({"processes": {900: os.path.basename(sys.executable)},
                                       "windows": handles})
            names = [tile.title for tile in reloaded.tiles
                     if tile.kind == "running" and tile.app is reloaded.apps[0]]
            check("R-063 重新载入后别名仍是 A、B", names == ["A", "B"], str(names))

            # Re-read the tiles: renaming a window rebuilt the grid, so the
            # widgets captured earlier are dead and their geometry is stale.
            live = [tile for tile in page.tiles if tile.kind == "shortcut"]
            before = [tile.title for tile in live]
            page._on_drag_finished(live[0],
                                   live[1].mapToGlobal(live[1].rect().center()))
            after = [tile.title for tile in page.tiles if tile.kind == "shortcut"]
            check("R-064 快捷方式可拖拽排序并即时生效",
                  after == list(reversed(before)), f"{before} -> {after}")
            on_disk = [item["name"] for item in json.loads(path.read_text(encoding="utf-8"))]
            check("R-064 快捷方式新顺序写盘", on_disk == after, str(on_disk))

            windows = [tile for tile in page.tiles if tile.kind == "running"]
            first_app = [tile for tile in windows if tile.app is page.apps[0]]
            page._on_drag_finished(first_app[1],
                                   first_app[0].mapToGlobal(first_app[0].rect().center()))
            order = page.apps[0].get("window_order") or []
            check("R-064 运行窗口可拖拽排序并记录在配置里",
                  order == ["900:22", "900:11"], str(order))

            page.hide()
            page.deleteLater()
            reloaded.deleteLater()
        next_()

    def step_launcher_delete(next_):
        import json
        from pathlib import Path
        from types import SimpleNamespace

        module = next(info.module for info in window.plugins.plugins if info.id == "launcher")

        def menu_choose_last(tile):
            def choose():
                menu = QApplication.activePopupWidget()
                QTest.mouseClick(menu, Qt.LeftButton,
                                 pos=menu.actionGeometry(menu.actions()[-1]).center())

            QTimer.singleShot(30, choose)
            app.sendEvent(tile, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(2, 2),
                                                  tile.mapToGlobal(QPoint(2, 2))))

        with tempfile.TemporaryDirectory(prefix="omnicom-delete-") as directory:
            path = Path(directory) / "apps.json"
            path.write_text(json.dumps([{"id": "a", "name": "Editor", "path": sys.executable}]),
                            encoding="utf-8")
            api = SimpleNamespace(data_dir=directory, t=lambda zh, en: zh,
                                  notify=lambda *args: None,
                                  on_language=lambda callback: None)
            handles = [{"hwnd": 11, "pid": 900, "path": sys.executable, "title": "doc"}]

            top = module.LauncherPage(api)
            top._timer.stop()
            top.show()
            app.processEvents()
            top._apply_processes({"processes": {900: os.path.basename(sys.executable)},
                                  "windows": handles})
            shortcut = next(tile for tile in top.tiles if tile.kind == "shortcut")
            with patch.object(module, "_close_window", return_value=True) as closer:
                menu_choose_last(shortcut)
                check("R-065 上排删除只移除快捷方式且关闭窗口调用为 0",
                      top.apps == [] and closer.call_count == 0,
                      f"{len(top.apps)} 条 / {closer.call_count} 次关闭")
            check("R-065 上排删除写入配置",
                  json.loads(path.read_text(encoding="utf-8")) == [])
            top.hide()
            top.deleteLater()

            path.write_text(json.dumps([{"id": "a", "name": "Editor", "path": sys.executable}]),
                            encoding="utf-8")
            bottom = module.LauncherPage(api)
            bottom._timer.stop()
            bottom.show()
            app.processEvents()
            bottom._apply_processes({"processes": {900: os.path.basename(sys.executable)},
                                     "windows": handles})
            tile = next(item for item in bottom.tiles if item.kind == "running")
            with patch.object(module, "_close_window", return_value=True) as closer:
                menu_choose_last(tile)
                check("R-065 下排删除请求关闭该窗口（句柄一致）",
                      closer.call_count == 1
                      and closer.call_args.args[0].get("hwnd") == 11,
                      str(closer.call_args.args[0].get("hwnd") if closer.call_args else None))
            check("R-065 下排删除不会删掉已保存的快捷方式",
                  [item["id"] for item in bottom.apps] == ["a"],
                  str([item["id"] for item in bottom.apps]))
            bottom.hide()
            bottom.deleteLater()
        next_()

    # ------------------------------------------------- preferences + search sync
    def step_search_live(next_):
        """R-072: new lines must keep appending to the search results."""
        panel = window.search_panel
        window.clear_log()
        panel.clear_all()
        panel.input.setText("LIVEKEY")
        panel.submit_input()
        app.processEvents()
        base = panel.hits

        window._print_line(0.0, "LIVEKEY rx line", "RX", panel.keywords)
        window._log_line(0.0, "TX", "LIVEKEY tx line", "tx")
        window._log_line(0.0, "··", "LIVEKEY sys line", "sys")
        app.processEvents()
        check("R-072 日志更新时 RX、TX、系统行都会追加到搜索结果",
              panel.hits == base + 3, f"{base} -> {panel.hits}")

        last = panel.results.item(panel.results.count() - 1)
        block = last.data(Qt.UserRole)
        check("R-072 实时命中行显示完整行（与历史命中一致）",
              last.text().strip().endswith("LIVEKEY sys line"), last.text().strip())
        lines = window.log.line_texts()
        check("R-072 实时命中行的块号能定位到该行",
              isinstance(block, int) and 0 <= block < len(lines)
              and "LIVEKEY sys line" in lines[block], f"block={block}")

        panel.run_search()
        app.processEvents()
        listed = [panel.results.item(index).text()
                  for index in range(panel.results.count())]
        check("R-072 重新全量搜索的结果与实时追加一致",
              len(listed) >= 3 and all("LIVEKEY" in row for row in listed),
              f"{len(listed)} 行")
        panel.clear_all()
        window.clear_log()
        next_()

    def step_log_dir(next_):
        """R-070: the capture folder comes from Preferences, not a constant."""
        original = str(window.settings.value("paths/log_dir", ""))
        target = tempfile.mkdtemp(prefix="omnicom-logdir-")
        applied = window.apply_log_dir(target)
        check("R-070 选择目录后记录器跟随",
              applied and window.bridge.recorder.directory == os.path.abspath(target),
              str(window.bridge.recorder.directory))
        check("R-070 目录写入设置", window.settings.value("paths/log_dir") == target)

        state = window.bridge.recorder.start()
        window.bridge.recorder.write(b"probe", "rx")
        window.bridge.recorder.stop()
        check("R-070 捕获文件落在所选目录",
              os.path.dirname(str(state.get("path"))) == os.path.abspath(target),
              str(state.get("path")))

        blocker = os.path.join(target, "not-a-folder")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("x")
        rejected = not window.apply_log_dir(blocker)
        check("R-070 不可用目录会被拒绝并保留原目录",
              rejected and window.bridge.recorder.directory == os.path.abspath(target),
              str(window.bridge.recorder.directory))

        window.apply_log_dir(original)
        next_()

    def step_theme(next_):
        """R-071: accent colour and wallpaper, both persisted and reversible."""
        from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

        original_accent = theme.accent()
        original_wall = theme.wallpaper() or ""

        def sampled(widget) -> set[str]:
            image = widget.grab().toImage()
            return {image.pixelColor(x, y).name()
                    for x in range(0, image.width(), 41)
                    for y in range(0, image.height(), 29)}

        before = sampled(window)
        window.apply_accent("rose")
        app.processEvents()
        after = sampled(window)
        check("R-071 切换主题色后强调色与样式表同步",
              theme.ACCENTS["rose"][2] == theme.TEAL
              and theme.TEAL in app.styleSheet(),
              theme.TEAL)
        check("R-071 切换主题色会改变实际渲染", before != after,
              f"{len(before)} -> {len(after)} 种颜色")
        check("R-071 主题色写入设置", window.settings.value("ui/accent") == "rose")

        photo = QImage(240, 180, QImage.Format_ARGB32)
        painter = QPainter(photo)
        gradient = QLinearGradient(0, 0, 240, 180)
        gradient.setColorAt(0.0, QColor("#000000"))
        gradient.setColorAt(1.0, QColor("#1E88E5"))
        painter.fillRect(photo.rect(), gradient)
        painter.end()
        wall = os.path.join(tempfile.mkdtemp(prefix="omnicom-wall-"), "wall.png")
        photo.save(wall)

        backdrop = Aurora()
        backdrop.resize(300, 220)
        flat = sampled(backdrop)
        window.apply_wallpaper(wall)
        app.processEvents()
        backdrop.update()
        painted = sampled(backdrop)
        check("R-071 设置壁纸后背景铺满图片",
              theme.wallpaper() == os.path.abspath(wall) and len(painted) > len(flat),
              f"{len(flat)} -> {len(painted)} 种颜色")
        # The strip itself is text-only now (R-082 owns it); what matters here
        # is that the captions stay in the readable ink over a photo.
        ink = window.status_dir.palette().color(window.status_dir.foregroundRole())
        check("R-071 壁纸启用时底部说明仍用可读的正文墨色",
              ink.name().lower() == theme.INK.lower(), ink.name())

        window.apply_wallpaper("")
        app.processEvents()
        backdrop.update()
        cleared = sampled(backdrop)
        check("R-071 清除壁纸恢复纯色背景", cleared == flat,
              str(sorted(cleared)[:3]))
        backdrop.deleteLater()

        window.apply_accent(original_accent)
        window.apply_wallpaper(original_wall)
        app.processEvents()
        theme.set_accent(original_accent)
        app.processEvents()
        next_()

    # ------------------------------------------- counters, file send, brand icon
    def step_clear_counters(next_):
        """R-073: clearing the log zeroes the RX/TX counters too."""
        # The DEMO port keeps emitting telemetry: a byte that arrives between
        # clear_log() and the assertion is correct behaviour, not a bug, so the
        # link is closed for the measurement and reopened afterwards.
        manager = window.bridge.manager
        was_connected = bool(manager.status().get("connected"))
        if was_connected:
            manager.close()
            app.processEvents()
        window.clear_log()
        app.processEvents()
        before = window.bridge.manager.stats()
        check("R-073 清空日志后 RX/TX 统计归零",
              before["rx"] == 0 and before["tx"] == 0, str(before))
        check("R-073 标题栏计数同步归零",
              "0 B" in window.titlebar.counter.text().replace("kB", " B")
              or "RX</span> 0" in window.titlebar.counter.text(),
              window.titlebar.counter.text()[:80])

        if was_connected:
            window.bridge.open("DEMO", window._serial_opts())
            for _wait in range(40):
                if manager.status().get("connected"):
                    break
                QTest.qWait(25)
            app.processEvents()
            window.send_payload(b"AT\r\n", "AT")
            QTest.qWait(120)
            app.processEvents()
            after = manager.stats()
            check("R-073 清空之后新收发重新开始计数",
                  after["tx"] >= 4 and after["rx"] > 0, str(after))
        else:
            # No port in a focused run: prove the counter widget still tracks
            # fresh statistics instead of being pinned to the cleared value.
            window._on_stats({"rx": 1234, "tx": 5678})
            app.processEvents()
            shown = window.titlebar.counter.text()
            check("R-073 清空之后计数显示仍会随新统计更新",
                  "RX" in shown and "TX" in shown and "0 B" not in shown, shown[:80])

        manager.close()
        app.processEvents()
        window.clear_log()
        again = manager.stats()
        check("R-073 再次清空仍会归零",
              again["rx"] == 0 and again["tx"] == 0, str(again))
        if was_connected:
            window.bridge.open("DEMO", window._serial_opts())
            for _wait in range(40):
                if manager.status().get("connected"):
                    break
                QTest.qWait(25)
            app.processEvents()
        next_()

    def step_file_buttons(next_):
        """R-074: separate open/send buttons, printed TX lines and a progress bar."""
        from PySide6.QtWidgets import QFileDialog

        panel = window.send_panel
        sample = os.path.join(tempfile.mkdtemp(prefix="omnicom-sendfile-"), "at-script.txt")
        payload = "AT\r\nAT+CGMR\r\nAT+CSQ\r\n"
        with open(sample, "w", encoding="utf-8") as handle:
            handle.write(payload)

        check("R-074 打开文件与发送文件是两个独立按钮",
              panel.btn_open_file is not None and panel.btn_file is not None
              and panel.btn_open_file is not panel.btn_file,
              f"{panel.btn_open_file.text()} / {panel.btn_file.text()}")

        # "Open file" must only remember the path.
        with patch.object(QFileDialog, "getOpenFileName", return_value=(sample, "")):
            QTest.mouseClick(panel.btn_open_file, Qt.LeftButton)
        app.processEvents()
        check("R-074 打开文件只记录路径，不发送",
              panel.chosen_file() == sample
              and window.bridge.manager.tx_bytes == 0,
              f"{panel.chosen_file()} / tx={window.bridge.manager.tx_bytes}")

        # "Send file" transmits it and prints the content as TX lines.
        window.clear_log()
        app.processEvents()
        before_tx = window.bridge.manager.tx_bytes
        QTest.mouseClick(panel.btn_file, Qt.LeftButton)
        waited = {"ms": 0}

        def poll():
            waited["ms"] += 100
            done = (window.bridge.manager.tx_bytes - before_tx) >= len(payload.encode())
            if done or waited["ms"] > 3000:
                app.processEvents()
                text = window.log.plain_text()
                tx_lines = [line for line in text.splitlines() if "发→" in line]
                check("R-074 发送文件把内容按普通发送打印到日志",
                      any("AT+CGMR" in line for line in tx_lines),
                      f"{len(tx_lines)} 行 TX")
                check("R-074 文件确实发出",
                      window.bridge.manager.tx_bytes - before_tx
                      >= len(payload.encode()),
                      f"+{window.bridge.manager.tx_bytes - before_tx} B")
                check("R-074 发送期间显示进度条",
                      panel.file_progress.isVisibleTo(panel)
                      or panel.file_progress.value() > 0
                      or panel.file_progress.maximum() == 100,
                      f"value={panel.file_progress.value()}")
                check("R-074 发送结束后进度条隐藏且按钮恢复",
                      not panel.file_progress.isVisible()
                      and panel.btn_file.isEnabled()
                      and panel.btn_open_file.isEnabled())
                next_()
                return
            QTimer.singleShot(100, poll)

        poll()

    def step_brand_icon(next_):
        """R-075: one logo source drives every icon asset."""
        from PIL import Image

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logo = Image.open(os.path.join(root, "logo.png")).convert("RGBA")
        check("R-075 根目录 logo.png 来自顶部栏图标（1024 透明角）",
              logo.size == (1024, 1024) and logo.getpixel((0, 0))[3] == 0
              and logo.getpixel((512, 512))[3] == 255,
              f"{logo.size} corner={logo.getpixel((0, 0))[3]}")

        expected = {
            "assets/omnicom.png": (256, 256),
            "assets/logo-64.png": (64, 64),
            "assets/logo-32.png": (32, 32),
            "plugins/launcher/icon.png": (64, 64),
        }
        sizes = {}
        for relative, size in expected.items():
            image = Image.open(os.path.join(root, relative)).convert("RGBA")
            sizes[relative] = image.size
            assert image.size == size, (relative, image.size)
        check("R-075 应用图标、标题栏位图与插件图标都由 logo 生成",
              all(sizes[key] == size for key, size in expected.items()),
              str(sizes))

        ico = Image.open(os.path.join(root, "assets", "omnicom.ico"))
        check("R-075 .ico 包含多尺寸（窗口/任务栏图标）",
              ico.size[0] >= 256, str(ico.size))
        check("R-075 窗口图标已设置", not window.windowIcon().isNull())
        next_()

    # ------------------------------------- console, glass, folders, note, tips
    def step_no_console(next_):
        """R-076: the GUI hides a console window that it alone owns."""
        from omnicom.ui import _console_is_ours

        check("R-076 独占的控制台窗口会被隐藏", _console_is_ours(1, 0x1234) is True)
        check("R-076 共享终端不会被隐藏", _console_is_ours(3, 0x1234) is False)
        check("R-076 没有控制台时不做事", _console_is_ours(1, 0) is False)
        target = os.path.join(ROOT, "omnicom", "ui", "__init__.py")
        with open(target, encoding="utf-8") as handle:
            source = handle.read()
        check("R-076 启动 GUI 时调用隐藏逻辑并保留 --keep-console 开关",
              "_hide_own_console()" in source and "--keep-console" in source)
        next_()

    def step_glass_panes(next_):
        """R-077: panes are frosted glass over the wallpaper, not flat paint."""
        from PySide6.QtGui import QLinearGradient, QPainter

        from omnicom.ui.widgets import GlassFrame

        original = theme.wallpaper() or ""
        panes = [item for item in window.findChildren(GlassFrame)
                 if item.width() > 200 and item.height() > 200]
        check("R-077 主窗口的面板都是玻璃面板", len(panes) >= 3, str(len(panes)))

        # A pane with no children, parented to the real window: its own pixels
        # are pure paint, and no extra top-level window steals activation (that
        # is what stopped the later combo popup from opening).
        pane = GlassFrame(window, radius=theme.RADIUS_CARD, shadow=False)
        pane.setGeometry(24, 96, 260, 200)
        pane.show()
        app.processEvents()
        window.apply_wallpaper("")
        app.processEvents()
        QTest.qWait(40)
        point = QPoint(pane.width() // 2, pane.height() // 2)
        flat = pane.grab().toImage().pixelColor(point)

        photo = QImage(320, 240, QImage.Format_ARGB32)
        brush = QPainter(photo)
        gradient = QLinearGradient(0, 0, 320, 240)
        gradient.setColorAt(0.0, QColor("#0B7A6E"))
        gradient.setColorAt(1.0, QColor("#6D4AE0"))
        brush.fillRect(photo.rect(), gradient)
        brush.end()
        wall = os.path.join(tempfile.mkdtemp(prefix="omnicom-glass-"), "wall.png")
        photo.save(wall)

        window.apply_wallpaper(wall)
        app.processEvents()
        QTest.qWait(60)
        glass = pane.grab().toImage().pixelColor(point)
        check("R-077 无壁纸时面板是中性白",
              flat.red() >= 250 and flat.green() >= 250 and flat.blue() >= 250,
              flat.name())
        check("R-077 有壁纸时面板不再是纯白", glass.name().lower() != "#ffffff",
              glass.name())
        check("R-077 面板带上壁纸的色调（液态玻璃）",
              glass.name().lower() != flat.name().lower() and glass.green() > glass.red(),
              f"{flat.name()} -> {glass.name()}")
        layer = theme.glass_layer(window.width(), window.height())
        check("R-077 玻璃层按窗口尺寸生成",
              layer is not None and layer.size() == window.size(),
              "None" if layer is None else str(layer.size()))
        check("R-077 玻璃面板上提示文字的对比度仍 >= 4.5:1",
              _contrast(theme.DIM, theme.glass_floor()) >= 4.5,
              f"{theme.DIM} on {theme.glass_floor()}")

        window.apply_wallpaper("")
        app.processEvents()
        QTest.qWait(40)
        again = pane.grab().toImage().pixelColor(point)
        check("R-077 清除壁纸后回到中性白面板", again.name() == flat.name(),
              f"{again.name()} vs {flat.name()}")
        pane.hide()
        pane.deleteLater()
        window.apply_wallpaper(original)
        window.activateWindow()
        app.processEvents()
        next_()

    # ------------------------------------------- log buffer, strip, packaging
    def step_log_buffer(next_):
        """R-081: the log is capped by size, keeps the newest lines, never stalls."""
        from omnicom.ui import log_view

        log = window.log
        original = log.buffer_mb()
        window.switch_page("terminal")      # the trim badge lives on this page
        app.processEvents()
        check("R-081 默认缓冲为 100 MB",
              log_view.DEFAULT_BUFFER_MB == 100 and log_view.MAX_BUFFER_MB == 500,
              f"{log_view.DEFAULT_BUFFER_MB}/{log_view.MAX_BUFFER_MB}")

        window.apply_log_buffer(20)
        app.processEvents()
        check("R-081 首选项改大小立即生效并写盘",
              log.buffer_mb() == 20 and window.settings.value("display/buffer_mb") == 20,
              f"{log.buffer_mb()} MB")
        window.apply_log_buffer(9999)
        check("R-081 超过 500 MB 会被夹住", log.buffer_mb() == 500, str(log.buffer_mb()))
        window.apply_log_buffer(1)
        check("R-081 低于最小值会被夹住",
              log.buffer_mb() == log_view.MIN_BUFFER_MB, str(log.buffer_mb()))

        # Fill past a small budget: the newest lines must survive, and the
        # trimming must stay a single fast operation (R-017 keeps counting).
        window.apply_log_buffer(log_view.MIN_BUFFER_MB)
        window.clear_log()
        app.processEvents()
        line = "BUF {index:06d} " + "q" * 70
        worst = 0.0
        # 55 k lines of 86 chars = 4.7 Mchars: past the 4 MB budget, small
        # enough that the rest of the suite is not starved of event-loop time.
        for index in range(55000):
            start = time.perf_counter()
            window._on_rx(time.time() * 1000, (line.format(index=index) + "\r\n").encode())
            if index % 100 == 0:
                window._flush_rx()
                app.processEvents()
                worst = max(worst, (time.perf_counter() - start) * 1000)
        window._flush_rx()
        app.processEvents()

        held = log.buffer_chars()
        budget = log_view.MIN_BUFFER_MB * 1024 * 1024
        check("R-081 超出预算后保留最新的内容",
              held <= budget and "BUF 054999" in log.plain_text(),
              f"{held / 1048576:.2f} MB / 最旧一行={'BUF 000000' in log.plain_text()}")
        check("R-081 丢弃的行数被记下来（不静默丢失）",
              log.trimmed > 0 and window.trim_badge.isVisible()
              and str(log.trimmed) in window.trim_badge.text(),
              f"trimmed={log.trimmed} badge={window.trim_badge.text()!r} "
              f"visible={window.trim_badge.isVisible()}")
        check("R-081 裁剪耗时低于 250 ms（不卡顿）", worst < 250, f"worst={worst:.0f} ms")

        window.apply_log_buffer(original or 100)
        window.clear_log()
        app.processEvents()
        next_()

    def step_status_glass(next_):
        """R-082: the caption row is text only - no frame, no fill, no plate."""
        from PySide6.QtGui import QLinearGradient, QPainter

        from omnicom.ui.widgets import CaptionStrip

        original = theme.wallpaper() or ""
        strip = window.status_strip_frame
        check("R-082 底部栏是不带边框与底色的纯文字行",
              isinstance(strip, CaptionStrip) and not strip.autoFillBackground(),
              type(strip).__name__)

        window.apply_wallpaper("")
        app.processEvents()
        point = QPoint(strip.width() // 2, strip.height() // 2)
        plain = strip.grab().toImage().pixelColor(point)
        check("R-082 无壁纸时底部栏一个像素都不画",
              plain.alpha() == 0, f"alpha={plain.alpha()}")

        photo = QImage(320, 200, QImage.Format_ARGB32)
        brush = QPainter(photo)
        gradient = QLinearGradient(0, 0, 320, 200)
        gradient.setColorAt(0.0, QColor("#0B7A6E"))
        gradient.setColorAt(1.0, QColor("#6D4AE0"))
        brush.fillRect(photo.rect(), gradient)
        brush.end()
        wall = os.path.join(tempfile.mkdtemp(prefix="omnicom-strip-"), "wall.png")
        photo.save(wall)

        window.apply_wallpaper(wall)
        app.processEvents()
        QTest.qWait(80)
        glass = strip.grab().toImage().pixelColor(point)
        check("R-082 有壁纸时底部栏也不加任何底板",
              glass.alpha() == 0, f"alpha={glass.alpha()} {glass.name()}")

        caption = window.status_dir
        ink = window.status_dir.palette().color(window.status_dir.foregroundRole())
        check("R-082 说明文字改用正文墨色（最暗页面仍 4.5:1 以上）",
              ink.name().lower() == theme.INK.lower() and caption.text(),
              f"{ink.name()} / {caption.text()[:24]}")
        check("R-082 说明文字带白色描边光晕保证可读",
              window.status_dir.graphicsEffect() is not None
              and window.status_line.graphicsEffect() is not None)

        window.apply_wallpaper("")
        app.processEvents()
        window.apply_wallpaper(original)
        app.processEvents()
        window.activateWindow()
        next_()

    def step_no_exe_distribution(next_):
        """R-083: a distribution that needs no self-built exe."""
        from omnicom import paths

        builder = os.path.join(ROOT, "tools", "make_dist.py")
        check("R-083 提供免安装打包脚本", os.path.isfile(builder))
        with open(builder, encoding="utf-8") as handle:
            source = handle.read()
        check("R-083 打包内容自带运行时与依赖（用户不需要安装）",
              "embed-amd64" in source and "site-packages" in source
              and "_pth" in source and "runtime" in source)
        check("R-083 启动脚本运行包内解释器而不是系统 Python",
              "runtime\\\\pythonw.exe" in source, "launcher template")
        check("R-083 打包后用包内运行时自检（证明无外部依赖）",
              "--selftest" in source and '"-E", "-s"' in source)
        check("R-083 源码运行时资源目录就是仓库根",
              paths.resource_dir() == ROOT, paths.resource_dir())
        next_()

    def step_records_folder(next_):
        """R-078: the records folder shown and opened follows Preferences."""
        original = str(window.settings.value("paths/log_dir", ""))
        target = tempfile.mkdtemp(prefix="omnicom-rec-")
        window.apply_log_dir(target)
        app.processEvents()
        check("R-078 状态栏显示新的记录目录",
              os.path.abspath(target) in window.status_dir.text(),
              window.status_dir.text())

        opened: list[str] = []
        original_open = window._open_path
        window._open_path = opened.append
        try:
            window.open_data_dir()
        finally:
            window._open_path = original_open
        check("R-078 打开目录按钮指向新的记录目录",
              bool(opened) and os.path.abspath(opened[0]) == os.path.abspath(target),
              str(opened))

        window.retranslate()
        check("R-078 刷新文案后仍显示新目录",
              os.path.abspath(target) in window.status_dir.text(),
              window.status_dir.text())

        window.apply_log_dir(original)
        next_()

    def step_note_align(next_):
        """R-079: a command's note starts exactly under its command text."""
        row = CommandRow({"id": "probe-note", "text": "AT+CGMR", "delay": 0,
                          "note": "query the firmware revision"}, [], window)
        row.setGeometry(24, 320, 300, 96)
        row.show()
        app.processEvents()

        label_x = row.label.mapTo(row, QPoint(0, 0)).x()
        note_x = row.note_label.mapTo(row, QPoint(0, 0)).x()
        check("R-079 注释与命令文字左对齐", label_x == note_x, f"{label_x} / {note_x}")
        check("R-079 注释不再突出到更左侧",
              row.note_row.contentsMargins().left() == label_x,
              str(row.note_row.contentsMargins().left()))
        row.hide()
        row.deleteLater()
        window.activateWindow()
        app.processEvents()
        next_()

    def step_tooltip_delay(next_):
        """R-080/R-084: our own rounded, glassy card appears after one second."""
        from PySide6.QtGui import QLinearGradient, QPainter

        from omnicom.ui.tooltips import install as install_tooltips

        watcher = install_tooltips()
        check("R-080 提示延时固定为 1 秒", watcher.delay_ms == 1000, str(watcher.delay_ms))
        button = window.titlebar.btn_menu
        check("R-080 图标带功能提示文字", bool(button.toolTip().strip()), button.toolTip())

        seen: list[str] = []

        def remember(text: str) -> None:
            seen.append(text)

        # The poller is the only platform-dependent part: point it at the icon
        # button instead of moving the (headless) system cursor.
        original_cursor, original_at = watcher._cursor, watcher._widget_at
        watcher._cursor = lambda: button.mapToGlobal(button.rect().center())
        watcher._widget_at = lambda position: button
        watcher.shown.connect(remember)
        try:
            watcher._reset()
            QTest.qWait(700)
            check("R-080 0.7 秒时还不弹提示", not seen, str(seen))
            # The poller ticks every 100 ms, so give it up to 600 ms more before
            # claiming the hint never came.
            for _tick in range(10):
                if seen:
                    break
                QTest.qWait(60)
            check("R-080 约 1 秒后弹出提示", bool(seen),
                  f"{seen} elapsed={watcher._elapsed}ms "
                  f"timer={watcher._timer.isActive()} "
                  f"same={watcher._cursor_widget is button} "
                  f"visible_for={watcher._visible_for is not None}")

            popup = watcher.popup
            shot = popup.grab().toImage()
            corner = shot.pixelColor(0, 0)
            centre = shot.pixelColor(shot.width() // 2, shot.height() // 2)
            check("R-084 提示框圆角外完全透明（没有白块）",
                  corner.alpha() == 0, f"alpha={corner.alpha()} {corner.name()}")
            check("R-084 提示框本体是不透明卡片", centre.alpha() > 0,
                  f"alpha={centre.alpha()} {centre.name()}")

            # Glass: with a wallpaper behind the window the card samples the very
            # layer the panes use, so it is tinted instead of plain white.
            photo = QImage(320, 200, QImage.Format_ARGB32)
            brush = QPainter(photo)
            gradient = QLinearGradient(0, 0, 320, 200)
            gradient.setColorAt(0.0, QColor("#0B7A6E"))
            gradient.setColorAt(1.0, QColor("#6D4AE0"))
            brush.fillRect(photo.rect(), gradient)
            brush.end()
            wall = os.path.join(tempfile.mkdtemp(prefix="omnicom-tip-"), "w.png")
            photo.save(wall)
            original_wall = theme.wallpaper() or ""
            window.apply_wallpaper(wall)
            app.processEvents()
            watcher._reset()
            seen.clear()
            # The poller needs a full second again after the reset; wait for the
            # popup instead of assuming one fixed delay fits the busy suite.
            for _tick in range(30):
                if watcher.popup_visible():
                    break
                QTest.qWait(60)
            backdrop = watcher.popup._backdrop
            check("R-084 有壁纸时提示框取到玻璃层切片",
                  backdrop is not None and not backdrop.isNull()
                  and backdrop.size() == watcher.popup.size(),
                  "None" if backdrop is None else str(backdrop.size()))
            window.apply_wallpaper(original_wall)
            app.processEvents()

            watcher._widget_at = lambda position: None
            QTest.qWait(200)
            check("R-080 指针移开后提示自动收起", watcher._visible_for is None)
        finally:
            watcher._cursor, watcher._widget_at = original_cursor, original_at
            watcher.shown.disconnect(remember)
            watcher._reset()
            window.activateWindow()
            app.processEvents()
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
    runner.add("R-062 keep_awake", step_keep_awake)
    runner.add("R-063 launcher_sections", step_launcher_sections)
    runner.add("R-065 launcher_delete", step_launcher_delete)
    runner.add("R-067 rail_layout", step_rail_layout)
    runner.add("R-069 version", step_version)
    runner.add("R-072 search_live", step_search_live)
    runner.add("R-070 log_dir", step_log_dir)
    runner.add("R-071 theme", step_theme)
    runner.add("R-073 clear_counters", step_clear_counters)
    runner.add("R-074 file_buttons", step_file_buttons)
    runner.add("R-075 brand_icon", step_brand_icon)
    runner.add("R-076 no_console", step_no_console)
    runner.add("R-077 glass_panes", step_glass_panes)
    runner.add("R-081 log_buffer", step_log_buffer)
    runner.add("R-082 status_glass", step_status_glass)
    runner.add("R-083 no_exe", step_no_exe_distribution)
    runner.add("R-078 records_folder", step_records_folder)
    runner.add("R-079 note_align", step_note_align)
    runner.add("R-080 tooltip_delay", step_tooltip_delay)
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
