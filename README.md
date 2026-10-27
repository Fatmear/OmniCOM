# OmniCOM — All-in-One Embedded Serial Debugger

原生 Python 桌面串口调试器（PySide6 / Qt），液态玻璃质感界面。**不打包 exe**，直接从源码运行。

## 运行

```bat
start.cmd                 :: 双击启动，无控制台窗口
python run.py             :: 或者命令行启动
python run.py --demo      :: 带虚拟演示串口（AT 回环 + 遥测，无需硬件）
start-console.cmd         :: 启动失败时用它，能看到报错
```

首次使用先装依赖：

```bat
python -m pip install -r requirements.txt
```

## 功能

| 区域 | 能力 |
| --- | --- |
| 左侧栏 | 串口选择 + 刷新、波特率/数据位/停止位/校验位、流控（无 / RTS-CTS / XON-XOFF）、DTR/RTS、自动重连开关 |
| 顶部 | 标签页：**终端** / **文件传输**（XMODEM · YMODEM · ZMODEM 同一页） |
| 终端 | 无损收发、HEX 显示/发送、毫秒时间戳开关、`\r \n` 等控制字符显示开关、发送回显、自动滚动 |
| 暂停 | 一个按钮暂停/继续：暂停期间串口继续接收，继续后把期间数据全部补印 |
| 右侧栏 | **可折叠**。① 扩展命令：粘贴一列命令自动导入、每条命令可设延时与定时发送、依次发送；② 多关键字搜索：命中行实时汇总，可选「任一/全部」匹配 |
| 文件 | 原始文件发送、接收记录到文件（暂停打印也不停）、保存当前视图 |
| 传输 | X/Y/ZMODEM 发送与接收，带进度、速率与实时协议日志；收到文件保存到 `omnicom-data/received/` |

## 数据位置

- 接收记录：`omnicom-data/logs/rx-YYYYMMDD-HHMMSS.log`
- 协议接收：`omnicom-data/received/`
- 扩展命令：`omnicom-data/commands.json`

## 自测

```bat
python tools/acceptance_test.py      :: 全功能验收：25 条需求 / 90 项检查
python tools/protocol_selftest.py    :: X/Y/ZMODEM 环回（23 项）
python tools/ui_selftest.py          :: GUI 验收（41 项，DEMO 端口）
python tools/ui_snapshot.py          :: 渲染 .artifacts/ui.png 供目视检查
python tools/contrast_audit.py       :: 白色主题文字对比度（需全部 >= 4.5:1）
```

> 验收脚本会把 `ui_selftest.py` 的每条 `PASS/FAIL` 行按 `R-\d+` 归到需求上；不带需求号的
> 游离检查会被判为失败，避免"检查被悄悄删掉却仍报全覆盖"。

## CLI（给脚本 / AI 用）

```bat
python -m omnicom.cli info
python -m omnicom.cli ports
python -m omnicom.cli send --port COM3 --data AT --expect OK --wait 2
python -m omnicom.cli session --port COM3 --script steps.json
python -m omnicom.cli selftest            :: 无硬件自检（28 项）
```

stdout 只有一个 JSON 文档（`ok` / `error`），人类信息走 stderr，退出码 0/1/2。
完整说明见 **[docs/CLI.md](docs/CLI.md)**；没有硬件时用 `--port DEMO` 联调。

## 目录

```
omnicom/
  serial_port.py    串口所有权：打开/关闭、读取线程、自动重连、演示设备、记录器
  commands.py       扩展命令模型与持久化、HEX/转义解析
  transfer.py       X/Y/ZMODEM 传输入口（GUI 与 CLI 共用，无 Qt 依赖）
  selftest.py       协议环回自检（CLI / GUI / tools 共用）
  cli.py            面向 AI 的 CLI（stdout 只输出 JSON）
  proto/            XMODEM / YMODEM / ZMODEM 引擎（与传输无关，可环回自测）
  ui/               Qt 界面：窗口、日志视图、发送区、命令栏、搜索栏、传输页
tools/              验收 / 自测 / 截图 / 图标脚本
docs/CLI.md         CLI 使用说明（面向 AI）
project-context/    需求 / 台账 / 决策 / 交接（先读这个再改代码）
```

> 公司策略禁止运行自编译 exe（见 `project-context/REQUIREMENTS.md` R-019），
> 因此 `build.py` 默认拒绝执行，仅作记录.
