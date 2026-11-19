"""Generate 64x64 icons for the terminal / transfer plugins.

Same visual language as the launcher icon: rounded white tile, teal glyph.
"""

import pathlib
import sys

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIZE = 64
SCALE = 8                      # draw big, downsample for smooth edges
TEAL = (19, 132, 150)
WHITE = (255, 255, 255)


def tile() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (SIZE * SCALE, SIZE * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [0, 0, SIZE * SCALE - 1, SIZE * SCALE - 1],
        radius=int(SIZE * SCALE * 0.22), fill=(*WHITE, 255))
    return image, draw


def save(image: Image.Image, path: pathlib.Path) -> None:
    image.resize((SIZE, SIZE), Image.LANCZOS).save(path)
    print("wrote", path)


def terminal_icon() -> None:
    image, draw = tile()
    s = SCALE
    width = 3 * s
    # ">" chevron
    draw.line([(16 * s, 20 * s), (26 * s, 32 * s)], fill=TEAL, width=width)
    draw.line([(26 * s, 32 * s), (16 * s, 44 * s)], fill=TEAL, width=width)
    # underscore cursor
    draw.line([(32 * s, 44 * s), (46 * s, 44 * s)], fill=TEAL, width=width)
    save(image, ROOT / "plugins" / "terminal" / "icon.png")


def transfer_icon() -> None:
    image, draw = tile()
    s = SCALE
    width = 3 * s
    # up arrow
    draw.line([(22 * s, 46 * s), (22 * s, 20 * s)], fill=TEAL, width=width)
    draw.line([(22 * s, 20 * s), (15 * s, 29 * s)], fill=TEAL, width=width)
    draw.line([(22 * s, 20 * s), (29 * s, 29 * s)], fill=TEAL, width=width)
    # down arrow
    draw.line([(42 * s, 18 * s), (42 * s, 44 * s)], fill=TEAL, width=width)
    draw.line([(42 * s, 44 * s), (35 * s, 35 * s)], fill=TEAL, width=width)
    draw.line([(42 * s, 44 * s), (49 * s, 35 * s)], fill=TEAL, width=width)
    save(image, ROOT / "plugins" / "transfer" / "icon.png")


if __name__ == "__main__":
    try:
        terminal_icon()
        transfer_icon()
    except ImportError:
        print("Pillow is required", file=sys.stderr)
        raise SystemExit(1) from None
