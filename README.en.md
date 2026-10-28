# OmniCOM 1.1.2

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
* No extra console window opens on launch; add `--keep-console` to keep one.
* To hand the app to someone else run `python tools/make_dist.py --zip` and send the `dist/OmniCOM/` folder (or the zip of the same name): it bundles the Python runtime and every dependency, so nothing has to be installed - `OmniCOM.cmd` starts the app.
* Pick a port on the left, set the parameters, then press *Open port* - data starts appearing on the *Serial log* page.
* Click the baud field and type a custom value. Enter or moving focus applies it; the dropdown still offers common rates.
* Type in the box at the bottom and press Ctrl+Enter or *Send*. Line ending, HEX and timed send live to its right.
* Drag the divider above the composer to grow the actual text editor; its height is restored on the next launch.

### 2. Serial log

* Each line reads `[hh:mm:ss.mmm]RX←◇payload` (received) or `TX→◇payload` (sent). Switch the language and the two direction markers change with it.
* *Auto-scroll to latest* always follows the newest line. Turn it off and a *back to latest* bubble appears while you read history.
* *Keep screen awake* stops Windows from blanking or sleeping on idle - handy during a long capture. It is released automatically when OmniCOM closes.
* *Show \r \n and other control chars* applies to both directions, so you can see the line ending that was really sent.
* *HEX view* prints 16 bytes per line: offset, hex, ASCII.
* Clearing the log also resets the RX/TX counters in the title bar.
The log is buffered by size: 100 MB of text by default (4-500 MB in Preferences).
When it is full the oldest lines are dropped and the newest are always kept; the log
toolbar reports how many lines went, and a fast peer never stalls the window.
File sending is two steps: *Open file* chooses the pending file, *Send file* transmits it;
the bytes are printed as normal send lines and a progress bar tracks the transfer.
* *Save view* writes the log to a file. *Capture RX + TX* writes every byte of both directions to one file in the background and never misses a byte, regardless of what the UI shows. *Clear capture file* empties it and keeps recording.
* Right-click the log to copy, copy all, save, find or jump to latest.
* The port button is neutral while disconnected and coloured only when connected. Opening and closing run in the background; the button is temporarily disabled.

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
* *Groups* organise them: the folder button creates one, groups take a note, and they collapse. Creating or renaming a group rejects existing names after trimming leading and trailing whitespace.
* Each command and its send/edit/delete actions share one primary line. A colour strip shows selection; notes appear below in smaller text. Hover for full text and delay.
* Every command supports a note, line ending, delay, timed repeat and HEX.
* Right-click a command to move groups. Use the export button at the top to select commands or a group to export.
* The exported JSON can be imported on another machine - groups merge automatically.
* Pasting several lines creates several commands.

### 6. Search

* Type a keyword and press Enter: it searches **the whole log, history included**, and lists hits as `line-number  text`.
* Select log text and right-click Find to replace the old query and search literally. A multi-line selection matches any of its lines.
* Double-click any row to jump to that line in the log; it flashes.
* Modes: Plain / Fuzzy (characters in order) / Whole word / Regex, plus a *Match case* switch.
* The expand arrow blows the results up to the full window; Esc exits.

### 7. Plugins

* A plugin is a folder under `plugins/` or `omnicom-data/plugins/`; each one gets its own tab in the top bar.
* Copy the whole folder to another machine's `plugins/`, press *Plugins → Reload* and it is installed.
* Ships with *Launcher*: the top row holds shortcuts (click opens a new window) and the bottom row holds running windows (click to switch to that window).
* Both rows can be reordered by dragging. Rename a running window to tell two instances apart (A and B); long names wrap instead of being covered.
* Removing a shortcut keeps its windows open; *End this window* closes just that one window.

### 8. Preferences and language

* *Settings → Preferences* switches the interface language. Chinese shows only Chinese, English only English - context menus included.
* The same page sets the log buffer size (4-500 MB, 100 MB by default), the default capture folder, a solid accent colour, or a wallpaper image. With a wallpaper the panes - and the hover tooltip - become liquid glass: the blurred photo behind them plus a translucent white wash; without one they stay plain white. The bottom status captions have no frame or fill at all, just text.
* The change applies immediately, no restart.

### 9. Command line (for scripts / AI)

* `python -m omnicom.cli ports` lists ports; `send` sends and waits for a reply; `read` reads; `monitor` streams.
* `session --script file.json` runs a scripted exchange.
* Every command writes exactly one JSON document to stdout; see `docs/CLI.md`.

### 10. Where the data lives

* `omnicom-data/logs/` captures (or the folder chosen in Preferences), `omnicom-data/received/` transferred files,
* `omnicom-data/commands.json` commands, `omnicom-data/plugins/` extra plugins. The *Records* label in the bottom-right corner shows the folder captures really go to.

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
