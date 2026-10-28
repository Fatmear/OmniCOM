"""Launcher tab icon, derived from the app logo (one source of truth).

Previously this was a hand-drawn 3x3 grid with hard-coded teal/blue/violet
tints, so it stopped matching the app icon and the accent themes (R-075).
"""
from __future__ import annotations

import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "logo.png")
TARGET = os.path.join(ROOT, "plugins", "launcher", "icon.png")
SIZE = 64

image = Image.open(SOURCE).convert("RGBA")
image.thumbnail((SIZE, SIZE), Image.LANCZOS)
canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
canvas.paste(image, ((SIZE - image.width) // 2, (SIZE - image.height) // 2), image)
canvas.save(TARGET)
print("wrote", TARGET, canvas.size)
