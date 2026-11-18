"""Draws the mobile app's icons from the same design as the PC icon (tools/make_icon.py).

    .venv\\Scripts\\python tools\\make_mobile_icons.py

Writes into mobile/assets:
  icon.png                     the app icon (iOS and stores): full square, no transparency
  android-icon-foreground.png  shield on transparent, inside Android's safe zone
  android-icon-background.png  the gradient alone
  android-icon-monochrome.png  one-colour shield for themed Android icons
  splash-icon.png              white shield for the start screen
  notification-icon.png        small white silhouette for the status bar
  favicon.png
"""

from pathlib import Path

from PIL import Image, ImageDraw

from make_icon import BOTTOM, CHECK, SIZE, TOP, _bezier

OUT = Path(__file__).resolve().parent.parent / "mobile" / "assets"
SS = 2  # draw at twice the size, then scale down, for smooth edges


def gradient(size: int) -> Image.Image:
    column = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    return column.resize((size, size)).convert("RGBA")


def shield(scale: float, check, body=(255, 255, 255, 255)) -> Image.Image:
    """The shield with its check mark, centred on a transparent square.

    scale: 1.0 is the size it has on the PC icon. check: colour, or None to cut the
    mark out of the shield (for one-colour icons).
    """
    big = SIZE * SS
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def at(x, y):  # scale around the shield's own centre
        cx, cy = 512, 527
        return ((cx + (x - cx) * scale) * SS, (cy + (y - cy) * scale) * SS)

    left = list(_bezier((512, 205), (400, 290), (252, 300)))
    left += list(_bezier((252, 520), (252, 730), (512, 850)))
    right = [(SIZE - x, y) for x, y in reversed(left)]
    d.polygon([at(x, y) for x, y in left + right], fill=body)
    pts = [at(385, 520), at(480, 615), at(650, 425)]
    width = round(72 * scale * SS)
    ink = check if check is not None else (0, 0, 0, 0)
    if check is None:  # cut out: draw on the alpha channel
        alpha = layer.getchannel("A")
        da = ImageDraw.Draw(alpha)
        da.line(pts, fill=0, width=width, joint="curve")
        for x, y in (pts[0], pts[-1]):
            da.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=0)
        layer.putalpha(alpha)
    else:
        d.line(pts, fill=ink, width=width, joint="curve")
        for x, y in (pts[0], pts[-1]):
            d.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=ink)
    return layer.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    blue = CHECK + (255,)

    icon = gradient(SIZE)
    icon.alpha_composite(shield(1.0, blue))
    icon.convert("RGB").save(OUT / "icon.png")

    # Android crops the outer third of adaptive icons, so the shield is drawn smaller there.
    shield(0.62, blue).save(OUT / "android-icon-foreground.png")
    gradient(SIZE).convert("RGB").save(OUT / "android-icon-background.png")
    shield(0.62, None).save(OUT / "android-icon-monochrome.png")

    shield(1.25, None).save(OUT / "splash-icon.png")
    shield(1.3, None).resize((96, 96), Image.LANCZOS).save(OUT / "notification-icon.png")
    icon.resize((48, 48), Image.LANCZOS).convert("RGB").save(OUT / "favicon.png")
    print("wrote the mobile icons to", OUT)


if __name__ == "__main__":
    main()
