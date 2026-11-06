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
    "R-062": "屏幕常亮开关（每次启动为关、关闭时释放、电源方案本就永不关屏时明确告知）",
    "R-063": "快捷启动上下两区 + 窗口实例重命名 + 长名称完整显示",
    "R-064": "快捷启动上下两区支持拖拽排序并持久化",
    "R-065": "上排删除只删快捷方式、下排删除只结束该窗口",
    "R-066": "收发双向记录到文件 + 随时清空并继续记录",
    "R-067": "左栏底部不再重复串口/流控/版本信息",
    "R-068": "左栏默认宽度与右侧栏一致",
    "R-069": "版本号为 1.1.5",
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
    "R-085": "任务栏最小化/恢复有收放动画（不是瞬间切换）",
    "R-086": "便携包内含面向 AI 的 CLI 说明（MIT 许可）",
    "R-087": "日志超宽行自动换行（无横向滚动条）",
    "R-088": "设置写文件、多窗口独立（不用注册表）",
    "R-089": "日志里 Ctrl+C 可复制（多行是真正换行）",
    "R-090": "发送框上键取历史（到顶触发，↓ 回草稿）",
    "R-091": "终端右键菜单可粘贴到串口",
    "R-092": "命令批量勾选：移动分组 / 删除",
    "R-093": "多行粘贴可指定导入分组",
    "R-094": "回车发送开关（Shift+回车换行）",
    "R-095": "命令只有左键点击才发送（右键不发送）",
    "R-096": "日志里 Ctrl+滚轮缩放字号，只改日志",
    "R-097": "一键创建桌面快捷方式（工具名 + 软件图标）",
    "R-098": "界面与日志字体/字号分开设置",
    "R-099": "打开/发送文件按钮与进度条并进发送区",
    "R-100": "标题栏产品名与标语紧凑且垂直居中",
    "R-102": "清空 / 保存视图放在左栏，用细线与记录操作分开",
    "R-103": "打开/发送文件按钮紧跟在发送按钮下面（同一列）",
    "R-104": "同一组控件的字号与开关尺寸统一",
    "R-105": "从任务栏恢复是瞬时的，不缩放不拉伸",
    "R-106": "进度条与文件名同一行，文件名靠右显示",
    "R-107": "后台检查 GitHub 新版本（红点 + NEW，失败不弹窗）",
    "R-108": "更新只覆盖程序文件，绝不动用户数据",
    "R-109": "更新检查是最后才做的最低优先级任务",
    "R-110": "\\r\\n 选项框与 HEX / 回车开关组成同高的一组",
    "R-113": "发送文件与停止是同一个按钮（发送中切换）",
    "R-114": "HEX 与回车发送的开关左右对齐",
    "R-115": "超长内容换行时首行仍带时间戳与方向",
    "R-116": "发送历史有上限（默认 50、最多 300）且可在首选项清除",
    "R-117": "端口列表显示端口号 + 设备名",
    "R-118": "「清除窗口 / 保存窗口」有自己的「窗口」标题行",
    "R-119": "多窗口多串口：CLI 按串口绑定窗口，窗口关口则 CLI 解绑",
    "R-120": "DTR / RTS 默认关闭（打开串口不复位板子）",
    "R-121": "可以用本地升级包离线更新",
    "R-122": "一次调用只输出一个 JSON 文档，退出码与 ok 一致",
    "R-123": "stdout / stderr 强制 UTF-8",
    "R-124": "--settle / --ok-if-silent：无 marker 的命令不再空等",
    "R-125": "同一串口的 CLI 调用串行化（端口锁）",
    "R-126": "--repeat / --interval / --ports：一次进程多个采样",
    "R-127": "新命令默认不勾选「依次发送」",
    "R-128": "多行粘贴里 # 之后是备注，命令部分不留尾随空格",
    "R-129": "命令字段名统一为「命令 / Command」（不再叫 AT / CMD）",
    "R-130": "按住命令拖动可改分组与排序",
    "R-131": "发送后输入框不清空，连续重复只记一条历史",
    "R-132": "扩展命令列表里的字段名统一改成「命令 / Command」",
    "R-133": "拖动时不再显示「拖到这里移出分组」等提示文字",
    "R-134": "历史记录：框里已是上一条时按一次上键就取到更早的一条",
    "R-135": "扩展命令的顺序重启后仍然保留（锁定 + 合并写回）",
    "R-136": "批量操作支持单组 / 多组 / 全部命令的范围选择",
    "R-137": "分组标题上也有垃圾桶，走与右键一致的删除流程",
    "R-138": "窗口四边有 1 px 深色描边（看得出窗口边界）",
    "R-139": "窗口外圈有柔和阴影（不只是黑边，像一块浮起来的板子）",
    "R-140": "拖标题栏到屏幕顶部最大化，最大化时往下拖还原到鼠标下",
    "R-141": "两个展开分组之间只留一段行距（不是一大块空白）",
    "R-142": "随包文档里的版本声明必须等于 __version__",
    "R-143": "检查更新要有进度、有结论、有当前版本，失败要说明原因",
    "R-144": "搜索到底跟随、上滑停止，无搜索专用开关或气泡",
    "R-145": "更新检查直接读取发布页，不访问 GitHub API",
    "R-146": "搜索侧栏单行可横向滚动，仅全屏完整换行且无水平滚动",
    "R-147": "独立动态多通道 ASCII 实时曲线插件",
    "R-148": "独立插件商店、版本状态、更新红点及安全安装卸载",
    "R-149": "曲线宽布局、仓库内插件目录及首选项多来源聚合",
    "R-150": "左栏自动滚动单次跳底、单项保存与点击响应",
    "R-151": "实时曲线真正绘出（固件前缀可解析、曲线不被网格遮挡）",
    "R-152": "全软件右键菜单为浅色主题，无黑底白字",
    "R-153": "全屏 / 还原瞬时切换（滑动动画已撤销）且不缩小窗口",
    "R-154": "关键字搜索支持按完整日期时间范围过滤",
    "R-155": "左栏与发送栏开关每次开启都回到默认，不再记忆",
    "R-156": "CLI 完整支持 DTR/RTS/CTS/DSR 控制线读写与复位脉冲",
    "R-157": "首选项下拉框/数值框未点开时滚轮不改配置",
    "R-158": "搜索时间输入框与相邻控件同外观，日历弹层浅色主题",
    "R-159": "左栏开关只有文字与开关本体可点，中间空白不生效",
    "R-160": "点击关闭按钮窗口立刻消失，配置落盘在隐藏之后",
    "R-161": "可拖拽数据看板（曲线/仪表盘/数值/进度条/指示灯，离线 ECharts）",
    "R-162": "自动重连只连回同一台设备（USB 序列号 / 拓扑位置）",
    "R-163": "最大化 / 还原 / Aero Snap 即时完成且无卡顿",
    "R-164": "曲线控件历史至少保留 5000 帧，不因降采样丢细节",
    "R-165": "看板卡片 × 真正删除控件，页面到 Python 的回传不丢失",
    "R-166": "指示灯圆点直径放大一倍且保持正圆",
    "R-167": "运行按钮配色对齐串口开关（空闲白色 / 运行主题色）",
    "R-168": "日志窗口 20 MB 以上清除立即生效不卡顿",
    "R-169": "看板卡片任何时候都能拖动，不需要先勾选「编辑」",
    "R-170": "曲线横轴纵轴独立缩放、刷新不重置、不误触拖动",
    "R-171": "看板禁止 Ctrl+滚轮缩放整个界面",
    "R-172": "看板新增进度条控件，按 min..max 展示当前值",
    "R-173": "看板去掉开关控件与编辑开关，网格背景常显",
    "R-174": "发布包不再内置任何插件，插件只从插件商店安装",
    "R-175": "搜索结果全屏切换不卡顿（行高自算并缓存，不逐行排版）",
    "R-176": "看板默认且始终为运行状态，按钮为「暂停/继续」，暂停状态不记忆",
    "R-177": "按时间过滤开关不闪烁（时间块一次布局到位）",
    "R-178": "插件商店每个插件带版本下拉框，可安装指定版本（含降级）",
    "R-179": "搜索结果已有数据时开关「按时间过滤」不卡顿（不重建已有行）",
    "R-180": "插件商店插件名加粗，并与介绍之间有分隔线",
    "R-181": "插件商店检查更新不再闪现空白小窗口（徽标不再变成顶层窗口）",
    "R-182": "数据看板五种控件都必须显示真实数值，不得留着 --",
    "R-183": "快捷启动插件有回归测试（上排快捷方式、下排运行中窗口、计数与改名）",
}


def readme_text() -> str:
    """Both READMEs joined, so a licence/footer check covers either language."""
    parts = []
    for name in ("README.md", "README.en.md"):
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                parts.append(handle.read())
    return "\n".join(parts)


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
    print("\n== 1/6 协议环回（X / Y / ZMODEM）")
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
    print("\n== 2/6 CLI（AI 接口）")

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

    # -- 1.1.4 contract: one document, UTF-8, settle, lock, batch ----------
    # ``--expect`` that never matches used to print the result *and* an error
    # document, so ``json.loads(stdout)`` died with "Extra data: line 2"
    # (L-150).  A stream only makes sense for --repeat/--ports.
    miss = run(["-m", "omnicom.cli", "send", "--port", "DEMO", "--data", "hi",
                "--wait", "0.4", "--expect", "NOPE"])
    documents = [line for line in (miss.stdout or "").splitlines() if line.strip()]
    try:
        one = json.loads(miss.stdout or "{}")
    except json.JSONDecodeError:
        one = {}
    add("R-122", "等待预期内容超时也只输出一个 JSON 文档",
        len(documents) == 1 and one.get("ok") is False,
        f"{len(documents)} 个文档")
    add("R-122", "退出码与 ok 一致（ok=false -> 1）",
        miss.returncode == 1, miss.returncode)
    good = run(["-m", "omnicom.cli", "send", "--port", "DEMO", "--data", "AT",
                "--wait", "1", "--expect", "OK"])
    add("R-122", "退出码与 ok 一致（ok=true -> 0）",
        good.returncode == 0, good.returncode)

    # ``run()`` decodes with errors="replace", so a cp936 byte shows up as
    # U+FFFD instead of raising: that is exactly the old symptom (L-147).
    add("R-123", "stdout 是 UTF-8（中文错误消息没有替换字符）",
        "\ufffd" not in (miss.stdout or ""), (miss.stdout or "")[:40])
    add("R-123", "中文错误消息完整到达调用方",
        "等待预期内容超时" in (miss.stdout or ""), (miss.stdout or "")[:40])

    # --settle must cut the read short instead of burning the whole --wait.
    started = time.monotonic()
    settle = cli_json("send", "--port", "DEMO", "--data", "ping",
                      "--wait", "3", "--settle", "0.2")
    settle_wall = time.monotonic() - started
    add("R-124", "--settle 收到数据后提前返回（不等满 --wait）",
        settle.get("ok") is True and settle_wall < 2.0,
        f"{settle_wall:.2f}s")
    add("R-124", "结果里带 silent 字段说明是否收到数据",
        settle.get("silent") is False and one.get("silent") is False,
        settle.get("silent"))

    # --ok-if-silent only forgives *silence*; a real mismatch stays a failure.
    silent_ok = cli_json("send", "--port", "DEMO", "--data", "hello",
                         "--wait", "0.4", "--expect", "ZZZ", "--ok-if-silent")
    add("R-124", "--ok-if-silent 不掩盖真正的内容不匹配",
        silent_ok.get("ok") is False, silent_ok.get("ok"))

    # --repeat: one process, one port open, one JSON line per sample.
    batch = run(["-m", "omnicom.cli", "send", "--port", "DEMO", "--data", "ping",
                 "--wait", "0.4", "--expect", "OK", "--repeat", "3",
                 "--interval", "0.05"])
    samples = [json.loads(item) for item in (batch.stdout or "").splitlines()
               if item.strip()]
    add("R-126", "--repeat 输出 NDJSON，每个采样一行",
        len(samples) == 3 and [item.get("sample") for item in samples] == [1, 2, 3],
        f"{len(samples)} 行")
    add("R-126", "--repeat 全部成功且退出码 0",
        batch.returncode == 0 and all(item.get("ok") for item in samples),
        batch.returncode)
    # ``notices`` only grows on open, so three identical notice lists mean the
    # port was opened once - which is the point of batching (L-151).
    add("R-126", "--repeat 只打开一次串口",
        len({tuple(item.get("notices") or []) for item in samples}) == 1
        and bool(samples and samples[0].get("notices")),
        (samples[0].get("notices") or [""])[0] if samples else "")

    # --ports parses a comma list; a single value still yields one document.
    fan = cli_json("send", "--ports", "DEMO", "--data", "ping", "--wait", "0.3")
    add("R-126", "--ports 支持逗号分隔的端口列表",
        fan.get("ok") is True and fan.get("port") == "DEMO", fan.get("port"))

    # Concurrent CLI calls on one port must queue, not fight (L-149).
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    procs = [subprocess.Popen(
        [sys.executable, "-m", "omnicom.cli", "send", "--port", "DEMO",
         "--data", "ping", "--wait", "0.5"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=child_env)
        for _ in range(3)]
    outputs = [proc.communicate(timeout=180) for proc in procs]
    add("R-125", "同一端口并发调用全部成功（没有互相抢占）",
        all(proc.returncode == 0 for proc in procs),
        [proc.returncode for proc in procs])
    add("R-125", "并发调用各自仍只输出一个文档",
        all(len((out or "").strip().splitlines()) == 1 for out, _ in outputs),
        "每个进程 1 行")

    # ---- R-156: control lines (DTR / RTS / CTS / DSR) --------------------
    # Read: every pin is reported, and the two inputs are booleans once a
    # handle is open (``None`` would mean "the driver cannot see this line").
    pins = cli_json("lines", "--port", "DEMO", "--direct")
    add("R-156", "lines 报告 DTR/RTS/CTS/DSR 四根线",
        pins.get("ok") is True
        and {"dtr", "rts", "cts", "dsr"} <= set(pins.get("lines") or {}),
        pins.get("lines"))
    add("R-156", "打开串口时 DTR/RTS 默认仍为低（R-120 不被破坏）",
        (pins.get("lines") or {}).get("dtr") is False
        and (pins.get("lines") or {}).get("rts") is False,
        pins.get("lines"))

    # Drive: ``--line-dtr on`` must actually raise the pin and report it.
    raised = cli_json("lines", "--port", "DEMO", "--direct", "--line-dtr", "on")
    add("R-156", "--line-dtr on 就地拉高 DTR",
        (raised.get("lines") or {}).get("dtr") is True
        and (raised.get("applied") or {}).get("dtr") is True,
        raised.get("lines"))
    add("R-156", "只改 DTR 时 RTS 保持不动",
        (raised.get("lines") or {}).get("rts") is False, raised.get("lines"))

    # A bad value must be refused, not silently coerced to False.  argparse
    # rejects it before ``line_value`` runs, which is the stronger outcome: the
    # process leaves with a non-zero code and never emits a success document.
    bad = run(["-m", "omnicom.cli", "lines", "--port", "DEMO", "--direct",
               "--line-dtr", "maybe"])
    add("R-156", "非法控制线取值被拒绝（不静默当作 off）",
        bad.returncode != 0 and '"ok": true' not in (bad.stdout or ""),
        f"exit={bad.returncode}")

    # --dsrdtr reaches the opts dict instead of being dropped (L-205).
    dsr = cli_json("lines", "--port", "DEMO", "--direct", "--dsrdtr")
    add("R-156", "--dsrdtr 出现在串口选项中",
        (dsr.get("opts") or {}).get("dsrdtr") is True, dsr.get("opts"))
    add("R-156", "默认不开启 --dsrdtr",
        (pins.get("opts") or {}).get("dsrdtr") is False, pins.get("opts"))

    # reset: both edges in order, and the pin ends high.
    pulse = cli_json("reset", "--port", "DEMO", "--direct", "--low-ms", "40")
    edges = pulse.get("edges") or []
    add("R-156", "reset 产生低->高两个沿",
        pulse.get("ok") is True and [item.get("edge") for item in edges]
        == ["low", "high"], [item.get("edge") for item in edges])
    add("R-156", "reset 结束时 DTR 为高",
        (pulse.get("lines") or {}).get("dtr") is True, pulse.get("lines"))

    # The scripted ``line`` / ``pulse`` verbs must work without hardware.
    script_path = os.path.join(ROOT, ".artifacts", "r156-lines.json")
    os.makedirs(os.path.dirname(script_path), exist_ok=True)
    with open(script_path, "w", encoding="utf-8") as handle:
        json.dump({"port": "DEMO", "steps": [
            {"line": {"dtr": "on", "rts": "on"}},
            {"lines": True},
            {"pulse": {"line": "dtr", "low_ms": 30}},
        ]}, handle)
    out = run(["-m", "omnicom.cli", "session", "--port", "DEMO", "--direct",
               "--script", script_path])
    payload = json.loads((out.stdout or "").strip() or "{}")
    results = payload.get("results") or []
    add("R-156", "脚本 line 步骤能拉高两根线",
        payload.get("ok") is True and bool(results)
        and (results[0].get("lines") or {}).get("dtr") is True
        and (results[0].get("lines") or {}).get("rts") is True,
        results[0] if results else "")
    add("R-156", "脚本 lines 步骤能读回控制线",
        len(results) > 1
        and {"dtr", "rts", "cts", "dsr"} <= set(results[1].get("lines") or {}),
        results[1] if len(results) > 1 else "")
    add("R-156", "脚本 pulse 步骤产生两个沿",
        len(results) > 2 and [item.get("level")
                              for item in (results[2].get("edges") or [])]
        == [False, True],
        results[2] if len(results) > 2 else "")

    # A scripted ``line`` step with a read-only pin or a bad word must fail the
    # step.  Filtering the key silently reported ``ok: true`` with an empty
    # patch, so ``{"line": {"cts": true}}`` looked like a successful reset.
    # A session stops at the first failed step (steps_run == 1), so each bad
    # step needs its own script.
    def scripted_line(spec: dict) -> dict:
        with open(script_path, "w", encoding="utf-8") as handle:
            json.dump({"port": "DEMO", "steps": [{"line": spec}]}, handle)
        result = run(["-m", "omnicom.cli", "session", "--port", "DEMO",
                      "--direct", "--script", script_path])
        doc = json.loads((result.stdout or "").strip() or "{}")
        steps = doc.get("results") or []
        return steps[0] if steps else {}

    step = scripted_line({"cts": "on"})
    add("R-156", "脚本 line 步骤拒绝只读线 CTS",
        step.get("ok") is False and "dtr" in str(step.get("error")), step)
    step = scripted_line({"dtr": "maybe"})
    add("R-156", "脚本 line 步骤拒绝非法取值",
        step.get("ok") is False and "不合法" in str(step.get("error")), step)


GUI_LINE = re.compile(r"^\s*(PASS|FAIL)\s+(R-\d+)\s+(.*?)\s*(?:\[(.*)\])?$")


def suite_gui() -> None:
    print("\n== 3/6 GUI 验收（offscreen + DEMO）")
    # The GUI suite grew past 380 checks; on this machine a cold run takes
    # ~6 min (font loading + 3 s network probes + DEMO port traffic), so
    # a 300 s cap turned a healthy run into TimeoutExpired (L-137).
    proc = run(["tools/ui_selftest.py"], timeout=900)
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


# ------------------------------------------------------- multi-window CLI
def _spawn_window(env: dict) -> subprocess.Popen:
    """A real OmniCOM window with a DEMO port, sharing ``env``'s data dir."""
    return subprocess.Popen(
        [sys.executable, "-m", "omnicom", "--demo"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def _endpoint_files(folder: str) -> list[str]:
    """Every endpoint file a window published (one per process)."""
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, name) for name in sorted(os.listdir(folder))
            if name.startswith("window-") and name.endswith(".json")]


def _wait_endpoints(folder: str, count: int, limit: float = 60.0) -> list[str]:
    end = time.time() + limit
    while time.time() < end:
        files = _endpoint_files(folder)
        if len(files) >= count:
            return files
        time.sleep(0.3)
    return _endpoint_files(folder)


def _read_endpoints(folder: str) -> list[dict]:
    """Every published endpoint record (skips files being rewritten)."""
    items: list[dict] = []
    for path in _endpoint_files(folder):
        with contextlib.suppress(OSError, ValueError):
            with open(path, encoding="utf-8") as handle:
                items.append(json.load(handle))
    return items


def _wait_owner(folder: str, serial: str, limit: float = 20.0) -> list[dict]:
    """Wait until ``serial`` is published as owned, or the deadline passes.

    Publishing happens on the GUI thread, so it is legitimately a few
    milliseconds behind the CLI reply - the endpoint must never be read with
    the assumption that it is already updated (R-119).
    """
    end = time.time() + limit
    items: list[dict] = []
    while time.time() < end:
        items = _read_endpoints(folder)
        if any(item.get("serial") == serial for item in items):
            return items
        time.sleep(0.2)
    return items


def suite_multiwindow() -> None:
    """R-119: two windows, two ports, one CLI that must pick the right one.

    Every window publishes its own endpoint file plus the serial port it
    owns, so a CLI asking for COM5 must land in the window that has COM5 -
    not in whichever window started first, which used to mix two devices in
    one log.
    """
    print("\n== 4/6 多窗口 CLI 绑定（两个真实窗口）")
    folder = os.path.join(_DATA_DIR, "multiwindow")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["OMNICOM_DATA_DIR"] = folder
    env["OMNICOM_SETTINGS_SUFFIX"] = ""
    endpoint_dir = os.path.join(folder, "control")

    def cli(*args: str, timeout: int = 180) -> dict:
        proc = subprocess.run([sys.executable, "-m", "omnicom.cli", *args],
                              cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
        lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
        try:
            return json.loads(lines[-1]) if lines else {"ok": False}
        except json.JSONDecodeError:
            return {"ok": False, "error": (proc.stdout or "")[-120:]}

    windows: list[subprocess.Popen] = []
    try:
        windows.append(_spawn_window(env))
        first = _wait_endpoints(endpoint_dir, 1)
        add("R-119", "第一个窗口发布自己的控制端点（每窗口一个文件）",
            len(first) == 1, [os.path.basename(item) for item in first])

        windows.append(_spawn_window(env))
        both = _wait_endpoints(endpoint_dir, 2)
        add("R-119", "第二个窗口另发一个端点，两个窗互不抢占",
            len(both) >= 2, [os.path.basename(item) for item in both])

        opened = cli("open", "--port", "DEMO")
        add("R-119", "CLI 让其中一个窗口打开 DEMO", opened.get("ok") is True,
            opened.get("error", ""))

        infos = _wait_owner(endpoint_dir, "DEMO")
        owners = [item for item in infos if item.get("serial") == "DEMO"]
        add("R-119", "端点文件公布「本窗口正在用哪个串口」",
            len(owners) == 1 and bool(owners[0].get("connected")),
            [(item.get("pid"), item.get("serial")) for item in infos])
        if not owners:
            raise RuntimeError("没有窗口公布 DEMO，后续路由检查无法进行")
        add("R-119", "端点带窗口槽位号，Windows 之间可区分",
            all(int(item.get("slot") or 0) > 0 for item in infos),
            [item.get("slot") for item in infos])

        sent = cli("send", "--port", "DEMO", "--data", "AT", "--wait", "0.6")
        add("R-119", "CLI 按串口绑定到「已打开该串口」的窗口",
            sent.get("ok") is True and sent.get("window_pid") == owners[0].get("pid"),
            f"pid={sent.get('window_pid')} expect={owners[0].get('pid')}")

        hijack = cli("send", "--port", "COM_NOT_THERE",
                     "--window", str(owners[0].get("pid")))
        add("R-119", "CLI 不会把一个窗口的串口换成另一个",
            hijack.get("ok") is False and "不能同时打开" in str(hijack.get("error")),
            str(hijack.get("error") or "")[:80])

        closed = cli("close", "--port", "DEMO")
        add("R-119", "CLI 关闭串口就是关掉窗口里那个串口",
            closed.get("ok") is True and closed.get("connected") is False,
            closed.get("error", ""))
        # A brand-new CLI call may legitimately reopen the port, so the
        # binding rule is proved on a *running* session: it must stop the
        # moment the window closes the port, not keep writing into nothing.
        reopened = cli("open", "--port", "DEMO")
        add("R-119", "串口关掉后可以再次打开（新的一次 CLI 调用）",
            reopened.get("ok") is True, reopened.get("error", ""))
        steps_path = os.path.join(folder, "steps.json")
        with open(steps_path, "w", encoding="utf-8") as handle:
            json.dump({"steps": [
                {"send": "AT", "wait": 0.4},
                {"delay": 5.0},
                {"send": "AT", "wait": 0.4},
            ]}, handle)
        session = subprocess.Popen(
            [sys.executable, "-m", "omnicom.cli", "session",
             "--port", "DEMO", "--script", steps_path],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace")
        time.sleep(2.0)
        cli("close", "--port", "DEMO")
        try:
            out, _err = session.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            session.kill()
            out, _err = session.communicate()
        result: dict = {}
        for line in reversed((out or "").splitlines()):
            with contextlib.suppress(ValueError):
                result = json.loads(line)
                break
        add("R-119", "窗口关掉串口后正在运行的 CLI 会话立刻结束（不会静默继续写）",
            result.get("ok") is False and "绑定结束" in str(result.get("error") or ""),
            str(result.get("error") or f"ok={result.get('ok')}")[:90])
    finally:
        for proc in windows:
            with contextlib.suppress(Exception):
                proc.kill()
        for proc in windows:
            with contextlib.suppress(Exception):
                proc.wait(20)
        time.sleep(1.5)

    info = cli("info", timeout=60)
    add("R-119", "窗口都退出后残留端点被清理，CLI 回到直连模式",
        info.get("window", {}).get("running") is False
        and info.get("window", {}).get("mode") == "direct",
        info.get("window"))
    stale = cli("send", "--port", "DEMO", "--data", "AT", "--wait", "0.4")
    add("R-119", "没有窗口时 CLI 自己开串口（via=direct）",
        stale.get("ok") is True and stale.get("via") == "direct",
        stale.get("error", ""))


# --------------------------------------------------------- local package
def _make_pyz(path: str, version: str) -> None:
    """A minimal program archive carrying only a version string."""
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("omnicom/__init__.py",
                         f'"""probe."""\n\n__version__ = "{version}"\n')


def _make_release(folder: str, version: str) -> str:
    """A release zip shaped like the real one (one top folder + omnicom-data)."""
    staging = os.path.join(folder, f"src-{version}")
    shutil.rmtree(staging, ignore_errors=True)
    bundle = os.path.join(staging, "OmniCOM")
    os.makedirs(os.path.join(bundle, "omnicom-data"), exist_ok=True)
    _make_pyz(os.path.join(bundle, "OmniCOM.pyz"), version)
    with open(os.path.join(bundle, "README.txt"), "w", encoding="utf-8") as handle:
        handle.write(f"OmniCOM {version}\n")
    with open(os.path.join(bundle, "omnicom-data", "commands.json"), "w",
              encoding="utf-8") as handle:
        handle.write('{"from": "package"}')
    target = os.path.join(folder, f"OmniCOM-{version}-win64")
    return shutil.make_archive(target, "zip", staging, "OmniCOM")


def suite_local() -> None:
    """R-121: an offline release zip can be installed, and nothing user-owned moves."""
    print("\n== 5/6 本地升级包（离线更新）")
    from omnicom import update

    folder = os.path.join(_DATA_DIR, "localupdate")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    previous = os.environ.get("OMNICOM_DATA_DIR")
    os.environ["OMNICOM_DATA_DIR"] = folder    # keep staging out of the suite dir
    try:
        zip_new = _make_release(folder, "9.9.9")
        add("R-121", "能从一个 release zip 里读出真实版本号",
            update.package_version(zip_new) == "9.9.9",
            update.package_version(zip_new))
        lying = os.path.join(folder, "OmniCOM-0.0.1-win64.zip")
        shutil.copyfile(zip_new, lying)
        add("R-121", "文件被改名也按包内版本号判断（不会误显示成别的版本）",
            update.package_version(lying) == "9.9.9",
            update.package_version(lying))

        import zipfile

        plain = os.path.join(folder, "notes.zip")
        with zipfile.ZipFile(plain, "w") as archive:
            archive.writestr("hello.txt", "hi")
        refused = ""
        try:
            update.stage_local(plain)
        except OSError as error:
            refused = str(error)
        add("R-121", "不是 OmniCOM 包的 zip 会被拒绝（不会把程序目录覆盖坏）",
            "OmniCOM.pyz" in refused, refused)

        # a bundle holding the user's own data, to prove nothing is lost
        target = os.path.join(folder, "bundle")
        os.makedirs(os.path.join(target, "omnicom-data"), exist_ok=True)
        with open(os.path.join(target, "omnicom-data", "commands.json"), "w",
                  encoding="utf-8") as handle:
            handle.write('{"from": "user"}')
        os.makedirs(os.path.join(target, "plugins", "mine"), exist_ok=True)
        with open(os.path.join(target, "plugins", "mine", "plugin.json"), "w",
                  encoding="utf-8") as handle:
            handle.write('{"name": "mine"}')

        staged = update.stage_local(zip_new)
        add("R-121", "本地 zip 走和下载一样的打包流程（进入待安装状态）",
            staged.get("version") == "9.9.9" and update.pending() is not None,
            staged.get("version"))
        written = update.apply_pending(target)
        add("R-121", "安装后程序文件被替换",
            "OmniCOM.pyz" in [item.replace("\\", "/") for item in written]
            and os.path.isfile(os.path.join(target, "OmniCOM.pyz")),
            sorted(written))
        user_path = os.path.join(target, "omnicom-data", "commands.json")
        with open(user_path, encoding="utf-8") as handle:
            user_data = handle.read()
        add("R-121", "本地升级同样不动用户数据目录",
            "user" in user_data, user_data[:40])
        mine_path = os.path.join(target, "plugins", "mine", "plugin.json")
        with open(mine_path, encoding="utf-8") as handle:
            mine = handle.read()
        add("R-121", "用户自己的插件不会被本地升级覆盖",
            "mine" in mine, mine[:40])
        add("R-121", "安装完成后待安装标记被清掉", update.pending() is None)

        same_zip = _make_release(folder, __version__)
        same = update.stage_local(same_zip)
        add("R-121", "装同一个版本不被禁止，但会标记为「就是当前版本」",
            same.get("version") == __version__ and same.get("same_version") is True,
            same.get("version"))
        update.clear_pending()

        older_zip = _make_release(folder, "0.0.1")
        older = update.stage_local(older_zip)
        add("R-121", "Older packages are accepted as downgrades, not the same version",
            older.get("version") == "0.0.1" and older.get("downgrade") is True
            and older.get("same_version") is False)
        update.apply_pending(target)
        with (
            zipfile.ZipFile(os.path.join(target, "OmniCOM.pyz")) as installed,
            open(user_path, encoding="utf-8") as current_data,
            open(mine_path, encoding="utf-8") as current_plugin,
        ):
            actual_version = update.VERSION_RE.search(
                installed.read("omnicom/__init__.py").decode("utf-8")).group(1)
            preserved = current_data.read() == user_data and current_plugin.read() == mine
        add("R-121", "A downgrade installs the older archive and preserves user files",
            actual_version == "0.0.1" and update.pending() is None and preserved)

        from pathlib import Path
        from unittest.mock import patch

        from omnicom.update_worker import apply_files

        source, destination = Path(folder) / "transaction-source", Path(folder) / "transaction-target"
        source.mkdir()
        destination.mkdir()
        for name in ("first.txt", "second.txt"):
            (source / name).write_bytes(b"new")
            (destination / name).write_bytes(b"old")
        (source / "added.txt").write_bytes(b"added")
        replace = os.replace
        commits = []

        def fail_second_commit(source, target):
            if Path(source).parent.name == "new":
                commits.append(str(target))
                if len(commits) == 2:
                    raise PermissionError("injected file lock")
            return replace(source, target)

        refused = False
        with patch("omnicom.update_worker.os.replace", side_effect=fail_second_commit):
            try:
                apply_files(source, destination)
            except PermissionError:
                refused = True
        add("R-108", "A mid-commit failure restores original files and removes new files",
            refused and len(commits) == 2
            and all((destination / name).read_bytes() == b"old"
                    for name in ("first.txt", "second.txt"))
            and not (destination / "added.txt").exists()
            and not list(destination.glob(".omnicom-update-*")))
    finally:
        if previous is None:
            os.environ.pop("OMNICOM_DATA_DIR", None)
        else:
            os.environ["OMNICOM_DATA_DIR"] = previous


def suite_installer(bundle: str, older_package: str | None = None) -> None:
    """Exercise the shipped updater with real embedded Python and Qt locks."""
    import hashlib
    import socket
    import zipfile
    from pathlib import Path

    from omnicom import update as update_module
    from omnicom.update_worker import parent_handle, wait_parent

    original = Path(bundle).resolve()
    if not (original / "runtime/python.exe").is_file():
        add("R-108", "Portable runtime is available", False, str(original))
        return
    root = Path(_DATA_DIR) / "installer"
    root.mkdir(parents=True, exist_ok=True)
    previous_version = "0.0.1"
    modes = ("local", "download", "locked", "defer")
    if older_package:
        assert Path(older_package).is_file()
        assert update_module.parse_version(update_module.package_version(older_package)) < update_module.parse_version(__version__)
        modes += ("downgrade",)
    for mode in modes:
        case = root / mode
        target = case / "OmniCOM"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(original, target, ignore=shutil.ignore_patterns(
            "omnicom-data", ".artifacts", ".omnicom-update-*"))
        with zipfile.ZipFile(original / "OmniCOM.pyz") as archive:
            with zipfile.ZipFile(target / "OmniCOM.pyz", "w") as old:
                for item in archive.infolist():
                    content = archive.read(item)
                    if item.filename == "omnicom/__init__.py":
                        initial_version = __version__ if mode == "downgrade" else previous_version
                        content = f'__version__ = "{initial_version}"\n'.encode()
                    old.writestr(item, content)
        protected = {}
        for name in ("omnicom-data/commands.json", "omnicom-data/settings.ini",
                     "omnicom-data/logs/capture.txt", "plugins/mine/plugin.json"):
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"user-owned content\r\n")
            protected[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        package = case / "release.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in original.rglob("*"):
                if path.is_file() and "omnicom-data" not in path.relative_to(original).parts:
                    archive.write(path, "OmniCOM/" + path.relative_to(original).as_posix())
            archive.writestr("OmniCOM/omnicom-data/commands.json", "not user data")
            archive.writestr("OmniCOM/plugins/mine/plugin.json", "not user plugin")
        if mode == "downgrade":
            shutil.copyfile(older_package, package)
        installed_version = update_module.package_version(str(package))
        env = dict(os.environ)
        env["OMNICOM_DATA_DIR"] = str(target / "omnicom-data")
        env["OMNICOM_SETTINGS_SUFFIX"] = "-installer-test"
        env["QT_QPA_PLATFORM"] = "offscreen"
        runtime = str(target / "runtime/python.exe")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(60)
        config = {"target": str(target), "port": listener.getsockname()[1],
                  "package": str(package), "mode": mode, "root": ROOT,
                  "version": installed_version, "snapshot": str(case / "progress.png"),
                  "trace": str(case / "progress.json")}
        probe = "CONFIG = " + repr(config) + "\n" + '''
import json,os,socket,sys,traceback
connection=socket.create_connection(('127.0.0.1',CONFIG['port']),timeout=60)
try:
    sys.path.insert(0,CONFIG['target']+'/OmniCOM.pyz')
    from omnicom import __version__,update,ui
    from PySide6.QtWidgets import QApplication,QMessageBox
    app=QApplication([])
    messages=[]
    def show(parent,title,text):
        box=QMessageBox()
        box.setWindowTitle(title)
        box.setText(text)
        box.show()
        app.processEvents()
        messages.append({'title':box.windowTitle(),'text':box.text()})
        box.close()
    QMessageBox.information=show
    QMessageBox.warning=show
    receiver=getattr(ui,'_show_update_result',None)
    if receiver:
        receiver(None)
        receiver(None)
    result={'pid':os.getpid(),'version':__version__,'messages':messages,
            'pending':update.pending(),
            'result_exists':os.path.isfile(CONFIG['target']+'/omnicom-data/updates/result.json'),
            'supports_result':receiver is not None}
    window=None
    if CONFIG['mode']=='downgrade':
        os.environ['OMNICOM_DATA_DIR']=CONFIG['target']+'/../window-probe-data'
        from omnicom.ui.main_window import OmniWindow
        from omnicom.ui.theme import app_stylesheet
        app.setStyle('Fusion')
        app.setStyleSheet(app_stylesheet())
        window=OmniWindow(demo=True)
        window.show()
        app.processEvents()
        result['window_visible']=window.isVisible()
        window.grab().save(CONFIG['target']+'/../restarted.png')
except BaseException:
    result={'pid':os.getpid(),'error':traceback.format_exc()}
connection.sendall(json.dumps(result).encode('utf-8')+b'\\n')
connection.recv(1)
connection.close()
if 'window' in globals() and window is not None:
    window.close()
    app.processEvents()
'''
        restart = [runtime, "-B", "-X", "utf8", "-c", probe]
        parent_code = "CONFIG = " + repr(config) + "\nRESTART = " + repr(restart) + "\n" + '''
import json,pathlib,sys
from PySide6 import QtCore
sys.path.insert(0,CONFIG['target']+'/OmniCOM.pyz')
from omnicom import update
if CONFIG['mode']=='download':
    path=update.download(pathlib.Path(CONFIG['package']).as_uri(),
                         str(pathlib.Path(update.updates_dir())/'download.zip'))
    update.stage(path,CONFIG['version'])
else:
    update.stage_local(CONFIG['package'])
worker=update.launch_installer(restart_command=RESTART,wait_confirmation=True,
    progress_ui={'title':'OmniCOM','message':'Installing. Please keep this window open.',
                 'complete':'Installation complete. Starting OmniCOM.',
                 'stylesheet':'','snapshot':CONFIG['snapshot'],'trace':CONFIG['trace']})
print(json.dumps({'worker':worker.pid}),flush=True)
action=input()
if action=='cancel':
    worker.cancel()
    print('DEFERRED',flush=True)
else:
    worker.confirm()
    print('VISIBLE',flush=True)
input()
'''
        locker = None
        parent = None
        try:
            if mode == "locked":
                locker = subprocess.Popen(
                    [runtime, "-B", "-c",
                     "from PySide6 import QtCore; print('LOCKED',flush=True); input()"],
                    cwd=target, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8")
                assert locker.stdout.readline().strip() == "LOCKED"
            parent = subprocess.Popen(
                [runtime, "-B", "-X", "utf8", "-c", parent_code],
                cwd=target, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8")
            ready = parent.stdout.readline()
            if not ready:
                raise AssertionError(parent.communicate(timeout=10)[1])
            worker_pid = json.loads(ready)["worker"]
            handle = parent_handle(worker_pid)
            with zipfile.ZipFile(target / "OmniCOM.pyz") as archive:
                before = archive.read("omnicom/__init__.py").decode()
            add("R-108", f"{mode}: installer waits while old Python and Qt are alive",
                (str(__version__) if mode == "downgrade" else previous_version) in before and parent.poll() is None
                and not (target / "omnicom-data/updates/result.json").exists())
            add("R-108", f"{mode}: file preparation completed before the old UI exits",
                bool(list(target.glob(".omnicom-update-*/new/OmniCOM.pyz"))))
            parent.stdin.write("cancel\n" if mode == "defer" else "confirm\n")
            parent.stdin.flush()
            if mode == "defer":
                assert parent.stdout.readline().strip() == "DEFERRED"
                wait_parent(handle, 90000)
                add("R-108", "Declining confirmation keeps the current process and version",
                    parent.poll() is None
                    and not list(target.glob(".omnicom-update-*"))
                    and (target / "omnicom-data/updates/pending.json").is_file()
                    and not (target / "omnicom-data/updates/result.json").exists())
                parent.communicate("\n", timeout=30)
                continue
            assert parent.stdout.readline().strip() == "VISIBLE"
            from PySide6.QtGui import QImage

            image = QImage(config["snapshot"])
            add("R-108", f"{mode}: independent progress window appears before the old UI exits",
                parent.poll() is None and not image.isNull()
                and len({image.pixelColor(x, y).rgba()
                         for y in range(0, image.height(), 3)
                         for x in range(0, image.width(), 3)}) > 8)
            visible = json.loads(Path(config["trace"]).read_text(encoding="utf-8"))["events"][0]
            add("R-075", f"{mode}: installer has the brand logo and taskbar/window icon",
                visible["icon"] and visible["logo"] and visible["taskbar_window"])
            parent.communicate("\n", timeout=30)
            assert parent.returncode == 0
            wait_parent(handle, 90000)
            events = json.loads(Path(config["trace"]).read_text(encoding="utf-8"))["events"]
            add("R-108", f"{mode}: progress completes and disappears before launching the app",
                [event["event"] for event in events] == ["visible", "complete", "restart"]
                and events[1]["value"] == events[1]["maximum"] == 100
                and not events[2]["visible"])
            connection, _address = listener.accept()
            with connection:
                connection.settimeout(30)
                response = connection.makefile("rb").readline()
                result = json.loads(response)
                restarted = parent_handle(result["pid"])
                connection.sendall(b"1")
            wait_parent(restarted, 30000)
            if result.get("error"):
                raise AssertionError(result["error"])
            expected = previous_version if mode == "locked" else installed_version
            add("R-108", f"{mode}: restarted process reports the actual version",
                result["version"] == expected, str(result))
            messages = result["messages"]
            if result["supports_result"]:
                add("R-108", f"{mode}: result popup contains current version exactly once",
                    len(messages) == 1 and expected in messages[0]["text"]
                    and (messages[0]["title"] == "\u66f4\u65b0\u6210\u529f") == (mode != "locked")
                    and not result["result_exists"], str(messages))
            else:
                add("R-121", "Legacy app starts without an acknowledgment or stale result file",
                    not messages and not result["result_exists"] and result.get("window_visible"),
                    str(result))
            add("R-108", f"{mode}: user files and plugins retain their hashes",
                all(hashlib.sha256((target / name).read_bytes()).hexdigest() == value
                    for name, value in protected.items()))
            if mode == "locked":
                add("R-108", "Locked Qt fails before committing and does not retry forever",
                    bool(result["pending"].get("failed"))
                    and "0.0.1" in result["version"]
                    and not list(target.glob(".omnicom-update-*")))
            else:
                with zipfile.ZipFile(package) as archive:
                    add("R-121", f"{mode}: program and runtime match the package after restart",
                        all(hashlib.sha256(archive.read(name)).digest()
                            == hashlib.sha256((target / name.removeprefix("OmniCOM/")).read_bytes()).digest()
                            for name in archive.namelist() if not name.endswith("/")
                            and name.startswith("OmniCOM/")
                            and name.split("/")[1] not in ("omnicom-data", "plugins"))
                        and result["pending"] is None)
        finally:
            listener.close()
            if parent is not None and parent.poll() is None:
                parent.communicate("\n", timeout=30)
            if locker is not None:
                locker.communicate("\n", timeout=30)
    report_path = Path(ROOT) / ".artifacts/update-selftest.json"
    report_path.write_text(json.dumps({"ok": not FAILED, "checks": RESULTS},
                                     ensure_ascii=False, indent=2), encoding="utf-8")


def suite_visual() -> None:
    print("\n== 6/6 视觉 / 交付形态")
    plugins = run(["tools/plugin_selftest.py"], timeout=180)
    for line in (plugins.stdout or "").splitlines():
        match = GUI_LINE.match(line)
        if match:
            status, req, name, detail = match.groups()
            add(req, name.strip(), status == "PASS", detail or "")
    add("R-148", "插件/曲线/换行独立回归整体通过", plugins.returncode == 0,
        "" if plugins.returncode == 0 else (plugins.stderr or "")[-1500:])
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
    # -- sample the page only: the transparent ring outside it carries the
    #    drop shadow (R-139), so the whole-widget histogram would no longer be
    #    a single colour and the check would fail for the right reason.
    page = probe.page_rect()
    counts: dict[str, int] = {}
    total = 0
    for y in range(page.top(), page.bottom() + 1):
        for x in range(page.left(), page.right() + 1):
            name = backdrop.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
            total += 1
    dominant = max(counts, key=counts.get)
    share = counts[dominant] / max(1, total)
    add("R-020", "背景层为单一纯色", dominant == theme.BG.lower() and share >= 0.98,
        f"{dominant} {share:.2%} in {page.width()}x{page.height()}")
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

    # -- R-086: an agent that only has the unzipped folder must be able to learn
    #    the whole interface.  The docs must exist, and the packaging scripts must
    #    actually copy them (source-existence alone would not prove delivery).
    docs_ok = []
    for doc in ("CLI.md", "AI-GUIDE.md"):
        path = os.path.join(ROOT, "docs", doc)
        docs_ok.append(os.path.isfile(path) and os.path.getsize(path) > 800)
    add("R-086", "仓库带 CLI.md 与 AI-GUIDE.md（给 AI 的接口说明）", all(docs_ok),
        str(docs_ok))
    packaging = ""
    for name in ("make_pyz.py", "make_dist.py"):
        with open(os.path.join(ROOT, "tools", name), encoding="utf-8") as handle:
            packaging += handle.read()
    add("R-086", "打包脚本会把 CLI.md / AI-GUIDE.md / LICENSE 一起复制",
        "AI-GUIDE.md" in packaging and "CLI.md" in packaging
        and "LICENSE" in packaging)
    # -- R-142: a version *declaration* inside a shipped file must equal
    #    __version__.  The 1.1.4 zip had to be rebuilt because the bundled
    #    AI-GUIDE.md still showed the previous release in its sample output.
    #    Only declarations are matched - prose such as "行为和 1.1.2 一致" or
    #    the interpreter version "3.13.14" is history, not a claim about this
    #    build, and flagging it would train everyone to ignore the guard.
    DECLARATIONS = (
        ("JSON 示例", re.compile(r'"version"\s*:\s*"(\d+\.\d+\.\d+)"')),
        ("版本徽章", re.compile(r"version-(\d+\.\d+\.\d+)-")),
        ("资产名", re.compile(r"OmniCOM-(\d+\.\d+\.\d+)-win64")),
    )
    shipped_docs = ("docs/AI-GUIDE.md", "docs/CLI.md", "README.md",
                    "README.en.md")
    wrong: list[str] = []
    seen: list[str] = []
    absent: list[str] = []
    for name in shipped_docs:
        path = os.path.join(ROOT, *name.split("/"))
        if not os.path.isfile(path):
            absent.append(name)
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for label, pattern in DECLARATIONS:
            for match in pattern.finditer(text):
                seen.append(f"{name}/{label}")
                if match.group(1) != __version__:
                    wrong.append(f"{name} {label} {match.group(1)}")
    add("R-142", "随包文档里的版本声明等于 __version__",
        not wrong and not absent,
        ", ".join(wrong + absent) if (wrong or absent)
        else f"{len(seen)} 处 = {__version__}")
    # A guard that scans nothing passes forever, so prove it still sees text.
    add("R-142", "版本守卫确实扫到了声明（规则失效时不能静默通过）",
        bool(seen), f"{len(seen)} 处" if seen else "一处都没扫到")
    fixture_path = os.path.join(ROOT, "tools", "make_screenshots.py")
    fixture = ""
    if os.path.isfile(fixture_path):
        with open(fixture_path, encoding="utf-8") as handle:
            fixture = handle.read()
    fixture_match = DECLARATIONS[0][1].search(fixture)
    add("R-142", "截图夹具的版本与 __version__ 一致（截图随包发布）",
        bool(fixture_match) and fixture_match.group(1) == __version__,
        fixture_match.group(1) if fixture_match else "未找到版本声明")
    license_path = os.path.join(ROOT, "LICENSE")
    licence_text = ""
    if os.path.isfile(license_path):
        with open(license_path, encoding="utf-8") as handle:
            licence_text = handle.read()
    # -- R-087: the GUI suite proves the behaviour; here the source must not
    #    regress to a sideways-scrolling log (the wrap setting lives in one
    #    place, so a silent revert would make the GUI check the only guard).
    with open(os.path.join(ROOT, "omnicom", "ui", "log_view.py"),
              encoding="utf-8") as handle:
        log_source = handle.read()
    add("R-087", "日志视图按宽度换行且关闭横向滚动条",
        "WidgetWidth" in log_source
        and "WrapAtWordBoundaryOrAnywhere" in log_source
        and "ScrollBarAlwaysOff" in log_source
        and "NoWrap" not in log_source,
        "log_view.py")
    add("R-086", "开源许可为 MIT（LICENSE + README 页脚）",
        "MIT License" in licence_text and "WITHOUT WARRANTY OF ANY KIND" in licence_text
        and "MIT" in readme_text(), "LICENSE")
    # -- R-095..R-099: the GUI suite proves the behaviour; these guards stop a
    #    silent revert of the source that makes those checks pass.
    def source_of(*parts: str) -> str:
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
            return handle.read()

    commands_source = source_of("omnicom", "ui", "commands_panel.py")
    add("R-095", "命令行只认左键释放，右键不触发发送",
        "event.button() == Qt.LeftButton" in commands_source
        and "mouseReleaseEvent" in commands_source
        and "send_requested" not in commands_source)

    log_source_full = source_of("omnicom", "ui", "log_view.py")
    window_source = source_of("omnicom", "ui", "main_window.py")
    i18n_source = source_of("omnicom", "i18n.py")
    add("R-096", "日志视图用 Ctrl+滚轮缩放并发信号给主窗口",
        "ControlModifier" in log_source_full
        and "fontZoomed" in log_source_full
        and "def zoom_font" in log_source_full
        and "fontZoomed.connect" in window_source)
    add("R-096", "缩放后的字号写进设置（display/log_px）",
        'setValue("display/log_px"' in window_source)

    shortcut_source = source_of("omnicom", "shortcut.py")
    dist_source = source_of("tools", "make_dist.py")
    add("R-097", "带创建桌面快捷方式的实现（COM 建 .lnk）",
        "create_shortcut" in shortcut_source
        and "WScript.Shell" in shortcut_source
        and "IconLocation" in shortcut_source)
    add("R-097", "打包时把一键快捷方式脚本放进便携包",
        "桌面快捷方式.cmd" in dist_source
        and "Create desktop shortcut.cmd" in dist_source
        and 'shortcut' in dist_source)
    add("R-097", "源码目录也有 create-shortcut.cmd 一键脚本",
        os.path.isfile(os.path.join(ROOT, "create-shortcut.cmd")))

    dialogs_source = source_of("omnicom", "ui", "dialogs.py")
    theme_source = source_of("omnicom", "ui", "theme.py")
    add("R-098", "首选项里界面与日志各有一套字体选择",
        "uiFontChanged" in dialogs_source and "logFontChanged" in dialogs_source
        and dialogs_source.count("_family_combo(") >= 2)
    add("R-098", "两套字体与字号在主题里分别存储",
        "def set_ui_family" in theme_source and "def set_log_family" in theme_source
        and "def set_ui_font_px" in theme_source and "def set_log_px" in theme_source
        and 'setValue("ui/font_px"' in window_source
        and 'setValue("display/log_px"' in window_source)

    add("R-102", "清空与保存视图按钮建在左栏，并用细线与记录操作分开",
        "self.btn_clear = QPushButton" in window_source
        and "self.btn_save_log = QPushButton" in window_source
        and "layout.addWidget(self._divider())" in window_source
        and "self.sec_log" not in window_source)

    add("R-100", "品牌两行按字形高度钉住并由上下留白居中",
        "_fit_brand_lines" in window_source
        and "tightBoundingRect" in window_source
        and "names.addStretch(1)" in window_source
        and "setContentsMargins(0, 0, 0, 2)" in window_source)

    send_source = source_of("omnicom", "ui", "send_panel.py")
    add("R-099", "文件按钮与发送按钮同列，进度条与文件名进右侧行",
        "side.addWidget(self.btn_send)" in send_source
        and "side.addWidget(self.btn_open_file)" in send_source
        and "side.addWidget(self.btn_file)" in send_source
        and "foot.addWidget(self.file_row, 3)" in send_source)

    add("R-103", "打开/发送文件按钮紧跟在发送按钮下面（同一列）",
        "side.addWidget(self.btn_send)" in send_source
        and send_source.index("side.addWidget(self.btn_open_file)")
        > send_source.index("side.addWidget(self.btn_send)"))

    add("R-104", "HEX 与回车开关用同一套紧凑尺寸（不跟主开关混用）",
        "OPT_SWITCH_W" in send_source and "OPT_SWITCH_H" in send_source
        and "def _option_row" in send_source
        and "setFixedHeight(OPT_ROW_H)" in send_source)

    add("R-105", "恢复窗口时不跑动画（只有最小化淡出）",
        "def animate_show" in window_source
        and "setWindowOpacity(1.0)" in window_source
        and "self._stop_motion()" in window_source
        and "def _settled_rect" in window_source)
    add("R-105", "激活变化不再取消最小化（只由 SC_RESTORE 取消）",
        # Only a state change is reacted to; an ActivationChange branch here is
        # exactly what ate real minimize clicks (L-132).
        "if event.type() != QEvent.WindowStateChange:" in window_source
        and "QEvent.ActivationChange" not in window_source
        and "SC_RESTORE" in window_source)

    add("R-105", "最小化只淡出、绝不缩放窗口（缩放会留残影并卡死按钮）",
        "def _motion_group(self, fade_from" in window_source
        and "self._motion_group(current, 0.0, self.MINIMIZE_MS)" in window_source
        and "MOTION_SCALE" not in window_source
        and "def _motion_rect" not in window_source
        and "_animatable" not in window_source)
    add("R-105", "先把窗口最小化，再恢复几何与不透明度",
        "self.showMinimized()" + "\n" + "        self._restore_geometry()"
        in window_source.replace("\r\n", "\n"))

    add("R-106", "文件名在进度条同一行并靠右对齐",
        "Qt.AlignRight | Qt.AlignVCenter" in send_source
        and "file_box.addWidget(self.file_progress" in send_source
        and "file_box.addWidget(self.file_hint, 1)" in send_source
        and "setMinimumWidth(0)" in send_source)

    update_source = source_of("omnicom", "update.py")
    dialog_source = source_of("omnicom", "ui", "update_dialog.py")
    add("R-107", "有后台查询 GitHub 发布页并解析版本的实现",
        "releases/latest" in update_source and "def check(" in update_source
        and "def is_newer" in update_source and "def parse_version" in update_source)
    add("R-107", "检查失败安静返回 None（不抛异常）",
        "return None" in update_source
        and "except (urllib.error.URLError, OSError, ValueError" in update_source)
    add("R-107", "发现新版本时菜单按钮亮红点并带 NEW",
        "set_badge" in window_source and "update_available" in window_source
        and "menu.update.new" in window_source)
    add("R-107", "对话框在没有新版本时不显示安装按钮",
        "self.btn_install" in dialog_source and "def render" in dialog_source)

    add("R-108", "应用更新时跳过用户数据目录与已有插件",
        'PROTECTED_DIR = "omnicom-data"' in update_source
        and "def _protected" in update_source
        and "clear_pending()" in update_source)
    startup_source = source_of("omnicom", "ui", "__init__.py")
    add("R-108", "旧待安装任务也不得绕过用户确认在启动时自动退出",
        "update.launch_installer()" not in startup_source
        and "wait_confirmation=True" in dialog_source
        and "_confirm_restart(self" in dialog_source)

    add("R-109", "更新检查排在窗口显示之后，并在独立线程里跑",
        "schedule_update_check" in window_source
        and "threading.Thread" in dialog_source
        and "class UpdateWatcher" in dialog_source)

    # -- R-143: "check for updates" must show progress, a verdict and the
    #    current version, and must say *why* it failed.  The GUI suite proves
    #    the wiring; here the reporter itself is exercised without a network.
    from omnicom import update as update_mod

    # A closed port exercises the release-page failure without a network.
    dead = "https://127.0.0.1:1/none"
    unreachable = update_mod.check_release(
        current=__version__, timeout=0.05, api=dead, atom=dead)
    add("R-143", "检查更新失败时返回结论而不是抛异常",
        isinstance(unreachable, dict) and unreachable.get("ok") is False
        and bool(unreachable.get("error")),
        f"keys={sorted(unreachable)}" if isinstance(unreachable, dict)
        else repr(unreachable))
    add("R-143", "检查结果带 source 字段，标明发布页来源",
        isinstance(unreachable, dict) and "source" in unreachable)
    add("R-143", "失败原因写成一句人话（不是裸异常）",
        isinstance(unreachable, dict)
        and "发布页：" in str(unreachable.get("error")),
        str(unreachable.get("error"))[:80] if isinstance(unreachable, dict) else "")
    add("R-143", "旧的 update.check() 失败仍安静返回 None（R-107 契约不变）",
        update_mod.check(timeout=0.05, api=dead) is None)
    traced_steps: list[tuple[int, int]] = []
    update_mod.check_release(current=__version__, timeout=0.05, api=dead,
                             atom=dead,
                             progress=lambda index, total:
                             traced_steps.append((index, total)))
    add("R-143", "检查过程会回报进度，失败也会走到 100%（进度条数据源）",
        bool(traced_steps) and traced_steps[-1] == (3, 3),
        f"{len(traced_steps)} 次，最后 {traced_steps[-1] if traced_steps else '-'}")
    add("R-143", "更新对话框有进度条、日志框和当前版本行",
        "self.progress = QProgressBar" in dialog_source
        and "QTextBrowser" in dialog_source
        and 'tr("update.version"' in dialog_source
        and "def _on_check_traced" in dialog_source)
    add("R-143", "后台检查的失败与进度信号都有接收者（否则界面永远停在检查中）",
        "failed = Signal" in dialog_source
        and "traced = Signal" in dialog_source
        and "chk_failed.connect" in dialog_source
        and "chk_traced.connect" in dialog_source
        and "update_watcher.failed.connect" in window_source)
    add("R-143", "没有缓存信息时打开对话框会主动开始检查",
        "def start_check" in dialog_source
        and "dialog.start_check()" in window_source)
    add("R-143", "直接读取发布页（releases.atom）",
        "releases.atom" in update_source
        and "def _check_web" in update_source
        and "def check_release" in update_source)

    import io
    import tempfile
    from unittest.mock import patch

    from omnicom.i18n import language, set_language

    logs = []
    requested_urls = []

    def release_response(url, *args, **kwargs):
        requested_urls.append(url)
        if url == update_mod.RELEASES_ATOM:
            return io.BytesIO(b'<entry><link href="https://github.com/Fatmear/OmniCOM/releases/tag/v9.9.9"/></entry>')
        if url.startswith(update_mod.RELEASES_ASSETS):
            return io.BytesIO(b'<a href="/Fatmear/OmniCOM/releases/download/v9.9.9/OmniCOM-9.9.9-win64.zip">zip</a>')
        raise AssertionError(f"unexpected request: {url}")

    with patch.object(update_mod, "_request", side_effect=release_response):
        page_result = update_mod.check_release(log=logs.append)
        quiet_result = update_mod.check()
    add("R-145", "两种检查入口都直接读发布页，不调用 API",
        page_result.get("ok") and page_result.get("source") == "web"
        and quiet_result.get("version") == "9.9.9"
        and len(requested_urls) == 4
        and all("api.github.com" not in url for url in requested_urls))
    previous_language = language()
    try:
        set_language("en")
        english_logs = []
        with patch.object(update_mod, "_check_web", side_effect=OSError("offline")):
            failed_page = update_mod.check_release(log=english_logs.append)
        add("R-145", "英文检查失败日志不含中文且明确给出结论",
            failed_page.get("ok") is False
            and "Release page" in failed_page.get("error", "")
            and not re.search(r"[\u4e00-\u9fff]", " ".join(english_logs)))
    finally:
        set_language(previous_language)

    class DownloadResponse(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.headers = {"Content-Length": "131072"}

    with tempfile.TemporaryDirectory() as download_dir:
        destination = os.path.join(download_dir, "update.zip")
        cancel_requested = []
        cancelled_download = False
        with patch.object(update_mod, "_request",
                          return_value=DownloadResponse(b"x" * 131072)):
            try:
                update_mod.download(
                    "test", destination,
                    progress=lambda *_: cancel_requested.append(True),
                    cancelled=lambda: bool(cancel_requested))
            except update_mod.DownloadCancelled:
                cancelled_download = True
        add("R-107", "下载中取消会删除部分文件且不生成完成文件",
            cancelled_download and not os.path.exists(destination)
            and not os.path.exists(destination + ".part"))
        with patch.object(update_mod, "_request",
                          return_value=DownloadResponse(b"complete")):
            update_mod.download("test", destination)
        with open(destination, "rb") as downloaded:
            add("R-107", "正常下载完整落盘且清除临时文件",
                downloaded.read() == b"complete"
                and not os.path.exists(destination + ".part"))
        with patch.object(update_mod, "updates_dir", return_value=download_dir), \
                patch.object(update_mod.zipfile, "ZipFile"), \
                patch.object(update_mod, "_safe_extract",
                             side_effect=lambda *_: cancel_requested.append(True)):
            cancel_requested.clear()
            with contextlib.suppress(update_mod.DownloadCancelled):
                update_mod.stage(destination, "9.9.9",
                                 cancelled=lambda: bool(cancel_requested))
        add("R-107", "解包结束时取消也不会写入待安装标记",
            bool(cancel_requested)
            and not os.path.exists(os.path.join(download_dir, update_mod.PENDING_MARKER))
            and not os.path.exists(os.path.join(download_dir, "staged-9.9.9")))

    from omnicom.ui.search_panel import SearchPanel
    from omnicom.ui.widgets import ScrollBottomPill, SwitchRow, TailFollow

    search_probe = SearchPanel()
    add("R-144", "搜索与日志复用到底跟随/离底停止状态机，追加受保护",
        isinstance(search_probe._tail_follow, TailFollow)
        and search_probe._tail_follow._view is search_probe.results)
    add("R-144", "搜索没有滚动开关或最新气泡实例",
        not search_probe.findChildren(ScrollBottomPill)
        and not search_probe.findChildren(SwitchRow))
    search_probe.set_fullscreen(True)
    fullscreen_clean = (search_probe.results.wordWrap()
                        and not search_probe.findChildren(ScrollBottomPill)
                        and not search_probe.findChildren(SwitchRow))
    search_probe.set_fullscreen(False)
    add("R-144", "全屏和侧栏切换不会重新创建搜索滚动控件",
        fullscreen_clean and not search_probe.results.wordWrap()
        and not search_probe.findChildren(ScrollBottomPill)
        and not search_probe.findChildren(SwitchRow))
    search_probe.add_hit(0, "probe", block=0)
    search_probe.clear_results()
    add("R-144", "清空结果不残留命中或滚动控件",
        search_probe.hits == 0 and search_probe.results.count() == 0
        and not search_probe.findChildren(ScrollBottomPill))
    search_probe.close()

    add("R-110", "\\r\\n 框与 HEX / 回车开关组成同高的一组",
        "OPT_BAND_H = 2 * OPT_ROW_H" in send_source
        and "self.line_end.setFixedHeight(OPT_BAND_H)" in send_source
        and send_source.index("stack.addWidget(self._option_holder)")
        < send_source.index("opts.addLayout(stack)")
        and "opts.addWidget(self.line_end, 1)" in send_source)

    add("R-113", "文件按钮在发送中变成停止，不再有第二个按钮",
        "def _on_file_click" in send_source
        and "self.abortRequested.emit()" in send_source
        and "btn_abort" not in send_source
        and 'tr("send.stop") if busy else tr("send.file")' in send_source)
    add("R-113", "停止状态仍可点击（不会把自己禁用掉）",
        "self.btn_file.setEnabled(True)" in send_source)

    add("R-114", "两个选项标签等宽，开关因此上下对齐",
        "OPT_LABEL_W" in send_source
        and "label.setFixedWidth(OPT_LABEL_W)" in send_source)

    add("R-094", "回车发送默认关闭且不被记住（R-155 改为会话状态）",
        "self.enter_sends = False" in send_source
        and 'setValue("display/enter_send", "0"' not in window_source
        and 'value("display/enter_send", "0")' not in window_source
        and '("row_enter", "display/enter_send")' in window_source)

    add("R-115", "日志按字符换行，首行不会被整段正文挤走",
        "self.setWordWrapMode(QTextOption.WrapAnywhere)" in log_source_full
        # The older mode must not be *set* anywhere; the comment that explains
        # why it was dropped is fine (and lives two lines above).
        and "setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)"
        not in log_source_full)

    add("R-116", "发送历史有硬上限，默认 50、最多 300",
        "HISTORY_DEFAULT = 50" in send_source
        and "HISTORY_MAX = 300" in send_source
        and "def set_history_limit" in send_source
        and "def clear_history" in send_source)
    add("R-116", "条数与会话一起持久化，清除会写回磁盘",
        'setValue("send/history_limit"' in window_source
        and 'setValue("send/history", "[]")' in window_source
        and "historyLimitChanged" in dialogs_source
        and "historyCleared" in dialogs_source)

    add("R-117", "端口标签带上设备名，并去掉描述里重复的端口名",
        "def _port_detail" in window_source
        and "_PORT_DETAIL_RE" in window_source
        and 'f"{name}  {detail}"' in window_source)
    add("R-117", "下拉弹窗加宽，闭合时仍能缩小（不撑破左栏）",
        "self.port_combo.view().setMinimumWidth(300)" in window_source
        and "setMinimumContentsLength(18)" not in window_source)

    add("R-118", "窗口分组有独立标题，文案走 i18n",
        'self.sec_window = SectionTitle(tr("rail.window"))' in window_source
        and 'self.sec_window.setText(tr("rail.window"))' in window_source
        and '"rail.window"' in i18n_source)
    add("R-118", "多一行标题后左栏仍不出现滚动条（行距 5 → 4 px）",
        "layout.setSpacing(4)" in window_source
        and "layout.setSpacing(5)" not in window_source)

    control_source = source_of("omnicom", "control.py")
    cli_source = source_of("omnicom", "cli.py")
    serial_source = source_of("omnicom", "serial_port.py")
    update_source = source_of("omnicom", "update.py")
    updlg_source = source_of("omnicom", "ui", "update_dialog.py")

    add("R-119", "每个窗口各发一个端点文件（不再抢同一个 control.json）",
        "ENDPOINT_DIR_NAME" in control_source
        and 'f"window-{int(pid)}.json"' in control_source
        and "def endpoints" in control_source
        and "def read_endpoint(pid" in control_source)
    add("R-119", "端点里公布本窗口占用的串口，并随状态变化重写",
        "def publish" in control_source
        and '"serial": str(self.info.get("serial") or "")`' not in control_source
        and "self._publish_channel(status)" in window_source
        and "_publish_channel" in window_source)
    add("R-119", "CLI 按串口选窗口：已开该串口的优先，其次空闲窗口",
        "def pick_endpoint" in cli_source
        and "return endpoint, \"owner\"" in cli_source
        and "return endpoint, \"idle\"" in cli_source
        and "def new_session" in cli_source)
    add("R-119", "CLI 绑定校验：窗口关串口后会话立刻结束",
        "def _check_binding" in cli_source
        and "CLI 绑定结束" in cli_source
        and "self._check_binding(reply)" in cli_source)
    add("R-119", "窗口拒绝把别人的串口换掉（不抢占）",
        "不能同时打开" in window_source
        and "def _control_open" in window_source)
    add("R-119", "死进程的端点会被清理，CLI 不再挂着等",
        "GetExitCodeProcess" in control_source
        and "STILL_ACTIVE" in control_source)

    add("R-120", "DTR / RTS 默认关闭（驱动与界面一致）",
        '"dtr": False' in serial_source
        and '"rts": False' in serial_source
        and 'SwitchRow("DTR", False' in window_source
        and 'SwitchRow("RTS", False' in window_source
        # R-155 removed the read; the pair must stay forced to its default and
        # the old key must be purged, never restored (a raised DTR resets the
        # board through the RESET capacitor - L-145).
        and 's.value("serial/dtr", "0")' not in window_source
        and '("row_dtr", "serial/dtr")' in window_source)
    add("R-120", "CLI 默认不开 DTR/RTS，需要显式 --dtr / --rts",
        # ``--no-dtr`` / ``--no-rts`` (R-156) turned the inline expression into
        # a computed local, so assert the semantics rather than the old literal:
        # the default is False and the flag is the only way to raise it.
        'dtr = bool(getattr(args, "dtr", False)) and not getattr(args, "no_dtr", False)'
        in cli_source
        and 'rts = bool(getattr(args, "rts", False)) and not getattr(args, "no_rts", False)'
        in cli_source
        and 'p.add_argument("--dtr", action="store_true",' in cli_source
        and "DEFAULT_OPTS" in serial_source
        and '"dtr": False' in serial_source)

    add("R-121", "可以从本地 zip 升级：版本号读包内，非法包被拒",
        "def package_version" in update_source
        and "def looks_like_bundle" in update_source
        and "def stage_local" in update_source
        and "BUNDLE_MARKER" in update_source)
    add("R-121", "更新对话框提供「从升级包更新」",
        "btn_local" in updlg_source
        and "def install_local" in updlg_source
        and "update.stage_local" in updlg_source)

    # -- R-132..R-138: the GUI suite proves the behaviour; these guards stop a
    #    silent revert of the code that makes those checks pass.
    store_source = source_of("omnicom", "commands.py")
    add("R-132", "命令字段名是「命令 / Command」，不再是缩写 CMD",
        '"cmd.field": ("命令", "Command")' in i18n_source
        and '("CMD", "CMD")' not in i18n_source
        and '("AT", "AT")' not in i18n_source)

    add("R-133", "拖动目标条不再带任何提示文字，只在拖动时画落点线",
        "cmd.drag.out" not in i18n_source
        and "class DropStrip" in commands_source
        and "_set_hot" in commands_source
        and "Drag to move here" not in commands_source)

    add("R-134", "上键先去重框内已显示的那一条，再往前取历史",
        "self.history[-1] == self._hist_draft.strip" in send_source
        and "self._hist_pos = len(self.history) - 1" in send_source)

    add("R-135", "命令库写入带锁 + 唯一临时文件 + 读改写合并",
        "class _library_lock" in store_source
        and "tempfile.mkstemp" in store_source
        and "def _merge_with_disk" in store_source
        and "_removed" in store_source
        and "load_warning" in store_source)
    add("R-135", "顺序变化会标记 _order_dirty，合并时本地顺序优先",
        "self._order_dirty = True" in store_source
        and "if not self._order_dirty:" in store_source)

    add("R-136", "批量操作有独立的范围下拉（全部 / 单组 / 多组）",
        "SCOPE_ALL" in commands_source and "SCOPE_MULTI" in commands_source
        and "self.scope_group = Combo()" in commands_source
        and "def _on_scope_changed" in commands_source
        and "def _pick_all" in commands_source)
    add("R-136", "分组标题带三态勾选框，可勾整组也能多组同时勾选",
        "pickAll = Signal(str, bool)" in commands_source
        and "setTristate(True)" in commands_source
        and "def _pick_group" in commands_source
        and "def _sync_group_picks" in commands_source)

    add("R-137", "分组标题上有垃圾桶，且与右键删除共用 remove_group",
        "self.trash_btn = GlyphButton(GlyphButton.TRASH" in commands_source
        and 'self.trash_btn.setToolTip(tr("cmd.deletegroup"))' in commands_source
        and "self.remove.emit(group[\"id\"])" in commands_source
        and "header.remove.connect(self.remove_group)" in commands_source
        and "def remove_group" in commands_source)

    add("R-138", "窗口描边用主题 token，颜色写法能被 QColor 解析",
        'WINDOW_EDGE = "#' in theme_source
        and "WINDOW_EDGE_PX" in theme_source
        and "def _paint_edge" in window_source
        and 'QColor(theme.WINDOW_EDGE)' in window_source)

    add("R-139", "窗口外圈有柔和阴影，用主题 token 且按环叠加（不是硬色块）",
        'WINDOW_SHADOW = "#' in theme_source
        and "WINDOW_SHADOW_ALPHA" in theme_source
        and "WINDOW_SHADOW_PX" in theme_source
        and "def _paint_shadow" in window_source
        and "def _shadow_image" in window_source
        and "for step in range(steps, 0, -1):" in window_source
        and 'QColor(theme.WINDOW_SHADOW)' in window_source)
    add("R-139", "阴影环由布局边距让出，且最大化时归零",
        "def _apply_window_shadow" in window_source
        and "_SHADOW_BASE_MARGINS" in window_source
        and "def window_shadow_px" in window_source
        and "if self.isMaximized() or self.isFullScreen():" in window_source)
    add("R-139", "阴影厚度不超过左栏无滚动的实测上限 8 px",
        "WINDOW_SHADOW_PX = 8" in theme_source, theme_source.count("WINDOW_SHADOW_PX ="))

    add("R-140", "标题栏拖到屏幕顶部会最大化（Aero Snap）",
        "SNAP_EDGE" in window_source
        and "def _snap_top" in window_source
        and "def _snap_to_max" in window_source
        and "showMaximized()" in window_source
        and "QGuiApplication.screenAt(cursor)" in window_source)
    add("R-140", "最大化状态下往下拖会还原到鼠标下方",
        "def _restore_from_max" in window_source
        and "SNAP_RELEASE" in window_source
        and "showNormal()" in window_source
        and "def _title_height" in window_source
        and "self._drag = QPoint(grab_x, grab_y)" in window_source)

    add("R-141", "分组正文里不再插入拖放条（组间距恢复为一段行距）",
        'self._group_bodies[group["id"]].layout().addWidget(strip)' not in commands_source
        and "spacing 5 + strip 14" in commands_source)
    add("R-141", "列表末尾仍保留唯一的拖出目标（不然拖不出分组）",
        "tail = DropStrip(UNGROUPED)" in commands_source
        and "class DropStrip" in commands_source)

    from omnicom import docs as docs_module

    zh_titles = [title for title, _ in docs_module.MANUAL["zh"]]
    en_titles = [title for title, _ in docs_module.MANUAL["en"]]
    add("R-034", "内置使用说明中英文章节一一对齐（同序、同数）",
        len(zh_titles) == len(en_titles) == 18
        and len(zh_titles) == len(set(zh_titles)),
        (len(zh_titles), len(en_titles)))
    add("R-034", "内置说明覆盖快捷键、故障排查、多窗口与备份",
        all(any(needle in title for title, _ in docs_module.MANUAL["zh"])
            for needle in ("快捷键", "遇到问题", "多窗口", "备份")),
        [title for title, _ in docs_module.MANUAL["zh"]])

    import re as _re

    image_re = _re.compile(r'!\[[^\]]*\]\(([^)]+)\)|<img src="([^"]+)"')

    def broken_images(doc: str, base: str) -> list[str]:
        """Relative image links in ``doc`` that do not resolve from ``base``."""
        with open(os.path.join(ROOT, doc), encoding="utf-8") as handle:
            body = handle.read()
        return [hit for hit in (one or two for one, two in image_re.findall(body))
                if not hit.startswith("http")
                and not os.path.isfile(os.path.join(ROOT, base, hit))]

    missing = broken_images("README.md", ".")
    add("R-034", "README 引用的截图都真实存在（不会出现坏图）", not missing,
        missing)
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
        readme_body = handle.read()
    add("R-034", "README 至少展示一张界面截图",
        any("docs/images/" in (one or two)
            for one, two in image_re.findall(readme_body)))
    # The English README and the shipped plugin guide carry the same pictures,
    # and a path typo there is invisible to anyone reading Chinese (R-182/R-183).
    en_missing = broken_images("README.en.md", ".")
    add("R-034", "README.en 引用的截图都真实存在", not en_missing, en_missing)
    plugins_missing = broken_images(os.path.join("docs", "PLUGINS.md"), "docs")
    add("R-034", "插件说明里的实机图都真实存在", not plugins_missing,
        plugins_missing)
    add("R-086", "打包版的 OmniCOM.cmd cli … 会保留 stdout 与退出码",
        # Text markers only: a literal backslash path here is one escaping
        # level away from silently matching the wrong occurrence.
        'if /i "%~1"=="cli" goto cli' in dist_source
        and "goto cli" in dist_source
        and ":cli" in dist_source
        and "exit /b %ERRORLEVEL%" in dist_source)

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
    parser.add_argument("--only",
                        choices=["protocol", "cli", "gui", "multi", "local", "visual", "installer"],
                        help="只跑某一个套件")
    parser.add_argument("--bundle", default=os.path.join(ROOT, "dist", "OmniCOM"))
    parser.add_argument("--older-package", help="Test a genuine older release ZIP as well")
    options = parser.parse_args()

    if options.only == "installer":
        suite_installer(options.bundle, options.older_package)
        return 1 if FAILED else 0

    started = time.time()
    suites = {
        "protocol": suite_protocol,
        "cli": suite_cli,
        "gui": suite_gui,
        "multi": suite_multiwindow,
        "local": suite_local,
        "visual": suite_visual,
    }
    if options.only:
        suites[options.only]()
    else:
        suite_protocol()
        suite_cli()
        if not options.fast:
            suite_gui()
            suite_multiwindow()
        suite_local()
        suite_visual()

    code = report()
    print(f"用时 {time.time() - started:.1f}s")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
