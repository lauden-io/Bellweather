"""Bellweather brand guidelines: builds guidelines.html and prints it to a PDF.

Every mark, badge and lockup in the PDF is vector: the geometry comes from
build_mark.py and export_brand.py, and the wordmark is live Outfit Medium
text, which the browser embeds in the PDF. The lockup uses the same
proportions as the exported PNGs, from Outfit metrics measured on
2026-11-10: cap height 0.700 em, B's left side bearing 0.076 em, ink width
7.397 cap heights.

Run: python branding/guidelines/build_guidelines.py
     -> branding/guidelines/guidelines.html and branding/Bellweather-Brand-Guidelines.pdf
Needs shapely, Edge or Chrome, and network access to Google Fonts.
"""

from __future__ import annotations

import colorsys
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import build_mark as bm  # noqa: E402
import export_brand as eb  # noqa: E402
import export_kit as ek  # noqa: E402

PDF = HERE.parent / "Bellweather-Brand-Guidelines.pdf"
VERSION = "1.0"
DATE = "November 2026"

CAP_EM, LSB_EM, INK_PER_CAP = 0.700, 0.076, 7.397
COBALT, SUN, INK, PAPER, WHITE = bm.COBALT, bm.SUN, bm.INK, eb.PAPER, "#FFFFFF"
COBALT_L, SUN_L, DARK = bm.COBALT_DARK, bm.SUN_DARK, bm.DARK_BG
MUTED = "#5d6472"
RED = "#D93A3A"


# ---------------------------------------------------------------- drawing

def mark_body(name: str, recolor: dict | None = None, clapper: str | None = None) -> str:
    lays, bell = eb.mark_layers(name)
    if recolor:
        lays = [(g, recolor.get(c, c)) for g, c in lays]
        bell = recolor.get(bell, bell)
    return bm.mark_body(lays, clapper or bell)


def badge_group(fill=COBALT, radius=eb.BADGE_RADIUS, frame=True, mark="on-cobalt", recolor=None, clapper=None) -> str:
    k, tx, ty = eb.badge_placement()
    rect = f'<path d="{eb.badge_path(r=radius)}" fill="{fill}"/>' if frame else ""
    return f'{rect}<g transform="translate({tx:.3f} {ty:.3f}) scale({k:.5f})">{mark_body(mark, recolor, clapper)}</g>'


def badge(size_mm: float, **kw) -> str:
    return f'<svg viewBox="0 0 120 120" style="width:{size_mm}mm;height:{size_mm}mm;display:block">{badge_group(**kw)}</svg>'


def open_mark(name: str, height_mm: float, **kw) -> str:
    x0, y0, x1, y1 = eb.X0, eb.Y0, eb.X1, eb.Y1
    w = height_mm * (x1 - x0) / (y1 - y0)
    return (f'<svg viewBox="{eb.viewbox()}" style="height:{height_mm}mm;width:{w:.2f}mm;display:block">'
            f'{mark_body(name, **kw)}</svg>')


def lockup_parts(text=INK, ratio=eb.LOGO_RATIO, gap=eb.LOGO_GAP, font="Outfit", weight=500, tracking=-0.01,
                 badge_kw=None, text_first=False):
    cap = 120 / ratio
    fs = cap / CAP_EM
    ink_w = INK_PER_CAP * cap + (tracking + 0.01) * fs * 10  # 10 gaps between 11 letters
    base = 60 + cap / 2
    g = badge_group(**(badge_kw or {}))
    if text_first:
        tx = -LSB_EM * fs
        bx = ink_w + gap * cap
        width = bx + 120
        art = f'<g transform="translate({bx:.2f} 0)">{g}</g>'
    else:
        ink_x = 120 + gap * cap
        tx = ink_x - LSB_EM * fs
        width = ink_x + ink_w
        art = g
    t = (f'<text x="{tx:.2f}" y="{base:.2f}" font-family="{font}" font-weight="{weight}" font-size="{fs:.2f}" '
         f'letter-spacing="{tracking * fs:.2f}" fill="{text}">Bellweather</text>')
    return art + t, width, cap, base


def lockup(height_mm: float, extra: str = "", style: str = "", **kw) -> str:
    inner, width, _, _ = lockup_parts(**kw)
    return (f'<svg viewBox="0 0 {width:.2f} 120" overflow="visible" '
            f'style="height:{height_mm}mm;width:{height_mm * width / 120:.2f}mm;display:block;{style}">{extra}{inner}</svg>')


def reversed_lockup(height_mm: float) -> str:
    return lockup(height_mm, text=WHITE, badge_kw={"frame": False})


def scoped(svg: str, suffix: str) -> str:
    """Inline SVG <style> rules are global to the page: give each diagram its own class names."""
    return re.sub(r'\.(lb|dim|dsh|gl)\{', lambda m: f".{m.group(1)}-{suffix}{{", 
                  re.sub(r'class="(lb|dim|dsh|gl)"', lambda m: f'class="{m.group(1)}-{suffix}"', svg))


# ---------------------------------------------------------------- colour maths

def hex_rgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def hsl(h: str) -> str:
    r, g, b = (v / 255 for v in hex_rgb(h))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    return f"{hue * 360:.0f}° {sat * 100:.0f}% {light * 100:.0f}%"


def rating(ratio: float) -> str:
    if ratio >= 7:
        return "AAA text"
    if ratio >= 4.5:
        return "AA text"
    if ratio >= 3:
        return "Large text, graphics"
    return "Decoration only"


# ---------------------------------------------------------------- page frame

PAGES: list[tuple[str, str, str]] = []  # (section label, html, class)


def add(html: str, section: str = "", cls: str = "") -> None:
    PAGES.append((section, html, cls))


def divider(num: str, title: str, items: list[str], note: str) -> None:
    lis = "".join(f"<li>{i}</li>" for i in items)
    add(f"""<div class="dv-num">{num}</div><div class="dv-body"><div class="dv-title">{title}</div>
<p class="dv-note">{note}</p><ul class="dv-list">{lis}</ul></div>
<div class="dv-mark">{open_mark('on-cobalt', 150)}</div>""", "", "divider")


def tile(inner: str, bg: str = "#F4F3EF", caption: str = "", cap_colour: str = MUTED, cls: str = "", style: str = "") -> str:
    cap = f'<div class="tcap" style="color:{cap_colour}">{caption}</div>' if caption else ""
    return f'<div class="tile {cls}" style="background:{bg};{style}"><div class="tin">{inner}</div>{cap}</div>'


def example(inner: str, caption: str, bg: str, ok: bool) -> str:
    sign = '<span class="ok">✓</span>' if ok else '<span class="x">✕</span>'
    edge = ";box-shadow:inset 0 0 0 .3mm #e2e0da" if bg in ("#fff", "#ffffff", WHITE) else ""
    return (f'<div class="ex"><div class="exa" style="background:{bg}{edge}">{inner}</div>'
            f'<div class="exc">{sign}{caption}</div></div>')


def bad(inner: str, caption: str, bg: str = "#F4F3EF") -> str:
    return example(inner, caption, bg, False)


def good(inner: str, caption: str, bg: str = "#F4F3EF", cap_colour: str = MUTED) -> str:
    return example(inner, caption, bg, True)


# ---------------------------------------------------------------- pages

def cover() -> None:
    add(f"""<div class="cv-top">{lockup(16, text=WHITE, badge_kw={"frame": False})}</div>
<div class="cv-title">Brand<br>guidelines</div>
<div class="cv-sub">How to use the Bellweather name, mark, colours and voice.<br>Version {VERSION} · {DATE}</div>
<div class="cv-mark">{open_mark('on-cobalt', 178)}</div>""", "", "cover")


def contents() -> None:
    rows = [
        ("01", "Brand", "The idea, the name, the principles"),
        ("02", "Logo", "Lockup, the motto, badge, construction, clear space, sizes, variants, misuse"),
        ("03", "Colour", "Palette, screen and print values, proportions, accessibility"),
        ("04", "Typography", "Outfit, the type scale, how to write the name"),
        ("05", "Voice", "How Bellweather speaks"),
        ("06", "Applications", "Home Assistant, GitHub, the web, credit badges"),
        ("07", "Resources", "Files, formats and the reasons behind the design"),
    ]
    toc = "".join(f'<div class="toc-row"><span class="toc-n">{n}</span><span class="toc-t">{t}</span><span class="toc-d">{d}</span></div>'
                  for n, t, d in rows)
    add(f"""<div class="cols" style="grid-template-columns:1fr 1.25fr;gap:16mm;height:100%">
<div><div class="kicker">About these guidelines</div><h1>One look, everywhere Bellweather appears</h1>
<p class="lead">These pages show how to use the Bellweather logo, colours, type and voice so the integration looks and sounds the same in Home Assistant, on GitHub, in documentation and anywhere else.</p>
<p>They are for contributors, writers and anyone who shows Bellweather: in a README, a blog post, a talk or a dashboard screenshot.</p>
<p><b>The one rule:</b> use the supplied files. Do not redraw, retype or recolour the logo. Every file listed in section 07 is generated from one geometry source, so they always match.</p></div>
<div class="toc">{toc}</div></div>""", "Contents")


def brand_idea() -> None:
    add(f"""<div class="cols" style="grid-template-columns:1.05fr 1fr;gap:14mm">
<div><div class="kicker">The idea</div><h1>The weather here and now, measured</h1>
<p class="lead">Bellweather tells you what the weather is doing at your home, from what was actually measured, and how it will change, from a forecast that keeps score of itself.</p>
<p><b>The name.</b> A bellwether was the sheep that wore a bell and led the flock; today the word means a leading indicator, something that shows which way things are heading. Bellweather adds the weather: an indicator you can hear and trust.</p>
<p><b>The mark</b> tells the same story in one shape:</p>
<ul class="bul"><li><b>a raindrop:</b> the weather at your home, right now;</li><li><b>that is also a bell:</b> a signal you notice, with its clapper below;</li><li><b>under a weathervane:</b> direction, the forecast, where things are heading.</li></ul></div>
<div class="panel center" style="background:#F4F3EF">{anatomy()}</div></div>""", "01 · Brand")


def badge_point(x: float, y: float, size_mm: float, top_mm: float = 0.0) -> tuple[float, float]:
    """A point of the mark grid, in mm, on a badge of size_mm placed at (0, top_mm)."""
    k, tx, ty = eb.badge_placement()
    return (tx + x * k) * size_mm / 120, top_mm + (ty + y * k) * size_mm / 120


def anatomy() -> str:
    size, top, label_x = 64.0, 4.0, 72.0
    parts = [
        ("Weathervane · direction", 89.0, 14.0),
        ("Raindrop · the weather now", 82.0, 66.0),
        ("Bell · a signal", 91.5, 95.6),
        ("Clapper", 67.5, 107.5),
    ]
    lines, labels = [], []
    for i, (text, x, y) in enumerate(parts):
        px, py = badge_point(x, y, size, top)
        ly = py if i < 3 else py + 3.0  # keep the lip and clapper labels apart
        lines.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r=".9" fill="{RED}"/>'
                     f'<polyline points="{px:.2f},{py:.2f} {label_x - 6:.2f},{ly:.2f} {label_x - 1.5:.2f},{ly:.2f}" fill="none" stroke="{RED}" stroke-width=".3"/>')
        labels.append(f'<div class="anl" style="top:{ly - 2.2:.2f}mm;left:{label_x}mm">{text}</div>')
    return (f'<div class="anat"><div style="position:absolute;left:0;top:{top}mm">{badge(size)}</div>'
            f'<svg viewBox="0 0 118 76" style="position:absolute;left:0;top:0;width:118mm;height:76mm">{"".join(lines)}</svg>'
            f'{"".join(labels)}</div>')


def principles() -> None:
    items = [
        ("Measured", "Every claim rests on data: what was observed, what was scored, what the evidence says. If we cannot show it, we do not say it."),
        ("Honest about cost", "Every setting says what it gives and what it costs. Nothing is hidden behind a toggle or a superlative."),
        ("Calm", "Clear and quiet. One accent colour, plain words, no alarm unless the weather calls for it."),
        ("Safe by default", "The most private and secure option is the default. Home data stays home."),
    ]
    cards = "".join(f'<div class="pcard"><div class="pnum">{i + 1:02d}</div><div class="ptitle">{t}</div><p>{d}</p></div>'
                    for i, (t, d) in enumerate(items))
    isnt = [("Precise", "Pedantic"), ("Confident", "Boastful"), ("Friendly", "Cute"), ("Careful", "Fearful"), ("Plain", "Bland")]
    rows = "".join(f'<div class="isr"><span>{a}</span><span>{b}</span></div>' for a, b in isnt)
    add(f"""<div class="kicker">Personality</div><h1>Four principles</h1>
<div class="pgrid">{cards}</div>
<div class="isbox"><div class="isr ish"><span>Bellweather is</span><span>but not</span></div>{rows}</div>""", "01 · Brand")


def logo_primary() -> None:
    add(f"""<div class="kicker">The logo</div><h1>The primary lockup</h1>
<div class="cols" style="grid-template-columns:1fr 62mm;gap:12mm;align-items:start">
<div class="panel center" style="background:#fff;border:0.3mm solid #e6e4de;height:118mm">{lockup(34)}</div>
<div><p class="lead">The cobalt badge and the wordmark, side by side. This is the logo: use it whenever there is room.</p>
<p>The badge carries its own background, so the logo reads the same on white, on paper and on dark grounds.</p>
<p>The wordmark is set in Outfit Medium and is part of the artwork: never type it out next to the badge.</p>
<p class="small">Files: <code>logo.png</code>, <code>logo@2x.png</code> (light grounds) and <code>dark_logo.png</code>, <code>dark_logo@2x.png</code> (dark grounds).</p></div></div>""", "02 · Logo")


def logo_family() -> None:
    add(f"""<div class="kicker">The logo family</div><h1>Four forms, one look</h1>
<div class="g2x2">
{tile(lockup(17), "#fff", "<b>Lockup, light.</b> The default, on white and light grounds.", style="border:0.3mm solid #e6e4de")}
{tile(lockup(17, text=PAPER), DARK, "<b>Lockup, dark.</b> Paper wordmark on dark grounds.", "#a8a8a8")}
{tile(badge(30), "#F4F3EF", "<b>Badge.</b> The icon: Home Assistant, favicons, avatars, app grids.")}
{tile(reversed_lockup(17), COBALT, "<b>Reversed, on cobalt.</b> Without its frame, on Bellweather cobalt only.", "#d7e3fb")}
</div>""", "02 · Logo")


def construction_badge() -> None:
    k, tx, ty = eb.badge_placement()
    grid = "".join(f'<line x1="{i}" y1="0" x2="{i}" y2="120" class="gl"/><line x1="0" y1="{i}" x2="120" y2="{i}" class="gl"/>' for i in range(10, 120, 10))
    mx0, my0 = tx + eb.X0 * k, ty + eb.Y0 * k
    mx1, my1 = tx + eb.X1 * k, ty + eb.Y1 * k
    r = eb.BADGE_RADIUS
    reach, _, _, _, _, q = eb._corner_params(r, eb.BADGE_SMOOTHING)
    Z = 3
    svg = f"""<svg viewBox="{-34 * Z} {-16 * Z} {196 * Z} {152 * Z}" style="width:118mm;display:block">
<style>.gl{{stroke:#ffffff;stroke-opacity:.18;stroke-width:.25}} .dim{{stroke:{RED};stroke-width:.45;fill:none}} .dsh{{stroke:{RED};stroke-width:.45;stroke-dasharray:1.6 1.2;fill:none}} .lb{{font:500 16px Outfit;fill:{RED}}}</style>
<g transform="scale({Z})">{badge_group()}{grid}
<rect x="{mx0:.2f}" y="{my0:.2f}" width="{mx1 - mx0:.2f}" height="{my1 - my0:.2f}" class="dsh"/>
<circle cx="{q:.2f}" cy="{q:.2f}" r="{r}" class="dsh"/>
<line x1="{q:.2f}" y1="{q:.2f}" x2="{q - r * 0.7071:.2f}" y2="{q - r * 0.7071:.2f}" class="dim"/>
<line x1="{reach:.2f}" y1="-3" x2="{reach:.2f}" y2="3" class="dim"/><line x1="-3" y1="{reach:.2f}" x2="3" y2="{reach:.2f}" class="dim"/>
<line x1="60" y1="-6" x2="60" y2="126" class="dsh"/><line x1="-6" y1="60" x2="126" y2="60" class="dsh"/>
<line x1="132" y1="0" x2="132" y2="120" class="dim"/><line x1="129" y1="0" x2="135" y2="0" class="dim"/><line x1="129" y1="120" x2="135" y2="120" class="dim"/>
<line x1="-8" y1="{my0:.2f}" x2="-8" y2="{my1:.2f}" class="dim"/><line x1="-11" y1="{my0:.2f}" x2="-5" y2="{my0:.2f}" class="dim"/><line x1="-11" y1="{my1:.2f}" x2="-5" y2="{my1:.2f}" class="dim"/></g>
<text x="{-1 * Z}" y="{-5 * Z}" class="lb">radius 27 · smoothing 60 % · curve starts at {reach:.1f}</text>
<text x="{137 * Z}" y="{62 * Z}" class="lb">120</text>
<text x="{-12 * Z}" y="{62 * Z}" class="lb" text-anchor="end">80 %</text>
</svg>"""
    svg = scoped(svg, "badge")
    add(f"""<div class="kicker">Construction</div><h1>The badge</h1>
<div class="cols" style="grid-template-columns:120mm 1fr;gap:14mm;align-items:center">
<div class="panel center" style="background:#F4F3EF;padding:6mm">{svg}</div>
<div><p class="lead">A cobalt square of 120 units with smoothed corners, holding the white bell and the sun vane.</p>
<ul class="bul"><li>The mark fills 80 % of the badge's height, centred on its bounding box.</li>
<li>Each corner is a 27-unit arc (22.5 %) eased into the edges: the curve starts 43.2 units from the corner with zero curvature and grows steadily into the arc. A plain circular corner jumps from straight to round in one point, and the eye sees that jump as a kink.</li>
<li>The corners are fixed. A sharper, rounder or plain circular corner is a different badge.</li>
<li>The sheen on the bell is a cut-out, so the cobalt shows through it.</li>
<li>Exported at 256 and 512 px for Home Assistant, and as <code>badge.svg</code> for everything else.</li></ul></div></div>""", "02 · Logo")


def svg_data(name: str) -> tuple[str, dict[str, float], float, float]:
    """An outlined SVG's body, its construction numbers and its size."""
    path = HERE.parent / name
    if not path.exists():
        raise SystemExit(f"{name} is missing: run branding/export_kit.py")
    svg = path.read_text(encoding="utf-8").strip()
    head = re.match(r"<svg [^>]*>", svg).group(0)
    data = {k: float(v) for k, v in re.findall(r'data-([a-z-]+)="([\d.]+)"', head)}
    w, h = (float(v) for v in re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', head).groups())
    body = re.sub(r"<title>.*?</title>", "", svg[len(head):].replace("</svg>", ""))
    return body, data, w, h


def motto_logo() -> None:
    mm, px = ek.motto_minimum()
    add(f"""<div class="kicker">The logo with the motto</div><h1>The logo with the motto</h1>
<div class="g2x2" style="grid-template-rows:50mm 50mm">
{tile(art_svg("logo-motto.svg", 19), "#fff", "<b>Beside the badge.</b> The default form: README headers, site headers, title slides.", style="border:0.3mm solid #e6e4de")}
{tile(art_svg("logo-motto-dark.svg", 19), DARK, "<b>Beside the badge, dark.</b> Paper wordmark, a soft grey motto.", "#a8a8a8")}
{tile(art_svg("logo-motto-stacked.svg", 27), "#F4F3EF", "<b>Stacked.</b> For square and portrait spaces: social posts, profile cards, stickers.")}
{tile(art_svg("logo-motto-stacked-reversed-white.svg", 27), COBALT, "<b>Stacked, on cobalt.</b> Without its frame, on Bellweather cobalt only.", "#d7e3fb")}
</div>
<div class="cols3" style="margin-top:6mm">
<p><b>When.</b> Where Bellweather introduces itself: the top of the README, a site header, a title slide, a social post. Everywhere else, in Home Assistant and next to other logos, use the logo or the badge: the motto is for the first meeting, not for every mention.</p>
<p><b>The words.</b> Always <i>The weather here and now, measured.</i>, exactly as in the files. The motto is part of the artwork: never type it next to the logo, and never change its words, size or colour.</p>
<p><b>Size.</b> The motto has to stay readable: the badge at least {mm} mm high in print and {px} px on screen, which keeps the motto at 7 pt and 11 px. Smaller than that, use the logo. Clear space as for the logo: a quarter of the badge's height on every side.</p></div>""", "02 · Logo")


def construction_motto() -> None:
    style = (f"<style>.dim{{stroke:{RED};stroke-width:.7;fill:none}} .dsh{{stroke:{RED};stroke-width:.7;stroke-dasharray:2.4 1.8;fill:none}} "
             f".lb{{font:500 10.5px Outfit;fill:{RED}}}</style>")

    def vdim(x, y0, y1, label, side=1):
        tx = x + 6 * side
        anchor = "start" if side > 0 else "end"
        return (f'<line x1="{x:.2f}" y1="{y0:.2f}" x2="{x:.2f}" y2="{y1:.2f}" class="dim"/>'
                f'<line x1="{x - 4:.2f}" y1="{y0:.2f}" x2="{x + 4:.2f}" y2="{y0:.2f}" class="dim"/>'
                f'<line x1="{x - 4:.2f}" y1="{y1:.2f}" x2="{x + 4:.2f}" y2="{y1:.2f}" class="dim"/>'
                f'<text x="{tx:.2f}" y="{(y0 + y1) / 2 + 3.5:.2f}" class="lb" text-anchor="{anchor}">{label}</text>')

    def hline(x0, x1, y, faint=False):
        op = ' style="stroke-opacity:.5"' if faint else ""
        return f'<line x1="{x0:.2f}" y1="{y:.2f}" x2="{x1:.2f}" y2="{y:.2f}" class="dsh"{op}/>'

    body, d, w, _ = svg_data("logo-motto.svg")
    cap, mcap, left, base, mbase = d["cap"], d["motto-cap"], d["left"], d["base"], d["motto-base"]
    right, top, mtop = left + d["word-width"], base - cap, mbase - mcap
    ratio = mcap / cap
    beside = f"""<svg viewBox="-40 -24 {w + 150:.2f} 172" style="width:150mm;display:block">{style}{body}
{hline(-20, w + 20, top)}{hline(-20, w + 20, base)}{hline(left - 10, w + 20, mtop)}{hline(left - 10, w + 20, mbase)}
{vdim(w + 14, top, base, "cap")}{vdim(w + 14, base, mtop, f"{ek.MOTTO_GAP:.2f} cap")}{vdim(w + 14, mtop, mbase, f"{ratio:.2f} cap")}
{vdim(-14, 0, top, "a", -1)}{vdim(-14, mbase, 120, "a", -1)}
<line x1="{left:.2f}" y1="-12" x2="{right:.2f}" y2="-12" class="dim"/><line x1="{left:.2f}" y1="-16" x2="{left:.2f}" y2="-8" class="dim"/><line x1="{right:.2f}" y1="-16" x2="{right:.2f}" y2="-8" class="dim"/>
<text x="{(left + right) / 2:.2f}" y="-17" class="lb" text-anchor="middle">wordmark width = motto width</text>
<line x1="{left:.2f}" y1="-6" x2="{left:.2f}" y2="132" class="dsh"/><line x1="{right:.2f}" y1="-6" x2="{right:.2f}" y2="132" class="dsh"/>
</svg>"""
    body, d, w, h = svg_data("logo-motto-stacked.svg")
    cap, mcap, base, mbase = d["cap"], d["motto-cap"], d["base"], d["motto-base"]
    top, mtop = base - cap, mbase - mcap
    stacked = f"""<svg viewBox="-20 -14 {w + 95:.2f} {h + 30:.2f}" style="width:76mm;display:block">{style}{body}
{hline(-10, w + 10, 120)}{hline(-10, w + 10, top)}{hline(-10, w + 10, base)}{hline(-10, w + 10, mtop)}
{vdim(w + 14, 120, top, f"{ek.STACK_GAP:.1f} cap")}{vdim(w + 14, base, mtop, f"{ek.MOTTO_GAP:.2f} cap")}
<line x1="{w / 2:.2f}" y1="-8" x2="{w / 2:.2f}" y2="{h + 8:.2f}" class="dsh" style="stroke-opacity:.5"/>
</svg>"""
    beside, stacked = scoped(beside, "mottob"), scoped(stacked, "mottos")
    light, dark, blue = (bm.contrast(ek.MOTTO_LIGHT, WHITE), bm.contrast(ek.MOTTO_DARK, DARK), bm.contrast(ek.MOTTO_ON_COBALT, COBALT))
    add(f"""<div class="kicker">Construction</div><h1>The logo with the motto</h1>
<div class="cols" style="grid-template-columns:1.75fr 1fr;gap:6mm;align-items:stretch">
<div class="panel center" style="background:#F4F3EF;padding:7mm 5mm">{beside}</div>
<div class="panel center" style="background:#F4F3EF;padding:7mm 5mm">{stacked}</div></div>
<div class="cols3" style="margin-top:6mm">
<p><b>Same width.</b> The motto is set exactly as wide as the wordmark, so the two lines start and end together; that makes its cap height {ratio:.2f} of the wordmark's. The badge, the gap and the wordmark keep the logo's sizes.</p>
<p><b>Spacing.</b> {ek.MOTTO_GAP:.2f} cap height from the wordmark's baseline to the motto's cap line. Beside the badge, the two lines sit in the middle of the badge's height (a = a); stacked, the badge is centred {ek.STACK_GAP:.1f} cap height above the wordmark, the same gap as in the logo.</p>
<p><b>Type and colour.</b> Outfit Regular (400), no tracking. The motto is <code>{ek.MOTTO_LIGHT}</code> on light grounds ({light:.1f} : 1 on white), <code>{ek.MOTTO_DARK}</code> on dark ({dark:.1f} : 1) and <code>{ek.MOTTO_ON_COBALT}</code> on cobalt ({blue:.1f} : 1): softer than the wordmark, still readable as text.</p></div>""", "02 · Logo")


def construction_lockup() -> None:
    inner, width, cap, base = lockup_parts()
    top = base - cap
    gap_x0 = 120
    gap_x1 = 120 + eb.LOGO_GAP * cap
    svg = f"""<svg viewBox="-30 -26 {width + 100:.2f} 182" style="width:240mm;display:block">
<style>.dim{{stroke:{RED};stroke-width:.7;fill:none}} .dsh{{stroke:{RED};stroke-width:.7;stroke-dasharray:2.4 1.8;fill:none}} .lb{{font:500 10.5px Outfit;fill:{RED}}}</style>
{inner}
<line x1="-20" y1="{top:.2f}" x2="{width + 20:.2f}" y2="{top:.2f}" class="dsh"/>
<line x1="-20" y1="{base:.2f}" x2="{width + 20:.2f}" y2="{base:.2f}" class="dsh"/>
<line x1="-20" y1="60" x2="{width + 20:.2f}" y2="60" class="dsh" style="stroke-opacity:.5"/>
<line x1="{width + 12:.2f}" y1="{top:.2f}" x2="{width + 12:.2f}" y2="{base:.2f}" class="dim"/>
<line x1="{width + 8:.2f}" y1="{top:.2f}" x2="{width + 16:.2f}" y2="{top:.2f}" class="dim"/><line x1="{width + 8:.2f}" y1="{base:.2f}" x2="{width + 16:.2f}" y2="{base:.2f}" class="dim"/>
<text transform="translate({width + 22:.2f} {(top + base) / 2:.2f}) rotate(90)" text-anchor="middle" class="lb">cap height</text>
<line x1="-12" y1="0" x2="-12" y2="120" class="dim"/><line x1="-16" y1="0" x2="-8" y2="0" class="dim"/><line x1="-16" y1="120" x2="-8" y2="120" class="dim"/>
<text transform="translate(-18 60) rotate(-90)" text-anchor="middle" class="lb">badge = 2.1 × cap height</text>
<line x1="{gap_x0:.2f}" y1="138" x2="{gap_x1:.2f}" y2="138" class="dim"/><line x1="{gap_x0:.2f}" y1="134" x2="{gap_x0:.2f}" y2="142" class="dim"/><line x1="{gap_x1:.2f}" y1="134" x2="{gap_x1:.2f}" y2="142" class="dim"/>
<text x="{(gap_x0 + gap_x1) / 2:.2f}" y="151" class="lb" text-anchor="middle">0.6 cap</text>
<text x="{width + 36:.2f}" y="63" class="lb" style="fill-opacity:.75">middle</text>
</svg>"""
    svg = scoped(svg, "lockup")
    add(f"""<div class="kicker">Construction</div><h1>The lockup</h1>
<div class="panel center" style="background:#F4F3EF;padding:8mm 6mm">{svg}</div>
<div class="cols3" style="margin-top:6mm">
<p><b>Size.</b> The badge is 2.1 times the wordmark's cap height, so the tall letters B, l, h and t span about half the badge.</p>
<p><b>Position.</b> The cap height is centred on the badge's middle line. The space between the badge and the B is 0.6 cap height.</p>
<p><b>Type.</b> Outfit Medium (500), tracking −1 %. The wordmark is artwork: place the supplied file, never retype it.</p></div>""", "02 · Logo")


def construction_mark() -> None:
    cx, cy, r = 60.0, 72.0, 22.0
    tip = (60.0, 24.0)
    a = math.acos(r / (cy - tip[1]))
    tl = (cx - r * math.sin(a), cy - r * math.cos(a))
    tr = (120 - tl[0], tl[1])
    vane_cy = 14.0
    Z = 5  # labels live outside the scaled group, so their font size is not clamped by the browser
    lab = [
        (100, 72.9, "circle r 22, centre (60, 72)"),
        (83, 45, "tangents to the tip (60, 24)"),
        (100, 83.9, "arc ends, y 83"),
        (100, 98.3, "crisp lip, y 97.4"),
        (97, -0.7, "vane's centre of mass on the axis"),
    ]
    texts = "".join(f'<text x="{x * Z:.1f}" y="{y * Z:.1f}" class="lb">{t}</text>' for x, y, t in lab)
    svg = f"""<svg viewBox="{8 * Z} {-6 * Z} {150 * Z} {126 * Z}" style="width:122mm;display:block">
<style>.dsh{{stroke:{RED};stroke-width:.35;stroke-dasharray:1.2 .9;fill:none}} .dim{{stroke:{RED};stroke-width:.35;fill:none}} .lb{{font:500 18px Outfit;fill:{RED}}}</style>
<g transform="scale({Z})"><g opacity=".9">{mark_body('color')}</g>
<circle cx="{cx}" cy="{cy}" r="{r}" class="dsh"/><circle cx="{cx}" cy="{cy}" r=".7" fill="{RED}"/>
<line x1="{tip[0]}" y1="{tip[1]}" x2="{tl[0]:.2f}" y2="{tl[1]:.2f}" class="dim"/><line x1="{tip[0]}" y1="{tip[1]}" x2="{tr[0]:.2f}" y2="{tr[1]:.2f}" class="dim"/>
<line x1="60" y1="-2" x2="60" y2="118" class="dsh"/>
<line x1="20" y1="83" x2="98" y2="83" class="dsh"/><line x1="20" y1="97.4" x2="98" y2="97.4" class="dsh"/>
<circle cx="60" cy="{vane_cy}" r="1" fill="{RED}"/>
<line x1="60" y1="14" x2="96" y2="-1.5" class="dim"/><line x1="82" y1="72" x2="99" y2="72" class="dim"/></g>
{texts}
</svg>"""
    svg = scoped(svg, "mark")
    add(f"""<div class="kicker">Construction</div><h1>The mark</h1>
<div class="cols" style="grid-template-columns:130mm 1fr;gap:10mm;align-items:start">
<div class="panel center" style="background:#fff;border:0.3mm solid #e6e4de;padding:4mm">{svg}</div>
<div>
<p><b>Grid.</b> 120 × 120 units. The drop-bell is a circle, centre (60, 72), radius 22, with straight tangents up to the tip (60, 24). The circle runs down to y 83, then one short curve turns into a crisp lip at y 97.4.</p>
<p><b>Vane.</b> A shaft 6 units thick, an arrow head and two tail fins, merged into one shape and scaled to 85 % around the pivot. Its centre of mass sits exactly on the rod's axis: the head is heavier than the tail, so the vane is shifted 3.9 units to the left. Centred on its outline instead, it pulls to the right.</p>
<p><b>Rounding.</b> Computed on the merged outline. The vane's outer corners are rounded with a 2-unit radius, and every inner joint (fins into shaft, shaft into rod, rod into tip) gets a 2-unit fillet. There are no seams and no ledges.</p>
<p><b>Colour seam.</b> The sun part ends just above the point where the rod meets the bell's sides, so the joint's fillet stays with the bell.</p>
<p><b>Sheen.</b> A long, curved highlight on the belly. It is cut out on light grounds and in the badge; on the open mark on dark it is filled with paper, because a cut-out there would read as a groove.</p></div></div>""", "02 · Logo")


def clear_space() -> None:
    inner, width, cap, base = lockup_parts()
    q = 30  # a quarter of the badge
    svg = f"""<svg viewBox="{-q - 8} {-q - 8} {width + 2 * q + 16:.2f} {120 + 2 * q + 16}" style="width:150mm;display:block">
<style>.dsh{{stroke:{RED};stroke-width:.8;stroke-dasharray:3 2.2;fill:none}} .lb{{font:500 13px Outfit;fill:{RED}}}</style>
<rect x="{-q}" y="{-q}" width="{width + 2 * q:.2f}" height="{120 + 2 * q}" class="dsh"/>
<rect x="{-q}" y="{-q}" width="{q}" height="{q}" fill="{RED}" fill-opacity=".14"/>
<rect x="{width:.2f}" y="120" width="{q}" height="{q}" fill="{RED}" fill-opacity=".14"/>
<text x="{-q + 6}" y="{-q / 2 + 3}" class="lb">¼</text>{inner}</svg>"""
    svg = scoped(svg, "clear")
    sizes = f"""<div class="minrow">
<div class="minc"><div class="minart">{lockup(6)}</div><div class="minl"><b>Logo</b><br>print: badge 6 mm high<br>screen: badge 20 px high</div></div>
<div class="minc"><div class="minart">{badge(5)}</div><div class="minl"><b>Badge</b><br>print: 5 mm<br>screen: 16 px</div></div>
<div class="minc"><div class="minart">{open_mark('color', 10)}</div><div class="minl"><b>Open mark</b><br>screen, light grounds: 32 px</div></div></div>"""
    add(f"""<div class="kicker">Clear space and size</div><h1>Room to breathe</h1>
<div class="cols" style="grid-template-columns:156mm 1fr;gap:12mm;align-items:start">
<div><div class="panel center" style="background:#F4F3EF;padding:6mm">{svg}</div>
<p class="small" style="margin-top:3mm">Keep at least a quarter of the badge's height clear on every side of the logo and of the badge. No text, edges or other logos inside it.</p></div>
<div><div class="sub">Minimum sizes</div>{sizes}
<p class="small">Below these sizes the vane and the wordmark lose their shape. The badge holds at 16 px because white on cobalt is {bm.contrast(WHITE, COBALT):.1f} : 1 and sun on cobalt {bm.contrast(SUN, COBALT):.1f} : 1; the open mark needs more because sun on white is only {bm.contrast(SUN, bm.LIGHT_BG):.1f} : 1.</p></div></div>""", "02 · Logo")


def variants() -> None:
    add(f"""<div class="kicker">Variants</div><h1>When to use which</h1>
<div class="g3x2">
{tile(lockup(11), "#fff", "<b>Lockup, light</b><br>Default on white, paper and light photos.", style="border:0.3mm solid #e6e4de")}
{tile(lockup(11, text=PAPER), DARK, "<b>Lockup, dark</b><br>On dark grounds and dark mode.", "#a8a8a8")}
{tile(reversed_lockup(11), COBALT, "<b>Reversed</b><br>On Bellweather cobalt only.", "#d7e3fb")}
{tile(badge(22), "#F4F3EF", "<b>Badge</b><br>Wherever a square icon is needed.")}
{tile(open_mark('mono-ink', 24), "#fff", "<b>One colour, ink</b><br>Single-colour print, engraving, stamps.", style="border:0.3mm solid #e6e4de")}
{tile(open_mark('mono-white', 24), "#2b3446", "<b>One colour, white</b><br>On photos and blue or mid-tone grounds.", "#c9cfdb")}
</div>
<p class="small" style="margin-top:4mm">The open mark in colour (cobalt bell, sun vane, no badge) is a secondary form for large sizes on light grounds, 32 px and up. When in doubt, use the badge.</p>""", "02 · Logo")


def busy_ground() -> str:
    """A CSS background of many small, high-contrast shapes in clashing
    colours, drawn as an SVG so it prints the same in the PDF."""
    import random
    from urllib.parse import quote

    rng = random.Random(20261111)
    colours = ["#1F5FD6", "#FFB31A", "#D93A3A", "#1E9E5A", "#0E1F3D", "#FFFFFF", "#8E44AD", "#F06EAA"]
    shapes = []
    for _ in range(170):
        x, y, r, c = rng.uniform(0, 200), rng.uniform(0, 140), rng.uniform(3, 11), rng.choice(colours)
        kind = rng.random()
        if kind < 0.4:
            shapes.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{c}"/>')
        elif kind < 0.75:
            shapes.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{2 * r:.1f}" height="{r:.1f}" fill="{c}" '
                          f'transform="rotate({rng.uniform(0, 180):.0f} {x:.1f} {y:.1f})"/>')
        else:
            shapes.append(f'<path d="M{x:.1f} {y:.1f}l{r:.1f} {2 * r:.1f}h{-2 * r:.1f}z" fill="{c}"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 140" preserveAspectRatio="xMidYMid slice">'
           f'<rect width="200" height="140" fill="#F7E6B0"/>{"".join(shapes)}</svg>')
    return f"url('data:image/svg+xml,{quote(svg)}') center / cover"


def backgrounds() -> None:
    sky = "linear-gradient(165deg,#d6ebfc 0%,#a9d0f3 55%,#7fb4e8 100%)"
    night = "linear-gradient(165deg,#2a3550 0%,#1b2236 60%,#121726 100%)"
    busy = busy_ground()
    near_cobalt = "#4A7FE0"  # a blue close to cobalt: the badge's edge turns muddy
    add(f"""<div class="kicker">Backgrounds</div><h1>Calm grounds only</h1>
<div class="g4">
{good(lockup(8.5), "White", "#fff")}
{good(lockup(8.5), "Paper", "#F4F3EF")}
{good(lockup(8.5), "Light sky", sky)}
{good(lockup(8.5, text=PAPER), "Dark", DARK, "#a8a8a8")}
{good(lockup(8.5, text=PAPER), "Night", night, "#a8b0c4")}
{good(reversed_lockup(8.5), "Cobalt: reversed", COBALT, "#d7e3fb")}
{bad(lockup(8.5, text=WHITE), "Badge on a blue close to cobalt: the edge turns muddy", near_cobalt)}
{bad(lockup(8.5), "Busy pattern: the logo gets lost", busy)}
</div>""", "02 · Logo")


def misuse() -> None:
    h = 6.6
    items = [
        (lockup(h, style="transform:scaleX(1.45);transform-origin:left center"), "Do not stretch or squash"),
        (lockup(h, style="transform:rotate(-9deg)"), "Do not rotate"),
        (lockup(h, badge_kw={"fill": "#2E9E5B", "recolor": {SUN: "#F2E205"}}), "Do not recolour"),
        (lockup(h, badge_kw={"clapper": SUN}), "No sun on the clapper"),
        (lockup(h, badge_kw={"radius": 0}), "Do not change the corners"),
        (lockup(h, style="filter:drop-shadow(1.2mm 1.2mm 1mm rgba(0,0,0,.45))"), "No shadows or effects"),
        (lockup(h, font="Georgia, serif", weight=400), "Do not retype the wordmark"),
        (lockup(h, text_first=True), "Do not rearrange"),
        (lockup(h, tracking=0.18), "Do not respace the letters"),
        (lockup(h, ratio=1.25), "Do not resize the parts"),
        (lockup(h, text=COBALT), "Do not recolour the wordmark"),
        (lockup(h, style="opacity:.35"), "Do not fade or tint"),
    ]
    cells = "".join(bad(f'<div class="mz">{art}</div>', cap) for art, cap in items)
    add(f"""<div class="kicker">Misuse</div><h1>What not to do</h1>
<div class="g4 tight">{cells}</div>""", "02 · Logo")


def palette() -> None:
    main = [
        ("Cobalt", COBALT, "The badge, the bell. Bellweather's own colour.", WHITE),
        ("Sun", SUN, "The vane. An accent, never text, never a background.", INK),
        ("Ink", INK, "The wordmark, text, one-colour marks.", WHITE),
        ("Paper", PAPER, "Calm grounds; the wordmark on dark.", INK),
        ("White", WHITE, "The bell in the badge; light grounds.", INK),
    ]
    cards = "".join(
        f'<div class="sw" style="background:{h};color:{fg}{";box-shadow:inset 0 0 0 .3mm #e2e0da" if h in (WHITE, PAPER) else ""}">'
        f'<div class="swn">{n}</div><div class="swv">HEX {h}<br>RGB {" ".join(str(v) for v in hex_rgb(h))}<br>HSL {hsl(h)}'
        f'<br>CMYK {cmyk_text(n)}</div>'
        f'<div class="swr">{role}</div></div>'
        for n, h, role, fg in main)
    dark = [("Cobalt light", COBALT_L, "Bell of the open mark on dark"), ("Sun light", SUN_L, "Vane of the open mark on dark"),
            ("Night", DARK, "Reference dark ground")]
    dcards = "".join(
        f'<div class="dsw"><div class="dchip" style="background:{h}"></div><div><b>{n}</b><br><code>{h}</code> · RGB {" ".join(str(v) for v in hex_rgb(h))}<br><span>{role}</span></div></div>'
        for n, h, role in dark)
    add(f"""<div class="kicker">Colour</div><h1>The palette</h1>
<div class="swrow">{cards}</div>
<div class="sub" style="margin-top:7mm">For dark grounds</div>
<div class="drow">{dcards}</div>
<p class="small" style="margin-top:3mm">HEX, RGB and HSL are for screens (sRGB). CMYK is for print on coated paper; the next page has the uncoated values and says which to use where.</p>""", "03 · Colour")


def cmyk_text(name: str) -> str:
    if name == "White":
        return "0 0 0 0 (paper)"
    row = next(c for c in ek.COLOURS if c[0] == name)
    return " ".join(str(v) for v in row[3])


def print_colour() -> None:
    def ink(v):
        return f"C{v[0]} M{v[1]} Y{v[2]} K{v[3]}"

    rows = "".join(
        f'<tr><td><span class="chip" style="background:{h}"></span>{n}</td><td><code>{h}</code> · {" ".join(str(v) for v in hex_rgb(h))}</td>'
        f'<td>{ink(cc)}</td><td class="de">{dc:.1f}</td><td>{ink(cu)}</td><td class="de">{du:.1f}</td></tr>'
        for n, h, _, cc, dc, cu, du in ek.COLOURS)
    add(f"""<div class="kicker">Colour</div><h1>Screen and print</h1>
<table class="ct wide pc"><tr><th>Colour</th><th>Screen: HEX · RGB</th><th>Coated paper: CMYK</th><th>ΔE</th><th>Uncoated paper: CMYK</th><th>ΔE</th></tr>{rows}</table>
<div class="cols3" style="margin-top:7mm">
<div class="use"><div class="use-t">Screens · HEX / RGB</div><p>Web, apps, Home Assistant, GitHub, slides, video, and every PNG and SVG. Office and home printers too: send them the RGB file and let the driver convert it.</p></div>
<div class="use"><div class="use-t">Coated paper · CMYK coated</div><p>Brochures, flyers, stickers and packaging on gloss or silk paper from a print shop. Profile: ISO Coated v2 (FOGRA39), the European standard for coated stock.</p></div>
<div class="use"><div class="use-t">Uncoated paper · CMYK uncoated</div><p>Letterheads, notebooks and business cards on matt, uncoated paper. Profile: PSO Uncoated ISO 12647.</p></div></div>
<div class="cols" style="grid-template-columns:1fr 1fr;gap:12mm;margin-top:6mm">
<p class="small"><b>What ΔE says.</b> The CIEDE2000 difference between the screen colour and what the ink prints; about 1 is the smallest difference visible side by side. Cobalt is brighter on screen than process inks can print, so it comes out a little duller on paper (7–8). Ink prints visibly lighter on uncoated paper (18), which cannot reach a deep navy.</p>
<p class="small"><b>How the values were made.</b> Converted from sRGB with LittleCMS, relative colorimetric with black point compensation, rounded to whole percent; <code>branding/colour_conversion.py</code> recomputes them for any profile. No Pantone colour is defined: where cobalt must match exactly (signage, textiles), pick the nearest spot colour from a physical Pantone guide and record it in this document. Send the press a PDF with the profile named, and ask for a proof.</p></div>""", "03 · Colour")


def proportions() -> None:
    pairs = [
        ("Ink on white", INK, WHITE), ("Ink on paper", INK, PAPER), ("Cobalt on white", COBALT, WHITE),
        ("White on cobalt", WHITE, COBALT), ("Sun on cobalt", SUN, COBALT), ("Paper on night", PAPER, DARK),
        ("Cobalt light on night", COBALT_L, DARK), ("Sun light on night", SUN_L, DARK), ("Sun on white", SUN, WHITE),
    ]
    rows = "".join(
        f'<tr><td><span class="pv" style="background:{bg};color:{fg}">Aa</span>{n}</td><td>{bm.contrast(fg, bg):.1f} : 1</td><td>{rating(bm.contrast(fg, bg))}</td></tr>'
        for n, fg, bg in pairs)
    share = bm.sun_share(eb.K, COBALT, eb.S)
    add(f"""<div class="kicker">Colour</div><h1>Proportion and contrast</h1>
<div class="cols" style="grid-template-columns:1fr 1fr;gap:14mm;align-items:start">
<div><div class="sub">In a layout</div>
<div class="bar"><span style="flex:62;background:{PAPER};color:{INK}">Paper / white 60 %+</span><span style="flex:22;background:{INK}">Ink 20 %</span><span style="flex:13;background:{COBALT}">Cobalt</span><span style="flex:3;background:{SUN}"></span></div>
<p class="small" style="margin-top:3mm">Calm grounds carry most of the area, ink carries the words, cobalt marks what is Bellweather's. Sun is a spark: the vane, and at most a small highlight.</p>
<div class="sub" style="margin-top:6mm">In the mark</div>
<p class="small">Sun covers {share * 100:.0f} % of the mark. Itten's teaching ratio for a balanced yellow and blue is about 27 % yellow: well below it, sun stays an accent instead of a rival.</p></div>
<div><div class="sub">Contrast (WCAG 2)</div><table class="ct"><tr><th>Pair</th><th>Ratio</th><th>Fit for</th></tr>{rows}</table>
<p class="small" style="margin-top:2mm">Thresholds: 7 : 1 AAA text, 4.5 : 1 AA text, 3 : 1 large text and graphics. Sun on white is decoration only: never use it for text or thin lines.</p></div></div>""", "03 · Colour")


def typeface() -> None:
    weights = "".join(f'<div class="wt" style="font-weight:{w}">{n} <span>{w}</span></div>' for w, n in ((300, "Light"), (400, "Regular"), (500, "Medium"), (600, "SemiBold")))
    add(f"""<div class="kicker">Typography</div><h1>Outfit</h1>
<div class="cols" style="grid-template-columns:1.15fr 1fr;gap:14mm;align-items:start">
<div><div class="aa">Aa</div>
<div class="glyphs">ABCDEFGHIJKLMNOPQRSTUVWXYZ<br>abcdefghijklmnopqrstuvwxyz<br>0123456789 ° % · : → °C km/h</div></div>
<div><p class="lead">A geometric sans with round, open forms that echo the drop and the bell. Free under the SIL Open Font License, from Google Fonts.</p>
<div class="wts">{weights}</div>
<p class="small">Medium (500) is the wordmark and headings; Regular (400) is body text; Light (300) only for very large display numbers. Where Outfit cannot be loaded (a GitHub README, a terminal), use the system's default sans-serif and never imitate the wordmark with it.</p></div></div>""", "04 · Typography")


def type_scale() -> None:
    scale = [("Display", "36 / 40", 500, 26, "The weather here and now"), ("Heading 1", "26 / 30", 500, 19, "Precipitation in the next hour"),
             ("Heading 2", "19 / 24", 500, 14, "How the forecast keeps score"),
             ("Body", "14 / 21", 400, 10.5, "Each model's bias over the last 30 days is removed, then models are weighted by their own record."),
             ("Small", "11 / 16", 400, 8.5, "Source: official stations within 25 km, updated 4 minutes ago."),
             ("Label", "10 / 14, caps +8 %", 500, 7.5, "SOURCES WORKING")]
    rows = "".join(
        f'<div class="ts"><div class="tsl"><b>{n}</b><br>{m} px<br>Outfit {w}</div>'
        f'<div class="tsx" style="font-weight:{w};font-size:{pt}pt;{"letter-spacing:.08em" if n == "Label" else ""}">{s}</div></div>'
        for n, m, w, pt, s in scale)
    spell = [("Bellweather", True), ("BellWeather", False), ("Bell Weather", False), ("BELLWEATHER", False), ("bellweather", False)]
    sp = "".join(f'<div class="spl {"y" if ok else "n"}"><span>{"✓" if ok else "✕"}</span>{s}</div>' for s, ok in spell)
    add(f"""<div class="kicker">Typography</div><h1>Scale and spelling</h1>
<div class="cols" style="grid-template-columns:1.6fr 1fr;gap:12mm;align-items:start">
<div>{rows}</div>
<div><div class="sub">Writing the name</div>{sp}
<p class="small" style="margin-top:3mm">One word, capital B, the rest lower case, in running text and headings alike. The lower-case form appears only where the system demands it: the domain <code>bellweather</code>, entity ids such as <code>weather.bellweather</code>, file names.</p></div></div>""", "04 · Typography")


def voice() -> None:
    pairs = [
        ("Rounded to about 10 km. Weather services learn your area, not your address.", "Military-grade privacy!"),
        ("7–31 % lower temperature error than ECMWF IFS alone, measured at six airports over 90 days.", "The most accurate forecast in the world."),
        ("Off by default. On: one more service learns your nearest airport's code.", "Turn this on for better results!"),
        ("No official station within 15 km reported in the last 30 minutes, so radar decides.", "Error: no data."),
    ]
    rows = "".join(f'<div class="vrow"><div class="vdo"><span class="ok">✓</span>{d}</div><div class="vdont"><span class="x">✕</span>{n}</div></div>' for d, n in pairs)
    add(f"""<div class="kicker">Voice</div><h1>How Bellweather speaks</h1>
<div class="cols" style="grid-template-columns:78mm 1fr;gap:12mm;align-items:start">
<div><ul class="bul">
<li><b>Say what was measured.</b> Numbers with their source and time beat adjectives.</li>
<li><b>Say what it costs.</b> Every option names its trade-off in the same breath.</li>
<li><b>Explain why.</b> A reason in one clause turns a rule into a choice.</li>
<li><b>Plain words.</b> Short sentences, everyday terms; technical terms only when they are the precise ones.</li>
<li><b>No hype.</b> No exclamation marks, no superlatives we cannot prove.</li></ul></div>
<div><div class="vhead"><span>Write this</span><span>Not this</span></div>{rows}</div></div>""", "05 · Voice")


def app_ha() -> None:
    add(f"""<div class="kicker">Applications</div><h1>Home Assistant</h1>
<div class="cols" style="grid-template-columns:1.3fr 1fr;gap:10mm;align-items:start">
<div><div class="ha">
<div class="ha-head"><div class="ha-logo">{lockup(6.2)}</div><div><div class="ha-title">Bellweather</div>
<div class="ha-meta">Version 0.1.0 · <span>Custom integration</span></div><div class="ha-links">1 service</div></div><div class="ha-btn">+ Add location</div></div>
<div class="ha-search">Search</div>
<div class="ha-sec">Integrations</div>
<div class="ha-list"><div class="ha-row">{badge(13)}<div><b>Bellweather</b><br><span>Custom integration</span></div></div>
<div class="ha-row">{badge(13)}<div><b>Bellweather</b><br><span>1 service</span></div></div></div>
<div class="ha-sec">Dark theme</div>
<div class="ha-list"><div class="ha-row dark">{badge(13)}<div><b>Bellweather</b><br><span>Custom integration</span></div></div>
<div class="ha-row dark">{badge(13)}<div><b>Bellweather</b><br><span>1 service</span></div></div></div></div>
<div class="small" style="margin-top:2.5mm">Mockup of the integration page header and integration lists. The real layout follows the Home Assistant version and theme.</div></div>
<div><p class="lead">Home Assistant reads the images from the integration's <code>brand</code> folder.</p>
<table class="ft"><tr><th>File</th><th>Size</th><th>Shows</th></tr>
<tr><td><code>icon.png</code></td><td>256 × 256</td><td>The badge</td></tr>
<tr><td><code>icon@2x.png</code></td><td>512 × 512</td><td>The badge, high density</td></tr>
<tr><td><code>logo.png</code></td><td>1231 × 256</td><td>Lockup, light</td></tr>
<tr><td><code>logo@2x.png</code></td><td>2461 × 512</td><td>Lockup, light, high density</td></tr>
<tr><td><code>dark_logo.png</code></td><td>1231 × 256</td><td>Lockup, dark</td></tr>
<tr><td><code>dark_logo@2x.png</code></td><td>2461 × 512</td><td>Lockup, dark, high density</td></tr></table>
<p class="small" style="margin-top:4mm">There is no <code>dark_icon</code>: the cobalt badge works on light and dark, so Home Assistant falls back to <code>icon.png</code>. All images are transparent and trimmed PNGs, as the Home Assistant brands specification asks.</p>
<p class="small">Entities follow Home Assistant's own icon set; the brand appears through the badge and the logo, never inside entity icons.</p></div></div>""", "06 · Applications")


def app_github() -> None:
    def readme(bg, line, txt, logo_name):
        return (f'<div class="gh" style="background:{bg};border-color:{line}"><div style="display:flex;justify-content:center">'
                f'{art_svg(logo_name, 12)}</div>'
                f'<p style="color:{txt};text-align:center"><b style="color:inherit">And the best forecast for your home, scored against what was measured.</b></p></div>')

    social = (f'<div class="social"><div>{reversed_lockup(17)}</div>'
              f'<div class="soc-t">The weather here and now, measured.</div><div class="soc-s">Home Assistant integration</div></div>')
    add(f"""<div class="kicker">Applications</div><h1>GitHub</h1>
<div class="cols" style="grid-template-columns:1fr 1fr;gap:10mm;align-items:start">
<div><div class="sub">README header, light and dark: the logo with the motto</div>{readme("#fff", "#d0d7de", "#1f2328", "logo-motto.svg")}<div style="height:5mm"></div>{readme("#0d1117", "#30363d", "#e6edf3", "logo-motto-dark.svg")}</div>
<div><div class="sub">Social preview, 1280 × 640</div>{social}
<div class="avrow"><div class="av">{badge(16)}</div><div class="small"><b>Repository avatar.</b> The badge on its own. Platforms crop avatars to a circle or a rounded square; the badge's clear space survives both.</div></div></div></div>""", "06 · Applications")


def app_web() -> None:
    def bar(bg, line, txt, t):
        return (f'<div class="wbar" style="background:{bg};border-color:{line}">{lockup(8, text=t)}'
                f'<nav style="color:{txt}">Docs<span>Sources</span><span>Releases</span></nav></div>')

    icons = "".join(f'<div class="ico">{badge(px * 0.22)}<span>{px} px</span></div>' for px in (16, 24, 32, 48, 180))
    add(f"""<div class="kicker">Applications</div><h1>Web and small sizes</h1>
<div class="sub">Site header, light and dark</div>
{bar("#fff", "#e3e3e3", "#444", INK)}<div style="height:4mm"></div>{bar(DARK, "#333", "#bbb", PAPER)}
<div class="cols" style="grid-template-columns:1.1fr 1fr;gap:12mm;margin-top:10mm;align-items:start">
<div><div class="sub">Favicons and app icons</div><div class="icos">{icons}</div>
<p class="small">The badge at every size, from the browser tab to a home screen; drawn on this page at 0.22 mm per pixel.</p></div>
<div><div class="sub">Ready-made files</div><ul class="bul">
<li><code>favicon.ico</code>: 16, 32 and 48 px in one file, for browser tabs.</li>
<li><code>apple-touch-icon.png</code>: 180 px, for phone home screens.</li>
<li><code>badge-16.png</code> to <code>badge-1024.png</code>: ten sizes for app stores, avatars and docs.</li>
<li><code>badge.svg</code>: any other size, always sharp.</li></ul>
<p class="small">All are in the brand kit's <code>badge</code> folder. Below 32 px, use the badge: the lockup's wordmark is too small to read.</p></div></div>""", "06 · Applications")


def art_svg(name: str, height_mm: float) -> str:
    """An outlined SVG from export_kit, inline, at a height in mm."""
    path = HERE.parent / name
    if not path.exists():
        raise SystemExit(f"{name} is missing: run branding/export_kit.py")
    svg = path.read_text(encoding="utf-8").strip()
    m = re.match(r'<svg [^>]*viewBox="0 0 ([\d.]+) ([\d.]+)"[^>]*>', svg)
    w, h = float(m.group(1)), float(m.group(2))
    return (f'<svg viewBox="0 0 {w} {h}" style="height:{height_mm}mm;width:{height_mm * w / h:.2f}mm;display:block">'
            + svg[m.end():])


def credit_badges() -> None:
    px = 25.4 / 96  # one CSS pixel in mm
    sizes = "".join(f'<div class="cbs"><div>{art_svg("credit-light.svg", h * px)}</div><span>{h} px</span></div>' for h in (20, 28, 40))
    dark = "".join(f'<div class="cbs"><div>{art_svg("credit-dark.svg", h * px)}</div><span>{h} px</span></div>' for h in (20, 28, 40))
    code = ek.EMBED.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    add(f"""<div class="kicker">Applications</div><h1>Credit badges</h1>
<div class="panel cbp" style="background:#fff;border:0.3mm solid #e6e4de">{sizes}</div>
<div class="panel cbp" style="background:{DARK};margin-top:3mm">{dark}</div>
<div class="cols" style="grid-template-columns:1fr 1.1fr;gap:10mm;align-items:start;margin-top:6mm">
<div><ul class="bul">
<li><b>Set only the height;</b> the width follows. Any height from 20 px; 28 px sits well next to body text.</li>
<li><b>Light on light pages, dark on dark ones.</b> The code below picks the right one by itself.</li>
<li><b>Leave a quarter of its height clear</b> around it, and link it to the project page.</li>
<li><b>Do not edit</b> the text, the colours or the proportions.</li></ul>
<p class="small"><b>Where to host it.</b> Copy <code>credit-light.svg</code> and <code>credit-dark.svg</code> to your own site and point the code there. This is the most private way: no third party sees your visitors, and the badge keeps working if the repository moves. Linking straight to the repository's copy also works, but then GitHub serves every view.</p>
<p class="small"><b>Why SVG.</b> Vector, sharp at any size, about 24 KB. The text is drawn as outlines, so it needs no font and looks the same on every site. PNGs at 28 and 56 px are in the kit for places that do not take SVG.</p></div>
<div><div class="sub">Embed code (HTML and GitHub Markdown)</div><pre class="code">{code}</pre></div></div>""", "06 · Applications")


def files() -> None:
    kit = [("logo/", "Logo and logo with the motto: SVG (light, dark, reversed) and PNG"), ("badge/", "badge.svg, 10 PNG sizes, favicon.ico, apple-touch-icon"),
           ("mark/", "Open mark: colour and one-colour SVG"), ("credit/", "Credit badges: SVG, PNG, embed code"),
           ("colour/", "ASE, GIMP palette, CSS, JSON"), ("social/", "Social preview, 1280 × 640"),
           ("Bellweather-Brand-Guidelines.pdf", "This document"), ("README.txt", "What is where, colours, rules in brief")]
    kl = "".join(f"<tr><td><code>{a}</code></td><td>{b}</td></tr>" for a, b in kit)
    rows = [
        ("branding/logo.svg · logo-dark.svg · logo-reversed-white.svg", "Logo, vector, outlined", "Any size; needs no font"),
        ("branding/logo-motto*.svg · logo-motto-stacked*.svg", "Logo with the motto, beside the badge and stacked", "READMEs, headers, title slides, social posts"),
        ("branding/badge.svg", "Badge, vector", "Any size; favicons, avatars"),
        ("branding/mark-*.svg", "Open mark, colour and one colour", "Large sizes, print, photos"),
        ("branding/credit-light.svg · credit-dark.svg", "Credit badges", "Embedding on other sites"),
        ("custom_components/bellweather/brand/*.png", "Home Assistant images", "Icon and logos"),
        ("branding/build_mark.py · export_brand.py", "Geometry source and exporter", "Every shape, the HA images"),
        ("branding/export_kit.py", "Kit builder", "SVG logos, credits, this PDF, the zip"),
        ("branding/colour_conversion.py", "Colour conversion", "CMYK values for any press profile"),
    ]
    tr = "".join(f"<tr><td><code>{a}</code></td><td>{b}</td><td>{c}</td></tr>" for a, b, c in rows)
    add(f"""<div class="kicker">Resources</div><h1>Files and formats</h1>
<div class="cols" style="grid-template-columns:1fr 1.25fr;gap:10mm;align-items:start">
<div class="kitbox"><div class="kit-t">Bellweather-Brand-Kit.zip</div><div class="kit-s">Everything in one download, in the repository's <code>branding</code> folder.</div>
<table class="ft">{kl}</table></div>
<div><table class="ft"><tr><th>Source file</th><th>What</th><th>Use</th></tr>{tr}</table></div></div>
<div class="cols3" style="margin-top:7mm">
<p><b>One source.</b> Every shape is computed by <code>build_mark.py</code> and <code>export_brand.py</code>; <code>export_kit.py</code> writes the SVG logos, the credit badges, this PDF and the zip. Change the geometry, run both, and every file stays in step.</p>
<p><b>Typeface.</b> Outfit, SIL Open Font License 1.1, from Google Fonts. In the SVG logos the letters are outlines drawn from Outfit, so they need no font; the PNG wordmark is checked against Outfit Medium's measured stroke-to-height ratio.</p>
<p><b>Formats.</b> SVG for anything that scales; PNG with transparency where a raster is required. Do not use JPEG for the logo: it blurs the edges and has no transparency.</p></div>""", "07 · Resources")


def rationale() -> None:
    refs = [
        "Kleffner, D. A. &amp; Ramachandran, V. S. (1992). On the perception of shape from shading. <i>Perception &amp; Psychophysics</i>, 52(1), 18–36.",
        "Walker, P., Francis, B. J. &amp; Walker, L. (2010). The brightness-weight illusion. <i>Experimental Psychology</i>, 57(6), 462–469.",
        "von Restorff, H. (1933). Über die Wirkung von Bereichsbildungen im Spurenfeld. <i>Psychologische Forschung</i>, 18, 299–342.",
        "Labrecque, L. I. &amp; Milne, G. R. (2012). Exciting red and competent blue. <i>Journal of the Academy of Marketing Science</i>, 40(5), 711–727.",
        "Itten, J. <i>The Art of Color</i>: contrast of extension (teaching ratio yellow : blue = 3 : 8).",
        "Henderson, P. W. &amp; Cote, J. A. (1998). Guidelines for selecting or modifying logos. <i>Journal of Marketing</i>, 62(2), 14–30.",
    ]
    rl = "".join(f"<li>{r}</li>" for r in refs)
    add(f"""<div class="kicker">Resources</div><h1>Why it looks like this</h1>
<div class="cols" style="grid-template-columns:1.3fr 1fr;gap:12mm;align-items:start">
<div><ul class="bul">
<li><b>One accent, not two.</b> An item that differs from its surroundings is remembered better (the von Restorff effect), and emphasis repeated on several elements cancels out. Sun appears in one place: the vane.</li>
<li><b>Light above, dark below.</b> The visual system assumes light comes from above, and darker objects look heavier. A bright vane over a deep bell reads as natural and stable; nothing bright pulls the eye to the bottom.</li>
<li><b>Measured proportions.</b> Sun covers about 15 % of the mark, well below the balance point for yellow and blue, so it stays an accent.</li>
<li><b>Corners without a kink.</b> The badge's corners change curvature continuously, the way a smoothly bent strip does; a circular corner meets the straight edge with a sudden change that shows at large sizes.</li>
<li><b>A badge, because the slots are square.</b> Home Assistant, app grids and browser tabs show icons in squares. The open mark is only {(eb.X1 - eb.X0) / (eb.Y1 - eb.Y0) * 100:.0f} % as wide as it is tall; the badge fills the slot and brings its own ground.</li>
<li><b>Works without colour.</b> In greyscale the mark keeps clear steps of lightness, so it does not depend on hue.</li>
<li><b>Blue for trust.</b> Blue is linked with competence in brand research, the right note for a forecast.</li></ul></div>
<div><div class="sub">References</div><ol class="refs">{rl}</ol></div></div>""", "07 · Resources")


def back_cover() -> None:
    add(f"""<div class="bc">{open_mark('on-cobalt', 30)}<div class="bc-t">Bellweather</div>
<div class="bc-s">Brand guidelines · Version {VERSION} · {DATE}</div>
<div class="bc-s" style="margin-top:2mm">Generated from the project's own geometry source. Questions and changes: open an issue in the project repository.</div></div>""", "", "cover back")


CSS = """
@page { size: 297mm 210mm; margin: 0 }
.chip { display: inline-block; width: 4mm; height: 4mm; border-radius: 1mm; vertical-align: -0.8mm; margin-right: 2.5mm; box-shadow: inset 0 0 0 .2mm rgba(0,0,0,.12) }
table.pc td.de { color: #5d6472; text-align: right; padding-right: 7mm } table.pc th:nth-child(4), table.pc th:nth-child(6) { padding-right: 7mm } table.pc th:nth-child(4), table.pc th:nth-child(6) { text-align: right }
.use { background: #F4F3EF; border-radius: 2.5mm; padding: 4.5mm 5mm } .use-t { font-weight: 500; font-size: 10.5pt; margin-bottom: 2mm } .use p { font-size: 9pt; margin: 0 }
.cbp { display: flex; align-items: flex-end; gap: 8mm; padding: 5mm 7mm } .cbs { display: flex; flex-direction: column; gap: 2mm; font-size: 7.5pt; color: #8a8f99 }
pre.code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 7.4pt; line-height: 1.45; background: #F4F3EF; border-radius: 2.5mm; padding: 4mm 5mm; margin: 0; white-space: pre-wrap; color: #2b3446 }
.kitbox { background: #F4F3EF; border-radius: 3mm; padding: 6mm 7mm } .kit-t { font-weight: 500; font-size: 14pt } .kit-s { font-size: 9pt; color: #5d6472; margin: 1.5mm 0 4mm }

* { box-sizing: border-box }
html, body { margin: 0; padding: 0 }
body { font-family: Outfit, sans-serif; color: #1d2433; -webkit-print-color-adjust: exact; print-color-adjust: exact }
.page { width: 297mm; height: 210mm; position: relative; overflow: hidden; break-after: page; background: #fff; padding: 21mm 20mm 18mm }
.hdr { position: absolute; top: 9mm; left: 20mm; right: 20mm; display: flex; justify-content: space-between; font-size: 7pt; letter-spacing: .1em; text-transform: uppercase; color: #8a8f99 }
.hdr b { color: #1F5FD6; font-weight: 500 }
.ftr { position: absolute; bottom: 8mm; left: 20mm; right: 20mm; display: flex; justify-content: space-between; font-size: 7pt; color: #8a8f99 }
h1 { font-weight: 500; font-size: 25pt; line-height: 1.1; margin: 0 0 6mm; letter-spacing: -.01em; color: #0E1F3D }
.kicker { font-size: 7.5pt; letter-spacing: .14em; text-transform: uppercase; color: #1F5FD6; font-weight: 500; margin-bottom: 2.5mm }
p { font-size: 9.5pt; line-height: 1.55; margin: 0 0 3mm; color: #3a4150 }
p.lead { font-size: 12.5pt; line-height: 1.45; color: #1d2433 }
p.small, .small { font-size: 8pt; line-height: 1.5; color: #5d6472 }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: .92em; background: #f1f0ec; padding: 0 .8mm; border-radius: .6mm }
b { font-weight: 500; color: #1d2433 }
.sub { font-size: 8pt; letter-spacing: .1em; text-transform: uppercase; color: #5d6472; font-weight: 500; margin: 0 0 3mm }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10mm }
.cols3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 9mm }
.panel { border-radius: 4mm; padding: 8mm }
.center { display: flex; align-items: center; justify-content: center }
ul.bul { margin: 0 0 3mm; padding-left: 4.5mm } ul.bul li { font-size: 9.5pt; line-height: 1.5; margin: 0 0 2.2mm; color: #3a4150 }
/* cover and dividers */
.cover, .divider { background: #1F5FD6; color: #fff }
.cover .ftr, .cover .hdr { display: none }
.cv-top { position: absolute; top: 20mm; left: 22mm }
.cv-title { position: absolute; left: 22mm; top: 70mm; font-size: 60pt; line-height: .98; font-weight: 500; letter-spacing: -.02em }
.cv-sub { position: absolute; left: 22mm; bottom: 24mm; font-size: 11pt; line-height: 1.5; color: #d7e3fb }
.cv-mark { position: absolute; right: 18mm; top: 16mm }
.divider .hdr { display: none } .divider .ftr { color: #b9cdf5 }
.dv-num { position: absolute; left: 22mm; top: 22mm; font-size: 120pt; font-weight: 300; line-height: 1; color: #fff; opacity: .95 }
.dv-body { position: absolute; left: 22mm; bottom: 26mm; width: 140mm }
.dv-title { font-size: 40pt; font-weight: 500; letter-spacing: -.01em; margin-bottom: 4mm }
.dv-note { color: #d7e3fb; font-size: 11pt; margin-bottom: 5mm }
.dv-list { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 2mm 6mm } .dv-list li { font-size: 9pt; color: #fff; opacity: .85 }
.dv-mark { position: absolute; right: 20mm; top: 24mm; opacity: .16 }
.back .bc { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center }
.bc-t { font-size: 22pt; font-weight: 500; margin-top: 6mm } .bc-s { font-size: 9pt; color: #d7e3fb; max-width: 150mm }
/* contents */
.toc { border-top: .3mm solid #e2e0da } .toc-row { display: grid; grid-template-columns: 14mm 34mm 1fr; padding: 4.2mm 0; border-bottom: .3mm solid #e2e0da; align-items: baseline }
.toc-n { color: #1F5FD6; font-weight: 500; font-size: 10pt } .toc-t { font-size: 13pt; font-weight: 500 } .toc-d { font-size: 9pt; color: #5d6472 }
/* anatomy */
.anat { position: relative; width: 118mm; height: 76mm }
.anl { position: absolute; font-size: 9pt; line-height: 4.4mm; color: #1d2433; white-space: nowrap }
/* principles */
.pgrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6mm }
.pcard { background: #F4F3EF; border-radius: 4mm; padding: 7mm; min-height: 76mm } .pnum { color: #1F5FD6; font-weight: 500; font-size: 9pt } .ptitle { font-size: 15pt; font-weight: 500; margin: 2mm 0 3mm; color: #0E1F3D }
.pcard p { font-size: 10pt; line-height: 1.55 }
.isbox { margin-top: 12mm; display: grid; grid-template-columns: repeat(6, 1fr); border-top: .3mm solid #e2e0da }
.isr { display: flex; flex-direction: column; padding: 4mm 0; font-size: 14pt; gap: 2.8mm } .isr span:first-child { line-height: 18pt } .isr span:last-child { line-height: 14pt } .isr span:last-child { color: #8a8f99; font-size: 11pt; text-decoration: line-through; text-decoration-color: rgba(217,58,58,.55) } .ish span { font-size: 7.5pt !important; letter-spacing: .1em; text-transform: uppercase; color: #5d6472 !important; text-decoration: none !important }
/* tiles */
.tile { border-radius: 3.5mm; padding: 6mm; display: flex; flex-direction: column; justify-content: space-between; min-width: 0; overflow: hidden }
.tin { flex: 1; display: flex; align-items: center; justify-content: center; gap: 6mm; min-height: 20mm }
.tcap { font-size: 8pt; line-height: 1.45; margin-top: 3mm } .tcap b { color: inherit }
.g2x2 { display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 58mm 58mm; gap: 6mm }
.g3x2 { display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: 54mm 54mm; gap: 5mm }
.g4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6mm 5mm }
.ex { display: flex; flex-direction: column; gap: 2.2mm; min-width: 0 } .exa { border-radius: 3.5mm; height: 46mm; display: flex; align-items: center; justify-content: center; overflow: hidden }
.exc { font-size: 8.5pt; color: #3a4150 } .g4.tight { gap: 5mm 4mm } .g4.tight .exa { height: 29mm }
.mz { display: flex; align-items: center; justify-content: center; width: 100% }
.bad .tcap { color: #3a4150 } .x { color: #D93A3A; font-weight: 600; margin-right: 1.6mm } .ok { color: #1E9E5A; font-weight: 600; margin-right: 1.6mm }
/* min sizes */
.minrow { display: flex; flex-direction: column; gap: 5mm; margin-bottom: 5mm } .minc { display: grid; grid-template-columns: 44mm 1fr; align-items: center; gap: 4mm }
.minart { display: flex; align-items: center; justify-content: center; background: #F4F3EF; border-radius: 2.5mm; height: 16mm } .minl { font-size: 8.5pt; line-height: 1.45; color: #3a4150 }
/* palette */
.swrow { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4mm }
.sw { border-radius: 3.5mm; height: 80mm; padding: 5mm; display: flex; flex-direction: column } .swn { font-size: 15pt; font-weight: 500 } .swv { margin-top: auto; font-size: 8pt; line-height: 1.6; font-family: Consolas, monospace } .swr { font-size: 8pt; line-height: 1.4; margin-top: 3mm; opacity: .9 }
.drow { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6mm } .dsw { display: flex; gap: 4mm; align-items: center; font-size: 8.5pt; line-height: 1.5 } .dsw span { color: #5d6472 } .dchip { width: 14mm; height: 14mm; border-radius: 3mm; flex: none }
.bar { display: flex; height: 16mm; border-radius: 3mm; overflow: hidden; box-shadow: inset 0 0 0 .3mm #e2e0da } .bar span { display: flex; align-items: center; padding: 0 3mm; font-size: 7.5pt; color: #fff; white-space: nowrap; overflow: hidden }
table { border-collapse: collapse; width: 100% } th { text-align: left; font-size: 7.5pt; letter-spacing: .08em; text-transform: uppercase; color: #5d6472; font-weight: 500; padding: 0 0 2mm; border-bottom: .3mm solid #d9d7d0 }
td { font-size: 8.5pt; padding: 1.7mm 2mm 1.7mm 0; border-bottom: .3mm solid #ecebe6; color: #2b3140; vertical-align: middle }
.pv { display: inline-flex; width: 9mm; height: 6mm; align-items: center; justify-content: center; border-radius: 1.2mm; margin-right: 3mm; font-weight: 500; font-size: 8pt; box-shadow: inset 0 0 0 .25mm rgba(0,0,0,.08) }
table.wide td { padding: 2.6mm 3mm 2.6mm 0; font-size: 9pt }
/* type */
.aa { font-size: 120pt; font-weight: 500; line-height: 1; color: #0E1F3D; letter-spacing: -.02em } .glyphs { font-size: 15pt; line-height: 1.5; color: #1d2433; margin-top: 4mm }
.wts { border-top: .3mm solid #e2e0da; margin: 4mm 0 } .wt { font-size: 17pt; padding: 2mm 0; border-bottom: .3mm solid #e2e0da } .wt span { font-size: 9pt; color: #8a8f99; font-weight: 400 }
.ts { display: grid; grid-template-columns: 30mm 1fr; gap: 4mm; padding: 2.4mm 0; border-bottom: .3mm solid #ecebe6; align-items: baseline } .tsl { font-size: 7.5pt; line-height: 1.4; color: #5d6472 } .tsx { color: #0E1F3D; line-height: 1.2 }
.spl { font-size: 14pt; padding: 2mm 0; border-bottom: .3mm solid #ecebe6; display: flex; gap: 3mm; align-items: center } .spl span { font-size: 10pt; font-weight: 600; width: 5mm } .spl.y span { color: #1E9E5A } .spl.n { color: #8a8f99; text-decoration: line-through; text-decoration-color: rgba(217,58,58,.6) } .spl.n span { color: #D93A3A }
/* voice */
.vhead { display: grid; grid-template-columns: 1fr 1fr; gap: 5mm; font-size: 7.5pt; letter-spacing: .1em; text-transform: uppercase; color: #5d6472; font-weight: 500; margin-bottom: 2mm }
.vrow { display: grid; grid-template-columns: 1fr 1fr; gap: 5mm; margin-bottom: 4mm } .vdo, .vdont { border-radius: 3mm; padding: 4mm 5mm; font-size: 10pt; line-height: 1.45 } .vdo { background: #EAF4EE } .vdont { background: #FBECEC; color: #6b5555 }
/* HA mock */
.ha { background: #eef1f6; border-radius: 4mm; padding: 8mm; font-family: Roboto, system-ui, sans-serif }
.ha-head { display: flex; gap: 5mm; align-items: center; white-space: nowrap } .ha-logo { width: 32mm; flex: none; display: flex; align-items: center } .ha-title { font-size: 19pt; font-weight: 600; color: #212121 }
.ha-meta { font-size: 9pt; color: #555 } .ha-meta span, .ha-links { color: #039be5 } .ha-links { font-size: 9pt } .ha-btn { margin-left: auto; background: #039be5; color: #fff; border-radius: 6mm; padding: 2.8mm 6mm; font-size: 9pt; font-weight: 600 }
.ha-search { margin: 6mm 0 5mm; background: #fafafa; border: .3mm solid #ddd; border-radius: 2.5mm; padding: 3mm 4mm; font-size: 9pt; color: #999 }
.ha-sec { font-size: 8pt; color: #666; margin: 4mm 0 2mm }
.ha-list { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm } .ha-row { background: #fff; border-radius: 3mm; padding: 4mm; display: flex; gap: 4mm; align-items: center; font-size: 10pt } .ha-row span { color: #777; font-size: 8.5pt }
.ha-row.dark { background: #1c1c1c; color: #e1e1e1 } .ha-row.dark b { color: #e1e1e1 } .ha-row.dark span { color: #9a9a9a }
/* github */
.gh { border: .3mm solid; border-radius: 3mm; padding: 7mm } .gh p { font-family: system-ui, sans-serif; font-size: 9.5pt; line-height: 1.5; margin: 4mm 0 0 }
.social { background: #1F5FD6; border-radius: 3mm; aspect-ratio: 2 / 1; padding: 10mm 11mm; display: flex; flex-direction: column; justify-content: center; color: #fff }
.soc-t { font-size: 15pt; font-weight: 500; margin-top: 7mm; letter-spacing: -.01em; white-space: nowrap } .soc-s { font-size: 10pt; color: #d7e3fb; margin-top: 1.5mm }
.avrow { display: flex; gap: 6mm; align-items: center; margin-top: 7mm } .av { flex: none }
/* web */
.wbar { border: .3mm solid; border-radius: 2.5mm; display: flex; align-items: center; justify-content: space-between; padding: 4.5mm 6mm }
.wbar nav { font-family: system-ui, sans-serif; font-size: 10pt } .wbar nav span { margin-left: 7mm }
.icos { display: flex; align-items: flex-end; gap: 7mm; margin-bottom: 4mm } .ico { display: flex; flex-direction: column; align-items: center; gap: 2mm; font-size: 7.5pt; color: #5d6472 }
.credit { display: flex; align-items: center; gap: 4mm; font-size: 10pt; color: #3a4150; background: #F4F3EF; border-radius: 2.5mm; padding: 5mm 6mm } .credit.dark { background: #1c1c1c; color: #cfcfcf }
ol.refs { margin: 0; padding-left: 5mm } ol.refs li { font-size: 8pt; line-height: 1.5; color: #3a4150; margin-bottom: 2.4mm }
"""


def build_html() -> str:
    cover()
    contents()
    divider("01", "Brand", ["The idea", "The name", "Principles"], "What Bellweather is, and the character behind every choice that follows.")
    brand_idea()
    principles()
    divider("02", "Logo", ["Primary lockup", "Family", "With the motto", "Construction", "Clear space", "Variants", "Backgrounds", "Misuse"],
            "The badge, the wordmark, and the rules that keep them consistent.")
    logo_primary()
    logo_family()
    motto_logo()
    construction_badge()
    construction_lockup()
    construction_motto()
    construction_mark()
    clear_space()
    variants()
    backgrounds()
    misuse()
    divider("03", "Colour", ["Palette", "Screen and print", "Proportion", "Contrast"], "Cobalt for Bellweather, sun as the single accent, calm grounds for everything else.")
    palette()
    print_colour()
    proportions()
    divider("04", "Typography", ["Outfit", "Type scale", "Writing the name"], "One typeface family with round, open forms.")
    typeface()
    type_scale()
    divider("05", "Voice", ["Principles", "Write this, not this"], "Measured, honest about cost, calm.")
    voice()
    divider("06", "Applications", ["Home Assistant", "GitHub", "Web and small sizes", "Credit badges"], "The system at work where people meet Bellweather.")
    app_ha()
    app_github()
    app_web()
    credit_badges()
    divider("07", "Resources", ["Files and formats", "Why it looks like this"], "Where the files are, and the evidence behind the design.")
    files()
    rationale()
    back_cover()

    total = len(PAGES)
    out = []
    for i, (section, body, cls) in enumerate(PAGES, start=1):
        hdr = f'<div class="hdr"><span><b>Bellweather</b> · Brand guidelines</span><span>{section}</span></div>' if section else ""
        ftr = f'<div class="ftr"><span>Version {VERSION} · {DATE}</span><span>{i:02d} / {total:02d}</span></div>'
        out.append(f'<section class="page {cls}">{hdr}{body}{ftr}</section>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Bellweather brand guidelines</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600&family=Roboto:wght@400;600&display=block" rel="stylesheet">
<style>{CSS}</style></head><body>{''.join(out)}
<script>document.fonts.ready.then(()=>{{document.title=document.fonts.check('500 20px Outfit')?'READY':'NOFONT'}})</script></body></html>"""


def main() -> None:
    page = HERE / "guidelines.html"
    page.write_text(build_html(), encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        args = [eb.browser(), "--headless=new", "--disable-gpu", f"--user-data-dir={Path(tmp) / 'profile'}",
                "--virtual-time-budget=15000", "--run-all-compositor-stages-before-draw"]
        dom = subprocess.run(args + ["--dump-dom", page.as_uri()], capture_output=True, text=True, timeout=240).stdout
        if "<title>READY</title>" not in dom:
            raise SystemExit("Outfit did not load; the PDF would use a fallback font")
        subprocess.run(args + ["--no-pdf-header-footer", f"--print-to-pdf={PDF}", page.as_uri()],
                       capture_output=True, timeout=240)
    print(f"{len(PAGES)} pages -> {PDF}")


if __name__ == "__main__":
    main()
