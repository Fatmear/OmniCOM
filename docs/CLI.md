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
| stdout | 恰好一个 JSON 对象；`monitor` 是 NDJSON（每行一个对象） |
| 成功判据 | `"ok": true`，进程退出码 `0` |
| 失败判据 | `"ok": false` 且带 `"error"`（字符串），退出码 `1` |
| 用法错误 | argparse 报错，退出码 `2` |
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
--no-dtr             打开后拉低 DTR
--no-rts             打开后拉低 RTS
--no-auto-reconnect  关闭拔插自动重连
--open-timeout 6.0   打开超时（秒）
```

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

### `read` — 读一段时间

```bash
python -m omnicom.cli read --port COM3 --timeout 2 --expect boot --record
```
返回 `received`（同上面的 `text/hex/lines/b64`）、`stats`、`record`（记录文件路径与字节数）。

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
| 1 | 运行期失败（`ok:false`） |
| 2 | 命令行用法错误 |
