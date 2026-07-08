"""BMR/TDEE and macro target computation (see API.md /profile + v3 pace).

Base BMR selection is unchanged from v2: Katch-McArdle when ``body_fat_pct`` is
set, else Mifflin-St Jeor, with the same activity factor. What changed in v3 is
how the goal deficit/surplus is applied:

- When the profile has BOTH ``pace_kg_per_week`` (> 0) AND ``weight_goal_kg``,
  and that goal points the same direction as ``goal`` (cut → lose, bulk → gain),
  the daily calorie delta is derived from the pace: 7700 kcal ≈ 1 kg, so
  ``delta = pace * 7700 / 7`` kcal/day, subtracted for a cut and added for a
  bulk. The result is clamped to at least 1200 kcal.
- Otherwise the legacy flat ±20% (cut) / +10% (bulk) is used.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .models import ProfileIn, Targets

ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
}

GOAL_ADJUST = {
    "cut": -0.20,
    "maintain": 0.0,
    "bulk": 0.10,
}

KCAL_PER_KG = 7700.0          # energy content of ~1 kg body mass
MIN_CALORIES = 1200.0        # hard floor for pace-based targets (API.md v3)


def _bmr(p: ProfileIn) -> float:
    """Basal metabolic rate.

    When ``body_fat_pct`` is set, use **Katch-McArdle** (lean-mass based):
        BMR = 370 + 21.6 * lean_mass_kg,  lean = weight * (1 - bf%/100)
    Otherwise **Mifflin-St Jeor**:
        BMR = 10*kg + 6.25*cm - 5*age + s   (s = +5 m / -161 f / -78 x)
    """
    if p.body_fat_pct is not None:
        lean_mass = p.weight_kg * (1.0 - p.body_fat_pct / 100.0)
        return 370.0 + 21.6 * lean_mass
    if p.sex == "m":
        s = 5
    elif p.sex == "f":
        s = -161
    else:
        s = -78  # average of the two for non-binary / unspecified
    return 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age + s


def _goal_direction(p: ProfileIn) -> int:
    """+1 to gain, -1 to lose, 0 to hold — inferred from goal."""
    if p.goal == "cut":
        return -1
    if p.goal == "bulk":
        return 1
    return 0


def _pace_applies(p: ProfileIn) -> bool:
    """True when the pace-based calorie delta should replace the flat %.

    Requires a positive pace, a set weight goal, and a goal that isn't
    'maintain' whose direction agrees with where the goal weight sits relative
    to current weight.
    """
    if not p.pace_kg_per_week or p.pace_kg_per_week <= 0:
        return False
    if p.weight_goal_kg is None:
        return False
    direction = _goal_direction(p)
    if direction == 0:
        return False
    # Goal weight must be on the correct side of current weight.
    if direction < 0 and p.weight_goal_kg >= p.weight_kg:
        return False
    if direction > 0 and p.weight_goal_kg <= p.weight_kg:
        return False
    return True


def compute_targets(p: ProfileIn) -> Targets:
    """Compute daily kcal + macro targets. BMR via Katch-McArdle when body-fat
    is known, else Mifflin-St Jeor; both use the same activity modifier and
    protein at 1.6 g/kg bodyweight.

    Goal delta: when a pace + goal weight are set (v3) the deficit/surplus is
    computed from the pace (7700 kcal/kg) and clamped to >=1200 kcal; otherwise
    the flat cut/bulk percentages apply."""
    bmr = _bmr(p)
    tdee = bmr * ACTIVITY_FACTORS.get(p.activity, 1.2)

    if _pace_applies(p):
        delta = p.pace_kg_per_week * KCAL_PER_KG / 7.0
        calories = tdee + _goal_direction(p) * delta
        calories = max(MIN_CALORIES, calories)
    else:
        calories = tdee * (1 + GOAL_ADJUST.get(p.goal, 0.0))
        calories = max(1000.0, calories)

    # Protein 1.6 g/kg bodyweight.
    protein_g = round(1.6 * p.weight_kg)
    protein_kcal = protein_g * 4

    # Fat ~25% of calories.
    fat_kcal = calories * 0.25
    fat_g = round(fat_kcal / 9)

    # Remaining calories → carbs.
    carbs_kcal = max(0.0, calories - protein_kcal - fat_kcal)
    carbs_g = round(carbs_kcal / 4)

    return Targets(
        calories=round(calories),
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
    )


def eta_date(p: ProfileIn, from_date: date | None = None) -> str | None:
    """Projected date (YYYY-MM-DD) to reach ``weight_goal_kg`` at the current
    pace, or None when not applicable.

    Needs a positive pace and a goal weight that differs from current weight.
    weeks_to_go = |goal - current| / pace, then add that many days from today.
    Returns None (already there) when the remaining distance rounds to 0.
    """
    if not p.pace_kg_per_week or p.pace_kg_per_week <= 0:
        return None
    if p.weight_goal_kg is None:
        return None
    remaining_kg = abs(p.weight_goal_kg - p.weight_kg)
    if remaining_kg < 0.05:
        return None
    weeks = remaining_kg / p.pace_kg_per_week
    days = int(round(weeks * 7.0))
    start = from_date or datetime.now(timezone.utc).date()
    return (start + timedelta(days=days)).isoformat()
