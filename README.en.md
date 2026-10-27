# OmniCOM 0.7.0

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

### 1. Quick start

* Double-click `start.cmd`, or run `python run.py`. Add `--demo` for a virtual DEMO port.
* Pick a port on the left, set the parameters, then press *Open port* - data starts appearing on the *Serial log* page.
* Type in the box at the bottom and press Ctrl+Enter or *Send*. Line ending, HEX and timed send live to its right.

### 2. Serial log

* Each line reads `[hh:mm:ss.mmm]RX←◇payload` (received) or `TX→◇payload` (sent). Switch the language and the two direction markers change with it.
* *Auto-scroll to latest* always follows the newest line. Turn it off and a *back to latest* bubble appears while you read history.
* *Show \r \n and other control chars* applies to both directions, so you can see the line ending that was really sent.
* *HEX view* prints 16 bytes per line: offset, hex, ASCII.
* *Save view* writes the log to a file. *Capture to file* writes the raw bytes in the background and never misses a byte, regardless of what the UI shows.
* Right-click the log to copy, copy all, save, find or jump to latest.

### 3. Terminal

* A real console page for a linux serial login: click it and every keystroke goes out.
* Enter sends CR (selectable CR/LF/CRLF), Ctrl+C sends 0x03, arrow keys send ANSI.
* Handles CR-overwrite, backspace, TAB, ANSI colours and clear-screen sequences.
* Copy with Ctrl+Shift+C, paste with Ctrl+Shift+V (plain Ctrl+C is the interrupt byte).

### 4. File transfer

* Pick XMODEM / YMODEM / ZMODEM, choose direction and file, press Start.
* Received files land in `omnicom-data/received/`.
* The port is exclusive during a transfer, so the log page stays quiet.

### 5. Commands

* Click a command to send it, or tick several and press *Send in order* (each keeps its own delay).
* *Groups* organise them: the folder button creates one, groups take a note, and they collapse.
* Every command supports a note, line ending, delay, timed repeat and HEX.
* Right-click a command to move it to another group or export just that one; the save icon on a group header exports the whole group.
* The exported JSON can be imported on another machine - groups merge automatically.
* Pasting several lines creates several commands.

### 6. Search

* Type a keyword and press Enter: it searches **the whole log, history included**, and lists hits as `line-number  text`.
* Double-click any row to jump to that line in the log; it flashes.
* Modes: Plain / Fuzzy (characters in order) / Whole word / Regex, plus a *Match case* switch.
* The expand arrow blows the results up to the full window; Esc exits.

### 7. Plugins

* A plugin is a folder under `plugins/` or `omnicom-data/plugins/`; each one gets its own tab in the top bar.
* Copy the whole folder to another machine's `plugins/`, press *Plugins → Reload* and it is installed.
* Ships with *Launcher*: add your apps, see a badge with the running instance count, and choose between opening another window or switching to the existing one.

### 8. Preferences and language

* *Settings → Preferences* switches the interface language. Chinese shows only Chinese, English only English - context menus included.
* The change applies immediately, no restart.

### 9. Command line (for scripts / AI)

* `python -m omnicom.cli ports` lists ports; `send` sends and waits for a reply; `read` reads; `monitor` streams.
* `session --script file.json` runs a scripted exchange.
* Every command writes exactly one JSON document to stdout; see `docs/CLI.md`.

### 10. Where the data lives

* `omnicom-data/logs/` captures, `omnicom-data/received/` transferred files,
* `omnicom-data/commands.json` commands, `omnicom-data/plugins/` extra plugins.

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
