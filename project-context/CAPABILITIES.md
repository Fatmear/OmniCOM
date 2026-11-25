# Current capabilities
Updated: 2026-11-25 (cycle: v1.1.7)

What the product does **today**. Status is about behaviour, not about code
existing. Refresh this at every handoff.

Status: working | partial | broken | planned | retired
Every non-`working` row cites a requirement or ledger id.

## Window and shell
| Capability | Status | Notes / evidence | ID |
| --- | --- | --- | --- |
| Frameless window, custom title bar, painted glyph buttons | working | R-100 brand block; glyph rendering verified | R-100 |
| Drag window edges/corners to resize, with matching cursors | working | `WS_THICKFRAME` + `WM_NCCALCSIZE` + `WM_SETCURSOR` all proven on a real window | R-204/R-205/R-206 |
| Aero snap by dragging to the screen top | working | hand-implemented with 40 px hysteresis | R-140 |
| Per-window state in `window-<n>.ini` (portable, never the registry) | working | OS-locked slot per process | R-088 |
| Keep screen awake | working | thread-scoped request; close releases it (`0x80000003` -> `0x80000000`) | R-062/R-343 |

## Serial and log
| Capability | Status | Notes / evidence | ID |
| --- | --- | --- | --- |
| Lossless RX/TX with counted, surfaced overflow | working | pause buffers and replays; loss is never silent | R-017 |
| Auto port detection and auto reconnect | partial | reconnect logic exists; a real unplug test is still missing | L-002 |
| Millisecond timestamps, HEX view, invisible-character display | working | incl. timestamp on an unfilled HEX tail line | R-201/R-203 |
| Open a log file for read-only review and lock the port | working | replay + follow; estimated time axis is labelled as estimated | R-309/R-331 |
| Live chart replay from a log | partial | continuation lines of a wrapped frame are still dropped | R-331 |

## Terminal (plugin)
| Capability | Status | Notes / evidence | ID |
| --- | --- | --- | --- |
| Real VT100 screen (cursor keys do not delete characters) | working | grid model, 49 terminal checks | R-207/R-208 |
| Column-accurate selection, double/triple click, Shift-extend | working | real `QMouseEvent` coverage | R-210 |
| Reflow on resize without losing or shifting characters | working | 88->51->120 column round trip | R-214 |
| Working scrollbar (drag, click-to-jump, monotonic thumb) | working | geometry assertions at both ends | R-215/R-216 |
| CLI access to the screen and bare keys | working | mirror grid independent of the visible tab | R-211/R-212 |

## Dashboard (store plugin, chart)
| Capability | Status | Notes / evidence | ID |
| --- | --- | --- | --- |
| Five widget kinds, drag, resize, scroll, centre-add | working | line/gauge/value/bar/lamp | R-161/R-313/R-316 |
| Independent X/Y zoom that live refresh does not reset | working | range kept on the widget, not in the option | R-170 |
| Multi-source curves per card with a legend | working | row click, space, hover all self-painted | R-325 |
| 5000-point curves keep the same shape as the data slides | working | sampling disabled; drawn points equal source points | R-336 |
| Source picker hover/selection is light, never black | working | `_paint_color()` converts stylesheet `rgba()` | R-341 |
| Rubber-band multi-select and group drag | working | real PointerEvent coverage | R-337 |
| Dashboard refresh rate under heavy load | partial | ~5 batches/s at worst load, P95 ~249 ms; not 30 fps | R-320 |
| History lost before reaching the retention cap | broken | user-reported; direct feed does not reproduce | L-327 |

## Plugins and store
| Capability | Status | Notes / evidence | ID |
| --- | --- | --- | --- |
| Store install/update/uninstall, downgrade kept public | working | real uninstall moves every active copy | R-327 |
| Core plugins only enable/disable, never uninstall | working | `uninstall("terminal")` raises | R-330 |
| Plugins decoupled from host internals | working | no `import omnicom.*` in plugin trees | R-322 |
| Update check works on a machine with no system CA store | working | bundled `assets/cacert.pem`, proven in-package | R-342 |
| Releases stay downloadable, `latest` never hides old versions | working | only the `latest` pointer is managed | L-167 |

## CLI
| Capability | Status | Notes / evidence | ID |
| --- | --- | --- | --- |
| One JSON document per command, exit code equals `ok` | working | NDJSON only for `monitor` / repeat / multi-port | R-022/R-122 |
| Two processes on one port queue, never interleave | working | `PortLock` system file lock | R-125 |
| `--port DEMO` works with no hardware | working | virtual loopback | R-004 |

## Delivery
| Capability | Status | Notes / evidence | ID |
| --- | --- | --- | --- |
| Portable folder, no installer, no self-built exe | working | bundled CPython + trimmed Qt | R-019/R-083 |
| Build gate refuses a package whose HTTPS is broken | working | `make_dist._verify_tls()` runs in-bundle | R-342 |
| Core plugins ship, store plugins do not | working | `CORE_PLUGINS` = terminal/transfer only | R-174/R-293 |
