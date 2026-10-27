"""Turn logo.png into a transparent-background app icon.

The source logo sits on a solid (near-white) plate.  Simply keying out white
would also punch holes in light parts of the mark, so the alpha is derived from
a flood-fill of the *border* colour instead: only pixels connected to the image
edge and close to the plate colour become transparent.  Interior white stays
opaque, and the edge is feathered so the result has no jaggies at 16 px.

Outputs (all from one run):
    assets/omnicom.png   256 px master
    assets/omnicom.ico   multi-size Windows icon (16/24/32/48/64/128/256)
    assets/logo-64.png   title-bar tile
"""

from __future__ import annotations

import os
import sys
from collections import deque

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "logo.png")
ASSETS = os.path.join(ROOT, "assets")
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _plate_colour(image: Image.Image) -> tuple[int, int, int]:
    """Most common colour along the border - that is the backdrop plate."""
    width, height = image.size
    counts: dict[tuple[int, int, int], int] = {}
    step = max(1, min(width, height) // 200)
    for x in range(0, width, step):
        for y in (0, height - 1, 1, height - 2):
            pixel = image.getpixel((x, y))[:3]
            counts[pixel] = counts.get(pixel, 0) + 1
    for y in range(0, height, step):
        for x in (0, width - 1, 1, width - 2):
            pixel = image.getpixel((x, y))[:3]
            counts[pixel] = counts.get(pixel, 0) + 1
    return max(counts, key=counts.get)


def make_transparent(image: Image.Image, tolerance: int = 34) -> Image.Image:
    image = image.convert("RGBA")
    width, height = image.size
    plate = _plate_colour(image)
    pixels = image.load()

    def close_to_plate(pixel: tuple[int, int, int]) -> bool:
        return (abs(pixel[0] - plate[0]) <= tolerance
                and abs(pixel[1] - plate[1]) <= tolerance
                and abs(pixel[2] - plate[2]) <= tolerance)

    # Flood fill from every border pixel: only the *connected* plate goes away.
    transparent = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        index = y * width + x
        if transparent[index]:
            continue
        if not close_to_plate(pixels[x, y][:3]):
            continue
        transparent[index] = 1
        if x > 0:
            queue.append((x - 1, y))
        if x + 1 < width:
            queue.append((x + 1, y))
        if y > 0:
            queue.append((x, y - 1))
        if y + 1 < height:
            queue.append((x, y + 1))

    # Soften the boundary: pixels adjacent to a transparent one get partial alpha
    # so a scaled-down icon does not show a hard 1 px halo.
    #
    # The source mask must stay untouched while we read it.  Writing the
    # feathered value back into the mask being tested made 75 count as a "hole",
    # which cascaded inwards and feathered the entire mark (0% opaque).
    mask = bytearray(width * height)
    for index in range(width * height):
        mask[index] = 0 if transparent[index] else 255

    alpha = bytearray(mask)
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            index = y * width + x
            if not mask[index]:
                continue
            holes = sum(1 for value in (mask[index - 1], mask[index + 1],
                                        mask[index - width], mask[index + width])
                        if value == 0)
            if holes:
                alpha[index] = max(70, 235 - 55 * holes)

    out = Image.new("RGBA", (width, height))
    out_pixels = out.load()
    for y in range(height):
        for x in range(width):
            index = y * width + x
            r, g, b = pixels[x, y][:3]
            a = alpha[index]
            if a == 0:
                out_pixels[x, y] = (r, g, b, 0)
                continue
            # Un-premultiply the plate so edges keep the mark's own colour.
            if a < 255:
                blend = a / 255.0
                r = int(max(0, min(255, (r - plate[0] * (1 - blend)) / blend)))
                g = int(max(0, min(255, (g - plate[1] * (1 - blend)) / blend)))
                b = int(max(0, min(255, (b - plate[2] * (1 - blend)) / blend)))
            out_pixels[x, y] = (r, g, b, a)

    return out.crop(out.getbbox() or (0, 0, width, height))


def square(image: Image.Image, size: int, pad_ratio: float = 0.06) -> Image.Image:
    """Fit the mark on a transparent square canvas with a little breathing room."""
    inner = int(size * (1 - pad_ratio * 2))
    scaled = image.copy()
    scaled.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2), scaled)
    return canvas


def main() -> int:
    if not os.path.isfile(SOURCE):
        print(f"logo not found: {SOURCE}")
        return 1
    os.makedirs(ASSETS, exist_ok=True)
    source = Image.open(SOURCE)
    print(f"source: {source.size} {source.mode}")
    transparent = make_transparent(source)
    print(f"trimmed: {transparent.size}")

    master = square(transparent, 256)
    master.save(os.path.join(ASSETS, "omnicom.png"))
    square(transparent, 64, pad_ratio=0.02).save(os.path.join(ASSETS, "logo-64.png"))
    square(transparent, 32, pad_ratio=0.02).save(os.path.join(ASSETS, "logo-32.png"))

    frames = [square(transparent, size, pad_ratio=0.02 if size <= 32 else 0.06)
              for size in ICON_SIZES]
    frames[-1].save(os.path.join(ASSETS, "omnicom.ico"), format="ICO",
                    sizes=[(s, s) for s in ICON_SIZES])

    # -- report how transparent it really is
    alpha = master.getchannel("A")
    histogram = alpha.histogram()
    total = sum(histogram)
    clear = histogram[0] / total
    opaque = histogram[255] / total
    print(f"alpha: {clear:.1%} fully transparent, {opaque:.1%} fully opaque, "
          f"{1 - clear - opaque:.1%} feathered")
    print("wrote assets/omnicom.png, assets/omnicom.ico, assets/logo-64.png, assets/logo-32.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
