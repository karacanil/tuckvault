"""The square code a phone scans to pair (a QR code), drawn with the small `segno` library.

segno is the one optional part of Tuckvault: without it everything works as before and
pairing falls back to typing the address and the short code.
"""


def _make(text: str):
    try:
        import segno
    except ImportError:
        return None
    return segno.make(text, error="m", micro=False)


def matrix(text: str):
    """Rows of booleans (True = dark), without the quiet border; None if segno is missing."""
    code = _make(text)
    return None if code is None else [[bool(cell) for cell in row] for row in code.matrix]


def terminal(text: str, border: int = 2):
    """The code as lines of block characters for a terminal with a dark background; None if segno is missing."""
    rows = matrix(text)
    if rows is None:
        return None
    width = len(rows) + 2 * border
    blank = [False] * width
    grid = [blank] * border + [[False] * border + row + [False] * border for row in rows] + [blank] * border
    if len(grid) % 2:
        grid.append(blank)
    # Two rows of the code per line of text. Light parts are drawn, dark parts are left as the background.
    blocks = {(False, False): "█", (False, True): "▀", (True, False): "▄", (True, True): " "}
    return "\n".join("".join(blocks[(top, bottom)] for top, bottom in zip(grid[i], grid[i + 1]))
                     for i in range(0, len(grid), 2))


def save_png(text: str, path: str, scale: int = 12) -> bool:
    """Write the code as a picture. False if segno is missing."""
    code = _make(text)
    if code is None:
        return False
    code.save(path, kind="png", scale=scale, border=4)
    return True
