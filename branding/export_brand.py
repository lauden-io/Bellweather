"""Bellweather brand export: every final file, from the geometry in build_mark.py.

Writes:

- branding/badge.svg and branding/mark-*.svg: the vector sources;
- custom_components/bellweather/brand/*.png: the images Home Assistant shows.
  The icon is the badge, 256 and 512 px square. It is cobalt, which works on
  light and dark, so there is no dark_icon and Home Assistant falls back to
  icon.png. The logo is the badge plus the wordmark, 256 and 512 px high, with
  a dark_ variant (light wordmark);
- branding/sheet.html: the brand sheet as one self-contained file. It covers
  the logo in use, the marks, colours, construction, the reasons behind each
  choice, and usage rules.

The wordmark is Outfit Medium (SIL Open Font License). A headless Edge or
Chrome renders it from Google Fonts, black on white with greyscale
antialiasing, and the grey levels become the alpha channel. The PNGs get
Outfit's exact shapes without a local copy of the font. The script stops if
the font did not load, or if the measured stem-to-cap ratio is not Outfit
Medium's.

Run: python branding/export_brand.py   (needs shapely, Pillow, Edge or Chrome)
"""

from __future__ import annotations

import base64
import io
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from shapely import affinity
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_mark as bm  # noqa: E402

REPO = HERE.parent
BRAND = REPO / "custom_components" / "bellweather" / "brand"
S, K, G = bm.FINAL_SCALE, bm.FINAL_KIND, bm.GLINT_LONG
CLAPPER = Point(60, 107.5).buffer(7.5, 128)
FULL = unary_union([bm.silhouette(S, G), CLAPPER])
X0, Y0, X1, Y1 = FULL.bounds

# Logo (chosen 2026-11-10 after comparing 1.55 to 2.1 in use): the badge is
# 2.1 times the wordmark's cap height, the cap height is centred on the badge, and the
# space between the badge and the B is 0.6 cap height.
WORD = "Bellweather"
WORD_WEIGHT = 500
WORD_TRACKING = -0.01  # em
LOGO_RATIO = 2.1
LOGO_GAP = 0.6
STEM_PER_CAP = {400: 0.134, 500: 0.164, 600: 0.192, 700: 0.220}  # measured on Outfit, 2026-11-10
PAPER = "#F3F2EE"  # wordmark on dark

# name: (bell and clapper, vane and rod, sheen). A sheen of None is a cut-out: the
# background shows through, which reads as light only on light backgrounds. On dark, a
# cut-out would show the dark background and read as a groove, so the sheen is filled.
MARKS = {
    "color": (bm.COBALT, bm.SUN, None),
    "color-dark": (bm.COBALT_DARK, bm.SUN_DARK, PAPER),
    "mono-ink": (bm.INK, bm.INK, None),
    "mono-white": ("#FFFFFF", "#FFFFFF", None),
    "on-cobalt": ("#FFFFFF", bm.SUN, None),
}
SHEEN = G.simplify(0.015, preserve_topology=True)

BADGE_RADIUS = 27  # of 120: 22.5 %
BADGE_FILL = 0.8  # the mark's height over the badge's
# Corner smoothing: the curve leaves each straight edge with zero curvature
# and eases into the 27-unit arc, so curvature changes continuously along the
# whole outline (G2). A plain circular corner jumps from straight to round at
# one point, which the eye sees as a kink at large sizes. 0.6 means the arc
# keeps 40 % of the turn and each transition takes 30 %; the corner then
# reaches 1.6 radii along each edge.
BADGE_SMOOTHING = 0.6
# a : b of the transition cubic. 1.2 is the longest ease-in whose curvature
# still rises monotonically to 1/r (checked from 0.5 to 3; above 1.2 it overshoots).
CORNER_AB = 1.2


def _corner_params(r: float, s: float):
    """Local corner frame, corner point at the origin, u along the incoming
    edge (towards the corner), v along the outgoing one. Returns the reach p,
    the transition cubic's control lengths a, b, m, the arc's tangent angle at
    its ends (phi) and the arc centre offset q."""
    if s <= 0 or r <= 0:
        return r, 0.0, 0.0, 0.0, 0.0, r
    phi = math.radians(45 * s)
    p = (1 + s) * r
    # end curvature of the cubic = 1/r  ->  b = 3 m^2 / (2 r sin phi); a = CORNER_AB * b
    k2 = 3 * (1 + CORNER_AB) / (2 * r * math.sin(phi))
    k1 = math.sin(phi) + math.cos(phi)
    k0 = r * (math.cos(phi) - math.sin(phi)) - p
    m = (-k1 + math.sqrt(k1 * k1 - 4 * k2 * k0)) / (2 * k2)
    b = 3 * m * m / (2 * r * math.sin(phi))
    q = r * math.cos(phi) + m * math.sin(phi)
    return p, CORNER_AB * b, b, m, phi, q


def _corners(w: float, h: float, x: float, y: float, r: float, s: float, which: str):
    """Per corner, clockwise from the top right: (corner point, incoming
    edge direction, outgoing edge direction, smoothed?)."""
    tr, br, bl, tl = ((x + w, y), (1, 0), (0, 1)), ((x + w, y + h), (0, 1), (-1, 0)), ((x, y + h), (-1, 0), (0, -1)), ((x, y), (0, -1), (1, 0))
    on = {"all": (1, 1, 1, 1), "left": (0, 0, 1, 1)}[which]
    return [(k, e, n, bool(f) and r > 0) for (k, e, n), f in zip((tr, br, bl, tl), on, strict=True)]


def _corner_segments(k, e, n, r, s):
    """Start point, first cubic, (arc start, arc end, centre), second cubic."""
    p, a, b, m, phi, q = _corner_params(r, s)

    def at(u, v):
        return (k[0] + u * e[0] + v * n[0], k[1] + u * e[1] + v * n[1])

    su, sv = -q + r * math.sin(phi), q - r * math.cos(phi)  # arc start
    c1 = (at(-p + a, 0), at(-p + a + b, 0), at(su, sv))
    c2 = (at(0, p - a - b), at(0, p - a), at(0, p))  # the mirror image across the corner's diagonal
    return at(-p, 0), c1, (at(su, sv), at(-sv, -su), at(-q, q)), c2


def badge_path(w: float = 120, h: float = 120, x: float = 0, y: float = 0, r: float | None = None,
               s: float = BADGE_SMOOTHING, corners: str = "all") -> str:
    """The badge outline (or any box with badge corners) as SVG path data."""
    r = BADGE_RADIUS * min(w, h) / 120 if r is None else r
    out = []
    for i, (k, e, n, smooth) in enumerate(_corners(w, h, x, y, r, s, corners)):
        if not smooth:
            out.append(("M" if i == 0 else "L") + f"{k[0]:.3f} {k[1]:.3f}")
            continue
        p0, c1, (_, q1, _), c2 = _corner_segments(k, e, n, r, s)
        out.append(("M" if i == 0 else "L") + f"{p0[0]:.3f} {p0[1]:.3f}")
        out.append("C" + " ".join(f"{px:.3f} {py:.3f}" for px, py in c1))
        out.append(f"A{r:.3f} {r:.3f} 0 0 1 {q1[0]:.3f} {q1[1]:.3f}")
        out.append("C" + " ".join(f"{px:.3f} {py:.3f}" for px, py in c2))
    return "".join(out) + "Z"


def badge_polygon(w: float = 120, h: float = 120, x: float = 0, y: float = 0, r: float | None = None,
                  s: float = BADGE_SMOOTHING, corners: str = "all", steps: int = 64) -> Polygon:
    """The same outline, densely sampled, for the rasters."""
    r = BADGE_RADIUS * min(w, h) / 120 if r is None else r
    pts = []

    def cubic(p0, c):
        for i in range(1, steps + 1):
            t = i / steps
            pts.append(tuple((1 - t) ** 3 * p0[j] + 3 * (1 - t) ** 2 * t * c[0][j] + 3 * (1 - t) * t * t * c[1][j] + t ** 3 * c[2][j]
                             for j in (0, 1)))

    for k, e, n, smooth in _corners(w, h, x, y, r, s, corners):
        if not smooth:
            pts.append(k)
            continue
        p0, c1, (q0, q1, centre), c2 = _corner_segments(k, e, n, r, s)
        pts.append(p0)
        cubic(p0, c1)
        a0 = math.atan2(q0[1] - centre[1], q0[0] - centre[0])
        a1 = math.atan2(q1[1] - centre[1], q1[0] - centre[0])
        if a1 < a0:
            a1 += 2 * math.pi
        for i in range(1, steps + 1):
            t = a0 + (a1 - a0) * i / steps
            pts.append((centre[0] + r * math.cos(t), centre[1] + r * math.sin(t)))
        cubic(q1, c2)
    return Polygon(pts)
README_TEXT = ("Weather for your home in Home Assistant: radar, weather stations and satellite snow cover "
               "fused into one current picture, with a forecast blended from 11 models.")


def mark_layers(name: str):
    bell, top, sheen = MARKS[name]
    kind = "A" if bell == top else K
    if sheen is None:
        return bm.layers(kind, S, bell, top, G), bell
    # no cut: a filled sheen over a hole would leave a hairline of background around it
    return bm.layers(kind, S, bell, top, None) + [(SHEEN, sheen)], bell


def viewbox() -> str:
    return f"{X0 - 0.01:.2f} {Y0 - 0.01:.2f} {X1 - X0 + 0.02:.2f} {Y1 - Y0 + 0.02:.2f}"


def svg_mark(name: str) -> str:
    lays, bell = mark_layers(name)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox()}">{bm.mark_body(lays, bell)}</svg>\n'


def badge_placement():
    """Scale and offset that put the mark in the badge, centred on its bounding box."""
    k = BADGE_FILL * 120 / (Y1 - Y0)
    return k, 60 - (X0 + X1) / 2 * k, 60 - (Y0 + Y1) / 2 * k


def svg_badge() -> str:
    lays, bell = mark_layers("on-cobalt")
    k, tx, ty = badge_placement()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
            f'<path d="{badge_path()}" fill="{bm.COBALT}"/>'
            f'<g transform="translate({tx:.3f} {ty:.3f}) scale({k:.5f})">{bm.mark_body(lays, bell)}</g></svg>\n')


def badge_geoms():
    """The badge as [(geometry, colour)] on a 0..120 square, the same layout as badge.svg."""
    lays, bell = mark_layers("on-cobalt")
    k, tx, ty = badge_placement()
    square = badge_polygon()

    def place(g):
        return affinity.translate(affinity.scale(g, k, k, origin=(0, 0)), tx, ty)

    return [(square, bm.COBALT)] + [(place(g), c) for g, c in lays + [(CLAPPER, bell)]]


def rgb(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def raster_geoms(geoms, ppu: float, size: tuple[int, int], origin: tuple[float, float], ss: int = 8) -> Image.Image:
    """Shapes as RGBA; supersampled coverage, box-filtered down (exact area antialiasing)."""
    w, h = size
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    for geom, colour in geoms:
        mask = Image.new("L", (w * ss, h * ss), 0)
        draw = ImageDraw.Draw(mask)

        def px(c):
            return ((c[0] - origin[0]) * ppu * ss, (c[1] - origin[1]) * ppu * ss)

        for p in bm.polygons(geom):
            draw.polygon([px(c) for c in p.exterior.coords], fill=255)
            for ring in p.interiors:
                draw.polygon([px(c) for c in ring.coords], fill=0)
        layer = Image.new("RGBA", size, rgb(colour) + (255,))
        layer.putalpha(mask.resize(size, Image.BOX))
        out = Image.alpha_composite(out, layer)
    return out


def badge_icon(px: int) -> Image.Image:
    return raster_geoms(badge_geoms(), px / 120, (px, px), (0, 0))


def browser() -> str:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        shutil.which("msedge"), shutil.which("google-chrome"), shutil.which("chromium"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise SystemExit("the wordmark needs Edge or Chrome")


def wordmark_mask(weight: int = WORD_WEIGHT, cap_px: int = 800):
    """Outfit 'Bellweather' as an alpha mask; returns (mask, cap top row, baseline row, stem/cap)."""
    size = cap_px / 0.6925  # Outfit's cap height is about 0.69 em; the real value is measured below
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>wait</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@{weight}&display=block" rel="stylesheet">
<style>body{{margin:0;background:#fff;color:#000}}div{{font-family:Outfit;font-weight:{weight};font-size:{size:.1f}px;
line-height:1.3;padding:60px 80px;white-space:nowrap;letter-spacing:{WORD_TRACKING}em}}</style></head><body><div>{WORD}</div>
<script>document.fonts.ready.then(()=>{{document.title=document.fonts.check('{weight} 50px Outfit')?'OK':'NO'}})</script>
</body></html>"""
    width, height = int(size * 6.5) + 200, int(size * 1.3) + 160
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "word.html"
        shot = Path(tmp) / "word.png"
        page.write_text(html, encoding="utf-8")
        args = [browser(), "--headless=new", "--disable-gpu", "--disable-lcd-text", "--hide-scrollbars",
                f"--user-data-dir={Path(tmp) / 'profile'}", "--virtual-time-budget=10000"]
        dom = subprocess.run(args + ["--dump-dom", page.as_uri()], capture_output=True, text=True, timeout=180).stdout
        if "<title>OK</title>" not in dom:
            raise SystemExit("Outfit did not load from Google Fonts")
        subprocess.run(args + [f"--window-size={width},{height}", f"--screenshot={shot}", page.as_uri()],
                       capture_output=True, timeout=180)
        alpha = Image.open(shot).convert("L").point(lambda v: 255 - v)
    bbox = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    if bbox is None:
        raise SystemExit("the wordmark render is empty")
    alpha = alpha.crop(bbox)
    px = alpha.load()
    w, h = alpha.size
    row = int(h * 0.62)  # below the B's middle bar: the first dark run is the B's stem
    x = 0
    while px[x, row] <= 128:
        x += 1
    s0 = x
    while px[x, row] > 128:
        x += 1
    stem = x - s0
    col = [y for y in range(h) if px[(s0 + x) // 2, y] > 128]
    top, base = col[0], col[-1] + 1
    ratio = stem / (base - top)
    if abs(ratio - STEM_PER_CAP[weight]) > 0.012:
        raise SystemExit(f"stem/cap {ratio:.3f}: not Outfit {weight}")
    return alpha, top, base, ratio


def logo(text_colour: str, height: int, word) -> Image.Image:
    """Badge plus wordmark, trimmed: the badge's height is the image's height."""
    alpha, top, base, _ = word
    ppu = height / 120
    cap = 120 / LOGO_RATIO
    k = cap * ppu / (base - top)
    text = alpha.resize((round(alpha.width * k), round(alpha.height * k)), Image.BOX)
    tx = round((120 + LOGO_GAP * cap) * ppu)
    ty = round((60 + cap / 2) * ppu - base * k)
    if ty < 0 or ty + text.height > height:
        raise SystemExit("the wordmark does not fit the badge's height")
    width = tx + text.width
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out.paste(badge_icon(height), (0, 0))
    mask = Image.new("L", (width, height), 0)
    mask.paste(text, (tx, ty))
    layer = Image.new("RGBA", (width, height), rgb(text_colour) + (255,))
    layer.putalpha(mask)
    return Image.alpha_composite(out, layer)


def data_uri(img_or_path, mime: str = "image/png") -> str:
    if isinstance(img_or_path, Path):
        raw = img_or_path.read_bytes()
    elif isinstance(img_or_path, str):
        raw = img_or_path.encode("utf-8")
    else:
        buf = io.BytesIO()
        img_or_path.save(buf, "PNG", optimize=True)
        raw = buf.getvalue()
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def save_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)


def in_use(word) -> str:
    """The logo and icon in everyday settings (all inline)."""
    def lk(colour, h):
        return data_uri(logo(colour, h * 2, word))

    icon_uri = {px: data_uri(badge_icon(px * 2)) for px in (16, 24, 32, 40, 60)}
    readme = "".join(
        f'<div class="scene readme" style="background:{bg};border-color:{line}"><img class="h56" src="{lk(ink, 56)}" alt="">'
        f'<p style="color:{txt}">{README_TEXT}</p></div>'
        for bg, line, ink, txt in (("#ffffff", "#d0d7de", bm.INK, "#1f2328"), ("#0d1117", "#30363d", PAPER, "#e6edf3")))
    bars = "".join(
        f'<div class="scene bar" style="background:{bg};border-color:{line}"><img class="h28" src="{lk(ink, 28)}" alt="">'
        f'<nav style="color:{txt}">Docs<span>Sources</span><span>Releases</span></nav></div>'
        for bg, line, ink, txt in (("#ffffff", "#e3e3e3", bm.INK, "#444"), (bm.DARK_BG, "#333", PAPER, "#bbb")))
    skies = (f'<div class="scene" style="background:linear-gradient(165deg,#d6ebfc 0%,#a9d0f3 55%,#7fb4e8 100%)">'
             f'<img class="h72" src="{lk(bm.INK, 72)}" alt=""></div>'
             f'<div class="scene" style="background:linear-gradient(165deg,#2a3550 0%,#1b2236 60%,#121726 100%)">'
             f'<img class="h72" src="{lk(PAPER, 72)}" alt=""></div>')
    tiny = "".join(
        f'<div class="scene" style="background:{bg}"><div class="in">'
        f'<img class="h32" src="{lk(ink, 32)}" alt=""><img class="h20" src="{lk(ink, 20)}" alt=""></div></div>'
        for bg, ink in (("#fff", bm.INK), (bm.DARK_BG, PAPER)))
    icons = "".join(
        f'<div class="scene" style="background:{bg}"><div class="row-item">'
        f'<img src="{icon_uri[40]}" width="40" height="40" alt=""><div><b style="color:{t1}">Bellweather</b>'
        f'<small style="color:{t2}">Custom integration · Weather</small></div></div>'
        f'<div class="in" style="margin-top:18px">'
        + "".join(f'<img src="{icon_uri[p]}" width="{p}" height="{p}" alt="">' for p in (16, 24, 32))
        + f'<div class="home"><img src="{icon_uri[60]}" width="60" height="60" alt=""><span style="color:{t1}">Bellweather</span></div>'
        f'</div></div>'
        for bg, t1, t2 in (("#fff", "#1f2328", "#6b6b6b"), (bm.DARK_BG, "#eee", "#999")))
    return (f'<h3>README header</h3><div class="pair">{readme}</div>'
            f'<h3>Site header bar, 28 px high</h3><div class="pair">{bars}</div>'
            f'<h3>On sky and night backgrounds</h3><div class="pair">{skies}</div>'
            f'<h3>Tiny: 32 px and 20 px high</h3><div class="pair">{tiny}</div>'
            f'<h3>The icon: a list row, 16 / 24 / 32 px, a home screen</h3><div class="pair">{icons}</div>')


def sheet(word) -> str:
    c = bm.contrast
    syms = "".join(
        f'<symbol id="{n}" viewBox="{viewbox()}">{bm.mark_body(*mark_layers(n))}</symbol>' for n in MARKS
    )
    share = bm.sun_share(K, bm.COBALT, S)
    mark_width_share = (X1 - X0) / (Y1 - Y0)

    def use(n, h):
        w = h * (X1 - X0) / (Y1 - Y0)
        return f'<svg height="{h}" width="{w:.1f}"><use href="#{n}"/></svg>'

    def tile(inner, bg, label, caption="#6b6b6b"):
        return f'<figure style="background:{bg}"><div class="in">{inner}</div><figcaption style="color:{caption}">{label}</figcaption></figure>'

    swatches = [
        ("Cobalt", bm.COBALT, "Badge; bell and clapper of the open mark", f"{c(bm.COBALT, bm.LIGHT_BG):.1f} : 1 on light"),
        ("Sun", bm.SUN, "Vane and rod; an accent, never text", f"{c(bm.SUN, bm.COBALT):.1f} : 1 on cobalt"),
        ("White", "#FFFFFF", "Bell and clapper in the badge", f"{c('#FFFFFF', bm.COBALT):.1f} : 1 on cobalt"),
        ("Ink", bm.INK, "Wordmark, one-colour mark", f"{c(bm.INK, bm.LIGHT_BG):.1f} : 1 on light"),
        ("Paper", PAPER, "Wordmark on dark", f"{c(PAPER, bm.DARK_BG):.1f} : 1 on dark"),
        ("Cobalt light", bm.COBALT_DARK, "Open mark on dark: bell, clapper", f"{c(bm.COBALT_DARK, bm.DARK_BG):.1f} : 1 on dark"),
        ("Sun light", bm.SUN_DARK, "Open mark on dark: vane, rod", f"{c(bm.SUN_DARK, bm.DARK_BG):.1f} : 1 on dark"),
    ]
    sw = "".join(
        f'<div class="sw"><div class="chip" style="background:{h}"></div><b>{n}</b><code>{h}</code><span>{role}</span><span>{ct}</span></div>'
        for n, h, role, ct in swatches
    )
    dark_bg, light_bg = bm.DARK_BG, "#ffffff"
    badge_uri = data_uri(svg_badge(), "image/svg+xml")  # inline: the page works as a single file
    cap = 120 / LOGO_RATIO
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bellweather brand</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--bg:#F4F3EF;--fg:#1d2433;--muted:#5d6472}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 Outfit,system-ui,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:32px 16px 64px}}
h1{{font-weight:500;font-size:30px;margin:0 0 4px}}h2{{font-weight:500;font-size:21px;margin:44px 0 12px}}
h3{{font-weight:500;font-size:15px;color:var(--muted);margin:22px 0 8px}}
p.lead{{color:var(--muted);margin:0 0 24px;max-width:720px}}
.row{{display:flex;flex-wrap:wrap;gap:12px}}
figure{{margin:0;border-radius:16px;padding:20px;display:flex;flex-direction:column;gap:10px;min-width:0}}
figure .in{{display:flex;gap:18px;align-items:center;flex-wrap:wrap}}figcaption{{font-size:13px}}
.hero figure{{flex:1 1 420px}}.hero img{{max-width:100%;height:auto}}
.pair{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:12px}}
.scene{{border-radius:14px;padding:20px 22px;overflow:hidden;min-width:0}}
.scene img{{display:block;max-width:100%;width:auto;object-fit:contain;object-position:left center}}
.h72{{height:72px}}.h56{{height:56px}}.h32{{height:32px}}.h28{{height:28px}}.h20{{height:20px}}
.readme{{border:1px solid;font:15px/1.5 system-ui,sans-serif}}.readme p{{margin:14px 0 0}}
.bar{{border-bottom:1px solid;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 18px;border-radius:10px}}
.bar nav{{font:14px system-ui,sans-serif;white-space:nowrap}}.bar nav span{{margin-left:16px}}
.in{{display:flex;gap:18px;align-items:center;flex-wrap:wrap}}
.row-item{{display:flex;gap:14px;align-items:center;font-family:system-ui,sans-serif}}.row-item b{{display:block;font-weight:600;font-size:15px}}
.row-item small{{font-size:13px}}.home{{display:flex;flex-direction:column;align-items:center;gap:6px;font:12px system-ui,sans-serif;margin-left:12px}}
.sw{{background:#fff;border-radius:14px;padding:14px;width:158px;display:flex;flex-direction:column;gap:2px;font-size:13px}}
.sw .chip{{height:56px;border-radius:10px;margin-bottom:8px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.08)}}.sw b{{font-weight:500;font-size:15px}}
.sw span{{color:var(--muted)}}code{{font-size:13px}}
ul{{max-width:820px;padding-left:20px}}li{{margin:6px 0}}a{{color:{bm.COBALT}}}
</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs>{syms}</defs></svg>
<main>
<h1>Bellweather brand</h1>
<p class="lead">A raindrop that is also a bell, under a weathervane: the weather at your home right now, the sound of a signal, and the direction
things are heading. The logo is the cobalt badge with the white bell and the sun vane, next to the wordmark in Outfit.</p>

<div class="row hero">
{tile(f'<img src="{data_uri(BRAND / "logo@2x.png")}" width="560" alt="Bellweather logo">', light_bg, "logo.png on light")}
{tile(f'<img src="{data_uri(BRAND / "dark_logo@2x.png")}" width="560" alt="Bellweather logo on dark">', dark_bg, "dark_logo.png on dark", "#9a9a9a")}
</div>

<h2>In use</h2>
{in_use(word)}

<h2>The badge and the open mark</h2>
<div class="row">
{tile(f'<img src="{badge_uri}" width="120" height="120" alt=""><img src="{badge_uri}" width="48" height="48" alt=""><img src="{badge_uri}" width="24" height="24" alt="">', light_bg, "Badge: the icon everywhere (icon.png)")}
{tile(f'<img src="{badge_uri}" width="120" height="120" alt=""><img src="{badge_uri}" width="48" height="48" alt=""><img src="{badge_uri}" width="24" height="24" alt="">', dark_bg, "Badge on dark: the same file", "#9a9a9a")}
{tile(use("color", 120) + use("color", 48), light_bg, "Open mark on light, 32 px and up")}
{tile(use("color-dark", 120) + use("color-dark", 48), dark_bg, "Open mark on dark", "#9a9a9a")}
{tile(use("mono-ink", 120) + use("mono-ink", 32), light_bg, "One colour, ink")}
{tile(use("mono-white", 120) + use("mono-white", 32), bm.COBALT, "One colour, white: on blue backgrounds", "#d7e3fb")}
</div>

<h2>Colours</h2>
<div class="row">{sw}</div>

<h2>Construction</h2>
<ul>
<li><b>Grid:</b> 120 × 120 units. The drop-bell is a circle (centre 60, 72; radius 22) with straight tangents up to the tip (60, 24). It runs down to y 83, then one short curve turns into a crisp lip at y 97.4.</li>
<li><b>Vane:</b> a shaft 6 units thick, an arrow head and two tail fins, merged into one shape and scaled to 85 % around the pivot. Its centre of mass sits exactly on the rod's axis: the head is heavier than the tail, so the vane is shifted 3.9 units to the left. Centred on its outline instead, it pulls to the right.</li>
<li><b>Rounding:</b> computed on the merged outline. Outer corners of the vane are rounded with a 2-unit radius, and every inner joint (fins into shaft, shaft into rod, rod into tip) gets a 2-unit fillet. There are no seams or ledges.</li>
<li><b>Colour seam:</b> the sun part ends just above the point where the rod meets the bell's sides, so the joint's fillet stays with the bell. The two colours overlap by 0.35 units under the top colour, so no hairline of background shows.</li>
<li><b>Sheen:</b> a long, curved highlight on the bell's belly. On light grounds, including the badge, it is a cut-out (true negative space). On the open mark on dark it is filled with paper: a cut-out there would show the dark background and read as a groove, not as light.</li>
<li><b>Badge:</b> a cobalt square, 120 units, corner radius 27 (22.5 %). The white bell with the sun vane fills 80 % of its height, centred on its bounding box.</li>
<li><b>Logo:</b> the badge is {LOGO_RATIO} times the wordmark's cap height ({cap:.1f} of 120 units), so the ascenders of B, l, h and t span half the badge. The cap height is centred on the badge, with {LOGO_GAP} cap height of space before the B. This ratio was chosen after comparing 1.55, 1.8 and 2.1 in use. The wordmark is Outfit Medium (SIL Open Font License), tracking −0.01 em.</li>
</ul>

<h2>Why it looks like this</h2>
<ul>
<li><b>One accent, not two.</b> An item that differs from its surroundings is remembered better (the von Restorff isolation effect), and emphasis repeated on several elements cancels out. Sun appears in one place only: the vane, which points the way, as a bellwether does.</li>
<li><b>Light above, dark below.</b> The visual system assumes light comes from above (<a href="https://www.scribd.com/doc/308865914/1992-Shape-From-Shading-perception">Kleffner &amp; Ramachandran, 1992</a>), and darker objects look heavier (<a href="https://eprints.lancs.ac.uk/id/eprint/49952/">Walker, Francis &amp; Walker, 2010</a>). A bright vane over a deep bell reads as natural and stable, and nothing bright pulls the eye to the bottom.</li>
<li><b>Measured proportions.</b> Sun covers {share * 100:.0f} % of the open mark. Itten's teaching ratio for a balanced yellow and blue is 3 : 8 (about 27 % yellow), a rule of thumb that has also been measured against. Well below it, yellow is an accent rather than a rival.</li>
<li><b>Grouping.</b> Same colour groups (Gestalt similarity). The bell and clapper share one colour, so they read as one object, the bell. The vane reads as a second object, the weather.</li>
<li><b>A badge, because the slots are square.</b> Home Assistant, app grids and browser tabs all show icons in squares. The open mark is only {mark_width_share * 100:.0f} % as wide as it is tall, so it left about 40 % of the slot empty. The badge fills the slot and carries its own ground, so it looks the same on light, dark and coloured backgrounds.</li>
<li><b>Works without colour.</b> In greyscale both the badge and the open mark keep clear steps of lightness. They do not depend on hue, which matters for colour-blind viewers.</li>
<li><b>Blue for trust.</b> In brand research, blue is linked with competence (<a href="https://econbiz.de/Record/exciting-red-and-competent-blue-the-importance-of-color-in-marketing-labrecque-lauren/10009997374">Labrecque &amp; Milne, 2012</a>), the right note for a forecast.</li>
</ul>

<h2>Usage</h2>
<ul>
<li><b>The badge is the default</b> wherever an icon or logo appears: the HA icon, the logo, the README, favicons, app grids.</li>
<li><b>Clear space:</b> at least a quarter of the badge's height on every side of the badge and of the logo.</li>
<li><b>Minimum size:</b> the badge works down to 16 px. White on cobalt is {c('#FFFFFF', bm.COBALT):.1f} : 1 and sun on cobalt {c(bm.SUN, bm.COBALT):.1f} : 1. The open mark needs 32 px or more on light backgrounds: sun on white is only {c(bm.SUN, bm.LIGHT_BG):.1f} : 1, and a thin vane fades.</li>
<li><b>On blue backgrounds</b> the cobalt badge would sink: use the white one-colour mark there.</li>
<li><b>Never:</b> recolour the parts, put sun on the clapper, change the badge's corner radius, stretch or rotate anything, add outlines or shadows, set the wordmark in another typeface, or put the logo on a busy photo without a calm area behind it.</li>
</ul>
</main></body></html>
"""


def main() -> None:
    for name in ("color", "color-dark", "mono-ink", "mono-white"):
        (HERE / f"mark-{name}.svg").write_text(svg_mark(name), encoding="utf-8")
    (HERE / "badge.svg").write_text(svg_badge(), encoding="utf-8")

    save_png(badge_icon(256), BRAND / "icon.png")
    save_png(badge_icon(512), BRAND / "icon@2x.png")
    word = wordmark_mask()
    save_png(logo(bm.INK, 256, word), BRAND / "logo.png")
    save_png(logo(bm.INK, 512, word), BRAND / "logo@2x.png")
    save_png(logo(PAPER, 256, word), BRAND / "dark_logo.png")
    save_png(logo(PAPER, 512, word), BRAND / "dark_logo@2x.png")
    # files from earlier iterations that no longer belong to the brand
    for stale in (BRAND / "dark_icon.png", BRAND / "dark_icon@2x.png", HERE / "mark-small.svg", HERE / "lockups.html"):
        stale.unlink(missing_ok=True)

    (HERE / "sheet.html").write_text(sheet(word), encoding="utf-8")
    print(f"stem/cap {word[3]:.3f}")
    for p in sorted(BRAND.glob("*.png")):
        with Image.open(p) as im:
            print(p.name, im.size)


if __name__ == "__main__":
    main()
