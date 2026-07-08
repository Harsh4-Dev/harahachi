"""Suggestion engine: mines a user's meal history for repeatable meals that
fit the remaining calorie/macro budget for the day.

v2: history is keyed by ``user_id`` (accounts own the data now), not device.
"""
from __future__ import annotations

from collections import defaultdict

from . import db
from .models import Suggestion, Targets


def _history_candidates(user_id: int) -> list[dict]:
    """Aggregate past meals into distinct dishes (averaged macros, frequency)."""
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT dish, calories, protein_g, carbs_g, fat_g FROM meals "
            "WHERE user_id=? ORDER BY eaten_at DESC LIMIT 400",
            (user_id,),
        ).fetchall()

    grouped: dict[str, list] = defaultdict(list)
    for r in rows:
        grouped[r["dish"].strip().lower()].append(r)

    candidates = []
    for _key, group in grouped.items():
        n = len(group)
        avg = lambda f: round(sum(g[f] for g in group) / n)  # noqa: E731
        candidates.append(
            {
                "dish": group[0]["dish"],
                "calories": avg("calories"),
                "protein_g": avg("protein_g"),
                "carbs_g": avg("carbs_g"),
                "fat_g": avg("fat_g"),
                "count": n,
            }
        )
    return candidates


def _fit_score(cand: dict, remaining: Targets) -> float:
    """Lower is better. Penalize overshooting remaining calories heavily,
    reward covering remaining protein, favor frequently-eaten meals."""
    cal_over = max(0, cand["calories"] - max(remaining.calories, 0))
    cal_fit = abs(cand["calories"] - max(remaining.calories, 0))
    protein_gap = max(0, remaining.protein_g - cand["protein_g"])
    score = cal_fit + cal_over * 1.5 + protein_gap * 2.0
    score -= min(cand["count"], 5) * 15  # frequency bonus
    return score


def rank_candidates(user_id: int, remaining: Targets, limit: int = 4) -> list[dict]:
    cands = _history_candidates(user_id)
    if not cands:
        return []
    # Prefer meals that don't blow the remaining calorie budget by a lot.
    ranked = sorted(cands, key=lambda c: _fit_score(c, remaining))
    return ranked[:limit]


def _reason(cand: dict, remaining: Targets) -> str:
    bits = []
    if remaining.calories > 0:
        left = remaining.calories - cand["calories"]
        if left >= 0:
            bits.append(f"fits your remaining {remaining.calories} kcal with {left} to spare")
        else:
            bits.append(f"about {cand['calories']} kcal, a touch over your remaining {remaining.calories}")
    else:
        bits.append(f"{cand['calories']} kcal — you're already near your target")
    if cand["protein_g"] >= max(1, remaining.protein_g * 0.4):
        bits.append(f"brings {cand['protein_g']}g protein")
    if cand.get("count", 1) > 1:
        bits.append(f"you've had it {cand['count']}x before")
    return "From your past travels: " + ", ".join(bits) + "."


def rule_based_suggestions(user_id: int, remaining: Targets, limit: int = 4) -> list[Suggestion]:
    ranked = rank_candidates(user_id, remaining, limit)
    out = []
    for c in ranked:
        out.append(
            Suggestion(
                dish=c["dish"],
                calories=c["calories"],
                protein_g=c["protein_g"],
                carbs_g=c["carbs_g"],
                fat_g=c["fat_g"],
                reason=_reason(c, remaining),
            )
        )
    return out
