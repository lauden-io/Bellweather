"""Bellweather mark: the geometry, built as one solid shape.

Everything sits on a 120 x 120 grid:

- raindrop-bell: a circle c(60, 72) r 22 with straight tangents up to the
  tip (60, 24). The circle runs down to y 83, then one short curve turns
  into the lip; the lip corners are small and crisp;
- rod: 4.5 wide, from inside the vane's shaft down into the tip;
- weathervane: a shaft 6 thick, an arrow head and two tail fins, merged
  into one polygon and moved so its centre of mass sits on the rod's axis
  (the head is heavier than the tail, so it is shifted left). It can be
  scaled around the pivot (the point where the rod meets the shaft);
- clapper: a circle r 7.5 under the lip.

Rounding is done on the merged outline, so every joint is continuous:

- the vane's outer corners are rounded with VANE_ROUND (opening:
  shrink, then grow back);
- every inner corner of the whole mark (fins into shaft, shaft into rod,
  rod into tip) gets a fillet of JOINT_ROUND (closing: grow, then shrink).

Colour treatments:

- A: one colour for the whole silhouette;
- B: sun vane, cobalt rod and bell. The seam sits where the shaft-to-rod
  fillets end, so the fillets stay with the vane;
- C: sun vane and rod, cobalt bell. The seam sits just above the point
  where the rod meets the bell's sides, so the fillets stay with the bell.

Two-colour seams overlap by 0.35 units under the top colour, so no hairline
of the background shows between the colours.

The glint is a cut-out (true negative space), so it works on any
background.

Run: python branding/build_mark.py  ->  writes the comparison pages
branding/variants.html and branding/options.html. The final files come from
branding/export_brand.py. Needs shapely.
"""

from __future__ import annotations

import math
from pathlib import Path

from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent

COBALT, SUN, INK = "#1F5FD6", "#FFB31A", "#0E1F3D"
COBALT_DARK, SUN_DARK = "#5B97FF", "#FFC340"
LIGHT_BG, DARK_BG = "#fafafa", "#1c1c1c"
VANE_ROUND = 2.0
JOINT_ROUND = 2.0
PIVOT = (60.0, 14.0)  # rod axis x, shaft centre y
SHAFT_HALF = 3.0
SEAM_OVERLAP = 0.35
STEPS = 48  # points per curve

# The accepted mark (option 2, 2026-11-10): vane at 85 %, sun vane and rod,
# cobalt bell and clapper. export_brand.py writes every final file from these.
FINAL_SCALE = 0.85
FINAL_KIND = "C"

VANE_SCALES = (1.0, 0.9, 0.85, 0.8)
PREVIEW_SCALE = 0.85


def cubic(p0, p1, p2, p3, n=STEPS):
    return [
        (
            (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t * t * p2[0] + t ** 3 * p3[0],
            (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t * t * p2[1] + t ** 3 * p3[1],
        )
        for t in (i / n for i in range(1, n + 1))
    ]


def quad(p0, p1, p2, n=STEPS // 2):
    return [
        ((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
         (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
        for t in (i / n for i in range(1, n + 1))
    ]


def body_left_half() -> list[tuple[float, float]]:
    """From the tip down the left side to the middle of the bottom."""
    cx, cy, r = 60.0, 72.0, 22.0
    tip = (60.0, 24.0)
    alpha = math.acos(r / (cy - tip[1]))
    tl = (cx - r * math.sin(alpha), cy - r * math.cos(alpha))
    y_end = 83.0
    p_end = (cx - math.sqrt(r * r - (y_end - cy) ** 2), y_end)
    a0 = math.atan2(tl[1] - cy, tl[0] - cx)  # about -152 degrees (upper left)
    a1 = math.atan2(p_end[1] - cy, p_end[0] - cx)  # about +150 degrees (lower left)
    if a0 < 0:
        a0 += 2 * math.pi  # go through 180 degrees (the leftmost point)
    pts = [tip, tl]
    pts += [(cx + r * math.cos(a0 + (a1 - a0) * i / STEPS), cy + r * math.sin(a0 + (a1 - a0) * i / STEPS)) for i in range(1, STEPS + 1)]
    # continue along the circle's tangent into the lip
    tx, ty = -(p_end[1] - cy), (p_end[0] - cx)  # tangent direction for the decreasing angle
    tx, ty = -tx, -ty
    if ty < 0:
        tx, ty = -tx, -ty  # pointing downwards
    norm = math.hypot(tx, ty)
    c1 = (p_end[0] + tx / norm * 3.0, p_end[1] + ty / norm * 3.0)
    pts += cubic(p_end, c1, (38.5, 91.8), (31.0, 92.8))
    pts += quad((31.0, 92.8), (27.6, 93.2), (27.8, 95.5))
    pts += quad((27.8, 95.5), (28.0, 97.4), (30.5, 97.4))
    pts.append((60.0, 97.4))
    return pts


def body() -> Polygon:
    left = body_left_half()
    right = [(120 - x, y) for x, y in reversed(left)]
    return Polygon(left + right[1:]).buffer(0)


def vane_raw(shift: float) -> Polygon:
    parts = [
        Polygon([(82, 4.5), (99, 14), (82, 23.5)]),
        Polygon([(46, 11), (38, 3.5), (31, 3.5), (39, 11)]),
        Polygon([(39, 17), (31, 24.5), (38, 24.5), (46, 17)]),
        Polygon([(35, 14), (39, 11), (83, 11), (83, 17), (39, 17)]),
    ]
    return affinity.translate(unary_union(parts), xoff=shift)


def vane_shift() -> float:
    return round(60.0 - vane_raw(0).centroid.x, 2)


def vane_shape(scale: float):
    """Rounded vane, centred on the rod, scaled around the pivot."""
    v = vane_raw(vane_shift())
    v = v.buffer(-VANE_ROUND, join_style="round").buffer(VANE_ROUND, join_style="round")
    return affinity.scale(v, scale, scale, origin=PIVOT) if scale != 1 else v


def silhouette(scale: float = 1.0, glint=None):
    rod = Polygon([(57.75, PIVOT[1]), (62.25, PIVOT[1]), (62.25, 32), (57.75, 32)])
    shape = unary_union([body(), rod, vane_shape(scale)])
    shape = shape.buffer(JOINT_ROUND, join_style="round").buffer(-JOINT_ROUND, join_style="round")
    if glint is not None:
        shape = shape.difference(glint)
    return shape.simplify(0.015, preserve_topology=True)


def lower_region(kind: str, scale: float, extend: float = 0.0):
    """The part of the grid that gets the bell colour (B and C)."""
    if kind == "B":
        seam = PIVOT[1] + SHAFT_HALF * scale + JOINT_ROUND  # where the shaft-to-rod fillets end
        # only the rod strip is cut at the seam; the vane's fins and head stay above 25.5
        return unary_union([box(55, seam - extend, 65, 130), box(-10, 25.5, 130, 130)])
    if kind == "C":
        return box(-10, 27.9 - extend, 130, 130)  # rod meets the bell's sides at y 28.36
    raise ValueError(kind)


def layers(kind: str, scale: float, bell: str, top: str, glint=None):
    shape = silhouette(scale, glint)
    if kind == "A":
        return [(shape, bell)]
    lower = shape.intersection(lower_region(kind, scale, SEAM_OVERLAP))
    upper = shape.difference(lower_region(kind, scale))
    return [(lower, bell), (upper, top)]


GLINT_LONG = LineString([(49.5, 55)] + cubic((49.5, 55), (47.2, 60), (46.3, 65.5), (46.6, 72), 24)).buffer(1.7, cap_style="round")


def polygons(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in polygons(g)]
    return []  # lines and points left over from an intersection


def to_path(geom) -> str:
    out = []
    for p in polygons(geom):
        for ring in [p.exterior, *p.interiors]:
            coords = list(ring.coords)
            out.append("M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in coords[:-1]) + " Z")
    return " ".join(out)


def mark_body(lays, clapper: str) -> str:
    paths = "".join(f'<path d="{to_path(g)}" fill="{c}" fill-rule="evenodd"/>' for g, c in lays)
    return f'{paths}<circle cx="60" cy="107.5" r="7.5" fill="{clapper}"/>'


def symbol(name: str, lays, clapper: str) -> str:
    return f'<symbol id="{name}" viewBox="0 0 120 120">{mark_body(lays, clapper)}</symbol>'


def svg_file(lays, clapper: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">{mark_body(lays, clapper)}</svg>\n'


def luminance(hex_colour: str) -> float:
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def icons(sym: str, sizes) -> str:
    return "".join(f'<svg width="{s}" height="{s}"><use href="#{sym}"/></svg>' for s in sizes)


def tile(inner: str, bg: str, label: str) -> str:
    colour = "#666" if bg == LIGHT_BG or bg == "#fff" else "#999"
    return (f'<div class="tile" style="background:{bg}"><div class="icons">{inner}</div>'
            f'<span style="color:{colour}">{label}</span></div>')


def badge(sym: str, size: int) -> str:
    return (f'<div style="width:{size}px;height:{size}px;border-radius:{size * 0.225:.0f}px;background:{COBALT};'
            f'display:flex;align-items:center;justify-content:center"><svg width="{size * 0.8:.0f}" height="{size * 0.8:.0f}">'
            f'<use href="#{sym}"/></svg></div>')


KINDS = {
    "A": "All cobalt (now)",
    "B": "Sun vane, cobalt rod",
    "C": "Sun vane and rod",
}


def main() -> None:
    g = GLINT_LONG
    syms = []
    for kind in KINDS:
        for s in VANE_SCALES:
            key = f"{kind}{round(s * 100)}"
            syms.append(symbol(f"{key}_l", layers(kind, s, COBALT, SUN, g), SUN))
            syms.append(symbol(f"{key}_d", layers(kind, s, COBALT_DARK, SUN_DARK, g), SUN_DARK))
            syms.append(symbol(f"{key}_w", layers(kind, s, "#fff", SUN, g), SUN))

    p = round(PREVIEW_SCALE * 100)
    sections = []
    for kind, label in KINDS.items():
        cells = "".join(tile(icons(f"{kind}{round(s * 100)}_l", (120,)), LIGHT_BG, f"Vane {round(s * 100)} %")
                        for s in VANE_SCALES)
        sections.append(f'<h2>{kind} · {label}</h2><div class="row">{cells}</div>')
    grid = "\n".join(sections)

    sizes = (120, 40, 32, 16)
    usage = "".join(
        f'<h2>{kind} · {label}, vane {p} %, in use</h2><div class="row">'
        + tile(icons(f"{kind}{p}_l", sizes), LIGHT_BG, "Light")
        + tile(icons(f"{kind}{p}_d", sizes), DARK_BG, "Dark")
        + tile(badge(f"{kind}{p}_w", 150) + badge(f"{kind}{p}_w", 48), "#fff", "App badge")
        + "</div>"
        for kind, label in KINDS.items()
    )
    close = ('<h2>Close-up, vane ' + str(p) + ' %</h2><div class="row">'
             + "".join(tile(icons(f"{k}{p}_l", (380,)), "#fff", KINDS[k]) for k in ("B", "C")) + "</div>")

    notes = (f'<p class="note">Contrast with the background: sun on light {contrast(SUN, LIGHT_BG):.1f}:1, '
             f'sun on dark {contrast(SUN_DARK, DARK_BG):.1f}:1, cobalt on light {contrast(COBALT, LIGHT_BG):.1f}:1, '
             f'light cobalt on dark {contrast(COBALT_DARK, DARK_BG):.1f}:1.</p>')

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Bellweather mark</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500&display=swap" rel="stylesheet">
<style>body{{margin:0;padding-bottom:24px;font-family:Outfit,system-ui,sans-serif;background:#F4F3EF}}h2{{font-weight:500;font-size:15px;margin:22px 22px 8px;color:#555}}
.row{{display:flex;flex-wrap:wrap;gap:12px;padding:0 22px}}.tile{{border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:6px}}
.icons{{display:flex;gap:16px;align-items:center}}.tile span{{font-size:13px}}.note{{margin:18px 22px 0;color:#555;font-size:14px}}
h1{{font-weight:500;font-size:19px;margin:22px 22px 0;color:#333}}</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs>{''.join(syms)}</defs></svg>
<h1>Vane size and colour</h1>
{grid}
<h1>In use</h1>
{usage}
{close}
{notes}
</body></html>"""
    (HERE / "variants.html").write_text(html, encoding="utf-8")
    options_page()
    print("vane shift", vane_shift())
    print(f"contrast sun/light {contrast(SUN, LIGHT_BG):.2f} sun_dark/dark {contrast(SUN_DARK, DARK_BG):.2f} "
          f"cobalt/light {contrast(COBALT, LIGHT_BG):.2f} cobalt_dark/dark {contrast(COBALT_DARK, DARK_BG):.2f}")


# Where the sun colour goes. Columns: key, label, kind, clapper on light, clapper on dark,
# vane on the badge, clapper on the badge (the badge is cobalt with a white bell).
OPTIONS = [
    ("O1", "1 · Sun at both ends (now)", "C", SUN, SUN_DARK, SUN, SUN),
    ("O2", "2 · Sun on top only", "C", COBALT, COBALT_DARK, SUN, "#fff"),
    ("O3", "3 · Light to dark, top to bottom", "C", INK, COBALT, SUN, INK),
    ("O4", "4 · Sun at the bottom only", "A", SUN, SUN_DARK, "#fff", SUN),
]
CLAPPER_AREA = math.pi * 7.5 ** 2


def sun_share(kind: str, clapper: str, scale: float) -> float:
    """Share of the mark's area in the sun colour (light version, glint included)."""
    lays = layers(kind, scale, COBALT, SUN, GLINT_LONG)
    total = silhouette(scale, GLINT_LONG).area + CLAPPER_AREA
    sun = sum(g.area for g, c in lays if c == SUN) + (CLAPPER_AREA if clapper == SUN else 0)
    return sun / total


def options_page() -> None:
    s = PREVIEW_SCALE
    g = GLINT_LONG
    syms, rows = [], []
    for key, label, kind, cl_l, cl_d, b_top, b_cl in OPTIONS:
        syms.append(symbol(f"{key}_l", layers(kind, s, COBALT, SUN, g), cl_l))
        syms.append(symbol(f"{key}_d", layers(kind, s, COBALT_DARK, SUN_DARK, g), cl_d))
        syms.append(symbol(f"{key}_w", layers(kind, s, "#fff", b_top, g), b_cl))
        share = sun_share(kind, cl_l, s)
        rows.append(
            f'<h2>{label} <small>sun colour: {share * 100:.0f} % of the area</small></h2><div class="row">'
            + tile(icons(f"{key}_l", (120, 40, 32, 16)), LIGHT_BG, "Light")
            + tile(icons(f"{key}_d", (120, 40, 32, 16)), DARK_BG, "Dark")
            + tile(badge(f"{key}_w", 132) + badge(f"{key}_w", 48), "#fff", "App badge")
            + tile(f'<div style="filter:grayscale(1)">{icons(f"{key}_l", (120,))}</div>', LIGHT_BG, "Greyscale")
            + "</div>")
        print(f"{key} sun share {share:.3f}")
    side = "".join(tile(icons(f"{k}_l", (250,)), "#fff", lbl.split(" · ")[0]) for k, lbl, *_ in OPTIONS)
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Bellweather colour placement</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500&display=swap" rel="stylesheet">
<style>body{{margin:0;padding-bottom:24px;font-family:Outfit,system-ui,sans-serif;background:#F4F3EF}}h2{{font-weight:500;font-size:15px;margin:22px 22px 8px;color:#444}}
h2 small{{font-size:13px;color:#888;margin-left:8px}}.row{{display:flex;flex-wrap:wrap;gap:12px;padding:0 22px}}
.tile{{border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:6px}}.icons{{display:flex;gap:16px;align-items:center}}
.tile span{{font-size:13px}}h1{{font-weight:500;font-size:19px;margin:22px 22px 0;color:#333}}</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs>{''.join(syms)}</defs></svg>
<h1>Where the sun colour goes (C, vane {round(s * 100)} %)</h1>
<h2>Side by side</h2><div class="row">{side}</div>
{''.join(rows)}
</body></html>"""
    (HERE / "options.html").write_text(html, encoding="utf-8")
    print(f"contrast ink/light {contrast(INK, LIGHT_BG):.2f} cobalt/dark {contrast(COBALT, DARK_BG):.2f} "
          f"ink/cobalt {contrast(INK, COBALT):.2f} luminance sun {luminance(SUN):.3f} cobalt {luminance(COBALT):.3f} "
          f"ink {luminance(INK):.3f} sun_dark {luminance(SUN_DARK):.3f} cobalt_dark {luminance(COBALT_DARK):.3f}")


if __name__ == "__main__":
    main()
