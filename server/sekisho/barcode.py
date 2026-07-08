"""Packaged-food barcode lookup via OpenFoodFacts, with SQLite caching.

Flow (see API.md `GET /food/barcode/{code}`):
  1. Serve from the `food_barcodes` cache when present (positive OR negative hit).
  2. Otherwise query OpenFoodFacts v2, normalize to our schema, cache, return.
  3. On a network failure with no cache: emit a deterministic mock product when
     mock is enabled, else report `found:false`.

Normalization rules:
  - `per_100g` comes straight from the `_100g` nutriment fields.
  - `serving` is the product's `serving_size` string (e.g. "70 g").
  - `per_serving` uses OFF's `_serving` fields when present; otherwise it is
    computed from `per_100g` scaled by the parsed grams in `serving_size`.
"""
from __future__ import annotations

import hashlib
import json
import re

import httpx

from .config import config
from .db import cursor
from .models import BarcodeOut, BarcodeProduct, MacroSet
from .security import now_iso

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
# Only pull the fields we need — smaller, faster response.
OFF_FIELDS = "product_name,brands,serving_size,nutriments"
_TIMEOUT = 8.0


# --------------------------------------------------------------------------- #
# Parsing / normalization helpers
# --------------------------------------------------------------------------- #
def _num(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _grams_from_serving(serving_size: str) -> float | None:
    """Extract a gram/ml weight from strings like '70 g', '250ml', '1 bar (21 g)'."""
    if not serving_size:
        return None
    # Prefer a value in parentheses: "1 bar (21 g)" -> 21
    paren = re.search(r"\(([^)]*)\)", serving_size)
    candidates = [paren.group(1)] if paren else []
    candidates.append(serving_size)
    for text in candidates:
        m = re.search(r"([\d.]+)\s*(?:g|ml)\b", text, re.IGNORECASE)
        if m:
            g = _num(m.group(1))
            if g:
                return g
    return None


def _macroset(cal, prot, carb, fat, fiber=None, sugar=None, sodium=None) -> MacroSet | None:
    if all(v is None for v in (cal, prot, carb, fat, fiber, sugar, sodium)):
        return None
    return MacroSet(
        calories=int(round(cal or 0)),
        protein_g=int(round(prot or 0)),
        carbs_g=int(round(carb or 0)),
        fat_g=int(round(fat or 0)),
        fiber_g=int(round(fiber)) if fiber is not None else None,
        sugar_g=int(round(sugar)) if sugar is not None else None,
        sodium_mg=int(round(sodium)) if sodium is not None else None,
    )


def _kcal(nutriments: dict, suffix: str) -> float | None:
    """Energy in kcal for a given field suffix ('_100g' or '_serving')."""
    kcal = _num(nutriments.get(f"energy-kcal{suffix}"))
    if kcal is not None:
        return kcal
    # Some products only report kJ; convert.
    kj = _num(nutriments.get(f"energy-kj{suffix}")) or _num(nutriments.get(f"energy{suffix}"))
    if kj is not None:
        return kj / 4.184
    return None


def _sodium_mg(nutriments: dict, suffix: str) -> float | None:
    """Sodium in mg. OFF reports sodium/salt in grams; salt = sodium * 2.5."""
    sodium_g = _num(nutriments.get(f"sodium{suffix}"))
    if sodium_g is not None:
        return sodium_g * 1000.0
    salt_g = _num(nutriments.get(f"salt{suffix}"))
    if salt_g is not None:
        return (salt_g / 2.5) * 1000.0
    return None


def normalize_off_product(code: str, product: dict) -> BarcodeProduct:
    n = product.get("nutriments") or {}
    serving = (product.get("serving_size") or "").strip()

    per_100g = _macroset(
        _kcal(n, "_100g"),
        _num(n.get("proteins_100g")),
        _num(n.get("carbohydrates_100g")),
        _num(n.get("fat_100g")),
        _num(n.get("fiber_100g")),
        _num(n.get("sugars_100g")),
        _sodium_mg(n, "_100g"),
    )
    per_serving = _macroset(
        _kcal(n, "_serving"),
        _num(n.get("proteins_serving")),
        _num(n.get("carbohydrates_serving")),
        _num(n.get("fat_serving")),
        _num(n.get("fiber_serving")),
        _num(n.get("sugars_serving")),
        _sodium_mg(n, "_serving"),
    )

    # Compute per_serving from per_100g x serving grams when OFF didn't give it.
    if per_serving is None and per_100g is not None:
        grams = _grams_from_serving(serving)
        if grams:
            f = grams / 100.0

            def _scale(v):
                return int(round(v * f)) if v is not None else None

            per_serving = MacroSet(
                calories=int(round(per_100g.calories * f)),
                protein_g=int(round(per_100g.protein_g * f)),
                carbs_g=int(round(per_100g.carbs_g * f)),
                fat_g=int(round(per_100g.fat_g * f)),
                fiber_g=_scale(per_100g.fiber_g),
                sugar_g=_scale(per_100g.sugar_g),
                sodium_mg=_scale(per_100g.sodium_mg),
            )

    brand = (product.get("brands") or "").split(",")[0].strip()
    name = (product.get("product_name") or "").strip() or f"Product {code}"
    return BarcodeProduct(
        name=name, brand=brand, serving=serving,
        per_serving=per_serving, per_100g=per_100g,
    )


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def _cache_get(code: str) -> BarcodeOut | None:
    with cursor() as cur:
        row = cur.execute(
            "SELECT found, payload, source FROM food_barcodes WHERE code=?", (code,)
        ).fetchone()
    if row is None:
        return None
    if not row["found"]:
        return BarcodeOut(found=False, code=code, source="cache")
    product = BarcodeProduct.model_validate_json(row["payload"])
    return BarcodeOut(found=True, code=code, product=product, source="cache")


def _cache_put(code: str, found: bool, product: BarcodeProduct | None, source: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO food_barcodes (code, found, payload, source, fetched_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET found=excluded.found, payload=excluded.payload, "
            "source=excluded.source, fetched_at=excluded.fetched_at",
            (
                code,
                1 if found else 0,
                product.model_dump_json() if product else None,
                source,
                now_iso(),
            ),
        )


# --------------------------------------------------------------------------- #
# Deterministic mock (no internet + mock enabled)
# --------------------------------------------------------------------------- #
def mock_product(code: str) -> BarcodeProduct:
    """A plausible, deterministic packaged product keyed off the barcode digits."""
    seed = int.from_bytes(hashlib.sha256(code.encode()).digest()[:8], "big")
    names = [
        ("Masala Instant Noodles", "Maggi", 70),
        ("Digestive Biscuits", "Britannia", 16),
        ("Roasted Chana", "Haldiram's", 30),
        ("Protein Bar", "Yoga Bar", 60),
        ("Peanut Butter", "Pintola", 32),
        ("Multigrain Chips", "Too Yumm", 27),
        ("Coconut Water", "Paper Boat", 200),
        ("Muesli", "Bagrry's", 40),
    ]
    name, brand, grams = names[seed % len(names)]
    # per-100g values, biased into a realistic packaged-snack range.
    kcal100 = 380 + (seed >> 3) % 160          # 380..540
    prot100 = 6 + (seed >> 7) % 18             # 6..24
    carb100 = 45 + (seed >> 11) % 30           # 45..75
    fat100 = 12 + (seed >> 15) % 20            # 12..32
    fiber100 = 2 + (seed >> 19) % 8            # 2..10
    sugar100 = 5 + (seed >> 23) % 25           # 5..30
    sodium100 = 200 + (seed >> 27) % 600       # 200..800 mg
    per_100g = MacroSet(calories=kcal100, protein_g=prot100, carbs_g=carb100, fat_g=fat100,
                        fiber_g=fiber100, sugar_g=sugar100, sodium_mg=sodium100)
    f = grams / 100.0
    per_serving = MacroSet(
        calories=int(round(kcal100 * f)),
        protein_g=int(round(prot100 * f)),
        carbs_g=int(round(carb100 * f)),
        fat_g=int(round(fat100 * f)),
        fiber_g=int(round(fiber100 * f)),
        sugar_g=int(round(sugar100 * f)),
        sodium_mg=int(round(sodium100 * f)),
    )
    return BarcodeProduct(
        name=name, brand=brand, serving=f"{grams} g",
        per_serving=per_serving, per_100g=per_100g,
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def lookup(code: str) -> BarcodeOut:
    code = code.strip()

    cached = _cache_get(code)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(
                OFF_URL.format(code=code),
                params={"fields": OFF_FIELDS},
                headers={"User-Agent": "Harahachi-Sekisho/1.0 (barcode lookup)"},
            )
            data = r.json()
    except Exception:
        # No internet / OFF unreachable. Deterministic mock when allowed; else miss.
        if config.allow_mock:
            product = mock_product(code)
            _cache_put(code, True, product, "mock")
            return BarcodeOut(found=True, code=code, product=product, source="mock")
        return BarcodeOut(found=False, code=code, source=None)

    if data.get("status") == 1 and data.get("product"):
        product = normalize_off_product(code, data["product"])
        _cache_put(code, True, product, "openfoodfacts")
        return BarcodeOut(found=True, code=code, product=product, source="openfoodfacts")

    # Known-unknown: cache the negative hit so repeat scans are cheap.
    _cache_put(code, False, None, "openfoodfacts")
    return BarcodeOut(found=False, code=code, source="openfoodfacts")
