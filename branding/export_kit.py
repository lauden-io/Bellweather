"""Bellweather brand kit: outlined vector logos, credit badges, PNG sizes,
favicons, colour swatches, the social preview, the guidelines PDF, and one
zip with everything.

The wordmark and the credit text are outlined: Outfit is rendered by the
browser, printed to PDF, and MuPDF turns the embedded glyphs back into
paths. The SVGs therefore need no font, and no font file is downloaded or
shipped (Outfit: SIL Open Font License 1.1).

Run after export_brand.py:
    python branding/export_kit.py
    -> branding/logo*.svg (with logo-motto*.svg), branding/credit-*.svg,
       branding/Bellweather-Brand-Guidelines.pdf, branding/Bellweather-Brand-Kit.zip
Needs shapely, Pillow, Edge or Chrome, MuPDF (mutool) and network access to Google Fonts.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_mark as bm  # noqa: E402
import export_brand as eb  # noqa: E402

KIT_ZIP = HERE / "Bellweather-Brand-Kit.zip"
KIT_NAME = "Bellweather-Brand-Kit"
ZIP_TIME = (2026, 11, 10, 0, 0, 0)  # fixed, so an unchanged kit gives an identical zip

COBALT, SUN, INK, PAPER, WHITE, NIGHT = bm.COBALT, bm.SUN, bm.INK, eb.PAPER, "#FFFFFF", bm.DARK_BG

# sRGB -> CMYK through the ECI / IDEAlliance press profiles with LittleCMS,
# relative colorimetric with black point compensation, rounded to whole
# percent; dE00 is CIEDE2000 between the screen colour and the rounded ink
# build (branding/colour_conversion.py recomputes the table, 2026-11-10).
COLOURS = [
    # name, hex, role, CMYK coated (ISO Coated v2 / FOGRA39), dE00, CMYK uncoated (PSO Uncoated), dE00
    ("Cobalt", COBALT, "Badge, bell; Bellweather's own colour", (85, 69, 0, 0), 7.4, (95, 64, 0, 0), 7.7),
    ("Sun", SUN, "The vane; accent only", (0, 36, 92, 0), 1.4, (0, 21, 100, 0), 4.0),
    ("Ink", INK, "Wordmark, text, one-colour marks", (95, 75, 26, 66), 5.8, (100, 68, 16, 71), 18.0),
    ("Paper", PAPER, "Calm grounds; wordmark on dark", (5, 4, 7, 0), 0.2, (3, 2, 4, 2), 0.2),
    ("Cobalt light", bm.COBALT_DARK, "Bell of the open mark on dark", (64, 42, 0, 0), 7.7, (61, 34, 0, 0), 5.8),
    ("Sun light", bm.SUN_DARK, "Vane of the open mark on dark", (0, 27, 81, 0), 0.8, (1, 16, 87, 1), 2.6),
]
DEBUG_DIR: Path | None = None
CREDIT_TEXT = "Weather data fused by "
CREDIT_NAME = "Bellweather"
CREDIT_H = 28  # nominal height in px; the SVG scales to any height
# The logo with the motto. The motto is set exactly as wide as the wordmark,
# in Outfit Regular, 0.42 wordmark cap heights below the wordmark's baseline.
MOTTO = "The weather here and now, measured."
MOTTO_WEIGHT = 400
MOTTO_GAP = 0.42  # wordmark cap heights from the wordmark's baseline to the motto's cap line
STACK_GAP = 0.6  # stacked: wordmark cap heights from the badge to the wordmark, as in the logo
MOTTO_LIGHT, MOTTO_DARK, MOTTO_ON_COBALT = "#4A5160", "#B9BCC4", "#E4ECFC"  # all at least 4.5 : 1 on their ground
BADGE_SIZES = (16, 32, 48, 64, 128, 180, 192, 256, 512, 1024)


# ------------------------------------------------------------------ outlines

@dataclass
class Glyph:
    char: str
    d: str  # path in em units, y up, origin at the pen position
    x: float  # pen position in em from the line start


def _numbers(s: str) -> list[float]:
    return [float(v) for v in re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", s)]


def _parse(d: str) -> list[tuple[str, list[float]]]:
    """MuPDF writes absolute M, L, H, V, C, Q and Z only."""
    out = []
    for cmd, args in re.findall(r"([MLHVCQZmlhvcqz])([^MLHVCQZmlhvcqz]*)", d):
        if cmd.islower():
            raise ValueError("relative path commands are not expected from MuPDF")
        out.append((cmd, _numbers(args)))
    return out


def outline_lines(lines: list[tuple[list[tuple[str, int]], float]]) -> list[list[Glyph]]:
    """Each line is ([(text, weight), ...], tracking in em). Returns its glyphs."""
    size = 100
    rows = []
    for i, (runs, tracking) in enumerate(lines):
        spans = "".join(f'<span style="font-weight:{w}">{t.replace(" ", "&#32;")}</span>' for t, w in runs)
        rows.append(f'<div style="top:{50 + i * 150}px;letter-spacing:{tracking}em">{spans}</div>')
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>wait</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500&display=block" rel="stylesheet">
<style>@page{{size:3000px {150 * len(lines) + 100}px;margin:0}}body{{margin:0}}
div{{position:absolute;left:50px;font-family:Outfit;font-size:{size}px;line-height:1;white-space:pre}}</style></head>
<body>{''.join(rows)}<script>document.fonts.ready.then(()=>{{document.title=
document.fonts.check('400 50px Outfit')&&document.fonts.check('500 50px Outfit')?'READY':'NOFONT'}})</script></body></html>"""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        page, pdf = t / "o.html", t / "o.pdf"
        page.write_text(html, encoding="utf-8")
        args = [eb.browser(), "--headless=new", "--disable-gpu", f"--user-data-dir={t / 'profile'}", "--virtual-time-budget=15000"]
        dom = subprocess.run(args + ["--dump-dom", page.as_uri()], capture_output=True, text=True, timeout=180).stdout
        if "<title>READY</title>" not in dom:
            raise SystemExit("Outfit did not load; the outlines would use a fallback font")
        subprocess.run(args + ["--no-pdf-header-footer", f"--print-to-pdf={pdf}", page.as_uri()], capture_output=True, timeout=180)
        subprocess.run([mutool(), "draw", "-q", "-F", "svg", "-o", str(t / "o%d.svg"), str(pdf), "1"], check=True, capture_output=True)
        svg = (t / "o1.svg").read_text(encoding="utf-8")
        if DEBUG_DIR:
            shutil.copy2(t / "o1.svg", DEBUG_DIR / "outline_debug.svg")
    paths = {k: d for k, d in re.findall(r'<path id="(font_[^"]+)" d="([^"]*)"', svg)}
    # With several fonts MuPDF writes each glyph as a group of paths in font units.
    for k, body in re.findall(r'<g id="(font_[^"]+)">(.*?)</g>', svg, flags=re.S):
        paths[k] = "".join(_to_em(d, m) for m, d in re.findall(r'<path transform="matrix\(([^)]*)\)" d="([^"]*)"', body))
    uses =re.findall(r'<use data-text="([^"]*)" xlink:href="#(font_[^"]+)" transform="matrix\(([^)]*)\)"', svg)
    out: list[list[Glyph]] = [[] for _ in lines]
    for char, ref, m in uses:
        a, _, _, d_, e, f = (float(v) for v in m.split(","))
        line = round((f / 0.75 - 50 - size * 0.8) / 150)  # pt -> px; baseline is about 0.8 em below the top
        line = min(max(line, 0), len(lines) - 1)
        out[line].append(Glyph(_unescape(char), paths[ref], (e / 0.75 - 50) / size))
        if abs(a - size * 0.75) > 1e-3 or abs(d_ + size * 0.75) > 1e-3:
            raise SystemExit("unexpected glyph transform from MuPDF")
    for line, (runs, _) in zip(out, lines, strict=True):
        text = "".join(t for t, _ in runs)
        if "".join(g.char for g in line) != text:
            raise SystemExit(f"outline mismatch: {''.join(g.char for g in line)!r} != {text!r}")
    return out


def _to_em(d: str, matrix: str) -> str:
    """A glyph path with MuPDF's scale matrix applied: em units, y up."""
    a, b, c, dd, e, f = (float(v) for v in matrix.split(","))
    if b or c:
        raise SystemExit("unexpected rotated glyph from MuPDF")
    out = []
    for cmd, v in _parse(d):
        if cmd == "H":
            out.append("H" + " ".join(f"{a * x + e:.6f}" for x in v))
        elif cmd == "V":
            out.append("V" + " ".join(f"{dd * y + f:.6f}" for y in v))
        else:
            out.append(cmd + " ".join(f"{a * v[i] + e:.6f} {dd * v[i + 1] + f:.6f}" for i in range(0, len(v), 2)))
    return "".join(out)


def _unescape(s: str) -> str:
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")


def glyph_path(glyphs: list[Glyph], scale: float, x0: float, base: float) -> tuple[str, tuple[float, float, float, float]]:
    """Glyphs placed with the first pen position at x0 and the baseline at
    `base`, `scale` units per em, y down. Returns (path d, ink bbox)."""
    parts = []
    xs, ys = [], []
    for g in glyphs:
        cur = (0.0, 0.0)

        def pt(x, y, ink=True):
            px, py = x0 + (g.x + x) * scale, base - y * scale
            if ink:  # control points are not ink
                xs.append(px)
                ys.append(py)
            return f"{px:.2f} {py:.2f}"

        seg = []
        start = None
        for cmd, v in _parse(g.d):
            if cmd == "M":
                cur = start = (v[0], v[1])
                seg.append("M" + pt(*cur))
                for i in range(2, len(v), 2):  # further pairs after M are line-tos
                    cur = (v[i], v[i + 1])
                    seg.append("L" + pt(*cur))
            elif cmd == "L":
                for i in range(0, len(v), 2):
                    cur = (v[i], v[i + 1])
                    seg.append("L" + pt(*cur))
            elif cmd == "H":
                for x in v:
                    cur = (x, cur[1])
                    seg.append("L" + pt(*cur))
            elif cmd == "V":
                for y in v:
                    cur = (cur[0], y)
                    seg.append("L" + pt(*cur))
            elif cmd == "C":
                for i in range(0, len(v), 6):
                    p0 = cur
                    c1, c2, cur = (v[i], v[i + 1]), (v[i + 2], v[i + 3]), (v[i + 4], v[i + 5])
                    seg.append("C" + " ".join((pt(*c1, ink=False), pt(*c2, ink=False), pt(*cur))))
                    for k in range(1, 16):  # the real curve's extent, not its control points
                        t = k / 16
                        bx = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * c1[0] + 3 * (1 - t) * t * t * c2[0] + t ** 3 * cur[0]
                        by = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * c1[1] + 3 * (1 - t) * t * t * c2[1] + t ** 3 * cur[1]
                        pt(bx, by)
            elif cmd == "Q":
                for i in range(0, len(v), 4):
                    p0 = cur
                    c1, cur = (v[i], v[i + 1]), (v[i + 2], v[i + 3])
                    seg.append("Q" + " ".join((pt(*c1, ink=False), pt(*cur))))
                    for k in range(1, 16):
                        t = k / 16
                        pt((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c1[0] + t * t * cur[0],
                           (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c1[1] + t * t * cur[1])
            elif cmd == "Z":
                seg.append("Z")
                cur = start or cur
        parts.append("".join(seg))
    return "".join(parts), (min(xs), min(ys), max(xs), max(ys))


def mutool() -> str:
    for c in (shutil.which("mutool"), r"C:\Program Files\MuPDF\mutool.exe"):
        if c and Path(c).exists():
            return c
    raise SystemExit("the outlines need MuPDF's mutool")


# ------------------------------------------------------------------ art

def badge_group(size: float, x: float = 0.0, y: float = 0.0, frame: bool = True, corners: str = "all") -> str:
    """The badge at `size` units, top left at (x, y)."""
    lays, bell = eb.mark_layers("on-cobalt")
    k, tx, ty = eb.badge_placement()
    s = size / 120
    rect = f'<path d="{eb.badge_path(corners=corners)}" fill="{COBALT}"/>' if frame else ""
    return (f'<g transform="translate({x:.3f} {y:.3f}) scale({s:.6f})">{rect}'
            f'<g transform="translate({tx:.3f} {ty:.3f}) scale({k:.5f})">{bm.mark_body(lays, bell)}</g></g>')


@dataclass
class Art:
    svg: str
    width: float
    height: float


def lockup(word: list[Glyph], text: str, frame: bool = True) -> Art:
    """Same layout as logo.png: badge 120, cap height 120/2.1 centred on the
    badge, 0.6 cap between the badge and the B's stem."""
    cap_em = cap_height_em(word)
    cap = 120 / eb.LOGO_RATIO
    scale = cap / cap_em
    base = 60 + cap / 2
    d, (ix0, _, _, _) = glyph_path(word, scale, 0, base)
    shift = 120 + eb.LOGO_GAP * cap - ix0
    d, (_, _, ix1, _) = glyph_path(word, scale, shift, base)
    w = ix1
    body = badge_group(120, frame=frame) + f'<path d="{d}" fill="{text}"/>'
    return Art(_svg(body, w, 120, "Bellweather"), w, 120)


def _placed(glyphs: list[Glyph], scale: float, left: float, base: float) -> tuple[str, tuple[float, float, float, float]]:
    """Glyphs whose ink starts exactly at `left`."""
    _, (x0, _, _, _) = glyph_path(glyphs, scale, 0, base)
    return glyph_path(glyphs, scale, left - x0, base)


def motto_sizes(word: list[Glyph], motto: list[Glyph]) -> dict[str, float]:
    """In badge units (badge 120): the wordmark's cap height, scale and ink
    width, and the motto's scale and cap height for the same width."""
    cap = 120 / eb.LOGO_RATIO
    scale = cap / cap_height_em(word)
    _, (wx0, _, wx1, _) = glyph_path(word, scale, 0, 0)
    _, (mx0, _, mx1, _) = glyph_path(motto, 1.0, 0, 0)
    m_scale = (wx1 - wx0) / (mx1 - mx0)
    return {"cap": cap, "scale": scale, "width": wx1 - wx0, "m_scale": m_scale, "m_cap": cap_height_em(word) * m_scale}


def _data(sizes: dict[str, float], **extra: float) -> str:
    """The construction numbers, for the guidelines' drawing."""
    values = {"cap": sizes["cap"], "motto-cap": sizes["m_cap"], "word-width": sizes["width"], **extra}
    return " ".join(f'data-{k}="{v:.3f}"' for k, v in values.items())


def motto_lockup(word: list[Glyph], motto: list[Glyph], text: str, motto_colour: str, frame: bool = True) -> Art:
    """Beside the badge: the wordmark and the motto as one block, centred on
    the badge's height; the badge, the gap and the wordmark's size as in the logo."""
    s = motto_sizes(word, motto)
    left = 120 + eb.LOGO_GAP * s["cap"]
    top = (120 - (s["cap"] + MOTTO_GAP * s["cap"] + s["m_cap"])) / 2
    base = top + s["cap"]
    m_base = base + MOTTO_GAP * s["cap"] + s["m_cap"]
    d, (_, _, x1, _) = _placed(word, s["scale"], left, base)
    dm, (_, _, _, my1) = _placed(motto, s["m_scale"], left, m_base)
    if my1 > 120:
        raise SystemExit("the motto does not fit inside the badge's height")
    body = badge_group(120, frame=frame) + f'<path d="{d}" fill="{text}"/><path d="{dm}" fill="{motto_colour}"/>'
    svg = _svg(body, x1, 120, f"Bellweather: {MOTTO}")
    svg = svg.replace("<svg ", f"<svg {_data(s, left=left, base=base, **{'motto-base': m_base})} ", 1)
    return Art(svg, x1, 120)


def motto_stacked(word: list[Glyph], motto: list[Glyph], text: str, motto_colour: str, frame: bool = True) -> Art:
    """Stacked: the badge centred above the wordmark, the motto below it."""
    s = motto_sizes(word, motto)
    w = s["width"]
    base = 120 + STACK_GAP * s["cap"] + s["cap"]
    m_base = base + MOTTO_GAP * s["cap"] + s["m_cap"]
    d, _ = _placed(word, s["scale"], 0, base)
    dm, (_, _, _, my1) = _placed(motto, s["m_scale"], 0, m_base)
    h = max(my1, m_base)
    body = badge_group(120, x=(w - 120) / 2, frame=frame) + f'<path d="{d}" fill="{text}"/><path d="{dm}" fill="{motto_colour}"/>'
    svg = _svg(body, w, h, f"Bellweather: {MOTTO}")
    svg = svg.replace("<svg ", f"<svg {_data(s, base=base, **{'motto-base': m_base})} ", 1)
    return Art(svg, w, h)


def cap_height_em(word: list[Glyph]) -> float:
    b = next(g for g in word if g.char == "B")
    return max(_numbers(b.d)[1::2])


def credit(glyphs: list[Glyph], cap_em: float, dark: bool) -> Art:
    """A credit badge: the badge on the left, the line on a calm field."""
    h = CREDIT_H
    fs = 0.43 * h
    cap = cap_em * fs
    base = h / 2 + cap / 2
    lead = [g for g in glyphs[: len(CREDIT_TEXT)]]
    name = glyphs[len(CREDIT_TEXT):]
    d_all, (ix0, _, _, _) = glyph_path(glyphs, fs, 0, base)
    shift = h + 0.36 * h - ix0
    d_lead, _ = glyph_path(lead, fs, shift, base)
    d_name, (_, _, ix1, _) = glyph_path(name, fs, shift, base)
    w = ix1 + 0.43 * h
    r = h * eb.BADGE_RADIUS / 120
    outline = eb.badge_path(w - 1, h - 1, .5, .5, r=r - .5)
    field, edge, lead_c, name_c = (NIGHT, "#3a3a3a", "#b9bcc4", PAPER) if dark else (WHITE, "#d9d7d0", "#4a5160", INK)
    body = (f'<path d="{outline}" fill="{field}" stroke="{edge}"/>'
            + badge_group(h, corners="left")
            + f'<path d="{d_lead}" fill="{lead_c}"/><path d="{d_name}" fill="{name_c}"/>')
    return Art(_svg(body, w, h, CREDIT_TEXT + CREDIT_NAME), w, h)


def _svg(body: str, w: float, h: float, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.2f} {h:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'role="img" aria-label="{title}"><title>{title}</title>{body}</svg>\n')


def build_art() -> dict[str, Art]:
    word, line, motto = outline_lines([([("Bellweather", eb.WORD_WEIGHT)], eb.WORD_TRACKING),
                                       ([(CREDIT_TEXT, 400), (CREDIT_NAME, 500)], 0.0),
                                       ([(MOTTO, MOTTO_WEIGHT)], 0.0)])
    cap_em = cap_height_em(word)
    if abs(cap_em - 0.700) > 0.005:
        raise SystemExit(f"Outfit cap height {cap_em:.3f} em, expected 0.700: not Outfit?")
    return {
        "logo.svg": lockup(word, INK),
        "logo-dark.svg": lockup(word, PAPER),
        "logo-reversed-white.svg": lockup(word, WHITE, frame=False),
        "logo-motto.svg": motto_lockup(word, motto, INK, MOTTO_LIGHT),
        "logo-motto-dark.svg": motto_lockup(word, motto, PAPER, MOTTO_DARK),
        "logo-motto-reversed-white.svg": motto_lockup(word, motto, WHITE, MOTTO_ON_COBALT, frame=False),
        "logo-motto-stacked.svg": motto_stacked(word, motto, INK, MOTTO_LIGHT),
        "logo-motto-stacked-dark.svg": motto_stacked(word, motto, PAPER, MOTTO_DARK),
        "logo-motto-stacked-reversed-white.svg": motto_stacked(word, motto, WHITE, MOTTO_ON_COBALT, frame=False),
        "credit-light.svg": credit(line, cap_em, dark=False),
        "credit-dark.svg": credit(line, cap_em, dark=True),
    }


# ------------------------------------------------------------------ rasters and files

def render_svg(svg_path: Path, png_path: Path, height: int) -> None:
    subprocess.run([mutool(), "draw", "-q", "-c", "rgba", "-h", str(height), "-o", str(png_path), str(svg_path)],
                   check=True, capture_output=True)
    with Image.open(png_path) as im:
        im.load()
        trimmed = im.crop(im.getbbox()) if im.getbbox() else im
    if trimmed.height != height:  # MuPDF rounds; keep the requested height exactly
        trimmed = trimmed.resize((round(trimmed.width * height / trimmed.height), height), Image.LANCZOS)
    eb.save_png(trimmed, png_path)


def ase(swatches: list[tuple[str, str, tuple[float, ...]]]) -> bytes:
    """Adobe Swatch Exchange 1.0: one group of global swatches."""
    def name(n: str) -> bytes:
        return struct.pack(">H", len(n) + 1) + (n + "\0").encode("utf-16-be")

    def block(kind: int, payload: bytes) -> bytes:
        return struct.pack(">HI", kind, len(payload)) + payload

    blocks = [block(0xC001, name("Bellweather"))]
    for n, model, values in swatches:
        blocks.append(block(0x0001, name(n) + model.encode("ascii") + struct.pack(f">{len(values)}f", *values) + struct.pack(">H", 0)))
    blocks.append(block(0xC002, b""))
    return b"ASEF" + struct.pack(">HHI", 1, 0, len(blocks)) + b"".join(blocks)


def colour_files(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    sw = []
    for n, h, _, cc, _, cu, _ in COLOURS:
        r, g, b = eb.rgb(h)
        sw.append((f"{n} (screen)", "RGB ", (r / 255, g / 255, b / 255)))
        sw.append((f"{n} (coated)", "CMYK", tuple(v / 100 for v in cc)))
        sw.append((f"{n} (uncoated)", "CMYK", tuple(v / 100 for v in cu)))
    sw.append(("White (screen)", "RGB ", (1.0, 1.0, 1.0)))
    (out / "bellweather.ase").write_bytes(ase(sw))
    gpl = ["GIMP Palette", "Name: Bellweather", "Columns: 4", "#"]
    gpl += [f"{eb.rgb(h)[0]:3d} {eb.rgb(h)[1]:3d} {eb.rgb(h)[2]:3d}\t{n}" for n, h, *_ in COLOURS] + ["255 255 255\tWhite"]
    (out / "bellweather.gpl").write_text("\n".join(gpl) + "\n", encoding="utf-8")
    css = [":root {"] + [f"  --bellweather-{n.lower().replace(' ', '-')}: {h};" for n, h, *_ in COLOURS] + ["}"]
    (out / "colours.css").write_text("\n".join(css) + "\n", encoding="utf-8")
    data = {n: {"hex": h, "rgb": list(eb.rgb(h)), "role": role,
                "cmyk_coated_fogra39": list(cc), "cmyk_uncoated_pso": list(cu), "delta_e00_coated": dc, "delta_e00_uncoated": du}
            for n, h, role, cc, dc, cu, du in COLOURS}
    (out / "colours.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def favicon(path: Path) -> None:
    icons = {px: eb.badge_icon(px) for px in (16, 32, 48)}
    icons[48].save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)], append_images=[icons[16], icons[32]])
    with Image.open(path) as ico:
        if sorted(ico.info.get("sizes", [])) != [(16, 16), (32, 32), (48, 48)]:
            raise SystemExit(f"favicon sizes {ico.info.get('sizes')}")


def social_preview(path: Path, reversed_logo: Art) -> None:
    inner = re.sub(r"^<svg[^>]*>|</svg>\s*$", "", reversed_logo.svg)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>wait</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500&display=block" rel="stylesheet">
<style>html,body{{margin:0;width:1280px;height:640px;overflow:hidden}}
body{{background:{COBALT};font-family:Outfit;color:#fff;display:flex;flex-direction:column;justify-content:center;padding:0 110px;box-sizing:border-box}}
.t{{font-size:54px;font-weight:500;letter-spacing:-.01em;margin-top:58px;line-height:1.1;white-space:nowrap}} .s{{font-size:30px;color:#d7e3fb;margin-top:16px}}</style></head>
<body><svg viewBox="0 0 {reversed_logo.width:.2f} 120" style="height:120px;width:{120 * reversed_logo.width / 120:.0f}px;display:block">{inner}</svg>
<div class="t">The weather here and now, measured.</div><div class="s">Home Assistant integration</div>
<script>document.fonts.ready.then(()=>{{document.title=document.fonts.check('500 50px Outfit')?'READY':'NOFONT'}})</script></body></html>"""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        page = t / "s.html"
        page.write_text(html, encoding="utf-8")
        args = [eb.browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={t / 'profile'}",
                "--virtual-time-budget=15000", "--force-device-scale-factor=1"]
        dom = subprocess.run(args + ["--dump-dom", page.as_uri()], capture_output=True, text=True, timeout=180).stdout
        if "<title>READY</title>" not in dom:
            raise SystemExit("Outfit did not load for the social preview")
        subprocess.run(args + ["--window-size=1280,640", f"--screenshot={t / 's.png'}", page.as_uri()], capture_output=True, timeout=180)
        with Image.open(t / "s.png") as im:
            if im.size != (1280, 640):
                raise SystemExit(f"social preview is {im.size}")
            eb.save_png(im.convert("RGB"), path)


EMBED = """<!-- Bellweather credit badge. Set the height you need; the width follows.
     Most private: copy credit-light.svg and credit-dark.svg to your own site and
     point src/srcset there, so no third party sees your visitors. -->
<a href="https://github.com/lauden-io/Bellweather">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="credit-dark.svg">
    <img src="credit-light.svg" alt="Weather data fused by Bellweather" height="28">
  </picture>
</a>
"""

README = """Bellweather brand kit
=====================

Everything for using the Bellweather name and logo. The rules are in
Bellweather-Brand-Guidelines.pdf; the short version is below.

Folders
  logo/     The logo (badge + wordmark). SVG is vector and needs no font.
            logo.svg            light grounds
            logo-dark.svg       dark grounds
            logo-reversed-white.svg   on Bellweather cobalt only
            *.png               transparent rasters, 256 and 512 px high
            logo-motto*.svg     the logo with the motto, beside the badge
            logo-motto-stacked*.svg   the same, stacked, for square spaces
            logo-motto*.png     both as transparent rasters (1x and 2x)
  badge/    The square icon: badge.svg, PNGs from 16 to 1024 px,
            favicon.ico (16, 32, 48) and apple-touch-icon.png (180).
  mark/     The open mark without the badge: colour (light / dark grounds)
            and one-colour (ink / white). Secondary; prefer the badge.
  credit/   "Weather data fused by Bellweather" badges, light and dark,
            as SVG and PNG, with embed.html (copy-paste code).
  colour/   bellweather.ase (Adobe, Affinity), bellweather.gpl (GIMP,
            Inkscape), colours.css, colours.json.
  social/   social-preview.png, 1280 x 640, for the repository and links.

Colours (screen: HEX; print: CMYK)
{colours}

  Screens, web, apps, office and home printers: use HEX / RGB.
  Commercial print on coated paper: CMYK coated (ISO Coated v2, FOGRA39).
  Commercial print on uncoated paper: CMYK uncoated (PSO Uncoated).
  Send the press a PDF with the profile named; ask for a proof.

Rules in brief
  - Use the badge everywhere a square icon goes; the logo where there is room.
  - Never retype the wordmark, recolour, stretch, rotate or add effects.
  - Clear space: a quarter of the badge's height on every side.
  - Minimum size: logo badge 6 mm / 20 px high; badge 5 mm / 16 px;
    logo with the motto: badge {motto_min_mm} mm / {motto_min_px} px high.
  - The motto is part of its artwork: never type it next to the logo.

The wordmark is artwork drawn from Outfit (SIL Open Font License 1.1).
"""


def motto_minimum(svg: str | None = None) -> tuple[int, int]:
    """The smallest badge height (mm in print, px on screen) at which the
    motto is still 7 pt in print and 11 px on screen."""
    import re as _re
    text = svg or (HERE / "logo-motto.svg").read_text(encoding="utf-8")
    m_cap = float(_re.search(r'data-motto-cap="([\d.]+)"', text).group(1))
    font = m_cap / 0.700  # the motto's font size in badge units (Outfit cap height 0.700 em)
    mm = math.ceil(120 / font * 7 * 25.4 / 72)
    px = math.ceil(120 / font * 11 / 4) * 4
    return mm, px


def write_kit(art: dict[str, Art], pdf: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / KIT_NAME
        for sub in ("logo", "badge", "mark", "credit", "colour", "social"):
            (root / sub).mkdir(parents=True)
        for name in ("logo.svg", "logo-dark.svg", "logo-reversed-white.svg"):
            (root / "logo" / name).write_text(art[name].svg, encoding="utf-8")
        for name in (n for n in art if n.startswith("logo-motto")):
            svg = root / "logo" / name
            svg.write_text(art[name].svg, encoding="utf-8")
            h = round(art[name].height)
            render_svg(svg, svg.with_suffix(".png"), h)
            render_svg(svg, svg.with_name(svg.stem + "@2x.png"), 2 * h)
        for p in sorted(eb.BRAND.glob("*logo*.png")):
            shutil.copy2(p, root / "logo" / p.name)
        shutil.copy2(HERE / "badge.svg", root / "badge" / "badge.svg")
        for px in BADGE_SIZES:
            eb.save_png(eb.badge_icon(px), root / "badge" / f"badge-{px}.png")
        eb.save_png(eb.badge_icon(180), root / "badge" / "apple-touch-icon.png")
        favicon(root / "badge" / "favicon.ico")
        for p in sorted(HERE.glob("mark-*.svg")):
            shutil.copy2(p, root / "mark" / p.name)
        for name in ("credit-light.svg", "credit-dark.svg"):
            svg = root / "credit" / name
            svg.write_text(art[name].svg, encoding="utf-8")
            render_svg(svg, svg.with_suffix(".png"), CREDIT_H)
            render_svg(svg, svg.with_name(svg.stem + "@2x.png"), CREDIT_H * 2)
        (root / "credit" / "embed.html").write_text(EMBED, encoding="utf-8")
        colour_files(root / "colour")
        social_preview(root / "social" / "social-preview.png", art["logo-reversed-white.svg"])
        shutil.copy2(pdf, root / pdf.name)
        rows = "\n".join(f"  {n:13} {h}   C{cc[0]} M{cc[1]} Y{cc[2]} K{cc[3]} coated   C{cu[0]} M{cu[1]} Y{cu[2]} K{cu[3]} uncoated"
                         for n, h, _, cc, _, cu, _ in COLOURS)
        mm, px = motto_minimum()
        (root / "README.txt").write_text(README.format(colours=rows, motto_min_mm=mm, motto_min_px=px), encoding="utf-8")
        tmp_zip = Path(tmp) / KIT_ZIP.name
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for f in sorted(p for p in root.rglob("*") if p.is_file()):
                info = zipfile.ZipInfo(f"{KIT_NAME}/{f.relative_to(root).as_posix()}", ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, f.read_bytes(), compresslevel=9)
        shutil.copy2(tmp_zip, KIT_ZIP)
    return KIT_ZIP


def main() -> None:
    art = build_art()
    for name, a in art.items():
        (HERE / name).write_text(a.svg, encoding="utf-8")
        print(f"{name}: {a.width:.1f} x {a.height:.0f}")
    sys.path.insert(0, str(HERE / "guidelines"))
    import build_guidelines  # noqa: E402  reads the SVGs written above
    build_guidelines.main()
    kit = write_kit(art, build_guidelines.PDF)
    with zipfile.ZipFile(kit) as z:
        print(f"{kit.name}: {len(z.namelist())} files, {kit.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
