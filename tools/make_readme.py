"""Render README.md (Chinese, default) and README.en.md from the in-app manual.

Keeping the READMEs generated from ``omnicom/docs.py`` means the repository
documentation can never drift away from what Help -> Documentation shows.

    python tools/make_readme.py

The presentation (badges, centred header, feature table) is hand-written below;
only the *user manual* chapters come from ``docs.py`` (R-034).
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from omnicom import __version__, docs  # noqa: E402

BADGES = (
    f"![version](https://img.shields.io/badge/version-{__version__}-2f6fed)"
    "![license](https://img.shields.io/badge/license-MIT-2ea44f)"
    "![python](https://img.shields.io/badge/python-3.13-3776ab)"
    "![ui](https://img.shields.io/badge/ui-Qt%20%2F%20PySide6-41cd52)"
    "![platform](https://img.shields.io/badge/platform-Windows-0078d4)"
)

HEADER_ZH = f"""<div align="center">

<img src="assets/logo-64.png" width="96" alt="OmniCOM">

# OmniCOM

**嵌入式串口调试工作台：一个窗口替代串口助手、终端、传输工具与脚本接口。**

{BADGES}

[快速开始](#快速开始) · [功能](#功能) · [便携包](#免安装便携包) · [命令行接口](#命令行接口) · [使用说明](#使用说明) · [验证](#验证)

</div>

---

## 为什么用它

嵌入式调试通常是四五个工具来回切换：一个看串口、一个发命令、一个传固件，
再开一个脚本记日志。OmniCOM 把这些收进一个原生窗口里，并且**不引入任何运行时负担**——
没有本地 web 服务、没有浏览器、没有随包分发的自编译可执行文件。

| | 常见做法 | OmniCOM |
| --- | --- | --- |
| 界面 | 网页套壳 / 单个文本框 | 原生 Qt 窗口，白色主题，液态玻璃面板 |
| 命令管理 | 手写脚本或记事本 | 可分组、可备注、可导入导出的命令库 |
| 传输 | 另开协议工具 | 内置 XMODEM / YMODEM / ZMODEM |
| 自动化 | 只能手点 | 单 JSON 输出的 CLI，供 AI 与脚本直接调用 |
| 分发 | 装 Python、装依赖 | 免安装便携包，解压即用 |

## 功能

<table>
<tr><td width="50%" valign="top">

**收发自检**

- 毫秒时间戳、方向标记、不可见字符可视化
- HEX 收发、定时发送、命令延时
- 按**字节预算**缓冲日志（默认 100 MB）
- 超限时弃旧留新，丢弃行数在界面可见
- 绝不静默丢字节，任何丢失都会计数上报

**命令库**

- 分组折叠即点即应，不写盘、不重建
- 每条命令可备注，注释与命令左对齐
- 单条 / 整组导出导入，同名分组自动合并
- 粘贴多行文本自动拆分入列

</td><td width="50%" valign="top">

**传输与终端**

- XMODEM / YMODEM / ZMODEM 收发
- 发送文件按普通发送写入日志并显示进度
- 终端页支持常见 ANSI 光标与颜色指令
- 收发双向同时记录到同一个文件

**工程化**

- 中英文界面严格隔离，无混排
- 插件系统：一个目录一个插件，复制即可用
- 关键字搜索支持普通 / 模糊 / 整词 / 正则
- 快捷启动上下两区，多窗口独立绑定

</td></tr>
</table>

## 快速开始

```bash
git clone https://github.com/Fatmear/OmniCOM.git
cd OmniCOM
start.cmd                  # 或 python run.py
```

需要 Python 3.13 与 PySide6：

```bash
python -m pip install -r requirements.txt
```

没有硬件也能完整试用：

```bash
python run.py --demo       # 内置虚拟串口 DEMO：回 AT 指令并周期性输出遥测
```

## 免安装便携包

```bash
python tools/make_dist.py --check --zip
```

产物：

```
dist/OmniCOM/                 # 98 MB，自带运行时
├── OmniCOM.cmd              # 双击启动（无控制台窗口）
├── console.cmd              # 同上，保留控制台以便看报错
├── OmniCOM.pyz              # 程序本体（同时是一份可读的源码归档）
├── CLI.md / AI-GUIDE.md     # 给 AI 与脚本用的命令行接口说明
├── runtime/                 # 官方 CPython embeddable + 裁剪后的 Qt 绑定
├── assets/  plugins/  logo.png
└── README.md / README.en.md

dist/OmniCOM-{__version__}-win64.zip
```

解压后直接双击 `OmniCOM.cmd`，目标机**不需要安装 Python、PySide6 或任何依赖**，
整个目录可以拷到 U 盘或另一台电脑。首次运行会在旁边生成 `omnicom-data/`。

## 命令行接口

同一个入口也是机器接口——`stdout` 只有一个 JSON 文档，可直接 `json.loads` 或 `| jq`：

```bash
OmniCOM.cmd cli info                      # 环境自检
OmniCOM.cmd cli ports                     # 列出串口
OmniCOM.cmd cli send --port COM3 --data "AT" --expect OK --wait 2
OmniCOM.cmd cli monitor --port COM3 --duration 5 --grep ERROR
OmniCOM.cmd cli transfer --port COM3 --proto y --dir send --file app.bin
OmniCOM.cmd cli session --port COM3 --script steps.json   # AI 主入口
OmniCOM.cmd cli selftest                  # 无硬件端到端自检
```

从源码运行等价于 `python -m omnicom.cli`。完整参考见 [`docs/CLI.md`](docs/CLI.md)。

## 架构

```mermaid
flowchart LR
    subgraph Core["串口核心（无界面依赖）"]
        PM[PortManager<br/>读线程 · 重连 · 统计]
        REC[Recorder<br/>裸字节流落盘]
        XF[X/Y/ZMODEM]
    end
    PM -->|Qt 信号| BR[SerialBridge]
    BR --> UI[OmniWindow<br/>日志 · 终端 · 传输 · 命令库]
    PM --> REC
    PM --> XF
    CLI[omnicom.cli<br/>单 JSON 输出] --> PM
    AI([AI / 脚本]) --> CLI
```

界面只通过 `SerialBridge` 的信号与串口交互，驱动的打开、关闭与线程回收都发生在
界面线程之外——窗口卡住不会连带丢失接收数据。

## 验证

```bash
python tools/acceptance_test.py     # 需求对账：每条需求都有可执行证据
python tools/ui_selftest.py         # GUI 端到端（离屏，无需硬件）
python tools/protocol_selftest.py   # X / Y / ZMODEM 内存环回
python -m ruff check .              # 代码风格
```

需求条目在 `project-context/REQUIREMENTS.md`，历史缺陷与结论在
`project-context/LEDGER.md`。

## 数据位置

| 路径 | 内容 |
| --- | --- |
| `omnicom-data/logs/` | 接收记录（原始字节） |
| `omnicom-data/received/` | 协议接收到的文件 |
| `omnicom-data/commands.json` | 扩展命令与分组 |
| `omnicom-data/plugins/` | 后加进来的插件 |
| `omnicom-data/plugin-data/<id>/` | 每个插件自己的数据 |

可用环境变量 `OMNICOM_DATA_DIR` 把整份数据放到别处。

## 使用说明
"""

HEADER_EN = f"""<div align="center">

<img src="assets/logo-64.png" width="96" alt="OmniCOM">

# OmniCOM

**A native serial workbench: one window instead of a serial assistant, a terminal,
a transfer tool and a script interface.**

{BADGES}

[Quick start](#quick-start) · [Features](#features) · [Portable build](#portable-build) · [CLI](#command-line-interface) · [Manual](#user-manual) · [Verify](#verify)

</div>

---

## Why

Embedded debugging usually means juggling four or five programs: one to watch the
port, one to send commands, one to flash firmware, and a script to keep the log.
OmniCOM folds all of that into a single native window - with no runtime tax: no
local web server, no browser, and no self-built executable in the distribution.

| | The usual setup | OmniCOM |
| --- | --- | --- |
| Interface | browser shell / one text box | native Qt window, light theme, glass panes |
| Commands | a script or a notepad file | a command library with groups and notes, importable/exportable |
| Transfer | a separate protocol tool | XMODEM / YMODEM / ZMODEM built in |
| Automation | clicking only | a single-JSON CLI for agents and scripts |
| Distribution | install Python, install deps | a portable folder, unzip and run |

## Features

<table>
<tr><td width="50%" valign="top">

**Receive & send**

- Millisecond timestamps, direction markers, visible control characters
- HEX send/receive, timed send, per-command delays
- Log buffered by **byte budget** (100 MB default)
- Oldest lines dropped first; the dropped count stays visible
- Nothing is ever dropped silently - every loss is counted and reported

**Command library**

- Group collapse is instant: no rebuild, no disk write
- A note per command, aligned with the command text
- Export/import one command or a whole group; same-name groups merge
- Paste multi-line text and it splits into entries

</td><td width="50%" valign="top">

**Transfer & terminal**

- XMODEM / YMODEM / ZMODEM, send and receive
- Sent files are printed to the log like normal sends, with a progress bar
- Terminal page understands the usual ANSI cursor and colour sequences
- Both directions recorded into one byte-exact file

**Engineering**

- Chinese and English strictly separated, never mixed
- Plugins are folders: copy one in and press reload
- Keyword search: plain / fuzzy / whole word / regex
- Launcher with two rows, multiple windows bound to their own targets

</td></tr>
</table>

## Quick start

```bash
git clone https://github.com/Fatmear/OmniCOM.git
cd OmniCOM
start.cmd                  # or: python run.py
```

Requires Python 3.13 and PySide6:

```bash
python -m pip install -r requirements.txt
```

No hardware needed to try it:

```bash
python run.py --demo       # virtual DEMO port: answers AT and streams telemetry
```

## Portable build

```bash
python tools/make_dist.py --check --zip
```

Output:

```
dist/OmniCOM/                # 98 MB with the bundled runtime
├── OmniCOM.cmd              # double-click to start (no console window)
├── console.cmd              # same, keeps a console for tracebacks
├── OmniCOM.pyz              # the program (also a readable source archive)
├── CLI.md / AI-GUIDE.md     # the command-line contract for agents and scripts
├── runtime/                 # official CPython embeddable + trimmed Qt bindings
├── assets/  plugins/  logo.png
└── README.md / README.en.md

dist/OmniCOM-{__version__}-win64.zip
```

Unzip and double-click `OmniCOM.cmd`. The target machine needs **no Python, no
PySide6, no pip**; the folder is relocatable (USB stick included). The first run
creates `omnicom-data/` beside it.

## Command-line interface

The same entry point is the machine interface - `stdout` carries exactly one JSON
document, so `json.loads` and `| jq` both work:

```bash
OmniCOM.cmd cli info                      # environment check
OmniCOM.cmd cli ports                     # list serial ports
OmniCOM.cmd cli send --port COM3 --data "AT" --expect OK --wait 2
OmniCOM.cmd cli monitor --port COM3 --duration 5 --grep ERROR
OmniCOM.cmd cli transfer --port COM3 --proto y --dir send --file app.bin
OmniCOM.cmd cli session --port COM3 --script steps.json   # the agent entry point
OmniCOM.cmd cli selftest                  # end-to-end check without hardware
```

From source the equivalent is `python -m omnicom.cli`. Full reference:
[`docs/CLI.md`](docs/CLI.md).

## Architecture

```mermaid
flowchart LR
    subgraph Core["Serial core (no UI dependency)"]
        PM[PortManager<br/>reader thread · reconnect · stats]
        REC[Recorder<br/>raw byte stream]
        XF[X/Y/ZMODEM]
    end
    PM -->|Qt signals| BR[SerialBridge]
    BR --> UI[OmniWindow<br/>log · terminal · transfer · commands]
    PM --> REC
    PM --> XF
    CLI[omnicom.cli<br/>single JSON document] --> PM
    AI([agent / script]) --> CLI
```

The window only talks to the port through `SerialBridge` signals; drivers are
opened, closed and joined off the GUI thread, so a busy window cannot lose
incoming bytes.

## Verify

```bash
python tools/acceptance_test.py     # requirement sweep, with executable evidence
python tools/ui_selftest.py         # GUI acceptance (offscreen, no hardware)
python tools/protocol_selftest.py   # X / Y / ZMODEM loopback
python -m ruff check .              # style
```

Requirements live in `project-context/REQUIREMENTS.md`; findings and closed
defects in `project-context/LEDGER.md`.

## Where the data lives

| Path | Contents |
| --- | --- |
| `omnicom-data/logs/` | Raw receive captures |
| `omnicom-data/received/` | Files received over a protocol |
| `omnicom-data/commands.json` | Commands and groups |
| `omnicom-data/plugins/` | Plugins added later |
| `omnicom-data/plugin-data/<id>/` | Each plugin's own data |

`OMNICOM_DATA_DIR` moves the whole thing somewhere else.

## User manual
"""

FOOTER_ZH = """
---

## 许可

MIT License —— 见 [`LICENSE`](LICENSE)。可自由用于商业项目、修改与再分发，
请保留版权与许可声明。

<div align="center">
<sub>用 Qt / PySide6 构建 · 为嵌入式调试而生</sub>
</div>
"""

FOOTER_EN = """
---

## Licence

MIT License - see [`LICENSE`](LICENSE). Free for commercial use, modification and
redistribution; keep the copyright and permission notice.

<div align="center">
<sub>Built with Qt / PySide6 · made for embedded debugging</sub>
</div>
"""


def render(lang: str) -> str:
    parts = [HEADER_ZH if lang == "zh" else HEADER_EN]
    for index, (title, paragraphs) in enumerate(docs.MANUAL[lang], start=1):
        parts.append(f"\n### {index}. {title}\n")
        for paragraph in paragraphs:
            parts.append(f"* {paragraph}")
        parts.append("")
    parts.append(FOOTER_ZH if lang == "zh" else FOOTER_EN)
    return "\n".join(parts).replace("\n\n\n", "\n\n").rstrip() + "\n"


def main() -> int:
    for lang, name in (("zh", "README.md"), ("en", "README.en.md")):
        text = render(lang)
        path = os.path.join(ROOT, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(f"wrote {name} ({len(text)} chars, {len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
