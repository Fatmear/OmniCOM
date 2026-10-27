"""Render README.md (Chinese, default) and README.en.md from the in-app manual.

Keeping the READMEs generated from ``omnicom/docs.py`` means the repository
documentation can never drift away from what Help -> Documentation shows.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from omnicom import __version__, docs  # noqa: E402

HEADER_ZH = f"""# OmniCOM {__version__}

OmniCOM 是一个原生 Python 串口工作台：不打包 exe、不起本地服务、不依赖浏览器，
双击 `start.cmd` 就能用。

![logo](assets/logo-64.png)

* 白色主题、自绘窗口按钮、可折叠左右两栏
* 串口日志 / 终端 / 文件传输（XMODEM / YMODEM / ZMODEM）三个内置页面
* 扩展命令可分组、可备注、可单条或整组导出给别人导入
* 关键字搜索支持普通、模糊、整词、正则四种匹配，并可切换是否区分大小写
* 插件系统：一个插件一个目录，复制到另一台电脑的 `plugins/` 再刷新即可
* 中英文两种界面语言，中文环境只显示中文，英文环境只显示英文

## 运行

```
start.cmd                       # 或 python run.py
python run.py --demo            # 没有硬件时使用虚拟串口 DEMO
```

## 验证

```
python tools/protocol_selftest.py     # X/Y/ZMODEM 回环
python tools/ui_selftest.py           # GUI 验收
python tools/acceptance_test.py       # 全需求验收
python -m ruff check .
```

## 使用说明
"""

HEADER_EN = f"""# OmniCOM {__version__}

OmniCOM is a native Python serial workbench: no packaged exe, no local server,
no browser. Double-click `start.cmd` and you are in.

![logo](assets/logo-64.png)

* Light theme, hand-painted window buttons, collapsible left and right columns
* Three built-in pages: serial log, terminal, file transfer (XMODEM/YMODEM/ZMODEM)
* Commands can be grouped, annotated, and exported one-by-one or as a group
* Keyword search with plain / fuzzy / whole-word / regex matching and a match-case switch
* Plugin system: one folder per plugin - copy it to another machine's `plugins/`
  folder, press reload, done
* Chinese and English interface: Chinese shows only Chinese, English only English

## Run

```
start.cmd                       # or python run.py
python run.py --demo            # virtual DEMO port when you have no hardware
```

## Verify

```
python tools/protocol_selftest.py     # X/Y/ZMODEM loopback
python tools/ui_selftest.py           # GUI acceptance
python tools/acceptance_test.py       # full requirement sweep
python -m ruff check .
```

## User manual
"""

FOOTER_ZH = """
## 数据位置

| 路径 | 内容 |
| --- | --- |
| `omnicom-data/logs/` | 接收记录（原始字节） |
| `omnicom-data/received/` | 协议接收到的文件 |
| `omnicom-data/commands.json` | 扩展命令与分组 |
| `omnicom-data/plugins/` | 后加进来的插件 |
| `omnicom-data/plugin-data/<id>/` | 每个插件自己的数据 |

## 许可

内部工具，未开源。
"""

FOOTER_EN = """
## Where the data lives

| Path | Contents |
| --- | --- |
| `omnicom-data/logs/` | Raw receive captures |
| `omnicom-data/received/` | Files received over a protocol |
| `omnicom-data/commands.json` | Commands and groups |
| `omnicom-data/plugins/` | Plugins added later |
| `omnicom-data/plugin-data/<id>/` | Each plugin's own data |

## Licence

Internal tool, not open source.
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
