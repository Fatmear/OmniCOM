"""Draw the launcher plugin's tab icon (a 3x3 app grid)."""

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "plugins", "launcher", "icon.png")
SIZE = 64

image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)

tile, gap, radius = 14, 6, 4
start = (SIZE - (tile * 3 + gap * 2)) // 2
tints = [(11, 122, 110), (72, 132, 240), (116, 85, 245)]
index = 0
for row in range(3):
    for column in range(3):
        x = start + column * (tile + gap)
        y = start + row * (tile + gap)
        color = tints[index % len(tints)]
        draw.rounded_rectangle((x, y, x + tile, y + tile), radius=radius,
                               fill=(*color, 255 if index != 4 else 120))
        index += 1

image.save(TARGET)
print("wrote", TARGET, image.size)
