# OmniCOM CLI — AI / 脚本接口

> 给 AI 代理的使用说明。**stdout 只有 JSON**，人类信息在 stderr，因此可以安全地
> `omnicom-cli ... | jq` 或直接 `json.loads(stdout)`。

- 程序入口：`python -m omnicom.cli`（仓库根目录下）/ `cli.cmd`（Windows 双击包装）
- 环境自检：`python -m omnicom.cli info`
- 无硬件自检：`python -m omnicom.cli selftest`（内部跑协议环回 + 虚拟串口会话）
- 虚拟串口 `DEMO`：**不需要任何硬件**，会回 AT 指令并每 ~700ms 输出一行遥测。
  任何带 `--port` 的命令都可以用 `--port DEMO` 联调。

## 契约（不要依赖未列出的字段）

| 事项 | 约定 |
| --- | --- |
| stdout | **恰好一个** JSON 对象，可以直接 `json.loads(stdout)`；只有 `monitor`、`send --repeat N`（N>1）、`send --ports A,B` 是 NDJSON（每行一个对象） |
| 成功判据 | `"ok": true`，进程退出码 `0` |
| 失败判据 | `"ok": false` 且带 `"error"`（字符串），退出码 `1` |
| 用法错误 | argparse 报错，退出码 `2` |
| 权威字段 | `ok` 是唯一权威，**退出码永远等于它**；不要两者混用 |
| 编码 | stdout / stderr 强制 UTF-8（可含中文），不会退回系统代码页 |
| 静默 | 结果里的 `silent: true` 表示这一次一个字节都没收到（不是"成功"） |
| 没有交互 | 不读 TTY、不等待按键，可放心从工具调用里执行 |
| 二进制安全 | 所有数据同时给出 `text` / `hex` / `b64`，任选一种消费 |
| 单位 | 键名自带单位：`*_ms` 毫秒、`*_bytes` 字节、`*_b64` base64 |

## 通用串口参数

`send` / `read` / `monitor` / `send-file` / `transfer` / `session` 都支持：

```
--port COM3          串口名；DEMO = 虚拟设备
--baud 115200        波特率
--bytesize 8         数据位 5/6/7/8
--stopbits 1         停止位 1/1.5/2
--parity N           校验位 N/E/O/M/S
--flow none          none / rtscts / xonxoff
--dtr                打开后拉高 DTR（默认关闭，避免复位板子）
--rts                打开后拉高 RTS（默认关闭）
--no-dtr / --no-rts  保持关闭（默认行为，兼容旧脚本）
--no-auto-reconnect  关闭拔插自动重连
--open-timeout 6.0   打开超时（秒）
--lock-timeout 20    同一串口被别的 CLI 占用时最多排队多少秒
```

### 同一串口不会抢占

两个 AI / 两个脚本同时操作 COM3 时，后到的那个**排队**，不会互相打断：CLI 在
`<data>/locks/COM3.lock` 上持系统文件锁（Windows `msvcrt` / POSIX `flock`），
进程退出（包括崩溃）时由系统释放。排队超过 `--lock-timeout` 就直接失败：

```json
{"ok": false, "error": "COM3 被其它 CLI 占用超过 20s，未执行"}
```

多窗口场景下串口由窗口持有，这层锁只挡"两个 CLI 同时直连同一个口"。

## 与界面共享串口（以及多窗口）

串口只能有一个占用者，所以 **CLI 从不自己开串口**：只要有一个 OmniCOM 窗口在跑，
CLI 就把请求交给窗口，由窗口去开/发/读。于是 CLI 做的事和用户点按钮做的事完全一样 ——
日志里看得到、计数算得上、记录文件是同一份，CLI 关串口就是关掉界面上那个串口。

| 情况 | 结果 |
| --- | --- |
| 有窗口 | `"via": "window"`，并返回 `window_pid` / `window_slot`，告诉你这次的流量进了哪扇窗 |
| 没有窗口 | `"via": "direct"`，CLI 自己独占串口（行为和 1.1.2 一致） |
| `--direct` 或 `OMNICOM_CLI_DIRECT=1` | 强制直连，不碰任何窗口 |

### 多个窗口、多个串口

每个窗口都发布自己的端点，并公布**它当前占用的串口**，所以 CLI 是**按串口**找窗口的：

1. **已经打开该串口的窗口**（日志/计数/设置都是用户正在看的那份）—— 优先；
2. 没有开串口的**空闲窗口** —— 在那里打开；
3. 所有窗口都在用**别的**串口 —— CLI 自己开一个（`via: direct`），**不会**把别人的串口顶掉。

例外：`--window <PID>` 可以钉死某个窗口，`--port` 可以按串口挑（`state`/`close`/`log`/`stats` 
也支持这两个参数）。

窗口关掉串口后，正在运行的 CLI 会话会**立即报错结束**：

```
{"ok": false, "error": "串口错误：窗口已关闭 COM3，CLI 绑定结束"}
```

这是故意的：CLI 和界面共享同一个会话，界面把口关了，CLI 再往里面写就是静默丢数据。
想继续就重新发一条命令（会重新开串口）。

查看有哪些窗口、每个占着什么串口：

```bash
python -m omnicom.cli info
```
```json
{"ok": true, "window": {"running": true, "mode": "window", "pid": 36416,
  "port": "COM3", "windows": [{"pid": 36416, "slot": 1, "serial": "COM3", "connected": true},
                                     {"pid": 43812, "slot": 2, "serial": "COM5", "connected": true}]}}
```

⚠ CLI 和窗口必须看**同一个数据目录**（`omnicom-data/`，可用 `OMNICOM_DATA_DIR` 改）。
在仓库里跑 CLI、却开着 `dist\OmniCOM\` 里的窗口，两边找不到同一个端点，CLI 就退回直连。

打包版要用 `runtime\python.exe OmniCOM.pyz cli ...`；`OmniCOM.cmd cli ...` 没有输出
（它是 `start` 一个无控制台的进程，stdout 直接丢了）。

### DTR / RTS

默认**都不拉** —— 抬高 DTR 会把 CH340/CP210x 这类板子复位，等于一开串口就重启。
要拉高：`--dtr` / `--rts`。旧的 `--no-dtr` / `--no-rts` 仍然接受（就是默认行为）。

## 命令

### `ports` — 列出串口

```bash
python -m omnicom.cli ports --demo
```
```json
{"ok": true, "count": 4, "serial_available": true,
 "ports": [{"name": "COM3", "description": "USB-SERIAL CH343", "hwid": "USB VID:PID=1A86:55D3 SER=...",
            "vid": 6790, "pid": 21971, "serial": "5AF7088351", "manufacturer": "wch.cn", "product": ""}]}
```

### `send` — 发送并按需等待应答

```bash
python -m omnicom.cli send --port COM3 --data "AT" --expect OK --wait 2
python -m omnicom.cli send --port COM3 --hex "01 03 00 00 00 01" --wait 0.5
```
```json
{"ok": true, "port": "COM3", "sent_bytes": 4, "sent_hex": "41 54 0d 0a",
 "payload_kind": "text:2B+2B", "elapsed_ms": 20, "matched": true,
 "response": {"bytes": 6, "hex": "0d 0a 4f 4b 0d 0a", "text": "\r\nOK\r\n",
              "escaped": "\\r\\nOK\\r\\n", "lines": ["", "OK"], "b64": "DQpPSw0K"},
 "rx_total_bytes": 6, "tx_total_bytes": 4, "notices": ["已打开 COM3 @ 115200"]}
```

要点：
- `--data` 支持 `\r \n \t \0 \xNN` 转义；`--line-end crlf|lf|cr|none` 追加行尾。
- `--expect` 可重复。给了 `--expect`/`--expect-regex` 但没等到 → `ok:false`、`matched:false`、退出码 1。
- `--drain-ms N`：发送前先丢弃已缓存数据，避免旧日志干扰匹配。
- `--limit N`：限制 JSON 里 `text/hex` 的长度（默认 65536 字节），`0` 表示不截断。

#### 不等满 `--wait`：`--settle`

没有 marker 可等的命令（`iperf -s`、`reboot`、只打印一行的日志命令）以前只能把
`--wait` 睡满。`--settle 0.4` 表示**收到第一批数据后**再静默 0.4 s 就返回：

```bash
python -m omnicom.cli send --port COM3 --data "iperf -s" --wait 30 --settle 0.5
# elapsed_ms 约 500，而不是 30000
```

静默计时只在**第一个字节到达后**才开始，所以"设备还没开口"不会被误判成"说完了"。

#### 可能沉默、也可能报错：`--ok-if-silent`

配合 `--expect` 使用。设备完全没回话时算成功（结果里多一个 `note` 说明），
但**只要收到过字节却不含预期内容**，仍然是失败 —— 它不会掩盖真正的错误：

```json
{"ok": true, "silent": true, "matched": false,
 "note": "没有收到任何数据，按 --ok-if-silent 视为成功"}
```

#### 高频轮询：`--repeat` / `--interval`

同一个进程、**只打开一次串口**，采样 N 次，每次输出一行 JSON：

```bash
python -m omnicom.cli send --port COM3 --data "AT+TEMP?" --expect OK \
    --wait 1 --repeat 50 --interval 0.2
```

每行多一个 `"sample": 1..N`。实测 5 次采样 0.86 s，而 5 次独立调用约 2.2 s ——
省掉的是每次的 Python 启动与串口重开。它在第一个失败处停下（退出码 1）。

#### 一次问多个端口：`--ports`

```bash
python -m omnicom.cli send --ports COM3,COM4,COM5 --data "AT" --expect OK --wait 1
```

每个端口一行 JSON（NDJSON），`--ports` 优先于 `--port`。串口参数（波特率等）
对所有端口相同。

### `read` — 读一段时间

```bash
python -m omnicom.cli read --port COM3 --timeout 2 --expect boot --record
```
返回 `received`（同上面的 `text/hex/lines/b64`）、`stats`、`record`（记录文件路径与字节数）、
`silent`（是否一个字节都没收到）。`--settle` / `--ok-if-silent` 与 `send` 同义；
`--expect` 没命中时 `ok:false`、退出码 1。

### `monitor` — 按行流式输出（NDJSON）

```bash
python -m omnicom.cli monitor --port COM3 --duration 5 --grep ERROR --limit-lines 20
```
```
{"ok": true, "t_ms": 1793081701180, "dir": "rx", "line": "...", "escaped": "..."}
{"ok": true, "done": true, "lines": 2, "stats": {"rx": 143, ...}}
```

### `send-file` — 原样发送文件（不走协议）

```bash
python -m omnicom.cli send-file --port COM3 --file firmware.bin
```
返回 `sent_bytes`、`elapsed_ms`、`rate_bytes_per_s`。`sent_bytes` 应等于文件大小。

### `transfer` — X / Y / ZMODEM

```bash
python -m omnicom.cli transfer --port COM3 --proto z --dir send --file app.bin
python -m omnicom.cli transfer --port COM3 --proto x --dir recv --save-dir ./in
```
```json
{"ok": true, "proto": "ZMODEM", "dir": "send", "bytes": 4096, "elapsed_ms": 812,
 "rate_bytes_per_s": 5044, "files": [], "log": ["ZMODEM 发送 ..."], "stats": {...}}
```
接收时 `files` 里每项是 `{"name","path","bytes"}`，已落盘。

### `session` — 脚本化步骤（**AI 主入口**）

一次打开串口，按顺序执行步骤，返回每步结果。适合「复位 → 等启动 → 发命令 → 抓输出」这类流程。

```bash
python -m omnicom.cli session --port COM3 --script steps.json
python -m omnicom.cli session --port COM3 --script -   # 从 stdin 读
```

脚本格式（JSON；顶层可以是数组，也可以是含 `steps` 的对象）：

```json
{
  "port": "COM3", "baudrate": 115200,
  "steps": [
    { "delay": 0.5 },
    { "send": "AT", "line_end": "crlf", "wait": 1.5, "expect": ["OK"] },
    { "send_hex": "01 03 00 00 00 01", "wait": 1.0, "expect_regex": "01 03" },
    { "drain": 1.0 },
    { "record": "start" },
    { "delay": 2 },
    { "record": "stop" },
    { "send_file": "C:/fw/app.bin" },
    { "transfer": { "proto": "y", "dir": "send", "file": "C:/fw/app.bin" } },
    { "reset_input": true },
    { "set": { "baudrate": 921600 } },
    { "note": "任意注释" }
  ]
}
```

步骤表：

| 步骤 | 字段 | 说明 |
| --- | --- | --- |
| `note` | `note` | 注释，原样返回 |
| `delay` | `delay` | 秒；>10 时按毫秒处理 |
| `set` | `set` | 热改参数：`baudrate/bytesize/stopbits/parity/dtr/rts/rtscts/xonxoff` |
| `send` | `send` 或 `send_hex`、可选 `line_end`、`wait`、`expect[]`、`expect_regex`、`drain_ms`、`raw`、`limit` | 发送并按需等待/断言 |
| `expect` | `expect[]` 或 `expect_regex`、`timeout`（默认 3s） | 只等不写 |
| `drain` | `drain`（秒）、`max_bytes`、`limit` | 读一段并返回内容 |
| `record` | `record: "start"｜"stop"` | 接收记录到文件 |
| `send_file` | `send_file` | 原样发送文件 |
| `transfer` | `transfer: {proto, dir, file?, name?, save_dir?}` | 协议传输 |
| `reset_input` | `reset_input: true` | 丢弃已缓存输入 |

返回：

```json
{"ok": true, "port": "COM3", "steps_total": 9, "steps_run": 9,
 "results": [{"index": 0, "step": "delay", "ok": true, "seconds": 0.5},
             {"index": 1, "step": "send", "ok": true, "sent_bytes": 4,
              "sent_hex": "41 54 0d 0a", "matched": true,
              "response": {"text": "\r\nOK\r\n", "lines": ["", "OK"]}}],
 "stats": {"rx": 143, "tx": 4, "chunks": 4, "txOps": 1}}
```

默认遇错即停（`--continue-on-error` 改为继续）；任一必填步骤失败 → 顶层 `ok:false`、退出码 1。

### `selftest` — 无硬件端到端自检

```bash
python -m omnicom.cli selftest
```
跑 28 项：CRC 向量、XMODEM（128/1024/空文件）、YMODEM、ZMODEM（4KB/70KB/空文件）在内存环回上的
收发一致性，以及 DEMO 端口的打开/AT 应答/文件发送/记录落盘。全部通过才 `ok:true`。

### `info` — 环境

返回 `version`、`python`、`pyserial`、`serial_available`、`data_dir`、`logs_dir`、`received_dir`、`demo_port`、`platform`。

## 典型 AI 工作流

```bash
# 1) 有硬件：找到端口
python -m omnicom.cli ports

# 2) 确认设备在跑
python -m omnicom.cli send --port COM3 --data "AT" --expect OK --drain-ms 100

# 3) 复位 + 抓启动日志 + 发命令
cat > steps.json <<'JSON'
{"steps":[
  {"send":"AT+CFUN=1,1","wait":3},
  {"drain":4,"limit":200000},
  {"send":"AT+CGMR","wait":1,"expect":["OK"]}
]}
JSON
python -m omnicom.cli session --port COM3 --script steps.json

# 4) 传固件
python -m omnicom.cli transfer --port COM3 --proto y --dir send --file app.bin

# 5) 没有硬件时，先用 DEMO 验证自己的命令/流程
python -m omnicom.cli send --port DEMO --data "AT" --expect OK
```

## 常见错误

| `error` 里出现 | 含义 | 处理 |
| --- | --- | --- |
| `被其他程序占用` / `拒绝访问` | 端口被别的程序（含本 GUI）占用 | 关掉占用方，或换端口 |
| `无法打开 COMx（设备可能已拔出）` | 设备不存在 | 重新 `ports` |
| `等待接收方握手超时` | 传输时对端没发 `C`/`NAK` | 设备端要先进入 `rx` 接收模式 |
| `等待 ZMODEM 帧超时` | 对端没有发起协议 | 检查是否选错协议或对端未就绪 |
| `未收到预期内容` | `--expect` 超时 | 放宽 `--wait`、加 `--drain-ms`，或改用 `read` 先看原始输出 |

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功（`ok:true`） |
| 1 | 运行期失败（`ok:false`，含"等不到预期内容"和"端口被占用"） |
| 2 | 命令行用法错误（参数不合法） |

`ok` 与退出码共用同一个判断，所以"只看退出码"和"只看 JSON"结论永远一致。
