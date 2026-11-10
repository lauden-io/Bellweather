"""Bellweather colours from sRGB to CMYK through press ICC profiles, with
LittleCMS (Pillow ImageCms): relative colorimetric with black point
compensation, rounded to whole percent. Also prints the CIEDE2000 difference
between the screen colour and the rounded ink build.

    python branding/colour_conversion.py [PROFILE.icc ...]

Without arguments it looks for ISO Coated v2 (ECI, FOGRA39) and PSO Uncoated
ISO12647 (ECI) in the usual colour folders. The values in export_kit.py
COLOURS come from this script (2026-11-10).
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageCms

SEARCH = [Path(r"C:\Windows\System32\spool\drivers\color"), Path(r"C:\Program Files (x86)\Corel"), Path(r"C:\Program Files\Adobe"),
          Path(r"C:\Program Files (x86)\Common Files\Adobe\Color\Profiles"), Path("/Library/ColorSync/Profiles"), Path("/usr/share/color/icc")]
WANTED = {"coated (ISO Coated v2, FOGRA39)": "ISOcoated_v2_eci.icc", "uncoated (PSO Uncoated ISO12647)": "PSO_Uncoated_NPscreen_ISO12647_eci.icc"}


def find(name: str) -> Path | None:
    for root in SEARCH:
        if root.exists():
            for p in root.rglob(name):
                return p
    return None


if len(sys.argv) > 1:
    PROFILES = {Path(a).stem: a for a in sys.argv[1:]}
else:
    PROFILES = {k: str(p) for k, n in WANTED.items() if (p := find(n))}
    if not PROFILES:
        raise SystemExit("no press profile found; pass the .icc paths")
COLOURS = {"Cobalt": "#1F5FD6", "Sun": "#FFB31A", "Ink": "#0E1F3D", "Paper": "#F3F2EE",
           "Cobalt light": "#5B97FF", "Sun light": "#FFC340"}
srgb = ImageCms.createProfile("sRGB")
lab = ImageCms.createProfile("LAB", 5000)
flags = ImageCms.Flags.BLACKPOINTCOMPENSATION


def de2000(l1, l2):
    L1, a1, b1 = l1; L2, a2, b2 = l2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    if C1p * C2p == 0: dh = 0
    elif dh > 180: dh -= 360
    elif dh < -180: dh += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    hs = h1p + h2p
    if C1p * C2p == 0: hbp = hs
    elif abs(h1p - h2p) <= 180: hbp = hs / 2
    else: hbp = (hs + 360) / 2 if hs < 360 else (hs - 360) / 2
    T = 1 - .17 * math.cos(math.radians(hbp - 30)) + .24 * math.cos(math.radians(2 * hbp)) + .32 * math.cos(math.radians(3 * hbp + 6)) - .2 * math.cos(math.radians(4 * hbp - 63))
    dth = 30 * math.exp(-((hbp - 275) / 25) ** 2)
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + .015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + .045 * Cbp, 1 + .015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dth)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def lab_of(img, prof, mode):
    t = ImageCms.buildTransform(prof, lab, mode, "LAB", renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
    L, a, b = ImageCms.applyTransform(img, t).getpixel((0, 0))
    return L * 100 / 255, a - 128, b - 128


for pname, path in PROFILES.items():
    cmyk = ImageCms.getOpenProfile(path)
    fwd = ImageCms.buildTransform(srgb, cmyk, "RGB", "CMYK", renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, flags=flags)
    print(pname)
    for n, h in COLOURS.items():
        rgb = tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
        src = Image.new("RGB", (1, 1), rgb)
        out = ImageCms.applyTransform(src, fwd)
        c = [round(v / 2.55) for v in out.getpixel((0, 0))]
        # round to whole percent, then measure what that ink build prints as
        q = Image.new("CMYK", (1, 1), tuple(round(v * 2.55) for v in c))
        d = de2000(lab_of(src, srgb, "RGB"), lab_of(q, cmyk, "CMYK"))
        print(f"  {n:13} {h}  C{c[0]} M{c[1]} Y{c[2]} K{c[3]}  dE00 {d:.1f}")
