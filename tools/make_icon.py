"""Generate assets/omnicom.ico (multi-size) to match the in-app logo."""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw


def make(size: int) -> Image.Image:
    scale = 8
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # rounded dark plate
    radius = int(canvas * 0.28)
    draw.rounded_rectangle([0, 0, canvas - 1, canvas - 1], radius=radius, fill=(8, 11, 18, 255))

    # ambient glow
    glow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    for step in range(26, 0, -1):
        alpha = int(2.2 * step)
        pad = int(canvas * 0.16) - step * 2
        gdraw.ellipse([pad, pad, canvas - pad, canvas - pad],
                      fill=(30, 200, 190, min(alpha, 46)))
    image.alpha_composite(glow)

    # wave arc, teal -> violet
    steps = 240
    width = max(2, int(canvas * 0.055))
    points = []
    for index in range(steps + 1):
        t = index / steps
        x = canvas * (0.18 + 0.64 * t)
        y = canvas * (0.63 - 0.24 * math.sin(math.pi * t))
        points.append((x, y))
    for index in range(len(points) - 1):
        t = index / (len(points) - 1)
        color = (
            int(94 + (167 - 94) * t),
            int(234 + (139 - 234) * t),
            int(212 + (250 - 212) * t),
            255,
        )
        draw.line([points[index], points[index + 1]], fill=color, width=width, joint="curve")

    # end caps
    for cx, cy, color in (
        (canvas * 0.18, canvas * 0.63, (94, 234, 212, 255)),
        (canvas * 0.82, canvas * 0.63, (167, 139, 250, 255)),
    ):
        r = canvas * 0.052
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "assets")
    os.makedirs(out_dir, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [make(size) for size in sizes]
    target = os.path.join(out_dir, "omnicom.ico")
    images[-1].save(target, format="ICO", sizes=[(s, s) for s in sizes])
    images[-1].save(os.path.join(out_dir, "omnicom.png"))
    print("wrote", target, os.path.getsize(target), "bytes")


if __name__ == "__main__":
    main()
