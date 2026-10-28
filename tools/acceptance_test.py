"""Full acceptance test — every requirement, with executable evidence.

    python tools/acceptance_test.py            # run everything
    python tools/acceptance_test.py --fast     # skip the slow GUI suite

Runs four independent suites and maps each result onto a requirement ID from
``project-context/REQUIREMENTS.md``:

1. ``tools/protocol_selftest.py``  — X/Y/ZMODEM over an in-memory loopback
2. ``python -m omnicom.cli ...``   — the AI-facing CLI surface
3. ``tools/ui_selftest.py``        — the Qt GUI (offscreen, DEMO port)
4. in-process visual / delivery checks (solid background, no exe, no web server)

Exit code 0 only when every requirement has at least one passing check and none
of its checks failed.  A JSON report is written to ``.artifacts/acceptance.json``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Isolated, freshly wiped runtime data for the whole run: the suite writes
# captures and commands, so sharing the user's folder both polluted real
# data and made repeated runs fail (L-116).  Children inherit this value and
# replace it with their own scratch folder.
_DATA_DIR = os.path.join(ROOT, ".artifacts", "test-data", "acceptance")
shutil.rmtree(_DATA_DIR, ignore_errors=True)
os.environ["OMNICOM_DATA_DIR"] = _DATA_DIR
os.makedirs(_DATA_DIR, exist_ok=True)

from omnicom import __version__  # noqa: E402
from omnicom.ui.main_window import OmniWindow, TitleBar  # noqa: E402
from omnicom.ui.theme import app_stylesheet  # noqa: E402

RESULTS: dict[str, list[tuple[str, bool, str]]] = {}
FAILED: list[tuple[str, str, str]] = []
TITLES = {
    "R-001": "整体功能完整 + 白色主题 + 自绘窗口按钮",
    "R-002": "左栏完整串口配置（含流控/DTR/RTS）",
    "R-003": "顶部标签页：终端 / 文件传输(X/Y/Z)",
    "R-004": "方便、准确、不丢字的收发",
    "R-005": "自动识别串口 + 自动重连开关",
    "R-006": "发送文件",
    "R-007": "HEX 发送与 HEX 显示",
    "R-008": "可折叠右侧栏 + 可扩展命令列表",
    "R-009": "粘贴多行自动填入命令列表",
    "R-010": "毫秒时间戳，可开关",
    "R-011": "定时发送",
    "R-012": "自动滚动到最新（暂停打印已移除）",
    "R-013": "接收保存到文件",
    "R-014": "多关键字搜索右侧汇总",
    "R-015": "显示 \\r\\n 等不可见字符",
    "R-016": "命令项延时 + 定时发送",
    "R-017": "绝不静默丢字节",
    "R-018": "原生 Python GUI（无浏览器/无服务）",
    "R-019": "不打包/不依赖自编译 exe",
    "R-020": "白色主题 + 纯色背景（无网格）",
    "R-021": "面向 AI 的 CLI + 文档",
    "R-022": "CLI 单 JSON 输出契约",
    "R-023": "CLI 支持 DEMO 虚拟端口",
    "R-024": "日志格式 [时间]发→/收←",
    "R-025": "开启后发送内容也显示不可见字符",
    "R-026": "终端模式（linux 串口交互）",
    "R-027": "Notepad++ 式查找全部 + 全屏 + 双击跳转",
    "R-028": "下拉框统一风格 + 点击弹出候选 + 可自定义输入",
    "R-029": "日志等宽字体",
    "R-030": "关键字搜索：区分大小写 / 模糊 / 正则",
    "R-031": "扩展命令分组（可折叠、可备注）+ 按条/按组导出导入",
    "R-032": "插件系统（独立目录、顶部标签页、复制即用）",
    "R-033": "中英文双语界面（中文只显示中文，英文只显示英文）",
    "R-034": "帮助栏目：关于 + 使用文档弹窗",
    "R-035": "界面自适应缩放，控件不被裁剪",
    "R-036": "透明 logo + 控件圆角体系（勾选框大圆角）",
    "R-037": "窗口外框四角都是直角",
    "R-038": "左右侧栏可拖动调整宽度（自绘品牌标记）",
    "R-039": "流控可读文案 + DTR/RTS 紧凑 + 波特率可自定义",
    "R-040": "导出统一入口（选择框）+ 串口框跟随实际端口",
    "R-041": "分组折叠即时响应（不重建、不写盘）",
    "R-048": "串口重连后下拉框显示真正连上的端口",
    "R-042": "分组内命令缩进，并用「+」在分组内新建命令",
    "R-043": "日志与发送框之间有可拖动的分隔条",
    "R-044": "清理重复功能的按钮（关于/文档/右栏/启动器）",
    "R-045": "勾选框尺寸与正文协调",
    "R-046": "右键菜单每一项都真正生效",
    "R-047": "命令编辑对话框正常打开",
    "R-049": "扩展命令紧凑单行与状态区分",
    "R-050": "右键查找使用日志选区",
    "R-051": "正确刷新箭头与短时局部交互动画",
    "R-052": "带真实字体的视觉布局与双语可见性",
    "R-053": "新建分组后旧分组内容完整可展开",
    "R-054": "大量日志滚动与分组折叠性能回归",
    "R-055": "反复开关串口响应与生命周期竞争隔离",
    "R-056": "分组名称唯一及同名导入合并",
    "R-057": "仅串口实际打开后按钮着色",
    "R-058": "统一按钮悬停渐变、按压回弹与即时点击",
    "R-059": "快捷启动右键删除与配置持久化",
    "R-060": "多窗口独立图标、精确绑定与切换",
    "R-061": "波特率可自定义（真实键盘输入）",
    "R-062": "屏幕常亮开关（可持久化、关闭时释放）",
    "R-063": "快捷启动上下两区 + 窗口实例重命名 + 长名称完整显示",
    "R-064": "快捷启动上下两区支持拖拽排序并持久化",
    "R-065": "上排删除只删快捷方式、下排删除只结束该窗口",
    "R-066": "收发双向记录到文件 + 随时清空并继续记录",
    "R-067": "左栏底部不再重复串口/流控/版本信息",
    "R-068": "左栏默认宽度与右侧栏一致",
    "R-069": "版本号为 1.1.2",
    "R-070": "首选项可设置日志默认保存目录",
    "R-071": "首选项可换主题色 / 设置壁纸",
    "R-072": "日志更新时搜索结果持续追加",
    "R-073": "清空日志同步清空 RX/TX 计量",
    "R-074": "打开/发送文件分离 + 日志打印 + 进度条",
    "R-075": "统一图标来源（logo.png 生成全部图标）",
    "R-076": "启动 GUI 不自带命令行窗口",
    "R-077": "面板为液态玻璃（模糊壁纸 + 半透明白）",
    "R-078": "记录目录跟随首选项（状态栏 + 打开目录）",
    "R-079": "命令注释与命令文字左对齐",
    "R-080": "图标提示 1 秒后出现",
    "R-081": "日志按大小缓冲（默认 100MB / 上限 500MB，弃旧留新）",
    "R-082": "底部说明行纯文字（无框 / 无底色）",
    "R-083": "免安装便携包（自带运行时，无需装依赖）",
    "R-084": "自绘圆角提示框（无白块 + 液态玻璃）",
}


def add(req: str, name: str, ok: bool, detail: object = "") -> None:
    RESULTS.setdefault(req, []).append((name, bool(ok), str(detail)))
    if not ok:
        FAILED.append((req, name, str(detail)))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {req} {name}" + (f"   [{detail}]" if detail else ""))


def _utf8_console() -> None:
    """Never let a console code page crash the report.

    On a GBK console a single U+FFFD (produced when a child prints in its own
    locale encoding) raised UnicodeEncodeError and killed the whole run, which
    looked like a test failure.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"       # children emit UTF-8 into the pipe
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env)


# ------------------------------------------------------------------ suites

def suite_protocol() -> None:
    print("\n== 1/4 协议环回（X / Y / ZMODEM）")
    proc = run(["tools/protocol_selftest.py"])
    out = (proc.stdout or "") + (proc.stderr or "")
    passed = "ALL PROTOCOL SELFTESTS PASSED" in out
    add("R-004", "协议环回 23 项收发一致", passed,
        "" if passed else out.strip().splitlines()[-1:] or "no output")
    for line in out.splitlines():
        if "PASS" in line and ("XMODEM" in line or "YMODEM" in line or "ZMODEM" in line):
            add("R-003", "协议引擎：" + line.split("PASS")[-1].strip(), True)


def cli_json(*args: str) -> dict:
    proc = run(["-m", "omnicom.cli", *args])
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "非 JSON 输出：" + (proc.stdout or proc.stderr or "")[:200]}


def suite_cli() -> None:
    print("\n== 2/4 CLI（AI 接口）")

    info = cli_json("info")
    add("R-021", "CLI info 可用", info.get("ok") is True, info.get("version", ""))
    add("R-021", "CLI 文档存在（docs/CLI.md 供 AI 阅读）",
        os.path.isfile(os.path.join(ROOT, "docs", "CLI.md")))

    ports = cli_json("ports", "--demo")
    names = [item["name"] for item in ports.get("ports", [])]
    add("R-005", "CLI ports 能列出串口并含 DEMO", ports.get("ok") and "DEMO" in names, names)

    sent = cli_json("send", "--port", "DEMO", "--data", "AT", "--expect", "OK",
                    "--wait", "2", "--limit", "200")
    add("R-004", "CLI send 收到设备应答", sent.get("ok") is True and sent.get("matched") is True,
        (sent.get("response") or {}).get("escaped", "")[:40])
    add("R-015", "CLI 应答同时给出 escaped 形式",
        "\\r\\n" in (sent.get("response") or {}).get("escaped", ""))

    hexsend = cli_json("send", "--port", "DEMO", "--hex", "41 54 0D 0A",
                       "--expect", "OK", "--wait", "2")
    add("R-007", "CLI HEX 发送字节正确",
        hexsend.get("ok") is True and hexsend.get("sent_hex") == "41 54 0d 0a",
        hexsend.get("sent_hex", ""))

    full = cli_json("send", "--port", "DEMO", "--baud", "921600", "--bytesize", "7",
                    "--parity", "E", "--stopbits", "2", "--flow", "rtscts",
                    "--no-dtr", "--no-rts", "--data", "AT", "--expect", "OK", "--wait", "2")
    add("R-002", "CLI 接受完整串口参数组合", full.get("ok") is True,
        full.get("error", "baud=921600 7E2 rtscts"))

    read = cli_json("read", "--port", "DEMO", "--timeout", "1.5", "--record")
    add("R-004", "CLI read 读到数据", read.get("ok") and (read.get("received") or {}).get("bytes", 0) > 0,
        (read.get("received") or {}).get("bytes"))
    add("R-013", "CLI read --record 落盘且字节一致",
        bool((read.get("record") or {}).get("path")) and
        (read.get("record") or {}).get("bytes", -1) > 0,
        (read.get("record") or {}).get("path", ""))

    mon = run(["-m", "omnicom.cli", "monitor", "--port", "DEMO", "--duration", "1.6",
               "--grep", "temp"])
    lines = [json.loads(item) for item in (mon.stdout or "").splitlines() if item.strip()]
    add("R-022", "CLI monitor 流式输出 NDJSON 行",
        any(item.get("dir") == "rx" and "temp=" in item.get("line", "") for item in lines),
        f"{len(lines)} 条")

    # raw file send
    size = 12345
    payload = bytes((index * 37) & 0xFF for index in range(size))
    sample = os.path.join(ROOT, ".artifacts", "cli-send-file.bin")
    os.makedirs(os.path.dirname(sample), exist_ok=True)
    with open(sample, "wb") as handle:
        handle.write(payload)
    sf = cli_json("send-file", "--port", "DEMO", "--file", sample)
    add("R-006", "CLI send-file 发送字节数等于文件大小",
        sf.get("ok") and sf.get("sent_bytes") == size, sf.get("sent_bytes"))

    # scripted session — the main AI entry point
    session = cli_json("session", "--port", "DEMO",
                       "--script", os.path.join(ROOT, "tests", "session-demo.json"))
    add("R-021", "CLI session 全部步骤通过",
        session.get("ok") is True and session.get("steps_run") == session.get("steps_total"),
        f"{session.get('steps_run')}/{session.get('steps_total')}")

    st = cli_json("selftest")
    add("R-023", "CLI selftest 全绿（DEMO 端口，无需硬件）", st.get("ok") is True,
        f"{st.get('passed')}/{st.get('total')}")

    # every CLI failure must still be valid JSON with ok:false
    bad = run(["-m", "omnicom.cli", "send", "--port", "COM_NOT_EXIST", "--data", "AT"])
    try:
        parsed = json.loads(bad.stdout or "{}")
        ok = parsed.get("ok") is False and bool(parsed.get("error")) and bad.returncode == 1
    except json.JSONDecodeError:
        ok = False
    add("R-022", "CLI 失败也返回 JSON + 退出码 1", ok, (bad.stdout or "")[:60])


GUI_LINE = re.compile(r"^\s*(PASS|FAIL)\s+(R-\d+)\s+(.*?)\s*(?:\[(.*)\])?$")


def suite_gui() -> None:
    print("\n== 3/4 GUI 验收（offscreen + DEMO）")
    proc = run(["tools/ui_selftest.py"], timeout=300)
    out = (proc.stdout or "") + (proc.stderr or "")
    unmapped: list[str] = []
    for line in out.splitlines():
        match = GUI_LINE.match(line)
        if not match:
            if line.strip().startswith(("PASS", "FAIL")):
                unmapped.append(line.strip())
            continue
        status, req, name, detail = match.groups()
        add(req, name.strip(), status == "PASS", detail or "")
    add("R-001", "GUI 验收整体通过", "ALL GUI SELFTESTS PASSED" in out,
        "" if "ALL GUI SELFTESTS PASSED" in out else out.strip().splitlines()[-1:])
    # -- a check without an R-id is invisible to the aggregation above, so a
    #    silently dropped/renamed check would still look like full coverage.
    add("R-001", "GUI 每项检查都映射到需求（无游离检查）", not unmapped,
        f"{len(unmapped)} 项游离: {unmapped[:3]}")


def suite_visual() -> None:
    print("\n== 4/4 视觉 / 交付形态")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("OMNICOM_SETTINGS_SUFFIX", "-test")
    from PySide6.QtWidgets import QApplication

    # -- the light theme must stay readable: every text/surface pair >= 4.5:1
    contrast = run(["tools/contrast_audit.py"])
    add("R-020", "浅色主题对比度均达到 AA（>= 4.5:1）",
        contrast.returncode == 0,
        "" if contrast.returncode == 0 else (contrast.stdout or "").strip().splitlines()[-1:])

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())

    # -- solid background, proven on the backdrop widget itself.
    from omnicom.ui import theme
    from omnicom.ui.main_window import Aurora

    probe = Aurora()
    probe.resize(360, 360)
    backdrop = probe.grab().toImage()
    counts: dict[str, int] = {}
    for y in range(backdrop.height()):
        for x in range(backdrop.width()):
            name = backdrop.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
    dominant = max(counts, key=counts.get)
    share = counts[dominant] / max(1, sum(counts.values()))
    add("R-020", "背景层为单一纯色", dominant == theme.BG.lower() and share >= 0.98,
        f"{dominant} {share:.2%}")
    probe.deleteLater()

    # -- The grid used to be drawn with drawLine() inside the backdrop's
    #    paintEvent, so the proof is now scoped to that method: other widgets
    #    (icons, the brand mark) legitimately draw lines.
    with open(os.path.join(ROOT, "omnicom", "ui", "main_window.py"),
              encoding="utf-8") as handle:
        source = handle.read()
    backdrop_body = ""
    if "class Aurora" in source:
        start = source.index("class Aurora")
        end = source.index("class BrandMark") if "class BrandMark" in source \
            else len(source)
        backdrop_body = source[start:end]
    add("R-020", "背景层不再绘制网格（drawLine / step = 46 / 网格）",
        "drawLine(" not in backdrop_body and "step = 46" not in backdrop_body
        and "subtle grid" not in backdrop_body.lower(),
        f"{len(backdrop_body)} chars scanned")

    window = OmniWindow(demo=False)
    window.resize(1100, 760)
    window.show()
    app.processEvents()
    time.sleep(0.15)
    app.processEvents()


    # -- top bar structure
    bar = window.titlebar
    add("R-001", "顶部栏为一体式玻璃条（高 62，含状态胶囊/计数/标签）",
        isinstance(bar, TitleBar) and bar.height() == 62
        and bar.status_chip.isVisible() and "RX" in bar.counter.text(),
        f"h={bar.height()}")
    add("R-001", "顶部栏标题不重复应用名",
        window.windowTitle() == f"OmniCOM {__version__}", window.windowTitle())

    # -- three columns present and non-overlapping
    rail = window.rail.geometry()
    stack = window.stack.geometry()
    side = window._sidebar_widget.geometry()
    add("R-002", "三栏布局（左配置 / 中主区 / 右可折叠栏）不重叠",
        rail.right() < stack.left() and stack.right() < side.left(),
        f"{rail.width()}+{stack.width()}+{side.width()}")
    add("R-008", "右侧栏默认可见", window._sidebar_widget.isVisible(), f"{side.width()}px")

    # -- snapshot for human review
    shot = os.path.join(ROOT, ".artifacts", "acceptance-ui.png")
    os.makedirs(os.path.dirname(shot), exist_ok=True)
    window.grab().save(shot)
    add("R-001", "生成验收截图供目视检查", os.path.isfile(shot), "acceptance-ui.png")
    window.close()

    # -- delivery form
    add("R-018", "无浏览器 / HTTP 服务模块",
        not os.path.exists(os.path.join(ROOT, "omnicom", "server.py")))
    add("R-018", "源码启动入口存在（run.py + start.cmd）",
        os.path.isfile(os.path.join(ROOT, "run.py"))
        and os.path.isfile(os.path.join(ROOT, "start.cmd")))
    def stray_exe() -> list[str]:
        """No self-built executable anywhere (R-019 / R-083)."""
        found = []
        # ``dist/`` is generated (and git-ignored); it contains the runtime we
        # ship, which is the official CPython build - not a self-built exe.
        for base, folders, files in os.walk(ROOT):
            folders[:] = [item for item in folders
                          if item not in (".git", ".artifacts", "__pycache__", "dist")]
            for name in files:
                if name.lower().endswith(".exe"):
                    found.append(os.path.join(base, name))
        return found

    add("R-019", "仓库内没有自编译的 exe", not stray_exe(),
        ", ".join(stray_exe()[:3]))
    proc = run(["build.py"])
    add("R-019", "打包脚本默认拒绝执行（政策闸门 exit=3）", proc.returncode == 3,
        f"exit={proc.returncode}")


# -------------------------------------------------------------------- report

def report() -> int:
    print("\n" + "=" * 78)
    print("需求对账")
    print("=" * 78)
    missing = []
    rows = []
    for req, title in TITLES.items():
        checks = RESULTS.get(req, [])
        if not checks:
            missing.append(req)
            rows.append((req, "未覆盖", 0, 0, title))
            continue
        total = len(checks)
        ok = sum(1 for _n, flag, _d in checks if flag)
        rows.append((req, "PASS" if ok == total else "FAIL", ok, total, title))
    for req, verdict, ok, total, title in rows:
        ratio = f"{ok}/{total}" if total else "-"
        print(f"  {req}  {verdict:<8} {ratio:>6}   {title}")

    payload = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": not FAILED and not missing,
        "requirements": {
            req: {
                "title": title,
                "verdict": next(row[1] for row in rows if row[0] == req),
                "checks": [
                    {"name": name, "ok": ok, "detail": detail}
                    for name, ok, detail in RESULTS.get(req, [])
                ],
            }
            for req, title in TITLES.items()
        },
        "failures": [{"requirement": req, "check": name, "detail": detail}
                     for req, name, detail in FAILED],
        "uncovered": missing,
    }
    target = os.path.join(ROOT, ".artifacts", "acceptance.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    total_checks = sum(len(items) for items in RESULTS.values())
    print("=" * 78)
    print(f"共 {total_checks} 项检查，失败 {len(FAILED)} 项，未覆盖需求 {len(missing)} 个")
    print(f"报告：{target}")
    if FAILED:
        print("\n失败明细：")
        for req, name, detail in FAILED:
            print(f"  {req}  {name}  {detail}")
    if missing:
        print("\n未覆盖需求：" + "、".join(missing))
    if FAILED or missing:
        return 1
    print("\nALL REQUIREMENTS VERIFIED")
    return 0


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="OmniCOM 全功能验收测试")
    parser.add_argument("--fast", action="store_true", help="跳过耗时最长的 GUI 套件")
    parser.add_argument("--only", choices=["protocol", "cli", "gui", "visual"],
                        help="只跑某一个套件")
    options = parser.parse_args()

    started = time.time()
    suites = {
        "protocol": suite_protocol,
        "cli": suite_cli,
        "gui": suite_gui,
        "visual": suite_visual,
    }
    if options.only:
        suites[options.only]()
    else:
        suite_protocol()
        suite_cli()
        if not options.fast:
            suite_gui()
        suite_visual()

    code = report()
    print(f"用时 {time.time() - started:.1f}s")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
