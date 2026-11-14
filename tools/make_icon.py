"""Draws the Tuckvault icon and writes tuckvault/assets/tuckvault.ico and .png.

Run after changing the design (needs Pillow, a development-only dependency):

    .venv\\Scripts\\python tools\\make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
TOP, BOTTOM = (20, 184, 166), (29, 78, 216)  # teal to blue
CHECK = (23, 92, 190)
OUT = Path(__file__).resolve().parent.parent / "tuckvault" / "assets"


def _bezier(p0, p1, p2, steps=40):
    for i in range(steps + 1):
        t = i / steps
        yield ((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
               (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])


def draw() -> Image.Image:
    # background: vertical gradient clipped to a rounded square
    gradient = Image.new("RGB", (1, SIZE))
    for y in range(SIZE):
        t = y / (SIZE - 1)
        gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    gradient = gradient.resize((SIZE, SIZE))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((32, 32, SIZE - 32, SIZE - 32), radius=210, fill=255)
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    d = ImageDraw.Draw(icon)
    # shield: peaked top, straight sides, curved to a point
    left = list(_bezier((512, 205), (400, 290), (252, 300)))
    left += list(_bezier((252, 520), (252, 730), (512, 850)))
    right = [(SIZE - x, y) for x, y in reversed(left)]
    d.polygon(left + right, fill=(255, 255, 255, 255))
    # check mark
    pts = [(385, 520), (480, 615), (650, 425)]
    d.line(pts, fill=CHECK, width=72, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - 36, y - 36, x + 36, y + 36), fill=CHECK)
    return icon


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icon = draw()
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "tuckvault.png")
    icon.save(OUT / "tuckvault.ico",
              sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print("wrote", OUT / "tuckvault.ico", "and tuckvault.png")


if __name__ == "__main__":
    main()
