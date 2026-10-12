"""Server-rendered HTML/CSS/JS for the /admin dashboard.

Follows DESIGN.md exactly: washi/sumi/sakura palette, glassmorphism cards with a
user-adjustable --glass-alpha slider (persisted in localStorage), Shippori Mincho
+ Zen Maru Gothic Google Fonts, and an inline-SVG old-Japanese-map background with
isochrone contour rings, dashed road, hanko seal markers, seigaiha waves, and
drifting sakura petals.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Shared <head>: fonts + palette + glass + map background CSS
# ---------------------------------------------------------------------------
HEAD = """
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;700&family=Zen+Maru+Gothic:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{
  --washi:#F3ECDA; --washi-deep:#E9DFC6; --sumi:#2B2A33; --sumi-soft:#55525E;
  --sakura:#E8A7B8; --sakura-deep:#D96C8A; --shu:#C73E3A; --matcha:#7BA05B;
  --ai:#2D4A63; --kin:#C9A227; --glass-alpha:0.55; --glass-blur:14px;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;min-height:100%;}
body{
  font-family:"Zen Maru Gothic",system-ui,sans-serif;
  color:var(--sumi); background:var(--washi);
  -webkit-font-smoothing:antialiased;
}
h1,h2,h3,.mincho,.stat{font-family:"Shippori Mincho",serif;}
.stat{font-feature-settings:"tnum";}
a{color:var(--ai);}
/* map background sits fixed behind everything */
.map-bg{position:fixed;inset:0;width:100vw;height:100vh;z-index:-1;overflow:hidden;}
.map-bg svg{width:100%;height:100%;display:block;}
/* per-tab scene layers: all stacked, only the active one visible (crossfade) */
.scene{position:fixed;inset:0;width:100vw;height:100vh;z-index:-1;overflow:hidden;
  opacity:0;transition:opacity .5s ease;pointer-events:none;}
.scene svg{width:100%;height:100%;display:block;}
.scene.active{opacity:1;}
@media (prefers-reduced-motion:reduce){.scene{transition:none;}}
.vignette{position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:radial-gradient(ellipse at 50% 40%,transparent 40%,rgba(43,42,51,0.10) 100%);}
.glass{
  background:rgba(243,236,218,var(--glass-alpha));
  backdrop-filter:blur(var(--glass-blur)) saturate(1.25);
  -webkit-backdrop-filter:blur(var(--glass-blur)) saturate(1.25);
  border:1px solid rgba(255,255,255,0.38);
  border-radius:18px;
  box-shadow:0 8px 32px rgba(43,42,51,0.14);
}
.glass-dark{
  background:rgba(43,42,51,calc(var(--glass-alpha)*0.85));
  color:var(--washi);
  backdrop-filter:blur(var(--glass-blur)) saturate(1.25);
  border:1px solid rgba(255,255,255,0.15);
  border-radius:18px;
}
.wrap{max-width:1080px;margin:0 auto;padding:26px 18px 80px;}
.topbar{display:flex;align-items:center;justify-content:space-between;
  padding:14px 20px;margin-bottom:22px;}
.brand{display:flex;align-items:baseline;gap:12px;}
.brand .kanji{font-size:30px;color:var(--shu);}
.brand .name{font-size:22px;letter-spacing:1px;}
.brand .romaji{font-size:12px;color:var(--sumi-soft);letter-spacing:2px;text-transform:uppercase;}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;}
.card{padding:20px 22px;}
.card h2{margin:0 0 14px;font-size:18px;display:flex;align-items:center;gap:9px;}
.card h2 .seal{width:26px;height:26px;border-radius:50%;border:2px solid var(--shu);
  display:inline-flex;align-items:center;justify-content:center;font-size:13px;
  color:var(--shu);background:var(--washi);}
.pill{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border-radius:999px;
  font-size:13px;font-weight:500;}
.pill .dot{width:9px;height:9px;border-radius:50%;}
.pill.ok{background:rgba(123,160,91,0.22);color:#3f5b28;}
.pill.ok .dot{background:var(--matcha);}
.pill.mock{background:rgba(201,162,39,0.22);color:#7a5f10;}
.pill.mock .dot{background:var(--kin);}
.muted{color:var(--sumi-soft);font-size:13px;}
table{width:100%;border-collapse:collapse;font-size:13.5px;}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid rgba(43,42,51,0.10);}
th{font-weight:500;color:var(--sumi-soft);font-size:12px;text-transform:uppercase;letter-spacing:.5px;}
.btn{font-family:inherit;cursor:pointer;border:none;border-radius:12px;padding:9px 16px;
  font-size:14px;font-weight:600;transition:transform .12s ease,filter .12s ease;}
.btn:active{transform:scale(.96);}
.btn-shu{background:var(--shu);color:var(--washi);}
.btn-ghost{background:rgba(43,42,51,0.06);color:var(--sumi);}
.btn-sm{padding:5px 12px;font-size:12.5px;}
.qrbox{display:flex;flex-direction:column;align-items:center;gap:12px;}
.qrbox img{width:220px;height:220px;background:#fff;border-radius:14px;padding:10px;
  box-shadow:0 4px 18px rgba(43,42,51,.16);}
.countdown{font-size:13px;color:var(--sumi-soft);}
.slider-row{display:flex;align-items:center;gap:14px;}
input[type=range]{flex:1;accent-color:var(--sakura-deep);}
.logrow td{font-size:12.5px;}
.status-2xx{color:var(--matcha);} .status-4xx{color:var(--kin);} .status-5xx{color:var(--shu);}
.footer{margin-top:26px;text-align:center;}
.ipline{font-family:"Shippori Mincho",serif;font-size:13px;color:var(--sumi-soft);}
code{background:rgba(43,42,51,.07);padding:2px 6px;border-radius:6px;font-size:12.5px;}
@keyframes drift{
  0%{transform:translate(0,0) rotate(0);opacity:0;}
  10%{opacity:.9;}
  90%{opacity:.9;}
  100%{transform:translate(-120px,90vh) rotate(220deg);opacity:0;}
}
.petal{animation:drift linear infinite;transform-origin:center;}
@media (prefers-reduced-motion:reduce){.petal{animation:none;opacity:.5;}}
.login-center{min-height:100vh;display:flex;align-items:center;justify-content:center;}
.login-card{padding:34px 32px;width:340px;text-align:center;}
.login-card input[type=password]{width:100%;padding:11px 14px;border-radius:12px;
  border:1px solid rgba(43,42,51,.22);background:rgba(255,255,255,.5);font-family:inherit;
  font-size:15px;margin:16px 0;}
.err{color:var(--shu);font-size:13px;min-height:18px;}
/* ---- glass-tab navigation (v2) ---- */
.tabs{display:flex;flex-wrap:wrap;gap:8px;padding:10px 12px;margin-bottom:20px;}
.tab{font-family:inherit;cursor:pointer;border:1px solid rgba(255,255,255,0.3);
  background:rgba(243,236,218,0.35);border-radius:14px;padding:9px 16px;font-size:14px;
  font-weight:600;color:var(--sumi-soft);display:flex;align-items:center;gap:7px;
  transition:transform .12s ease,background .12s ease,color .12s ease;}
.tab:active{transform:scale(.96);}
.tab .k{font-family:"Shippori Mincho",serif;color:var(--shu);font-size:15px;}
.tab.active{background:rgba(231,167,184,0.32);color:var(--sumi);border-color:rgba(217,108,138,0.5);}
.tab .badge{background:rgba(43,42,51,0.10);border-radius:999px;padding:1px 8px;font-size:11.5px;}
.page{display:none;}
.page.active{display:block;}
label.fld{display:block;font-size:12.5px;color:var(--sumi-soft);margin:10px 0 4px;
  text-transform:uppercase;letter-spacing:.4px;}
input.inp,select.inp{width:100%;padding:9px 12px;border-radius:10px;
  border:1px solid rgba(43,42,51,.22);background:rgba(255,255,255,.55);font-family:inherit;
  font-size:14px;color:var(--sumi);}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.chk{display:inline-flex;align-items:center;gap:7px;font-size:13.5px;margin-top:12px;}
.tag{display:inline-block;background:rgba(45,74,99,0.14);color:var(--ai);border-radius:8px;
  padding:2px 9px;font-size:11.5px;font-weight:600;margin-right:6px;}
.tag.vis{background:rgba(123,160,91,0.2);color:#3f5b28;}
.tag.active-t{background:rgba(199,62,58,0.16);color:var(--shu);}
.tag.off{background:rgba(43,42,51,0.09);color:var(--sumi-soft);}
.backend-item{border:1px solid rgba(43,42,51,0.10);border-radius:14px;padding:14px 16px;margin-bottom:12px;
  background:rgba(255,255,255,0.28);}
.backend-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;}
.test-out{font-size:12px;margin-top:8px;white-space:pre-wrap;word-break:break-word;}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;}
.filters input,.filters select{padding:6px 10px;border-radius:9px;border:1px solid rgba(43,42,51,.2);
  background:rgba(255,255,255,.5);font-family:inherit;font-size:13px;}
.big-num{font-family:"Shippori Mincho",serif;font-size:34px;color:var(--shu);}
.drill{margin-top:14px;padding-top:14px;border-top:1px solid rgba(43,42,51,0.12);}
.macrochip{display:inline-block;border-radius:8px;padding:2px 9px;font-size:12px;margin-right:6px;}
.mc-p{background:rgba(123,160,91,0.2);color:#3f5b28;}
.mc-c{background:rgba(45,74,99,0.16);color:var(--ai);}
.mc-f{background:rgba(201,162,39,0.2);color:#7a5f10;}
.linkish{cursor:pointer;color:var(--ai);text-decoration:underline;}
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);z-index:50;
  padding:11px 20px;border-radius:12px;font-size:14px;font-weight:600;
  box-shadow:0 8px 28px rgba(43,42,51,.22);opacity:0;transition:opacity .2s ease;pointer-events:none;}
.toast.show{opacity:1;}
.toast.ok{background:var(--matcha);color:#fff;} .toast.bad{background:var(--shu);color:#fff;}
</style>
"""

# ---------------------------------------------------------------------------
# Dense ukiyo-e scene background (DESIGN.md §4 Density block).
#
# A deterministic, seed-driven inline-SVG scene: overlapping shaded mountain
# ranges (occasional snow caps), pine-forest clusters (rare kin autumn trees),
# village/pagoda clusters, torii gates, ink boats with washi sails, kasumi mist
# bands over seigaiha water, and a red-bordered vertical-text legend cartouche.
# Each admin tab passes a different seed + layout knobs so Status/Users/Devices/
# Backends/Requests/Push each read as a different province of the same world.
# ---------------------------------------------------------------------------
import math as _math
import random as _random


def _mountain_range(rng, base_y: float, count: int, spread: tuple[float, float],
                    height_range: tuple[float, float], fill: str, opacity: float,
                    snow: bool) -> str:
    """A row of overlapping shaded peaks. Returns SVG string."""
    x0, x1 = spread
    parts = []
    n = count
    for i in range(n):
        cx = x0 + (x1 - x0) * (i + rng.uniform(-0.3, 0.3)) / max(1, n - 1)
        h = rng.uniform(*height_range)
        w = h * rng.uniform(1.1, 1.7)
        peak_x = cx + rng.uniform(-w * 0.12, w * 0.12)
        left = cx - w
        right = cx + w
        # shaded triangle body
        parts.append(
            f'<path d="M {left:.0f} {base_y:.0f} L {peak_x:.0f} {base_y-h:.0f} '
            f'L {right:.0f} {base_y:.0f} Z" fill="{fill}" fill-opacity="{opacity:.2f}" '
            f'stroke="var(--sumi)" stroke-opacity="{opacity*0.7:.2f}" stroke-width="1"/>'
        )
        # inner ridge shading line
        parts.append(
            f'<path d="M {peak_x:.0f} {base_y-h:.0f} L {peak_x-w*0.28:.0f} {base_y:.0f}" '
            f'stroke="var(--sumi)" stroke-opacity="{opacity*0.5:.2f}" stroke-width="1" fill="none"/>'
        )
        # occasional snow cap
        if snow and rng.random() < 0.4:
            snow_h = h * rng.uniform(0.22, 0.34)
            sy = base_y - h + snow_h
            lx = peak_x - snow_h * 0.9
            rx = peak_x + snow_h * 0.9
            parts.append(
                f'<path d="M {lx:.0f} {sy:.0f} Q {peak_x-snow_h*0.3:.0f} {sy+3:.0f} '
                f'{peak_x:.0f} {sy-1:.0f} Q {peak_x+snow_h*0.3:.0f} {sy+3:.0f} {rx:.0f} {sy:.0f} '
                f'L {peak_x:.0f} {base_y-h:.0f} Z" fill="#FBF7EE" fill-opacity="0.9"/>'
            )
    return "".join(parts)


def _pine(rng, x: float, y: float, s: float, autumn: bool) -> str:
    """A small stylized pine/conifer. kin (gold) tint when autumn."""
    fill = "var(--kin)" if autumn else "var(--matcha)"
    op = 0.85 if autumn else 0.7
    trunk = f'<rect x="{x-0.8*s:.1f}" y="{y-1*s:.1f}" width="{1.6*s:.1f}" height="{3*s:.1f}" fill="var(--sumi)" fill-opacity="0.5"/>'
    tiers = []
    for k, ty in enumerate((-9, -6, -3)):
        tw = (2.2 + k * 1.3) * s
        tiers.append(
            f'<path d="M {x-tw:.1f} {y+ty*s:.1f} L {x:.1f} {y+(ty-4)*s:.1f} '
            f'L {x+tw:.1f} {y+ty*s:.1f} Z" fill="{fill}" fill-opacity="{op:.2f}"/>'
        )
    return trunk + "".join(tiers)


def _forest(rng, cx: float, cy: float, n: int, autumn_chance: float) -> str:
    parts = []
    for _ in range(n):
        px = cx + rng.uniform(-38, 38)
        py = cy + rng.uniform(-16, 16)
        s = rng.uniform(0.8, 1.5)
        parts.append(_pine(rng, px, py, s, rng.random() < autumn_chance))
    return "".join(parts)


def _house(x: float, y: float, s: float) -> str:
    """A tiny hip-roofed house in sumi line + washi fill."""
    return (
        f'<g>'
        f'<rect x="{x-4*s:.1f}" y="{y-3*s:.1f}" width="{8*s:.1f}" height="{5*s:.1f}" '
        f'fill="var(--washi)" stroke="var(--sumi)" stroke-opacity="0.55" stroke-width="0.8"/>'
        f'<path d="M {x-5.5*s:.1f} {y-3*s:.1f} L {x:.1f} {y-7*s:.1f} L {x+5.5*s:.1f} {y-3*s:.1f} Z" '
        f'fill="var(--washi-deep)" stroke="var(--sumi)" stroke-opacity="0.6" stroke-width="0.8"/>'
        f'</g>'
    )


def _pagoda(x: float, y: float, s: float) -> str:
    """A 3-tier pagoda in sumi line."""
    parts = [f'<rect x="{x-1.4*s:.1f}" y="{y-3*s:.1f}" width="{2.8*s:.1f}" height="{3*s:.1f}" '
             f'fill="var(--washi)" stroke="var(--sumi)" stroke-opacity="0.5" stroke-width="0.7"/>']
    for k, ty in enumerate((-3, -8, -13)):
        rw = (7 - k * 1.6) * s
        parts.append(
            f'<path d="M {x-rw:.1f} {y+ty*s:.1f} Q {x:.1f} {y+(ty-3)*s:.1f} {x+rw:.1f} {y+ty*s:.1f} '
            f'L {x+rw*0.7:.1f} {y+(ty-1)*s:.1f} L {x-rw*0.7:.1f} {y+(ty-1)*s:.1f} Z" '
            f'fill="var(--shu)" fill-opacity="0.42" stroke="var(--sumi)" stroke-opacity="0.4" stroke-width="0.6"/>'
        )
    parts.append(f'<line x1="{x:.1f}" y1="{y-16*s:.1f}" x2="{x:.1f}" y2="{y-13*s:.1f}" '
                 f'stroke="var(--sumi)" stroke-opacity="0.5" stroke-width="0.8"/>')
    return "".join(parts)


def _village(rng, cx: float, cy: float, with_pagoda: bool) -> str:
    parts = []
    if with_pagoda:
        parts.append(_pagoda(cx, cy, rng.uniform(1.0, 1.4)))
    for _ in range(rng.randint(3, 6)):
        parts.append(_house(cx + rng.uniform(-30, 30), cy + rng.uniform(-4, 14), rng.uniform(0.9, 1.4)))
    return "".join(parts)


def _torii(x: float, y: float, s: float) -> str:
    return (
        f'<g stroke="var(--shu)" stroke-opacity="0.7" stroke-width="{1.6*s:.1f}" fill="none">'
        f'<line x1="{x-6*s:.1f}" y1="{y-9*s:.1f}" x2="{x+6*s:.1f}" y2="{y-9*s:.1f}"/>'
        f'<line x1="{x-5*s:.1f}" y1="{y-6*s:.1f}" x2="{x+5*s:.1f}" y2="{y-6*s:.1f}"/>'
        f'<line x1="{x-4*s:.1f}" y1="{y-9*s:.1f}" x2="{x-4*s:.1f}" y2="{y:.1f}"/>'
        f'<line x1="{x+4*s:.1f}" y1="{y-9*s:.1f}" x2="{x+4*s:.1f}" y2="{y:.1f}"/>'
        f'</g>'
    )


def _boat(x: float, y: float, s: float) -> str:
    return (
        f'<g>'
        f'<path d="M {x-9*s:.1f} {y:.1f} Q {x:.1f} {y+5*s:.1f} {x+9*s:.1f} {y:.1f} '
        f'L {x+6*s:.1f} {y+2.5*s:.1f} L {x-6*s:.1f} {y+2.5*s:.1f} Z" '
        f'fill="var(--sumi)" fill-opacity="0.55"/>'
        f'<path d="M {x:.1f} {y:.1f} L {x:.1f} {y-11*s:.1f} L {x+6*s:.1f} {y-2*s:.1f} Z" '
        f'fill="var(--washi)" stroke="var(--sumi)" stroke-opacity="0.4" stroke-width="0.6"/>'
        f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{y-12*s:.1f}" '
        f'stroke="var(--sumi)" stroke-opacity="0.5" stroke-width="0.8"/>'
        f'</g>'
    )


def _mist_band(y: float, w: float, opacity: float) -> str:
    """A kasumi mist band — stacked soft horizontal strokes."""
    parts = []
    for k in range(3):
        yy = y + k * 7
        x0 = 40 + (k % 2) * 60
        parts.append(
            f'<path d="M {x0} {yy} q 60 -6 140 0 t 160 0 t 160 0" fill="none" '
            f'stroke="#FBF7EE" stroke-opacity="{opacity:.2f}" stroke-width="6" stroke-linecap="round"/>'
        )
    return "".join(parts)


def _seigaiha(rng, y0: float, rows: int) -> str:
    parts = []
    for row in range(rows):
        y = y0 + row * 24
        for x in range(-40, 1240, 44):
            parts.append(
                f'<path d="M {x} {y} a 22 22 0 0 1 44 0" fill="none" '
                f'stroke="var(--ai)" stroke-opacity="0.14" stroke-width="1.3"/>'
            )
    return "".join(parts)


def _cartouche(x: float, y: float) -> str:
    """Red-bordered vertical-text legend cartouche (腹八分 · 旅路の図)."""
    chars = "腹八分"
    col1 = "".join(
        f'<text x="{x+14}" y="{y+22+i*20}" text-anchor="middle" font-size="16" '
        f'font-family="Shippori Mincho, serif" fill="var(--shu)">{c}</text>'
        for i, c in enumerate(chars)
    )
    chars2 = "旅路"
    col2 = "".join(
        f'<text x="{x+34}" y="{y+22+i*20}" text-anchor="middle" font-size="15" '
        f'font-family="Shippori Mincho, serif" fill="var(--sumi)" fill-opacity="0.75">{c}</text>'
        for i, c in enumerate(chars2)
    )
    return (
        f'<g opacity="0.9">'
        f'<rect x="{x}" y="{y}" width="48" height="86" rx="4" fill="var(--washi)" '
        f'fill-opacity="0.55" stroke="var(--shu)" stroke-width="2"/>'
        f'<rect x="{x+3}" y="{y+3}" width="42" height="80" rx="3" fill="none" '
        f'stroke="var(--shu)" stroke-opacity="0.4" stroke-width="0.8"/>'
        f'{col1}{col2}</g>'
    )


# Per-tab scene knobs: seed + composition variation so each admin tab reads as a
# different province of the same world.
SCENE_TABS = {
    "status":   {"seed": 1101, "sea": "right",  "ranges": 4, "villages": 3, "boats": 2, "cartouche": (60, 560)},
    "users":    {"seed": 2202, "sea": "bottom", "ranges": 5, "villages": 4, "boats": 3, "cartouche": (1090, 70)},
    "devices":  {"seed": 3303, "sea": "left",   "ranges": 3, "villages": 3, "boats": 2, "cartouche": (1090, 560)},
    "backends": {"seed": 4404, "sea": "right",  "ranges": 5, "villages": 2, "boats": 1, "cartouche": (60, 70)},
    "requests": {"seed": 5505, "sea": "bottom", "ranges": 4, "villages": 4, "boats": 3, "cartouche": (1090, 560)},
    "push":     {"seed": 6606, "sea": "left",   "ranges": 6, "villages": 3, "boats": 2, "cartouche": (60, 560)},
}


def scene_svg(tab: str) -> str:
    """Build a dense, deterministic ukiyo-e province scene for one admin tab."""
    cfg = SCENE_TABS.get(tab, SCENE_TABS["status"])
    rng = _random.Random(cfg["seed"])
    W, H = 1200, 780

    # Sea occupies one edge; land fills the rest. seigaiha + mist + boats go on sea.
    sea = cfg["sea"]
    layers = [f'<rect width="{W}" height="{H}" fill="url(#sky)"/>']

    # ---- Terrain tint provinces (faction-territory feel) ----
    for tint, op in (("var(--sakura)", 0.10), ("var(--matcha)", 0.09)):
        bx, by = rng.uniform(120, 800), rng.uniform(120, 480)
        bw, bh = rng.uniform(180, 320), rng.uniform(130, 220)
        layers.append(
            f'<ellipse cx="{bx:.0f}" cy="{by:.0f}" rx="{bw:.0f}" ry="{bh:.0f}" '
            f'fill="{tint}" fill-opacity="{op:.2f}"/>'
        )
        # dashed province border
        layers.append(
            f'<ellipse cx="{bx:.0f}" cy="{by:.0f}" rx="{bw:.0f}" ry="{bh:.0f}" fill="none" '
            f'stroke="var(--sumi)" stroke-opacity="0.3" stroke-width="1" stroke-dasharray="3 4"/>'
        )

    # ---- Overlapping mountain ranges (back to front, snow on the back rows) ----
    n_ranges = cfg["ranges"]
    for r in range(n_ranges):
        base_y = 300 + r * 42 + rng.uniform(-10, 10)
        back = r < n_ranges - 2
        layers.append(_mountain_range(
            rng, base_y=base_y, count=rng.randint(5, 9),
            spread=(rng.uniform(-40, 120), rng.uniform(900, 1240)),
            height_range=(60 + r * 12, 130 + r * 16),
            fill="var(--washi-deep)", opacity=0.30 + r * 0.06, snow=back,
        ))

    # ---- Pine forests (8+ clusters, rare autumn/kin) ----
    forest_spots = [(rng.uniform(80, 1120), rng.uniform(360, 660)) for _ in range(rng.randint(8, 11))]
    for (fx, fy) in forest_spots:
        layers.append(_forest(rng, fx, fy, rng.randint(4, 7), autumn_chance=0.18))

    # ---- Winding river ----
    rx0 = rng.uniform(300, 800)
    layers.append(
        f'<path d="M {rx0:.0f} 260 Q {rx0-80:.0f} 420 {rx0+40:.0f} 540 '
        f'T {rx0-40:.0f} 760" fill="none" stroke="var(--ai)" stroke-opacity="0.28" stroke-width="2.5"/>'
    )

    # ---- Roads: branching dashed network with station seals ----
    stations = []
    sx = 120
    for i in range(6):
        stations.append((sx, rng.uniform(380, 620)))
        sx += rng.uniform(160, 200)
    road_pts = " L ".join(f"{x:.0f},{y:.0f}" for x, y in stations)
    layers.append(
        f'<path d="M {road_pts}" fill="none" stroke="var(--sumi)" stroke-width="2.2" '
        f'stroke-dasharray="8 4" stroke-opacity="0.5" stroke-linecap="round"/>'
    )
    # a couple of dead-end spurs with torii at the ends
    for _ in range(2):
        sxi = rng.choice(stations)
        ex, ey = sxi[0] + rng.uniform(-70, 70), sxi[1] + rng.uniform(-90, -40)
        layers.append(
            f'<path d="M {sxi[0]:.0f} {sxi[1]:.0f} L {ex:.0f} {ey:.0f}" fill="none" '
            f'stroke="var(--sumi)" stroke-width="1.6" stroke-dasharray="6 4" stroke-opacity="0.4"/>'
        )
        layers.append(_torii(ex, ey, rng.uniform(0.9, 1.3)))

    # ---- Villages / pagoda clusters beside stations ----
    for i in range(min(cfg["villages"], len(stations))):
        vx, vy = stations[i]
        layers.append(_village(rng, vx + rng.uniform(-10, 30), vy + rng.uniform(18, 40),
                               with_pagoda=(i % 2 == 0)))

    # ---- Station hanko seals ----
    seal_glyphs = "本茶暦道市手"
    for i, (x, y) in enumerate(stations):
        active = (i == 0)
        center = "var(--shu)" if active else "var(--washi)"
        glyph = "var(--washi)" if active else "var(--shu)"
        layers.append(
            f'<g><circle cx="{x:.0f}" cy="{y:.0f}" r="16" fill="{center}" '
            f'stroke="var(--shu)" stroke-width="2.2"/>'
            f'<text x="{x:.0f}" y="{y+5:.0f}" text-anchor="middle" font-size="15" '
            f'font-family="Shippori Mincho, serif" fill="{glyph}">{seal_glyphs[i]}</text></g>'
        )

    # ---- Sea band with seigaiha, mist, boats ----
    if sea == "bottom":
        layers.append(_seigaiha(rng, 690, 4))
        layers.append(_mist_band(660, W, 0.5))
        for _ in range(cfg["boats"]):
            layers.append(_boat(rng.uniform(120, 1080), rng.uniform(700, 760), rng.uniform(0.9, 1.4)))
    elif sea in ("left", "right"):
        base_x = 40 if sea == "left" else 1000
        # vertical seigaiha column
        for row in range(10):
            yy = 200 + row * 40
            for k in range(4):
                xx = base_x + k * 40
                layers.append(
                    f'<path d="M {xx} {yy} a 20 20 0 0 1 40 0" fill="none" '
                    f'stroke="var(--ai)" stroke-opacity="0.13" stroke-width="1.2"/>'
                )
        layers.append(_mist_band(240 if sea == "left" else 240, 400, 0.45))
        for _ in range(cfg["boats"]):
            bx = rng.uniform(50, 150) if sea == "left" else rng.uniform(1030, 1140)
            layers.append(_boat(bx, rng.uniform(300, 620), rng.uniform(0.9, 1.3)))

    # ---- Legend cartouche ----
    cx, cy = cfg["cartouche"]
    layers.append(_cartouche(cx, cy))

    # ---- Compass rose ----
    ox, oy = (1120, 120) if sea != "right" else (90, 700)
    layers.append(
        f'<g transform="translate({ox} {oy})" opacity="0.7">'
        f'<circle r="30" fill="none" stroke="var(--sumi)" stroke-width="1" stroke-opacity="0.4"/>'
        f'<path d="M0 -26 L5 0 L0 26 L-5 0 Z" fill="var(--sumi)" fill-opacity="0.35"/>'
        f'<path d="M-26 0 L0 5 L26 0 L0 -5 Z" fill="var(--sumi)" fill-opacity="0.20"/>'
        f'<circle r="3.5" fill="var(--sakura-deep)"/>'
        f'<text x="0" y="-33" text-anchor="middle" font-size="9" fill="var(--sumi)" opacity="0.6">N</text></g>'
    )

    # ---- Paper aging noise ----
    layers.append(f'<rect width="{W}" height="{H}" filter="url(#paperNoise)" opacity="0.04"/>')

    body = "\n".join(layers)
    return (
        f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice" '
        f'xmlns="http://www.w3.org/2000/svg">\n'
        f'<defs>\n'
        f'<filter id="paperNoise"><feTurbulence type="fractalNoise" baseFrequency="0.9" '
        f'numOctaves="2" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter>\n'
        f'<radialGradient id="sky" cx="45%" cy="35%" r="80%">'
        f'<stop offset="0%" stop-color="#F7F1E1"/><stop offset="100%" stop-color="#E9DFC6"/>'
        f'</radialGradient>\n'
        f'</defs>\n{body}\n</svg>'
    )


# ---------------------------------------------------------------------------
# The old-Japanese-map SVG background (isochrone rings, road, seals, waves)
# ---------------------------------------------------------------------------
def _map_svg() -> str:
    # six stations along a meandering road
    stations = [
        (150, 620, "本"), (330, 470, "茶"), (520, 540, "暦"),
        (700, 380, "道"), (900, 460, "市"), (1080, 300, "手"),
    ]
    rings = []
    for (sx, sy, _k) in stations:
        for i, r in enumerate((44, 80, 120, 168)):
            op = 0.22 - i * 0.035
            wobble = 6 + i * 4
            rings.append(
                f'<ellipse cx="{sx}" cy="{sy}" rx="{r+wobble}" ry="{r}" '
                f'fill="var(--washi-deep)" fill-opacity="0.20" '
                f'stroke="var(--sumi)" stroke-opacity="{op:.2f}" stroke-width="1"/>'
            )
    rings_svg = "\n".join(rings)

    # road path through station centers
    pts = [f"{sx},{sy}" for (sx, sy, _k) in stations]
    road = "M " + " L ".join(pts)

    seals = []
    for i, (sx, sy, k) in enumerate(stations):
        active = i == 0
        ring = "var(--shu)"
        center = "var(--shu)" if active else "var(--washi)"
        glyph = "var(--washi)" if active else "var(--shu)"
        seals.append(
            f'<g>'
            f'<circle cx="{sx}" cy="{sy}" r="19" fill="{center}" stroke="{ring}" stroke-width="2.5"/>'
            f'<text x="{sx}" y="{sy+6}" text-anchor="middle" font-size="18" '
            f'font-family="Shippori Mincho, serif" fill="{glyph}">{k}</text>'
            f'</g>'
        )
    seals_svg = "\n".join(seals)

    # seigaiha waves along the bottom
    waves = []
    for row in range(3):
        y = 700 + row * 26
        for x in range(-40, 1240, 46):
            waves.append(
                f'<path d="M {x} {y} a 23 23 0 0 1 46 0" fill="none" '
                f'stroke="var(--ai)" stroke-opacity="0.15" stroke-width="1.4"/>'
            )
    waves_svg = "\n".join(waves)

    # scattered sakura clusters near the road
    def blossom(cx, cy, s):
        petals = []
        for a in range(5):
            ang = a * 72
            petals.append(
                f'<ellipse cx="{cx}" cy="{cy-6*s}" rx="{3.4*s}" ry="{6*s}" '
                f'fill="var(--sakura)" transform="rotate({ang} {cx} {cy})"/>'
            )
        return f'<g opacity="0.85">{"".join(petals)}'\
               f'<circle cx="{cx}" cy="{cy}" r="{1.8*s}" fill="var(--kin)"/></g>'

    clusters = "".join(
        blossom(x, y, s) for (x, y, s) in
        [(250, 540, 1.3), (430, 520, 1.0), (600, 470, 1.4), (800, 430, 1.1),
         (980, 400, 1.2), (200, 400, 0.9), (720, 560, 1.0)]
    )

    return f'''
<svg viewBox="0 0 1200 780" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <filter id="paperNoise"><feTurbulence type="fractalNoise" baseFrequency="0.9"
      numOctaves="2" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter>
    <radialGradient id="sky" cx="45%" cy="35%" r="80%">
      <stop offset="0%" stop-color="#F7F1E1"/><stop offset="100%" stop-color="#E9DFC6"/>
    </radialGradient>
  </defs>
  <rect width="1200" height="780" fill="url(#sky)"/>
  <g>{rings_svg}</g>
  <g opacity="0.65">{waves_svg}</g>
  <path d="{road}" fill="none" stroke="var(--sumi)" stroke-width="2.5"
        stroke-dasharray="8 4" stroke-opacity="0.55" stroke-linecap="round"/>
  {clusters}
  <g>{seals_svg}</g>
  <!-- traveler marker on first station -->
  <g transform="translate(150 620)">
    <circle r="7" fill="var(--sumi)"/>
    <path d="M -4 8 L 4 8 L 0 -2 Z" fill="var(--kin)"/>
  </g>
  <!-- compass rose -->
  <g transform="translate(1110 700)" opacity="0.75">
    <circle r="34" fill="none" stroke="var(--sumi)" stroke-width="1" stroke-opacity="0.4"/>
    <path d="M0 -30 L6 0 L0 30 L-6 0 Z" fill="var(--sumi)" fill-opacity="0.35"/>
    <path d="M-30 0 L0 6 L30 0 L0 -6 Z" fill="var(--sumi)" fill-opacity="0.20"/>
    <circle r="4" fill="var(--sakura-deep)"/>
    <text x="0" y="-38" text-anchor="middle" font-size="10" fill="var(--sumi)" opacity="0.6">N</text>
  </g>
  <rect width="1200" height="780" filter="url(#paperNoise)" opacity="0.04"/>
</svg>
'''


def _petals_html() -> str:
    # a few drifting sakura petals over the whole page
    spans = []
    petal_svg = (
        '<svg width="18" height="18" viewBox="0 0 18 18">'
        '<path d="M9 1 C 12 4, 13 9, 9 16 C 5 9, 6 4, 9 1 Z" fill="var(--sakura-deep)"/></svg>'
    )
    starts = [(6, 18, 0), (32, 26, 5), (58, 22, 11), (80, 30, 3), (46, 20, 8)]
    for (left, dur, delay) in starts:
        spans.append(
            f'<span class="petal" style="position:fixed;top:-30px;left:{left}vw;'
            f'z-index:0;animation-duration:{dur}s;animation-delay:{delay}s;">{petal_svg}</span>'
        )
    return "".join(spans)


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------
LOGIN_HTML = f"""<!doctype html><html lang="en"><head>{HEAD}<title>Sekisho · 関所</title></head>
<body>
<div class="map-bg">{_map_svg()}</div><div class="vignette"></div>
{_petals_html()}
<div class="login-center">
  <form class="glass login-card" method="post" action="/admin/login">
    <div style="font-size:38px;color:var(--shu);font-family:'Shippori Mincho',serif;">関所</div>
    <div class="romaji" style="letter-spacing:3px;color:var(--sumi-soft);font-size:12px;margin-top:4px;">SEKISHO GATEWAY</div>
    <input type="password" name="password" placeholder="Admin password" autofocus required>
    <div class="err"><!--ERR--></div>
    <button class="btn btn-shu" style="width:100%;" type="submit"<!--DISABLED-->>Enter the checkpoint</button>
    <p class="muted" style="margin-top:16px;">Password is from <code>SEKISHO_ADMIN_PASSWORD</code><!--PWHINT-->.</p>
  </form>
</div>
</body></html>"""


def login_html(message: str = "", disabled: bool = False, on_vercel: bool = False) -> str:
    """Login page. ``message`` is server-controlled text (escaped anyway)."""
    import html as _html
    page = LOGIN_HTML.replace("<!--ERR-->", _html.escape(message))
    page = page.replace("<!--DISABLED-->", " disabled" if disabled else "")
    page = page.replace("<!--PWHINT-->", "" if on_vercel else " or printed in the server log")
    return page


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------


_DASHBOARD_BODY = r"""<!doctype html><html lang="en"><head>__HEAD__<meta name="csrf-token" content="__CSRF__"><title>Sekisho Admin · 関所</title></head>
<body>
__SCENES__<div class="vignette"></div>
__PETALS__
<div id="toast" class="toast"></div>
<div class="wrap">
  <div class="glass topbar">
    <div class="brand">
      <span class="kanji mincho">関所</span>
      <div><div class="name mincho">Sekisho</div>
      <div class="romaji">edge-ai gateway · admin</div></div>
    </div>
    <form method="post" action="/admin/logout" style="margin:0;">
      <input type="hidden" name="csrf_token" value="__CSRF__">
      <button class="btn btn-ghost btn-sm" type="submit">Log out</button>
    </form>
  </div>

  <div class="glass tabs">
    <button class="tab active" data-tab="status"><span class="k">道</span>Status</button>
    <button class="tab" data-tab="users"><span class="k">人</span>Users <span class="badge" id="tab-users-n">·</span></button>
    <button class="tab" data-tab="devices"><span class="k">手</span>Devices <span class="badge" id="tab-devices-n">·</span></button>
    <button class="tab" data-tab="backends"><span class="k">脳</span>Backends <span class="badge" id="tab-backends-n">·</span></button>
    <button class="tab" data-tab="requests"><span class="k">暦</span>Requests</button>
    <button class="tab" data-tab="push"><span class="k">鐘</span>Push <span class="badge" id="tab-push-n">·</span></button>
  </div>

  <!-- ============ STATUS ============ -->
  <div class="page active" id="page-status">
    <div class="grid">
      <div class="glass card">
        <h2><span class="seal">道</span>Backend status</h2>
        <div id="backend-status"><span class="muted">Loading…</span></div>
      </div>
      <div class="glass card">
        <h2><span class="seal">駅</span>Tunnel / status</h2>
        <div id="status-info"><span class="muted">Loading…</span></div>
      </div>
      <div class="glass card">
        <h2><span class="seal">鐘</span>Reminder scheduler</h2>
        <div id="scheduler-info"><span class="muted">Loading…</span></div>
      </div>
      <div class="glass card" style="grid-column:1 / -1;">
        <h2><span class="seal">茶</span>Glass transparency</h2>
        <div class="slider-row">
          <span class="muted">clear</span>
          <input type="range" id="glass-slider" min="0.15" max="0.9" step="0.01" value="0.55">
          <span class="muted">frosted</span>
          <span class="stat" id="glass-val" style="width:52px;text-align:right;">0.55</span>
        </div>
        <p class="muted" style="margin-top:10px;">Adjusts <code>--glass-alpha</code> for every panel. Saved in this browser.</p>
      </div>
      <div class="glass footer card" style="grid-column:1 / -1;">
        <div class="ipline" id="iplines">Detecting network…</div>
      </div>
    </div>
  </div>

  <!-- ============ USERS ============ -->
  <div class="page" id="page-users">
    <div class="glass card">
      <h2><span class="seal">人</span>Users</h2>
      <table><thead><tr>
        <th>User</th><th>Goal</th><th>Meals</th><th>Workouts</th><th>Last activity</th><th>Failed PINs</th><th></th>
      </tr></thead><tbody id="users-tb"><tr><td colspan="7" class="muted">Loading…</td></tr></tbody></table>
      <div class="drill" id="user-drill" style="display:none;"></div>
    </div>
  </div>

  <!-- ============ DEVICES ============ -->
  <div class="page" id="page-devices">
    <div class="grid">
      <div class="glass card" style="grid-column:1 / -1;">
        <h2><span class="seal">手</span>Paired devices</h2>
        <table><thead><tr>
          <th>Name</th><th>Device ID</th><th>Active user</th><th>Last seen</th><th>Requests</th><th></th>
        </tr></thead><tbody id="devices-tb"><tr><td colspan="6" class="muted">Loading…</td></tr></tbody></table>
      </div>
      <div class="glass card">
        <h2><span class="seal">符</span>Pair a new device</h2>
        <div class="qrbox">
          <img id="qr" alt="Pairing QR" src="">
          <div class="countdown" id="qr-countdown">Preparing token…</div>
          <button class="btn btn-shu btn-sm" id="new-token">New pairing QR</button>
        </div>
        <p class="muted" style="margin-top:12px;">Scan on the phone in the Harahachi app. Expires in 300s, single-use.</p>
      </div>
    </div>
  </div>

  <!-- ============ BACKENDS ============ -->
  <div class="page" id="page-backends">
    <div class="grid">
      <div class="glass card" style="grid-column:1 / -1;">
        <h2><span class="seal">算</span>NVIDIA NIM model slots</h2>
        <div id="nim-status" class="muted">Loading…</div>
        <table style="margin-top:10px;"><thead><tr><th>Task</th><th>Model</th><th>Fallback (tried once)</th><th></th></tr></thead>
          <tbody id="nim-slots"></tbody></table>
        <p class="muted" style="margin-top:10px;">Empty model/fallback = use the env/default value. Fallback <code>none</code> disables it.
          The API key is read from <code>NVIDIA_API_KEY</code> only — it is never stored or shown here.</p>
      </div>
      <div class="glass card" style="grid-column:1 / -1;">
        <h2><span class="seal">脳</span>LLM backends</h2>
        <div id="backends-list"><span class="muted">Loading…</span></div>
      </div>
      <div class="glass card" style="grid-column:1 / -1;">
        <h2><span class="seal">加</span>Add a backend</h2>
        <div class="row2">
          <div><label class="fld">Preset</label>
            <select class="inp" id="bk-preset">
              <option value="">— choose —</option>
              <option value="nvidia">NVIDIA API</option>
              <option value="lmstudio">LM Studio</option>
              <option value="vllm">vLLM</option>
              <option value="openai">OpenAI</option>
            </select></div>
          <div><label class="fld">Name</label><input class="inp" id="bk-name" placeholder="My NVIDIA"></div>
        </div>
        <div class="row2">
          <div><label class="fld">Kind</label>
            <select class="inp" id="bk-kind">
              <option value="nvidia">nvidia</option><option value="lmstudio">lmstudio</option>
              <option value="vllm">vllm</option><option value="openai">openai</option>
            </select></div>
          <div><label class="fld">Model</label><input class="inp" id="bk-model" placeholder="meta/llama-3.2-90b-vision-instruct"></div>
        </div>
        <label class="fld">Base URL</label><input class="inp" id="bk-url" placeholder="https://integrate.api.nvidia.com/v1">
        <label class="fld">API key (optional)</label><input class="inp" id="bk-key" placeholder="nvapi-…">
        <div style="display:flex;gap:20px;">
          <label class="chk"><input type="checkbox" id="bk-vision"> Vision-capable</label>
          <label class="chk"><input type="checkbox" id="bk-enabled" checked> Enabled</label>
        </div>
        <div style="margin-top:14px;"><button class="btn btn-shu" id="bk-create">Add backend</button></div>
      </div>
    </div>
  </div>

  <!-- ============ REQUESTS ============ -->
  <div class="page" id="page-requests">
    <div class="glass card">
      <h2><span class="seal">暦</span>Request log <span class="muted" style="font-weight:400;">(last 200)</span></h2>
      <div class="filters">
        <input id="flt-device" placeholder="filter device…">
        <input id="flt-user" placeholder="filter user…">
        <input id="flt-path" placeholder="filter route…">
        <select id="flt-status"><option value="">any status</option><option value="2">2xx</option>
          <option value="4">4xx</option><option value="5">5xx</option></select>
        <label class="muted" style="display:flex;align-items:center;gap:6px;">
          <input type="checkbox" id="flt-auto" checked> auto-refresh</label>
      </div>
      <table><thead><tr>
        <th>When</th><th>Device</th><th>User</th><th>Method</th><th>Path</th><th>Status</th><th>Time</th>
      </tr></thead><tbody id="logs-tb"><tr><td colspan="7" class="muted">Loading…</td></tr></tbody></table>
    </div>
  </div>

  <!-- ============ PUSH ============ -->
  <div class="page" id="page-push">
    <div class="glass card">
      <h2><span class="seal">鐘</span>Push reminders</h2>
      <div class="big-num" id="push-count">·</div>
      <p class="muted">active web-push subscriptions</p>
      <div style="margin-top:14px;"><button class="btn btn-shu" id="push-test">Send test notification</button></div>
      <div class="muted" id="push-result" style="margin-top:10px;"></div>
    </div>
  </div>
</div>

<script nonce="__NONCE__">
const root=document.documentElement;
const CSRF=(document.querySelector('meta[name="csrf-token"]')||{}).content||'';
function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function ago(iso){if(!iso) return 'never';const d=new Date(iso),s=(Date.now()-d.getTime())/1000;
  if(s<60) return Math.floor(s)+'s ago';if(s<3600) return Math.floor(s/60)+'m ago';
  if(s<86400) return Math.floor(s/3600)+'h ago';return Math.floor(s/86400)+'d ago';}
function statusClass(st){return st>=500?'status-5xx':st>=400?'status-4xx':'status-2xx';}
function toast(msg,ok){const t=document.getElementById('toast');t.textContent=msg;
  t.className='toast show '+(ok?'ok':'bad');setTimeout(()=>{t.className='toast';},2600);}
async function api(url,opts){opts=Object.assign({credentials:'same-origin'},opts||{});
  const m=(opts.method||'GET').toUpperCase();
  if(m!=='GET'&&m!=='HEAD'){opts.headers=Object.assign({},opts.headers||{},{'X-CSRF-Token':CSRF});}
  const r=await fetch(url,opts);let j=null;try{j=await r.json();}catch(e){}
  if(r.status===401&&j&&j.error&&j.error.code==='unauthorized'){location.href='/admin';}
  return {ok:r.ok,status:r.status,body:j};}
function form(obj){const fd=new FormData();for(const k in obj)fd.append(k,obj[k]);return fd;}

document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('page-'+t.dataset.tab).classList.add('active');
  // swap the province scene behind the glass to match the active tab
  document.querySelectorAll('.scene').forEach(s=>s.classList.remove('active'));
  const sc=document.getElementById('scene-'+t.dataset.tab);
  if(sc) sc.classList.add('active');
  if(t.dataset.tab==='users') loadUsers();
  if(t.dataset.tab==='backends') loadBackends();
}));

const slider=document.getElementById('glass-slider'),gval=document.getElementById('glass-val');
const saved=localStorage.getItem('sekisho_glass_alpha');
if(saved){root.style.setProperty('--glass-alpha',saved);slider.value=saved;gval.textContent=(+saved).toFixed(2);}
slider.addEventListener('input',()=>{root.style.setProperty('--glass-alpha',slider.value);
  gval.textContent=(+slider.value).toFixed(2);localStorage.setItem('sekisho_glass_alpha',slider.value);});

async function refreshState(){
  const {ok,body:s}=await api('/admin/api/state'); if(!ok) return;
  const b=s.backend, bd=document.getElementById('backend-status');
  const txt=b.active_text?('<code>'+esc(b.active_text.name)+'</code> · '+esc(b.active_text.model)):'none';
  const vis=b.active_vision?('<code>'+esc(b.active_vision.name)+'</code> · '+esc(b.active_vision.model)):'none';
  if(b.connected){
    bd.innerHTML='<span class="pill ok"><span class="dot"></span>Connected</span>'+
      '<p class="muted" style="margin-top:12px;">Active text: '+txt+'<br>Active vision: '+vis+
      '<br>Backends: '+b.enabled+' enabled / '+b.count+' total</p>';
  }else{
    bd.innerHTML='<span class="pill mock"><span class="dot"></span>'+(b.allow_mock?'Mock mode':'Unavailable')+'</span>'+
      '<p class="muted" style="margin-top:12px;">Active text: '+txt+'<br>Active vision: '+vis+
      '<br>'+(b.count?'Active backend unreachable; ':'No backends configured; ')+
      (b.allow_mock?'serving deterministic mock so the app still works.':'mock disabled — analyze/chat return 503.')+'</p>';
  }
  const sch=s.scheduler||{};
  const schLine=sch.running?('<span class="pill ok"><span class="dot"></span>running</span>')
    :('<span class="pill mock"><span class="dot"></span>stopped</span>');
  document.getElementById('status-info').innerHTML=
    '<p class="muted">Public URL:<br><code>'+esc(s.public_url)+'</code></p>'+
    '<p class="muted">Mode: <b>'+esc(b.mode)+'</b> · mock '+(b.allow_mock?'on':'off')+'</p>'+
    '<p class="muted">Version: <b>'+esc(s.version||'?')+'</b> · DB '+esc(s.db_size||'?')+'</p>'+
    '<p class="muted">Uptime: '+esc(s.uptime)+'</p>'+
    '<p class="muted">Push subs: '+s.push_subscriptions+'</p>';
  const sd=document.getElementById('scheduler-info');
  if(sd) sd.innerHTML=
    '<p>Reminder scheduler: '+schLine+'</p>'+
    '<p class="muted">Last tick: '+ago(sch.last_tick)+' · sent '+(sch.last_tick_sent||0)+
    ' · ticks '+(sch.ticks||0)+(sch.restarts?(' · restarts '+sch.restarts):'')+'</p>'+
    '<p class="muted">Next fire: '+(sch.next_fire?esc(sch.next_fire.replace("T"," ").slice(0,16)+' UTC'):'—')+
    ' · VAPID '+(sch.vapid_configured?'ok':'missing')+'</p>'+
    (sch.last_error?('<p class="muted" style="color:var(--shu)">Last error: '+esc(sch.last_error)+'</p>'):'');
  document.getElementById('tab-devices-n').textContent=s.devices.length;
  const dt=document.getElementById('devices-tb');
  if(!s.devices.length){dt.innerHTML='<tr><td colspan="6" class="muted">No devices paired yet.</td></tr>';}
  else{dt.innerHTML=s.devices.map(d=>{
    const rev=(d.revoked?' <span class="tag off">revoked</span>':'')+
      (d.pin_locked?' <span class="tag off">PIN-locked</span>':'');
    const btn=(d.pin_locked?'<button class="btn btn-ghost btn-sm dev-unlock" data-id="'+esc(d.device_id)+'">Unlock</button> ':'')+
      (d.revoked?'':'<button class="btn btn-ghost btn-sm revoke" data-id="'+esc(d.device_id)+'">Revoke</button>');
    const usr=d.active_username?esc(d.active_username):'<span class="muted">—</span>';
    return '<tr><td>'+esc(d.name)+rev+'</td><td><code>'+esc(d.device_id)+'</code></td><td>'+usr+
      '</td><td>'+ago(d.last_seen)+'</td><td class="stat">'+d.req_count+
      '</td><td style="text-align:right;">'+btn+'</td></tr>';}).join('');
    dt.querySelectorAll('.revoke').forEach(x=>x.addEventListener('click',async()=>{
      await api('/admin/api/revoke',{method:'POST',body:form({device_id:x.dataset.id})});
      toast('Device revoked',true);refreshState();}));
    dt.querySelectorAll('.dev-unlock').forEach(x=>x.addEventListener('click',async()=>{
      const r=await api('/admin/api/devices/unlock',{method:'POST',body:form({device_id:x.dataset.id})});
      toast(r.ok?'Device unlocked':'Unlock failed',r.ok);refreshState();}));}
  window._logs=s.logs; renderLogs();
  const ip=s.ips,lines=[];
  if(ip.tailscale.length) lines.push('Tailscale: '+ip.tailscale.map(x=>x+':'+s.port).join('  ·  '));
  if(ip.lan.length) lines.push('LAN: '+ip.lan.map(x=>x+':'+s.port).join('  ·  '));
  if(ip.other.length) lines.push('Other: '+ip.other.map(x=>x+':'+s.port).join('  ·  '));
  lines.push('Admin: http://'+s.host+':'+s.port+'/admin');
  document.getElementById('iplines').innerHTML=lines.map(esc).join('<br>');
  document.getElementById('push-count').textContent=s.push_subscriptions;
  document.getElementById('tab-push-n').textContent=s.push_subscriptions;
}
['flt-device','flt-user','flt-path','flt-status'].forEach(id=>document.getElementById(id).addEventListener('input',renderLogs));
function renderLogs(){
  const logs=window._logs||[];const lb=document.getElementById('logs-tb');
  const fd=document.getElementById('flt-device').value.toLowerCase();
  const fu=document.getElementById('flt-user').value.toLowerCase();
  const fp=document.getElementById('flt-path').value.toLowerCase();
  const fs=document.getElementById('flt-status').value;
  const rows=logs.filter(l=>(!fd||(l.device_name||'').toLowerCase().includes(fd))&&
    (!fu||(l.username||'').toLowerCase().includes(fu))&&
    (!fp||(l.path||'').toLowerCase().includes(fp))&&(!fs||String(l.status).startsWith(fs)));
  if(!rows.length){lb.innerHTML='<tr><td colspan="7" class="muted">No matching requests.</td></tr>';return;}
  lb.innerHTML=rows.map(l=>'<tr class="logrow"><td>'+ago(l.at)+'</td><td>'+esc(l.device_name||'—')+
    '</td><td>'+esc(l.username||'—')+'</td><td>'+esc(l.method)+'</td><td><code>'+esc(l.path)+'</code></td>'+
    '<td class="'+statusClass(l.status)+' stat">'+l.status+'</td>'+
    '<td class="muted stat">'+(l.duration_ms!=null?l.duration_ms+'ms':'—')+'</td></tr>').join('');
}

async function loadUsers(){
  const {ok,body}=await api('/admin/api/users'); if(!ok) return;
  document.getElementById('tab-users-n').textContent=body.users.length;
  const tb=document.getElementById('users-tb');
  if(!body.users.length){tb.innerHTML='<tr><td colspan="7" class="muted">No users yet.</td></tr>';return;}
  tb.innerHTML=body.users.map(u=>{
    const lk=u.locked==='hard'?' <span class="tag off">locked</span>':u.locked==='temp'?' <span class="tag off">cooling down</span>':'';
    const unl=(u.locked||u.failed_logins)?'<button class="btn btn-ghost btn-sm user-unlock" data-id="'+u.id+'">Unlock</button> ':'';
    return '<tr><td><span class="linkish user-link" data-id="'+u.id+'">'+esc(u.username)+
    '</span></td><td>'+esc(u.goal||'—')+'</td><td class="stat">'+u.meal_count+'</td><td class="stat">'+
    u.workout_count+'</td><td>'+ago(u.last_meal)+'</td><td class="stat">'+(u.failed_logins||0)+lk+'</td>'+
    '<td style="text-align:right;">'+unl+'<button class="btn btn-ghost btn-sm user-link" data-id="'+u.id+'">View</button></td></tr>';}).join('');
  tb.querySelectorAll('.user-link').forEach(x=>x.addEventListener('click',()=>drillUser(x.dataset.id)));
  tb.querySelectorAll('.user-unlock').forEach(x=>x.addEventListener('click',async()=>{
    const r=await api('/admin/api/users/'+x.dataset.id+'/unlock',{method:'POST'});
    toast(r.ok?'Sign-in unlocked':'Unlock failed',r.ok);loadUsers();}));
}
// Adherence mini-chart: 8 weekly bars, height ∝ adherence %, coloured by band.
function adherenceSvg(weeks){
  if(!weeks||!weeks.length) return '<p class="muted">Not enough history yet.</p>';
  const W=280,H=64,n=weeks.length,bw=W/n;
  const bars=weeks.map((w,i)=>{
    const pct=Math.max(0,Math.min(100,w.adherence_pct||0));
    const h=Math.max(2,Math.round(pct/100*(H-14)));
    const x=Math.round(i*bw)+2,y=H-h-12,ww=Math.max(4,Math.round(bw)-4);
    const col=pct>=70?'#3f5b28':pct>=40?'#c88a2e':'#b5462f';
    return '<rect x="'+x+'" y="'+y+'" width="'+ww+'" height="'+h+'" rx="2" fill="'+col+'"/>'+
      '<text x="'+(x+ww/2)+'" y="'+(H-2)+'" font-size="7" text-anchor="middle" fill="#8a857c">'+
      esc((w.week_start||'').slice(5))+'</text>';
  }).join('');
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" style="max-width:'+W+'px;">'+bars+'</svg>';
}
const _drillState={};  // id -> {page, day}
async function drillUser(id){
  const st=_drillState[id]||(_drillState[id]={page:0,day:''});
  const qs='?page='+st.page+'&per_page=10'+(st.day?('&day='+encodeURIComponent(st.day)):'');
  const {ok,body}=await api('/admin/api/users/'+id+'/full'+qs);
  const d=document.getElementById('user-drill');
  d.style.display='block';
  if(!ok){d.innerHTML='<p class="muted">Could not load user.</p>';return;}
  const p=body.profile,t=body.targets,dt=body.day_totals,sk=body.streaks||{},mp=body.meals_page||{};
  let html='<h3 class="mincho">'+esc(body.user.username)+
    (body.user.is_admin?' <span class="macrochip mc-p">keeper</span>':'')+'</h3>';
  html+='<p>Checkpoint keeper: <strong>'+(body.user.is_admin?'yes':'no')+'</strong> '+
    '<button class="btn btn-ghost btn-sm" id="keeper-toggle" data-id="'+body.user.id+
    '" data-val="'+(body.user.is_admin?'0':'1')+'">'+
    (body.user.is_admin?'Revoke admin':'Grant admin')+'</button></p>';
  if(p){
    html+='<p class="muted">'+esc(p.sex)+' · age '+p.age+' · '+p.height_cm+'cm · '+p.weight_kg+'kg · '+
      esc(p.activity)+' · goal '+esc(p.goal)+(p.body_fat_pct!=null?(' · body fat '+p.body_fat_pct+'%'):'')+
      (p.weight_goal_kg!=null?(' · goal wt '+p.weight_goal_kg+'kg'):'')+'</p>';
    if(t) html+='<p>Targets: <span class="stat">'+t.calories+'</span> kcal · '+
      '<span class="macrochip mc-p">'+t.protein_g+'g P</span><span class="macrochip mc-c">'+t.carbs_g+
      'g C</span><span class="macrochip mc-f">'+t.fat_g+'g F</span></p>';
  }else{html+='<p class="muted">No profile set.</p>';}
  html+='<p>Today: <span class="stat">'+dt.calories+'</span> kcal in · '+dt.burned+' burned · '+
    '<span class="macrochip mc-p">'+dt.protein_g+'g P</span><span class="macrochip mc-c">'+dt.carbs_g+
    'g C</span><span class="macrochip mc-f">'+dt.fat_g+'g F</span></p>';
  // Streaks + stamps
  html+='<p>Streak: <strong>'+(sk.current||0)+'</strong> day'+((sk.current===1)?'':'s')+
    ' · best <strong>'+(sk.best||0)+'</strong>'+
    ((sk.stamps&&sk.stamps.length)?(' · '+sk.stamps.map(s=>'<span class="macrochip mc-c">'+esc(s)+'</span>').join('')):'')+'</p>';
  // Adherence mini-chart
  html+='<h4 style="margin:14px 0 4px;">Weekly adherence</h4>'+adherenceSvg(body.weekly_adherence);
  // Paginated meal history + day filter
  html+='<h4 style="margin:14px 0 6px;">Meal history <span class="muted" style="font-weight:400;">('+
    (mp.total||0)+' total)</span></h4>';
  html+='<div class="filters"><input id="drill-day" placeholder="YYYY-MM-DD day filter" value="'+esc(st.day||'')+'">'+
    '<button class="btn btn-ghost btn-sm" id="drill-day-apply">Filter</button>'+
    (st.day?'<button class="btn btn-ghost btn-sm" id="drill-day-clear">Clear</button>':'')+'</div>';
  if(body.meals.length){
    html+='<table><thead><tr><th>When</th><th>Dish</th><th>Kcal</th><th>P/C/F</th><th>Src</th></tr></thead><tbody>'+
      body.meals.map(m=>'<tr><td>'+esc((m.eaten_at||'').slice(0,16).replace("T"," "))+'</td><td>'+esc(m.dish)+
      '</td><td class="stat">'+m.calories+'</td><td class="muted">'+m.protein_g+'/'+m.carbs_g+'/'+m.fat_g+
      '</td><td class="muted">'+esc(m.source)+'</td></tr>').join('')+'</tbody></table>';
    html+='<div class="filters" style="margin-top:8px;align-items:center;">'+
      '<button class="btn btn-ghost btn-sm" id="drill-prev"'+(mp.page<=0?' disabled':'')+'>‹ Prev</button>'+
      '<span class="muted">page '+((mp.page||0)+1)+' / '+(mp.total_pages||1)+'</span>'+
      '<button class="btn btn-ghost btn-sm" id="drill-next"'+((mp.page+1>=mp.total_pages)?' disabled':'')+'>Next ›</button></div>';
  }else{html+='<p class="muted">No meals'+(st.day?' on '+esc(st.day):'')+'.</p>';}
  // Weights / water / workouts / devices
  if(body.weights&&body.weights.length){
    html+='<h4 style="margin:14px 0 6px;">Weights</h4><p class="muted">'+
      body.weights.slice(0,10).map(w=>esc(w.date)+': '+w.weight_kg+'kg').join(' · ')+'</p>';}
  if(body.water&&body.water.length){
    html+='<h4 style="margin:14px 0 6px;">Water (ml/day)</h4><p class="muted">'+
      body.water.slice(0,10).map(w=>esc(w.date)+': '+w.water_ml).join(' · ')+'</p>';}
  if(body.workouts&&body.workouts.length){
    html+='<h4 style="margin:14px 0 6px;">Workouts</h4><table><thead><tr><th>When</th><th>Kind</th>'+
      '<th>Min</th><th>Burned</th></tr></thead><tbody>'+body.workouts.map(w=>'<tr><td>'+
      esc((w.done_at||'').slice(0,16).replace("T"," "))+'</td><td>'+esc(w.kind)+'</td><td class="stat">'+
      w.minutes+'</td><td class="stat">'+w.calories_burned+'</td></tr>').join('')+'</tbody></table>';}
  if(body.devices&&body.devices.length){
    html+='<h4 style="margin:14px 0 6px;">Devices</h4><p class="muted">'+
      body.devices.map(dv=>esc(dv.name)+(dv.revoked?' (revoked)':'')+' · seen '+ago(dv.last_seen)).join('<br>')+'</p>';}
  d.innerHTML=html;
  const kt=document.getElementById('keeper-toggle');
  if(kt) kt.addEventListener('click',async()=>{
    await api('/admin/api/users/'+kt.dataset.id+'/admin',{method:'POST',body:form({is_admin:kt.dataset.val})});
    drillUser(id);
    loadUsers();
  });
  const pv=document.getElementById('drill-prev');
  if(pv) pv.addEventListener('click',()=>{if(st.page>0){st.page--;drillUser(id);}});
  const nx=document.getElementById('drill-next');
  if(nx) nx.addEventListener('click',()=>{st.page++;drillUser(id);});
  const da=document.getElementById('drill-day-apply');
  if(da) da.addEventListener('click',()=>{st.day=document.getElementById('drill-day').value.trim();st.page=0;drillUser(id);});
  const dc=document.getElementById('drill-day-clear');
  if(dc) dc.addEventListener('click',()=>{st.day='';st.page=0;drillUser(id);});
}

const PRESET_URL={nvidia:'https://integrate.api.nvidia.com/v1',lmstudio:'http://host.docker.internal:1234/v1',
  vllm:'http://host.docker.internal:8000/v1',openai:'https://api.openai.com/v1'};
document.getElementById('bk-preset').addEventListener('change',e=>{
  const k=e.target.value; if(!k) return;
  document.getElementById('bk-kind').value=k;
  document.getElementById('bk-url').value=PRESET_URL[k]||'';
  if(k==='nvidia') document.getElementById('bk-vision').checked=true;
});
document.getElementById('bk-create').addEventListener('click',async()=>{
  const payload={name:document.getElementById('bk-name').value||'backend',
    kind:document.getElementById('bk-kind').value,base_url:document.getElementById('bk-url').value,
    model:document.getElementById('bk-model').value,api_key:document.getElementById('bk-key').value,
    vision:document.getElementById('bk-vision').checked?'1':'0',
    enabled:document.getElementById('bk-enabled').checked?'1':'0'};
  const {ok,body}=await api('/admin/api/backends',{method:'POST',body:form(payload)});
  if(ok){toast('Backend added',true);['bk-name','bk-model','bk-url','bk-key'].forEach(i=>document.getElementById(i).value='');
    loadBackends();}else{toast((body&&body.error&&body.error.message)||'Failed',false);}
});
async function loadNim(){
  const {ok,body}=await api('/admin/api/nim'); if(!ok) return;
  const st=document.getElementById('nim-status');
  st.innerHTML='Provider: <b>'+esc(body.provider)+'</b> · '+esc(body.base_url)+' · key: '+
    (body.key_configured?('set ('+esc(body.key_hint)+')'):'<span style="color:var(--shu)">NVIDIA_API_KEY not set</span>')+
    ' · inline image limit: '+esc(body.max_image_b64)+' base64 chars';
  const tb=document.getElementById('nim-slots');
  tb.innerHTML=Object.values(body.slots).map(sl=>'<tr><td><b>'+esc(sl.task)+'</b><br><span class="muted">'+esc(sl.source)+'</span></td>'+
    '<td><input class="inp" id="nim-m-'+esc(sl.task)+'" value="'+esc(sl.source==='admin'?sl.model:'')+'" placeholder="'+esc(sl.model)+'"></td>'+
    '<td><input class="inp" id="nim-f-'+esc(sl.task)+'" value="" placeholder="'+esc(sl.fallback||'none')+'"></td>'+
    '<td style="text-align:right;white-space:nowrap;"><button class="btn btn-ghost btn-sm nim-save" data-task="'+esc(sl.task)+'">Save</button> '+
    '<button class="btn btn-ghost btn-sm nim-test" data-task="'+esc(sl.task)+'">Test</button>'+
    '<div class="muted" id="nim-t-'+esc(sl.task)+'"></div></td></tr>').join('');
  tb.querySelectorAll('.nim-save').forEach(x=>x.addEventListener('click',async()=>{
    const t=x.dataset.task;
    const r=await api('/admin/api/nim/slot',{method:'POST',body:form({task:t,
      model:document.getElementById('nim-m-'+t).value,fallback:document.getElementById('nim-f-'+t).value})});
    toast(r.ok?'Slot '+t+' saved':((r.body&&r.body.error&&r.body.error.message)||'Failed'),r.ok);loadNim();}));
  tb.querySelectorAll('.nim-test').forEach(x=>x.addEventListener('click',async()=>{
    const t=x.dataset.task,out=document.getElementById('nim-t-'+t);out.textContent='Testing…';
    const r=await api('/admin/api/nim/test',{method:'POST',body:form({task:t})});
    out.textContent=(r.body&&r.body.ok)?('ok · '+esc(r.body.model)):('FAIL: '+((r.body&&r.body.error)||'unreachable'));}));
}
async function loadBackends(){
  loadNim();
  const {ok,body}=await api('/admin/api/backends'); if(!ok) return;
  document.getElementById('tab-backends-n').textContent=body.backends.length;
  const el=document.getElementById('backends-list');
  if(!body.backends.length){el.innerHTML='<p class="muted">No backends yet. Add one below — mock analyzer is active meanwhile.</p>';return;}
  el.innerHTML=body.backends.map(b=>{
    const tags=(b.vision?'<span class="tag vis">vision</span>':'')+
      (b.enabled?'<span class="tag">enabled</span>':'<span class="tag off">disabled</span>')+
      (b.id===body.active_text_id?'<span class="tag active-t">active text</span>':'')+
      (b.id===body.active_vision_id?'<span class="tag active-t">active vision</span>':'');
    return '<div class="backend-item" data-id="'+b.id+'"><div class="backend-head">'+
      '<div><b>'+esc(b.name)+'</b> <span class="muted">('+esc(b.kind)+')</span><br>'+
      '<span class="muted"><code>'+esc(b.base_url)+'</code> · '+esc(b.model)+
      (b.has_key?' · key set':'')+'</span><div style="margin-top:6px;">'+tags+'</div></div>'+
      '<div style="text-align:right;">'+
      '<button class="btn btn-ghost btn-sm bk-test" data-id="'+b.id+'">Test</button> '+
      '<button class="btn btn-ghost btn-sm bk-edit" data-id="'+b.id+'">Edit</button> '+
      '<button class="btn btn-ghost btn-sm bk-txt" data-id="'+b.id+'">Set text</button> '+
      (b.vision?'<button class="btn btn-ghost btn-sm bk-vis" data-id="'+b.id+'">Set vision</button> ':'')+
      '<button class="btn btn-ghost btn-sm bk-tgl" data-id="'+b.id+'" data-en="'+(b.enabled?0:1)+'">'+(b.enabled?'Disable':'Enable')+'</button> '+
      '<button class="btn btn-ghost btn-sm bk-del" data-id="'+b.id+'">Delete</button>'+
      '</div></div>'+
      '<div class="bk-editform" id="edit-'+b.id+'" style="display:none;margin-top:10px;">'+
      '<div class="filters">'+
      '<input id="ed-name-'+b.id+'" value="'+esc(b.name)+'" placeholder="name">'+
      '<select id="ed-kind-'+b.id+'">'+['vllm','nvidia','lmstudio','openai'].map(k=>'<option value="'+k+'"'+(b.kind===k?' selected':'')+'>'+k+'</option>').join('')+'</select>'+
      '<input id="ed-url-'+b.id+'" value="'+esc(b.base_url)+'" placeholder="base_url" style="min-width:240px;">'+
      '<input id="ed-model-'+b.id+'" value="'+esc(b.model)+'" placeholder="model">'+
      '<input id="ed-key-'+b.id+'" value="" placeholder="'+(b.has_key?'•••••• set — blank keeps it':'api key (optional)')+'">'+
      '<label class="muted" style="display:flex;align-items:center;gap:5px;"><input type="checkbox" id="ed-vis-'+b.id+'"'+(b.vision?' checked':'')+'> vision</label>'+
      '<label class="muted" style="display:flex;align-items:center;gap:5px;"><input type="checkbox" id="ed-en-'+b.id+'"'+(b.enabled?' checked':'')+'> enabled</label>'+
      '<button class="btn btn-shu btn-sm bk-save" data-id="'+b.id+'" data-haskey="'+(b.has_key?1:0)+'">Save</button>'+
      (b.has_key?'<button class="btn btn-ghost btn-sm bk-clearkey" data-id="'+b.id+'">Clear key</button>':'')+
      '</div></div>'+
      '<div class="test-out muted" id="test-'+b.id+'"></div></div>';
  }).join('');
  el.querySelectorAll('.bk-edit').forEach(x=>x.addEventListener('click',()=>{
    const f=document.getElementById('edit-'+x.dataset.id);
    f.style.display=(f.style.display==='none'?'block':'none');}));
  el.querySelectorAll('.bk-save').forEach(x=>x.addEventListener('click',async()=>{
    const id=x.dataset.id;
    const payload={name:document.getElementById('ed-name-'+id).value,
      kind:document.getElementById('ed-kind-'+id).value,
      base_url:document.getElementById('ed-url-'+id).value,
      model:document.getElementById('ed-model-'+id).value,
      vision:document.getElementById('ed-vis-'+id).checked?'1':'0',
      enabled:document.getElementById('ed-en-'+id).checked?'1':'0'};
    // Only send api_key when the admin typed a new one — blank keeps the stored key.
    const nk=document.getElementById('ed-key-'+id).value.trim();
    if(nk) payload.api_key=nk;
    const {ok,body}=await api('/admin/api/backends/'+id,{method:'POST',body:form(payload)});
    if(ok){toast('Backend updated',true);loadBackends();}
    else{toast((body&&body.error&&body.error.message)||'Update failed',false);}
  }));
  el.querySelectorAll('.bk-clearkey').forEach(x=>x.addEventListener('click',async()=>{
    // Send an explicit empty api_key to clear the stored credential.
    await api('/admin/api/backends/'+x.dataset.id,{method:'POST',body:form({api_key:''})});
    toast('API key cleared',true);loadBackends();}));
  el.querySelectorAll('.bk-test').forEach(x=>x.addEventListener('click',async()=>{
    const out=document.getElementById('test-'+x.dataset.id);out.textContent='Testing…';
    const {body}=await api('/admin/api/backends/'+x.dataset.id+'/test',{method:'POST'});
    if(body&&body.ok){out.className='test-out';out.style.color='#3f5b28';
      out.textContent='ping ok · models: '+(body.models||[]).slice(0,6).join(', ');}
    else{out.style.color='var(--shu)';out.textContent='FAIL: '+((body&&body.error)||'unreachable');}
  }));
  el.querySelectorAll('.bk-txt').forEach(x=>x.addEventListener('click',async()=>{
    await api('/admin/api/backends/active',{method:'POST',body:form({slot:'text',backend_id:x.dataset.id})});
    toast('Active text backend set',true);loadBackends();}));
  el.querySelectorAll('.bk-vis').forEach(x=>x.addEventListener('click',async()=>{
    await api('/admin/api/backends/active',{method:'POST',body:form({slot:'vision',backend_id:x.dataset.id})});
    toast('Active vision backend set',true);loadBackends();}));
  el.querySelectorAll('.bk-tgl').forEach(x=>x.addEventListener('click',async()=>{
    await api('/admin/api/backends/'+x.dataset.id,{method:'POST',body:form({enabled:x.dataset.en})});
    loadBackends();}));
  el.querySelectorAll('.bk-del').forEach(x=>x.addEventListener('click',async()=>{
    if(!confirm('Delete this backend?'))return;
    await api('/admin/api/backends/'+x.dataset.id+'/delete',{method:'POST'});
    toast('Backend deleted',true);loadBackends();}));
}

document.getElementById('push-test').addEventListener('click',async()=>{
  const r=document.getElementById('push-result');r.textContent='Sending…';
  const {body}=await api('/admin/api/push/test',{method:'POST'});
  if(body&&body.ok){r.style.color='#3f5b28';r.textContent='Sent to '+body.sent+' of '+body.total+' subscriptions.';toast('Test push sent',true);}
  else{r.style.color='var(--shu)';r.textContent=(body&&body.message)||('Sent 0 · '+((body&&body.errors&&body.errors[0])||'no subscriptions'));toast('Push not sent',false);}
});

let expiresAt=0;
async function newToken(){
  const {ok,body}=await api('/admin/api/pair',{method:'POST'}); if(!ok) return;
  document.getElementById('qr').src='/admin/api/qr?token='+encodeURIComponent(body.token)+'&_='+Date.now();
  expiresAt=Date.now()+body.expires_in*1000;
}
function tickQr(){
  if(expiresAt===0) return;
  const left=Math.max(0,Math.round((expiresAt-Date.now())/1000));
  document.getElementById('qr-countdown').textContent='Expires in '+left+'s';
  if(left<=15) newToken();
}
document.getElementById('new-token').addEventListener('click',newToken);

refreshState(); newToken();
setInterval(()=>{const a=document.getElementById('flt-auto');if(!a||a.checked)refreshState();},5000);
setInterval(tickQr,1000);
</script>
</body></html>"""


def _scene_layers() -> str:
    """One fixed background layer per admin tab, each a different province scene.
    The 'status' layer starts active; the tab-switch JS crossfades between them."""
    order = ["status", "users", "devices", "backends", "requests", "push"]
    layers = []
    for tab in order:
        active = " active" if tab == "status" else ""
        layers.append(f'<div class="scene{active}" id="scene-{tab}">{scene_svg(tab)}</div>')
    return "".join(layers)


def dashboard_html(nonce: str = "", csrf: str = "") -> str:
    """Dashboard page. ``nonce`` authorizes the one inline <script> under the
    admin CSP; ``csrf`` is echoed in a meta tag + the logout form and sent back
    as X-CSRF-Token on every state-changing fetch."""
    return (
        _DASHBOARD_BODY
        .replace("__HEAD__", HEAD)
        .replace("__SCENES__", _scene_layers())
        .replace("__PETALS__", _petals_html())
        .replace("__NONCE__", nonce)
        .replace("__CSRF__", csrf)
    )
