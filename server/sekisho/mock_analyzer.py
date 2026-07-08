"""Deterministic mock meal analyzer.

Keyed off image hash + hint so the same photo always yields the same plausible
result, but different photos yield varied plates. Lets the whole system demo
without a GPU/vLLM.

Per API.md the analyzer must return realistic MULTI-ITEM Indian plates: a main
plus sides (dal + rice + roti + sabzi + curd + pickle + papad; thali; biryani +
raita). Each item carries its own macros and the top-level numbers are the sum,
so the app's floating item-chip overlay can be exercised without a model.
"""
from __future__ import annotations

import hashlib
import random

from .models import Analysis, AnalysisItem, Point

# A component library. Each entry: (name, kcal, protein_g, carbs_g, fat_g) for a
# typical serving. The mock composes a plate from a main + a few sides.
# Values are per-serving estimates; the plate's totals are the sum of picked items.
_MAINS = [
    ("Dal tadka", 180, 9, 22, 6),
    ("Rajma masala", 210, 11, 28, 6),
    ("Chana masala", 220, 11, 30, 7),
    ("Paneer butter masala", 340, 14, 14, 26),
    ("Palak paneer", 300, 15, 12, 22),
    ("Chicken curry", 280, 26, 8, 16),
    ("Egg curry (2 eggs)", 260, 16, 9, 18),
    ("Fish curry", 240, 24, 7, 12),
    ("Mixed veg sabzi", 160, 5, 18, 8),
    ("Aloo gobi", 190, 5, 24, 9),
    ("Bhindi masala", 170, 4, 16, 10),
    ("Chicken biryani", 520, 24, 62, 18),
    ("Veg biryani", 460, 10, 70, 14),
    ("Chole", 250, 12, 34, 7),
]

# Common carbohydrate bases (the "with rice/roti" part).
_BASES = [
    ("Steamed rice", 210, 4, 45, 1),
    ("Jeera rice", 240, 4, 46, 5),
    ("Roti x2", 220, 7, 40, 4),
    ("Roti x3", 330, 11, 60, 6),
    ("Naan", 280, 8, 48, 6),
    ("Paratha", 260, 6, 36, 11),
]

# Small sides that round out a real Indian plate.
_SIDES = [
    ("Curd", 90, 5, 7, 5),
    ("Raita", 80, 4, 8, 4),
    ("Green salad", 35, 2, 6, 1),
    ("Pickle", 30, 0, 3, 2),
    ("Papad", 60, 3, 8, 2),
    ("Roasted papad", 45, 2, 7, 1),
    ("Gulab jamun (1)", 150, 2, 24, 6),
    ("Boondi", 90, 1, 12, 4),
]

# Special one-shot plates that itemize their own way.
_SPECIAL_PLATES = [
    # thali: many katoris
    [("Dal fry", 180, 9, 22, 6), ("Steamed rice", 200, 4, 43, 1), ("Roti x2", 220, 7, 40, 4),
     ("Mixed veg sabzi", 150, 5, 17, 7), ("Curd", 90, 5, 7, 5), ("Pickle", 30, 0, 3, 2),
     ("Papad", 60, 3, 8, 2)],
    # masala dosa
    [("Masala dosa", 380, 8, 52, 15), ("Sambar", 120, 6, 18, 3), ("Coconut chutney", 90, 2, 6, 7),
     ("Tomato chutney", 45, 1, 8, 1)],
    # idli
    [("Idli (3)", 220, 8, 44, 2), ("Sambar", 120, 6, 18, 3), ("Coconut chutney", 90, 2, 6, 7)],
    # chole bhature
    [("Chole", 260, 12, 34, 8), ("Bhature (2)", 420, 9, 52, 20), ("Onion salad", 30, 1, 6, 0),
     ("Pickle", 30, 0, 3, 2)],
]

_PORTION_MULT = {"small": 0.75, "regular": 1.0, "large": 1.3}

# High-calorie clarifiers, asked when the plate has an ambiguous rich component.
_QUESTIONS = [
    "Was there ghee brushed on the roti or rice?",
    "Was that paneer, or tofu/soy paneer?",
    "Was the rice a small katori or a large heaped serving?",
    "Was the gravy cream-based or a lighter tomato base?",
    "How much oil/ghee was the sabzi cooked in?",
]

# Components that make the plate "rich/ambiguous" -> worth a clarifying question.
_RICH_MARKERS = ("paneer", "ghee", "butter", "biryani", "naan", "paratha", "bhature", "cream")


def _seed(image_bytes: bytes, hint: str) -> int:
    h = hashlib.sha256()
    h.update(image_bytes)
    h.update(b"\x00")
    h.update(hint.encode("utf-8"))
    return int.from_bytes(h.digest()[:8], "big")


def _plate_points(n: int, rng: random.Random) -> list[Point]:
    """Plausible, spread-out normalized centers for `n` plate components.

    Components are placed on a ring around the plate center so the app's
    floating item-chips fan out without overlapping. Small jitter keeps it
    from looking mechanical; every point stays inside 0..1 (API.md
    "Complex/Indian meals").
    """
    import math

    cx, cy = 0.5, 0.52
    radius = 0.26 if n > 1 else 0.0
    start = rng.uniform(0.0, 2 * math.pi)
    pts: list[Point] = []
    for i in range(n):
        if n == 1:
            x, y = cx, cy
        else:
            ang = start + (2 * math.pi * i) / n
            jx = (rng.random() - 0.5) * 0.06
            jy = (rng.random() - 0.5) * 0.06
            x = cx + radius * math.cos(ang) + jx
            y = cy + radius * math.sin(ang) * 0.85 + jy  # squash vertically (plate is wider)
        x = round(min(0.96, max(0.04, x)), 3)
        y = round(min(0.96, max(0.06, y)), 3)
        pts.append(Point(x=x, y=y))
    return pts


def _est_nutrients(name: str, carbs_g: int, fat_g: int) -> tuple[int, int, int]:
    """Plausible fiber/sugar/sodium for a mock item, derived from its carbs/fat
    and name markers (sweets are sugary, pickles/papad salty). Deterministic —
    no RNG — so the same plate always yields the same nutrient extras."""
    low = name.lower()
    # Fiber ~ a fraction of carbs (more for dals/veg/salad, less for rice/sweets).
    fiber_ratio = 0.10
    if any(k in low for k in ("dal", "rajma", "chana", "chole", "sabzi", "salad", "gobi", "bhindi", "palak", "sambar")):
        fiber_ratio = 0.22
    elif any(k in low for k in ("rice", "naan", "gulab", "jamun", "boondi", "bhature")):
        fiber_ratio = 0.04
    fiber = max(0, int(round(carbs_g * fiber_ratio)))
    # Sugar: high for sweets, modest for gravies/curd, low otherwise.
    if any(k in low for k in ("gulab", "jamun", "boondi", "raita", "curd")):
        sugar = max(0, int(round(carbs_g * 0.5)))
    elif any(k in low for k in ("masala", "curry", "butter", "chutney", "salan")):
        sugar = max(0, int(round(carbs_g * 0.15)))
    else:
        sugar = max(0, int(round(carbs_g * 0.05)))
    # Sodium (mg): baseline plus extra for salty/pickled items and rich gravies.
    sodium = 180 + fat_g * 8
    if any(k in low for k in ("pickle", "papad", "salan", "masala", "curry")):
        sodium += 260
    return fiber, sugar, sodium


def _scaled_item(comp: tuple, mult: float, rng: random.Random, point: Point | None = None) -> AnalysisItem:
    name, kcal, p, c, f = comp
    variation = 1.0 + (rng.random() - 0.5) * 0.16  # +/-8%
    m = mult * variation
    carbs_g = max(0, round(c * m))
    fat_g = max(0, round(f * m))
    fiber_g, sugar_g, sodium_mg = _est_nutrients(name, carbs_g, fat_g)
    return AnalysisItem(
        name=name,
        calories=max(0, int(round(kcal * m / 5) * 5)),
        protein_g=max(0, round(p * m)),
        carbs_g=carbs_g,
        fat_g=fat_g,
        fiber_g=fiber_g,
        sugar_g=sugar_g,
        sodium_mg=sodium_mg,
        point=point,
    )


def _compose_plate(rng: random.Random, hint: str) -> list[tuple]:
    """Pick the raw (unscaled) component tuples that make up the plate."""
    low = hint.lower()

    # A hint may steer us to a special plate.
    if "thali" in low:
        return list(_SPECIAL_PLATES[0])
    if "dosa" in low:
        return list(_SPECIAL_PLATES[1])
    if "idli" in low:
        return list(_SPECIAL_PLATES[2])
    if "chole" in low or "bhature" in low:
        return list(_SPECIAL_PLATES[3])

    # 1 in 4 plates is a special itemized plate for variety.
    if rng.random() < 0.25:
        return list(rng.choice(_SPECIAL_PLATES))

    # Otherwise: main + base + 1-3 sides -> a 3-5 item plate.
    main = rng.choice(_MAINS)
    # Biryani is a complete carb+protein dish; pair it with raita not rice.
    if "biryani" in main[0].lower():
        plate = [main, ("Raita", 80, 4, 8, 4)]
        if rng.random() < 0.6:
            plate.append(rng.choice([("Salan", 90, 2, 8, 6), ("Green salad", 35, 2, 6, 1)]))
        return plate

    base = rng.choice(_BASES)
    plate = [main, base]
    n_sides = rng.choice([1, 2, 2, 3])
    chosen = rng.sample(_SIDES, k=min(n_sides, len(_SIDES)))
    plate.extend(chosen)
    return plate


def analyze_mock(image_bytes: bytes, hint: str = "", portion: str = "regular") -> Analysis:
    rng = random.Random(_seed(image_bytes, hint))
    mult = _PORTION_MULT.get(portion, 1.0)

    raw_components = _compose_plate(rng, hint)
    points = _plate_points(len(raw_components), rng)
    items = [_scaled_item(c, mult, rng, pt) for c, pt in zip(raw_components, points)]

    calories = sum(i.calories for i in items)
    protein_g = sum(i.protein_g for i in items)
    carbs_g = sum(i.carbs_g for i in items)
    fat_g = sum(i.fat_g for i in items)
    fiber_g = sum(i.fiber_g or 0 for i in items)
    sugar_g = sum(i.sugar_g or 0 for i in items)
    sodium_mg = sum(i.sodium_mg or 0 for i in items)

    # Dish label: main component + "& sides" when there is more than one item.
    main_name = items[0].name
    dish = main_name if len(items) == 1 else f"{main_name} thali" if len(items) >= 5 else f"{main_name} & sides"

    # Ask a clarifying question when the plate has a rich/ambiguous component.
    plate_text = " ".join(i.name.lower() for i in items)
    is_rich = any(marker in plate_text for marker in _RICH_MARKERS)
    if is_rich:
        confidence = round(rng.uniform(0.66, 0.83), 2)
        question = rng.choice(_QUESTIONS)
    else:
        confidence = round(rng.uniform(0.85, 0.94), 2)
        question = None

    return Analysis(
        dish=dish,
        calories=calories,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        fiber_g=fiber_g,
        sugar_g=sugar_g,
        sodium_mg=sodium_mg,
        confidence=confidence,
        question=question,
        items=items,
    )
