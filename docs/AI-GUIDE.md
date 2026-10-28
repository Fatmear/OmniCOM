# OmniCOM — AI / agent guide

> Read this first. It is the contract, not a tutorial.
> **`stdout` is exactly one JSON document.** Human-readable text goes to `stderr`.

## Entry points

| Context | Command |
| --- | --- |
| Portable build (no Python installed) | `OmniCOM.cmd cli <subcommand> …` |
| Same, keeping a console | `console.cmd cli <subcommand> …` |
| From source | `python -m omnicom.cli <subcommand> …` |

The GUI is started by `OmniCOM.cmd` with no arguments. **Never launch the GUI to
automate something** — use the `cli` subcommands.

## Contract

| Topic | Guarantee |
| --- | --- |
| stdout | exactly one JSON object; `monitor` emits NDJSON (one object per line) |
| success | `"ok": true`, exit code `0` |
| failure | `"ok": false` **and** `"error"` (string), exit code `1` |
| bad usage | argparse message on stderr, exit code `2` |
| interactivity | none — never reads a TTY, never waits for a key |
| payloads | every payload gives `text`, `hex`, `b64` and `escaped`; consume whichever fits |
| units | in the key name: `*_ms`, `*_bytes`, `*_b64`, `rate_bytes_per_s` |

Parse with `json.loads(stdout)`. Do not scrape stderr for data — it is only there
for humans.

## No hardware? Use `DEMO`

`--port DEMO` is a virtual device: it answers `AT` with `OK` and streams a
telemetry line about every 700 ms. Every command that takes `--port` accepts it,
so a flow can be built and validated before a board is attached.

```bash
OmniCOM.cmd cli send --port DEMO --data "AT" --expect OK --wait 2
```

## Subcommands

### `info` — environment and paths

```bash
OmniCOM.cmd cli info
```
```json
{"ok": true, "version": "1.1.2", "python": "3.13.14", "pyserial": "3.5",
 "serial_available": true, "data_dir": "…", "logs_dir": "…",
 "received_dir": "…", "demo_port": "DEMO", "platform": "win32"}
```
Run this first: it proves the runtime is intact and tells you where captures go.

### `ports` — enumerate serial ports

```bash
OmniCOM.cmd cli ports            # real ports only
OmniCOM.cmd cli ports --demo     # include DEMO
```
`ports[]` items carry `name`, `description`, `hwid`, `vid`, `pid`, `serial`,
`manufacturer`.

### `send` — write bytes, optionally wait for a reply

```bash
OmniCOM.cmd cli send --port COM3 --data "AT+CGMR" --expect OK --wait 2
OmniCOM.cmd cli send --port COM3 --hex "01 03 00 00 00 01" --wait 0.5
```

| Flag | Meaning |
| --- | --- |
| `--data` / `--hex` | text (with `\r \n \t \0 \xNN` escapes) or hex bytes |
| `--line-end crlf\|lf\|cr\|none` | appended to `--data` (default `crlf`) |
| `--wait N` | seconds to keep reading after the write |
| `--expect S` (repeatable) | required substring in the reply |
| `--expect-regex R` | required regex in the reply |
| `--drain-ms N` | discard buffered bytes first, so stale output cannot match |
| `--limit N` | cap the `text`/`hex` length in the JSON (default 65536, `0` = no cap) |

Reply arrives as `response: {bytes, hex, text, escaped, lines, b64}`.
Missing `--expect` ⇒ `ok:false`, `matched:false`, exit 1.

### `read` — collect for a while

```bash
OmniCOM.cmd cli read --port COM3 --timeout 2 --expect boot --record
```
Returns `received`, `stats`, and `record` (capture file path and byte count) when
`--record` is given.

### `monitor` — stream lines (NDJSON)

```bash
OmniCOM.cmd cli monitor --port COM3 --duration 5 --grep ERROR --limit-lines 20
```
One object per line: `{"ok":true,"t_ms":…,"dir":"rx|tx","line":…,"escaped":…}`.
The last line is `{"ok":true,"done":true,"lines":N,"stats":{…}}`. Good for a
long-running command where you want the first N matching lines.

### `send-file` — raw bytes, no protocol

```bash
OmniCOM.cmd cli send-file --port COM3 --file firmware.bin
```
`sent_bytes` must equal the file size; otherwise treat it as a failure.

### `transfer` — XMODEM / YMODEM / ZMODEM

```bash
OmniCOM.cmd cli transfer --port COM3 --proto y --dir send --file app.bin
OmniCOM.cmd cli transfer --port COM3 --proto z --dir recv --save-dir ./in
```
`files[]` on receive: `{name, path, bytes}` — already written to disk.

### `session` — the main agent entry point

One port open, a list of steps, one result per step. This is what you should use
for "reset → wait for boot → send command → capture output" sequences.

```bash
OmniCOM.cmd cli session --port COM3 --script steps.json
OmniCOM.cmd cli session --port COM3 --script -       # read from stdin
```

```json
{"port": "COM3", "baudrate": 115200,
 "steps": [
   {"send": "AT+CFUN=1,1", "wait": 3},
   {"drain": 4, "limit": 200000},
   {"send": "AT+CGMR", "wait": 1, "expect": ["OK"]},
   {"record": "start"}, {"delay": 2}, {"record": "stop"},
   {"transfer": {"proto": "y", "dir": "send", "file": "C:/fw/app.bin"}}
 ]}
```

Step kinds: `note`, `delay`, `set` (hot-change `baudrate/bytesize/stopbits/parity/
dtr/rts/rtscts/xonxoff`), `send` (`send`|`send_hex`, `line_end`, `wait`, `expect[]`,
`expect_regex`, `drain_ms`, `raw`, `limit`), `expect` (wait only), `drain`,
`record` (`start`|`stop`), `send_file`, `transfer`, `reset_input`.

Result: `{"ok":true,"steps_total":N,"steps_run":N,"results":[…],"stats":{…}}`.
Stops at the first failing step unless `--continue-on-error` is passed; any failed
required step makes the top level `ok:false` with exit code 1.

### `selftest` — prove the build works, without hardware

```bash
OmniCOM.cmd cli selftest
```
28 checks: CRC vectors, XMODEM (128/1024/empty), YMODEM, ZMODEM (4 KB/70 KB/empty)
over an in-memory loopback, plus a DEMO session (open, `AT`, file send, capture to
disk). `ok:true` only when everything passes.

## Shared serial options

`send`, `read`, `monitor`, `send-file`, `transfer`, `session` all accept:

```
--port COM3        port name; DEMO = virtual device
--baud 115200
--bytesize 8       5/6/7/8
--stopbits 1       1/1.5/2
--parity N         N/E/O/M/S
--flow none        none/rtscts/xonxoff
--no-dtr --no-rts  leave the line low after opening
--no-auto-reconnect
--open-timeout 6.0
```

## Error messages you will meet

| `error` contains | Meaning | What to do |
| --- | --- | --- |
| `被其他程序占用` / `拒绝访问` | port is taken (often by the OmniCOM GUI itself) | close the other owner, or pick another port |
| `无法打开 COMx（设备可能已拔出）` | port does not exist | re-run `ports` |
| `等待接收方握手超时` | peer never sent `C`/`NAK` | the device must be in receive mode first |
| `等待 ZMODEM 帧超时` | peer never started the protocol | wrong protocol, or the peer is not ready |
| `未收到预期内容` | `--expect` timed out | raise `--wait`, add `--drain-ms`, or use `read` to see raw output |

## Rules of thumb for agents

1. Run `cli info` before a long flow — it catches a broken runtime in one call.
2. Prefer `session` over many `send` invocations: one open/close cycle, one JSON.
3. Always pass `--expect` when a command has a reply; silence is not success.
4. Use `--drain-ms` after a reset, or you will match the previous session's output.
5. Never parse stderr as data; never rely on fields not listed here.
6. `DEMO` proves the flow, not the driver. Do not infer real timings from it.
