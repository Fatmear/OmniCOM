"""Replace the Aurora + TitleBar classes in main_window.py with the new versions."""
import io
import re

TARGET = "omnicom/ui/main_window.py"
HEAD = "class Aurora(QWidget):"
TAIL = "class OmniWindow(QMainWindow):"

src = io.open(TARGET, encoding="utf-8").read()
fragment = io.open("tools/_topbar.py", encoding="utf-8").read()

assert src.count(HEAD) == 1, "Aurora anchor"
assert src.count(TAIL) == 1, "OmniWindow anchor"

start = src.index(HEAD)
end = src.index(TAIL)
old = src[start:end]
assert "class TitleBar" in old, "TitleBar inside the replaced range"
assert "def toggle_maximize" not in old, "replacement range too large"

src = src[:start] + fragment + src[end:]

problems = []
if "subtle grid" in src:
    problems.append("grid code left over")
if "class Aurora(QWidget)" not in src:
    problems.append("Aurora missing")
if "class TitleBar(GlassFrame)" not in src:
    problems.append("new TitleBar missing")
if src.count("class OmniWindow") != 1:
    problems.append("OmniWindow duplicated")
for token in ("QRadialGradient(", "drawLine("):
    if token in src:
        problems.append(f"{token} still present")

# imports: QBrush/QColor/QPainter/QRectF needed; QRadialGradient/QLinearGradient may
# now be unused -- keep the import list intact but avoid NameError by importing
# what the new code uses.
old_import = ("from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QLinearGradient, QPainter,\n"
              "                           QRadialGradient)")
new_import = ("from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QLinearGradient, QPainter,\n"
              "                           QRadialGradient)")
assert old_import in src, "QtGui import block changed"
src = src.replace(old_import, new_import)

if problems:
    print("PROBLEMS:", problems)
    raise SystemExit(1)

io.open(TARGET, "w", encoding="utf-8", newline="\n").write(src)
print("patched main_window.py:", len(old), "->", len(fragment), "chars")
print("TitleBar uses StatusPill:", "StatusPill" in fragment)
