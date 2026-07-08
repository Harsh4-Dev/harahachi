"""v3 feedback-loop helpers: weight trend, water, streaks, insights, export.

Kept separate from app.py so the route handlers stay thin. All functions are
user-scoped (``user_id``) and read/write through ``db.cursor()``.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from . import db
from .models import ProfileIn
from .nutrition import compute_targets

WATER_ML_PER_KG = 35.0
WATER_MIN_ML = 2000
STREAK_MIN_FRACTION = 0.5  # a day must reach >=50% of target to earn its stamp


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _profile_in(user_id: int) -> ProfileIn | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return None
    keys = row.keys()
    return ProfileIn(
        name=row["name"] or "", age=row["age"], sex=row["sex"], height_cm=row["height_cm"],
        weight_kg=row["weight_kg"], activity=row["activity"], goal=row["goal"],
        body_fat_pct=row["body_fat_pct"], body_fat_goal_pct=row["body_fat_goal_pct"],
        weight_goal_kg=row["weight_goal_kg"] if "weight_goal_kg" in keys else None,
        pace_kg_per_week=row["pace_kg_per_week"] if "pace_kg_per_week" in keys else None,
    )


def target_calories(user_id: int) -> int | None:
    p = _profile_in(user_id)
    return compute_targets(p).calories if p else None


# ---------------------------------------------------------------------------
# Water (35 ml/kg, min 2000)
# ---------------------------------------------------------------------------
def water_target_ml(user_id: int) -> int:
    p = _profile_in(user_id)
    if p is None:
        return WATER_MIN_ML
    return max(WATER_MIN_ML, int(round(p.weight_kg * WATER_ML_PER_KG)))


def water_for_day(user_id: int, day: str) -> int:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT water_ml FROM water_log WHERE user_id=? AND date=?", (user_id, day)
        ).fetchone()
    return int(row["water_ml"]) if row else 0


def add_water(user_id: int, ml: int, day: str | None = None) -> int:
    """Add ``ml`` (may be negative to undo) to the day's total, floored at 0.
    Returns the new total."""
    day = day or _today()
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT water_ml FROM water_log WHERE user_id=? AND date=?", (user_id, day)
        ).fetchone()
        current = int(row["water_ml"]) if row else 0
        new_total = max(0, current + int(ml))
        cur.execute(
            "INSERT INTO water_log (user_id, date, water_ml, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, date) DO UPDATE SET water_ml=excluded.water_ml, "
            "updated_at=excluded.updated_at",
            (user_id, day, new_total, now),
        )
    return new_total


# ---------------------------------------------------------------------------
# Weight + 7-day moving-average trend
# ---------------------------------------------------------------------------
def log_weight(user_id: int, weight_kg: float, day: str | None = None) -> None:
    day = day or _today()
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO weights (user_id, date, weight_kg, logged_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, date) DO UPDATE SET weight_kg=excluded.weight_kg, "
            "logged_at=excluded.logged_at",
            (user_id, day, float(weight_kg), now),
        )


def _weight_rows(user_id: int, frm: str | None, to: str | None) -> list[dict]:
    q = "SELECT date, weight_kg FROM weights WHERE user_id=?"
    params: list = [user_id]
    if frm:
        q += " AND date>=?"
        params.append(frm)
    if to:
        q += " AND date<=?"
        params.append(to)
    q += " ORDER BY date ASC"
    with db.cursor() as cur:
        return [{"date": r["date"], "weight_kg": r["weight_kg"]} for r in cur.execute(q, params)]


def weight_history(user_id: int, frm: str | None = None, to: str | None = None) -> dict:
    """Return {entries, trend} where trend is the 7-day trailing moving average
    over the returned entries (window = the entry plus up to 6 prior entries)."""
    entries = _weight_rows(user_id, frm, to)
    trend: list[dict] = []
    window: list[float] = []
    for e in entries:
        window.append(e["weight_kg"])
        if len(window) > 7:
            window.pop(0)
        avg = sum(window) / len(window)
        trend.append({"date": e["date"], "avg_kg": round(avg, 2)})
    return {"entries": entries, "trend": trend}


# ---------------------------------------------------------------------------
# Streaks + stamp book
# ---------------------------------------------------------------------------
def _daily_calories(user_id: int) -> dict[str, int]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT substr(eaten_at,1,10) d, SUM(calories) c FROM meals "
            "WHERE user_id=? GROUP BY d ORDER BY d",
            (user_id,),
        ).fetchall()
    return {r["d"]: int(r["c"]) for r in rows}


def _is_on_target(cals: int, target: int) -> bool:
    """A day earns its stamp when calories land <= target AND >= 50% of it."""
    if target <= 0:
        return False
    return cals <= target and cals >= STREAK_MIN_FRACTION * target


def streaks(user_id: int) -> dict:
    """Compute current + best on-target streaks and the stamp book.

    Stamp kinds: first_log (earliest logged day), on_target (a qualifying day),
    milestone_7 / milestone_30 (a qualifying day that is the 7th/30th consecutive
    on-target day). The current streak counts consecutive on-target days ending
    today (or yesterday if today isn't logged/on-target)."""
    target = target_calories(user_id) or 0
    by_day = _daily_calories(user_id)
    all_days = sorted(by_day.keys())

    stamps: list[dict] = []
    if all_days:
        stamps.append({"date": all_days[0], "kind": "first_log"})

    # Walk chronologically, tracking the run length so milestones land on the
    # exact 7th/30th consecutive on-target day.
    run = 0
    prev: date | None = None
    on_target_days: list[str] = []
    for d in all_days:
        cur_date = date.fromisoformat(d)
        on = _is_on_target(by_day[d], target)
        if on:
            if prev is not None and (cur_date - prev).days == 1 and run > 0:
                run += 1
            else:
                run = 1
            on_target_days.append(d)
            kind = "on_target"
            if run == 7:
                kind = "milestone_7"
            elif run == 30:
                kind = "milestone_30"
            stamps.append({"date": d, "kind": kind})
            prev = cur_date
        else:
            run = 0
            prev = cur_date

    # Best streak = longest consecutive on-target run.
    best = 0
    cur_run = 0
    prev = None
    for d in on_target_days:
        cur_date = date.fromisoformat(d)
        if prev is not None and (cur_date - prev).days == 1:
            cur_run += 1
        else:
            cur_run = 1
        best = max(best, cur_run)
        prev = cur_date

    # Current streak = consecutive on-target days ending today or yesterday.
    on_set = set(on_target_days)
    today = datetime.now(timezone.utc).date()
    anchor = today if today.isoformat() in on_set else today - timedelta(days=1)
    current = 0
    cursor_d = anchor
    while cursor_d.isoformat() in on_set:
        current += 1
        cursor_d -= timedelta(days=1)

    return {"current": current, "best": best, "stamps": stamps}


# ---------------------------------------------------------------------------
# Weekly insights
# ---------------------------------------------------------------------------
def week_bounds(any_day: str) -> tuple[str, str]:
    """Monday..Sunday bounds (inclusive) for the ISO week containing any_day."""
    d = date.fromisoformat(any_day)
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def insights(user_id: int, any_day: str) -> dict:
    """Compute the week's averages, adherence, best/worst day, weight delta and
    macro split. Returns a dict WITHOUT the summary — the caller adds that (LLM
    in Annai's voice, or the rule-based fallback in ``insights_summary``)."""
    start, end = week_bounds(any_day)
    target = target_calories(user_id) or 0

    with db.cursor() as cur:
        meal_rows = cur.execute(
            "SELECT substr(eaten_at,1,10) d, SUM(calories) c, SUM(protein_g) p, "
            "SUM(carbs_g) cb, SUM(fat_g) f FROM meals "
            "WHERE user_id=? AND substr(eaten_at,1,10)>=? AND substr(eaten_at,1,10)<=? "
            "GROUP BY d", (user_id, start, end),
        ).fetchall()
        water_rows = cur.execute(
            "SELECT date, water_ml FROM water_log WHERE user_id=? AND date>=? AND date<=?",
            (user_id, start, end),
        ).fetchall()

    by_day = {r["d"]: dict(r) for r in meal_rows}
    water_by_day = {r["date"]: int(r["water_ml"]) for r in water_rows}
    logged_days = sorted(by_day.keys())
    n = len(logged_days)

    def _avg(field: str) -> float:
        if not n:
            return 0.0
        return sum(int(by_day[d][field] or 0) for d in logged_days) / n

    avg_cal = _avg("c")
    avg_p = _avg("p")
    avg_cb = _avg("cb")
    avg_f = _avg("f")
    avg_water = (sum(water_by_day.get(d, 0) for d in logged_days) / n) if n else 0.0

    # Adherence: fraction of logged days that were on-target.
    on_days = [d for d in logged_days if _is_on_target(int(by_day[d]["c"] or 0), target)]
    adherence = round(100.0 * len(on_days) / n, 1) if n else 0.0

    # Best/worst day by closeness to target (best = closest under; worst = furthest off).
    best_day = worst_day = None
    if n and target > 0:
        def _dist(d: str) -> float:
            return abs(int(by_day[d]["c"] or 0) - target)
        best_day = min(logged_days, key=_dist)
        worst_day = max(logged_days, key=_dist)
    elif n:
        best_day = logged_days[0]
        worst_day = logged_days[-1]

    # Weight delta across the week (last logged - first logged in range).
    wrows = _weight_rows(user_id, start, end)
    weight_delta = round(wrows[-1]["weight_kg"] - wrows[0]["weight_kg"], 2) if len(wrows) >= 2 else None

    # Macro split % of calories (protein/carbs *4, fat *9).
    p_kcal, c_kcal, f_kcal = avg_p * 4, avg_cb * 4, avg_f * 9
    macro_kcal = p_kcal + c_kcal + f_kcal
    if macro_kcal > 0:
        macro_split = {
            "protein": round(100 * p_kcal / macro_kcal, 1),
            "carbs": round(100 * c_kcal / macro_kcal, 1),
            "fat": round(100 * f_kcal / macro_kcal, 1),
        }
    else:
        macro_split = {"protein": 0.0, "carbs": 0.0, "fat": 0.0}

    return {
        "week_start": start,
        "week_end": end,
        "days_logged": n,
        "target_calories": target,
        "averages": {
            "calories": round(avg_cal),
            "protein_g": round(avg_p),
            "carbs_g": round(avg_cb),
            "fat_g": round(avg_f),
            "water_ml": round(avg_water),
        },
        "adherence_pct": adherence,
        "best_day": best_day,
        "worst_day": worst_day,
        "weight_delta_kg": weight_delta,
        "macro_split_pct": macro_split,
    }


def weekly_adherence(user_id: int, weeks: int = 8) -> list[dict]:
    """Adherence % + average calories for each of the last ``weeks`` ISO weeks
    (oldest first). Powers the per-user admin adherence mini-chart."""
    target = target_calories(user_id) or 0
    today = date.fromisoformat(_today())
    this_monday = today - timedelta(days=today.weekday())
    out: list[dict] = []
    for i in range(weeks - 1, -1, -1):
        monday = this_monday - timedelta(weeks=i)
        sunday = monday + timedelta(days=6)
        with db.cursor() as cur:
            rows = cur.execute(
                "SELECT substr(eaten_at,1,10) d, SUM(calories) c FROM meals "
                "WHERE user_id=? AND substr(eaten_at,1,10)>=? AND substr(eaten_at,1,10)<=? "
                "GROUP BY d", (user_id, monday.isoformat(), sunday.isoformat()),
            ).fetchall()
        by_day = {r["d"]: int(r["c"] or 0) for r in rows}
        n = len(by_day)
        on = sum(1 for c in by_day.values() if _is_on_target(c, target))
        adherence = round(100.0 * on / n, 1) if n else 0.0
        avg_cal = round(sum(by_day.values()) / n) if n else 0
        out.append({
            "week_start": monday.isoformat(),
            "days_logged": n,
            "adherence_pct": adherence,
            "avg_calories": avg_cal,
        })
    return out


def insights_summary(data: dict) -> str:
    """Rule-based Annai-voice summary from the computed insight fields."""
    avg = data["averages"]
    n = data["days_logged"]
    if n == 0:
        return ("No stops logged on the road this week, traveler. Log a meal or two "
                "and I'll chart the week's journey for you.")
    parts: list[str] = []
    parts.append(
        f"This week you averaged {avg['calories']} kcal a day across {n} logged "
        f"{'day' if n == 1 else 'days'}"
    )
    if data["target_calories"]:
        parts.append(f", staying on-target {data['adherence_pct']:.0f}% of them")
    parts.append(".")
    ms = data["macro_split_pct"]
    parts.append(
        f" Your plate leaned {ms['protein']:.0f}% protein · {ms['carbs']:.0f}% carbs · "
        f"{ms['fat']:.0f}% fat."
    )
    wd = data["weight_delta_kg"]
    if wd is not None:
        if wd < -0.05:
            parts.append(f" The scale eased {abs(wd):.1f} kg lighter — the road is working.")
        elif wd > 0.05:
            parts.append(f" The scale rose {wd:.1f} kg this week.")
        else:
            parts.append(" Weight held steady.")
    if avg["water_ml"]:
        parts.append(f" You drank about {avg['water_ml']} ml of water a day.")
    parts.append(" Keep to about eight-tenths full and next week's walk stays gentle.")
    return "".join(parts)


def insights_prompt(data: dict) -> list[dict]:
    """Messages for the text backend to write the summary in Annai's voice."""
    from .chat_assistant import ANNAI_PERSONA
    system = (
        ANNAI_PERSONA + " Write a SHORT (2-3 sentence) weekly insight summary for the "
        "traveler, warm and journey-flavored but plain about the numbers. No lists, "
        "no markdown — just the paragraph."
    )
    import json as _json
    user = "This week's stats (JSON):\n" + _json.dumps({
        "days_logged": data["days_logged"],
        "avg_calories": data["averages"]["calories"],
        "target_calories": data["target_calories"],
        "adherence_pct": data["adherence_pct"],
        "macro_split_pct": data["macro_split_pct"],
        "weight_delta_kg": data["weight_delta_kg"],
        "avg_water_ml": data["averages"]["water_ml"],
    })
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# Favorites + recent (quick-log)
# ---------------------------------------------------------------------------
def set_favorite(user_id: int, meal_id: int, favorite: bool) -> bool:
    """Mark/unmark a meal as favorite. Returns True if the meal existed."""
    with db.cursor() as cur:
        row = cur.execute("SELECT id FROM meals WHERE id=? AND user_id=?", (meal_id, user_id)).fetchone()
        if row is None:
            return False
        cur.execute("UPDATE meals SET favorite=? WHERE id=? AND user_id=?",
                    (1 if favorite else 0, meal_id, user_id))
    return True


def recent_dishes(user_id: int, limit: int = 20) -> list[dict]:
    """Quick-log list: favorites first, then most-frequent recent dishes, deduped
    by dish name (case-insensitive), capped at ``limit``.

    Each entry carries the dish's most-recent macros + a ``favorite`` flag and a
    ``count`` of how many times it's been logged."""
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT id, dish, calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, "
            "sodium_mg, eaten_at, favorite FROM meals WHERE user_id=? ORDER BY eaten_at DESC",
            (user_id,),
        ).fetchall()

    seen: dict[str, dict] = {}
    for r in rows:
        key = (r["dish"] or "").strip().lower()
        if not key:
            continue
        if key not in seen:
            seen[key] = {
                "dish": r["dish"],
                "calories": r["calories"],
                "protein_g": r["protein_g"],
                "carbs_g": r["carbs_g"],
                "fat_g": r["fat_g"],
                "fiber_g": r["fiber_g"],
                "sugar_g": r["sugar_g"],
                "sodium_mg": r["sodium_mg"],
                "last_eaten_at": r["eaten_at"],
                "favorite": False,
                "count": 0,
            }
        seen[key]["count"] += 1
        if r["favorite"]:
            seen[key]["favorite"] = True

    items = list(seen.values())
    # Favorites first; within each group by frequency (count) desc, then recency
    # (newest first). Two stable passes: order by recency descending first, then
    # by (favorite, count) — the stable sort preserves newest-first within ties.
    items.sort(key=lambda x: x["last_eaten_at"] or "", reverse=True)
    items.sort(key=lambda x: (not x["favorite"], -x["count"]))
    return items[:limit]


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def export_csv(user_id: int) -> str:
    """All of the user's meals, workouts, weights and water as one CSV.

    A leading ``type`` column distinguishes the record kinds so a single file can
    carry every table. csv.writer handles quoting/escaping of commas, quotes and
    newlines in free-text fields (dish names, workout notes)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "type", "id", "date_or_time", "name_or_dish", "calories", "protein_g",
        "carbs_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg",
        "minutes", "calories_burned", "note", "weight_kg", "water_ml", "source",
    ])
    with db.cursor() as cur:
        for r in cur.execute(
            "SELECT id, dish, calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, "
            "sodium_mg, eaten_at, source FROM meals WHERE user_id=? ORDER BY eaten_at",
            (user_id,),
        ):
            w.writerow([
                "meal", r["id"], r["eaten_at"], r["dish"], r["calories"], r["protein_g"],
                r["carbs_g"], r["fat_g"],
                r["fiber_g"] if r["fiber_g"] is not None else "",
                r["sugar_g"] if r["sugar_g"] is not None else "",
                r["sodium_mg"] if r["sodium_mg"] is not None else "",
                "", "", "", "", "", r["source"],
            ])
        for r in cur.execute(
            "SELECT id, kind, minutes, calories_burned, note, done_at FROM workouts "
            "WHERE user_id=? ORDER BY done_at", (user_id,),
        ):
            w.writerow([
                "workout", r["id"], r["done_at"], r["kind"], "", "", "", "", "", "", "",
                r["minutes"], r["calories_burned"], r["note"] or "", "", "", "",
            ])
        for r in cur.execute(
            "SELECT id, date, weight_kg FROM weights WHERE user_id=? ORDER BY date", (user_id,),
        ):
            w.writerow([
                "weight", r["id"], r["date"], "", "", "", "", "", "", "", "",
                "", "", "", r["weight_kg"], "", "",
            ])
        for r in cur.execute(
            "SELECT date, water_ml FROM water_log WHERE user_id=? ORDER BY date", (user_id,),
        ):
            w.writerow([
                "water", "", r["date"], "", "", "", "", "", "", "", "",
                "", "", "", "", r["water_ml"], "",
            ])
    return buf.getvalue()
