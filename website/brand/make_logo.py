"""Build Murakumo's logo files: the mura / kumo wordmark and the blade-k icon.

    uv run --no-project --with fonttools --with brotli --with uharfbuzz python website/brand/make_logo.py

The letters are Outfit Bold (SIL Open Font License 1.1, https://github.com/Outfitio/Outfit-Fonts),
turned into plain shapes, so the SVG files need no font. The script downloads the font the first time
into website/brand/.cache/ (ignored by git).

Writes, in website/home/brand/:
  murakumo.svg        the wordmark for light backgrounds: "mura" in ink, a blade cut, "kumo" in blue
  murakumo-dark.svg   the same for dark backgrounds
  icon.svg            the icon: a k whose arm is a blade, on a blue tile
and copies icon.svg to website/home/mark.svg and website/overrides/assets/mark.svg (the site's and
the docs' favicon) and apps/canvas/public/favicon.svg.
"""

from __future__ import annotations

import math
import shutil
import urllib.request
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CACHE = HERE / ".cache"
FONT_URL = "https://cdn.jsdelivr.net/fontsource/fonts/outfit@5.2.5/latin-700-normal.woff2"

INK, BLUE = "#17191c", "#2457d6"
INK_DARK, BLUE_DARK = "#ececea", "#6f9bff"


def load_font() -> Path:
    ttf = CACHE / "outfit-700.ttf"
    if not ttf.exists():
        CACHE.mkdir(exist_ok=True)
        woff2 = CACHE / "outfit-700.woff2"
        urllib.request.urlretrieve(FONT_URL, woff2)
        font = TTFont(woff2)
        font.flavor = None
        font.save(ttf)
    return ttf


FONT = load_font()
font = TTFont(FONT)
glyphs = font.getGlyphSet()
UPEM = font["head"].unitsPerEm
hbfont = hb.Font(hb.Face(hb.Blob.from_file_path(str(FONT))))


def shape(text: str, size: float = 100, tracking: float = -0.02) -> tuple[list[str], float]:
    """The glyph outlines of text as SVG path data, baseline at y=0, and the total advance."""
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf)
    scale, x, order, paths = size / UPEM, 0.0, font.getGlyphOrder(), []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        pen = SVGPathPen(glyphs)
        transform = (scale, 0, 0, -scale, x + pos.x_offset * scale, -pos.y_offset * scale)
        glyphs[order[info.codepoint]].draw(TransformPen(pen, transform))
        paths.append(pen.getCommands())
        x += pos.x_advance * scale + tracking * size
    return paths, x


def fmt(points) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def blade(x0, y0, x1, y1, bulge, thick, steps=40) -> str:
    """A slightly curved blade from (x0, y0) to (x1, y1): pointed at both ends, widest in the middle."""
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    one, two = [], []
    for i in range(steps + 1):
        t = i / steps
        off = bulge * 4 * t * (1 - t)
        half = thick * math.sin(math.pi * t) ** 0.7 / 2
        bx, by = x0 + dx * t, y0 + dy * t
        one.append((bx + nx * (off + half), by + ny * (off + half)))
        two.append((bx + nx * (off - half), by + ny * (off - half)))
    return fmt(one + two[::-1])


def svg(inner: str, box: tuple[float, float, float, float], title: str = "Murakumo") -> str:
    x, y, w, h = box
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:g} {y:g} {w:.1f} {h:g}" role="img" aria-label="{title}">'
        f"<title>{title}</title>{inner}</svg>\n"
    )


def wordmark(ink: str, blue: str) -> str:
    """mura / kumo: the two halves of the name (gathering / cloud), parted by a clean cut."""
    mura, mura_w = shape("mura")
    kumo, kumo_w = shape("kumo")
    gap = 30
    cut = mura_w + gap / 2 - 4
    left = "".join(f'<path d="{d}"/>' for d in mura)
    right = "".join(f'<path d="{d}"/>' for d in kumo)
    inner = (
        f'<g fill="{ink}">{left}</g>'
        f'<polygon points="{blade(cut - 12, 18, cut + 16, -88, 2.4, 6.5)}" fill="{blue}"/>'
        f'<g fill="{blue}" transform="translate({mura_w + gap:.2f} 0)">{right}</g>'
    )
    return svg(inner, (-4, -92, mura_w + gap + kumo_w + 8, 114))


def icon(blue: str = BLUE) -> str:
    """The k, its arm a blade, white on a blue rounded tile."""
    (k,), _ = shape("k")
    no_arm = [(-5, -110), (15.6, -110), (15.6, -19), (70, -19), (70, 30), (-5, 30)]
    inner = (
        '<defs><clipPath id="m-arm"><polygon points="' + fmt(no_arm) + '"/></clipPath>'
        '<clipPath id="m-tile"><rect width="128" height="128" rx="28"/></clipPath></defs>'
        f'<g clip-path="url(#m-tile)"><rect width="128" height="128" fill="{blue}"/>'
        '<g transform="translate(38 108) scale(1.02)" fill="#fff">'
        f'<path d="{k}" clip-path="url(#m-arm)"/><polygon points="{blade(14, -16, 64, -88, 3.2, 9.5)}"/></g></g>'
    )
    return svg(inner, (0, 0, 128, 128))


def main() -> None:
    out = ROOT / "website" / "home" / "brand"
    out.mkdir(parents=True, exist_ok=True)
    (out / "murakumo.svg").write_text(wordmark(INK, BLUE))
    (out / "murakumo-dark.svg").write_text(wordmark(INK_DARK, BLUE_DARK))
    (out / "icon.svg").write_text(icon())
    for target in ("website/home/mark.svg", "website/overrides/assets/mark.svg", "apps/canvas/public/favicon.svg"):
        shutil.copyfile(out / "icon.svg", ROOT / target)
    print(f"Wrote the logo files to {out.relative_to(ROOT)}/ and the favicons.")


if __name__ == "__main__":
    main()
