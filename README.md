<div align="center">

<img src="assets/logo-64.png" width="96" alt="OmniCOM">

# OmniCOM

**嵌入式串口调试工作台：一个窗口替代串口助手、终端、传输工具与脚本接口。**

![version](https://img.shields.io/badge/version-1.1.2-2f6fed)![license](https://img.shields.io/badge/license-MIT-2ea44f)![python](https://img.shields.io/badge/python-3.13-3776ab)![ui](https://img.shields.io/badge/ui-Qt%20%2F%20PySide6-41cd52)![platform](https://img.shields.io/badge/platform-Windows-0078d4)

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

dist/OmniCOM-1.1.2-win64.zip
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

### 1. 快速开始

* 双击 `start.cmd`，或运行 `python run.py`。没有硬件时加 `--demo` 会多出一个虚拟串口 DEMO。
* 启动时不会另外弹出命令行窗口；想保留控制台输出，加 `--keep-console`。
* 要拷给别人用，运行 `python tools/make_dist.py --zip`，把生成的 `dist/OmniCOM/` 目录（或同名 zip）整个发过去，对方双击里面的 `OmniCOM.cmd` 即可：包内自带 Python 运行时与全部依赖，不需要另外安装任何东西。
* 左侧选择串口 → 设置波特率等参数 → 点「打开串口」，接收的数据会立刻出现在「串口日志」页。
* 自定义波特率可直接点击输入，按回车或移开焦点后应用；也可以从下拉列表选择常用值。
* 底部输入框写内容，Ctrl+Enter 或点「发送」发出。行尾、HEX、定时发送都在输入框右侧。
* 拖动日志与发送区之间的分隔条可直接增高输入框，高度会在下次启动时恢复。

### 2. 串口日志页

* 每一行的格式是 `[时:分:秒.毫秒]收←◇内容` 或 `发→◇内容`。
* 「自动滚动到最新」打开时永远跟着最新一行；关掉后可以自由翻阅历史，底部会浮出「回到最新」气泡。
* 「保持屏幕常亮」打开后系统不会因空闲息屏或睡眠，适合长时间盯串口；关闭应用会自动恢复。
* 「显示 \r \n 等控制字符」对收和发同时生效，能看到真实发出的换行。
* 「HEX 显示」按 16 字节一行显示偏移 + 十六进制 + ASCII。
* 「保存视图」把当前日志写到文件；「收发记录」在后台把收发两个方向的原始字节写进同一个文件，与界面显示无关，不会漏字节；「清空记录文件」只清掉已写的内容，记录继续。
* 清空日志时，顶部栏的 RX / TX 计量也一起归零，之后从零重新统计。
* 日志按大小缓冲：默认保留 100 MB 文字（首选项可改成 4 ~ 500 MB），满了就丢弃最早的行、始终保留最新的内容，被丢弃的行数会显示在日志工具栏上，接再快也不会卡住界面。
* 发送文件分两步：先「打开文件」选好待发文件，再「发送文件」；发送的内容按普通发送打印到日志，下方进度条显示传输进度。
* 日志框右键可以复制、复制全部、保存、查找、跳到最新。
* 串口按钮在未连接时为中性颜色，成功打开后才着色；开关在后台执行，期间按钮暂时不可用。

### 3. 终端模式页

* 这是给 linux 串口控制台用的：点一下终端区域，键盘输入直接送进串口。
* Enter 发送回车（可选 CR / LF / CRLF），Ctrl+C 发送 0x03，方向键发送 ANSI 序列。
* 支持 CR 回到行首覆盖、退格、TAB 对齐、ANSI 颜色，以及清屏序列。
* 复制用 Ctrl+Shift+C，粘贴用 Ctrl+Shift+V（普通 Ctrl+C 会当作中断字符发出）。

### 4. 文件传输页

* 选择 XMODEM / YMODEM / ZMODEM，设置方向与文件，点开始。
* 接收的文件保存在 `omnicom-data/received/`。
* 传输期间串口被独占，日志页不打印，避免把协议字节弄脏。

### 5. 扩展命令

* 右侧栏的命令可以点一下直接发送，也可以勾选多条后「依次发送」（每条按自己的延时）。
* 「分组」用来归类：点文件夹图标新建分组，可以给分组写备注；分组可折叠。创建和改名不允许与已有分组同名，首尾空格不计。
* 每条命令与发送、编辑、删除按钮排在同一主行，色条区分选中状态；备注在命令下方以小字显示，与命令文字左对齐，悬停可查看完整内容和延时。
* 每条命令支持备注、行尾、延时、周期定时发送、HEX。
* 右键一条命令可以移动分组；顶部导出按钮统一选择要导出的命令或分组。
* 导出的 JSON 在另一台电脑上「导入」即可使用，分组会自动合并。
* 粘贴多行命令会自动拆分成多条。

### 6. 关键字搜索

* 输入关键字回车，会搜索**整个日志（含此前的历史）**，结果按「行号 + 内容」列出。
* 选中日志文字后右键「查找」，会替换旧查询并立即按普通文本搜索；多行选区按行匹配任一项。
* 双击任意一行跳到日志原文并高亮。
* 匹配方式：普通 / 模糊（字符按顺序出现即可）/ 全词 / 正则表达式；另有「区分大小写」开关。
* 右上角箭头按钮可把结果全窗口显示，Esc 退出。

### 7. 插件

* 插件是一个独立目录，放在 `plugins/` 或 `omnicom-data/plugins/` 下，每个插件在顶部占一个标签页。
* 把一个插件目录整个复制到另一台电脑的 `plugins/` 下，点「插件 → 刷新插件」就能用。
* 自带「快捷启动」插件：上方是快捷方式（点一下新开一个窗口），下方是正在运行的窗口（点一下切过去）；
* 两排都支持拖拽排序，右键可以重命名——下排按窗口命名，所以两个记事本可以分别叫 A、B，长名字会自动换行不遮挡。
* 上排「从列表移除」只删快捷方式，不关闭已开窗口；下排「结束这个窗口」只关那一个窗口。

### 8. 首选项与语言

* 「设置 → 首选项」里可以切换界面语言：中文环境只显示中文，英文环境只显示英文，包括右键菜单。
* 同一页还能设置日志缓冲大小（4 ~ 500 MB，默认 100 MB）和「收发记录」的默认保存目录、切换主题色（只改强调色与选中高亮），或选一张图片当壁纸：有壁纸时各面板会变成液态玻璃（取面板后方的模糊壁纸再叠一层半透明白），鼠标提示框也是同款玻璃；没有壁纸就是原来的纯白面板。底部的状态文字不加框也不加底色，直接压在背景上。
* 切换立即生效，不需要重启。

### 9. 命令行（给脚本 / AI 用）

* `python -m omnicom.cli ports` 列出串口；`send` 发送并等待应答；`read` 读取；`monitor` 流式输出。
* `session --script 文件.json` 可以跑一段脚本化的收发流程。
* 所有命令的 stdout 都是一个 JSON 文档，详细说明见 `docs/CLI.md`。

### 10. 数据放在哪

* `omnicom-data/logs/` 收发记录（首选项改了目录后按新目录）；`omnicom-data/received/` 协议收到的文件；
* 右下角「记录目录」显示的就是当前实际保存收发记录的位置，点「打开目录」会直接打开它。
* `omnicom-data/commands.json` 扩展命令；`omnicom-data/plugins/` 后加的插件。

---

## 许可

MIT License —— 见 [`LICENSE`](LICENSE)。可自由用于商业项目、修改与再分发，
请保留版权与许可声明。

<div align="center">
<sub>用 Qt / PySide6 构建 · 为嵌入式调试而生</sub>
</div>
