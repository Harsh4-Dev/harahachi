"""Annai (案内人) assistant — POST /chat v2.

Builds a rich system prompt (profile + targets, today's totals/remaining, 14-day
meal history, a short app manual), calls the active text backend, parses ONE
fenced ```json action block (log_meal | log_workout | set_goal), and returns the
reply with the action stripped out. The caller (app.py) executes the action.

A rule-based mock runs when no backend is configured: it can parse
"I ate <common Indian food>" into a log_meal action and answer basic
remaining-macros questions.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import backends, db
from .models import ProfileIn, Targets
from .nutrition import compute_targets

APP_MANUAL = (
    "This is Harahachi, a calorie/journey tracker styled as an old Japanese road "
    "map. Stations: Honjin (home/journey bar), Chaya (camera meal capture + "
    "barcode), Koyomi (calendar), Dojo (workouts), Ichiba (store), Tegata "
    "(profile/targets/account). Logging: photograph a meal at Chaya to auto-"
    "itemize macros, scan a barcode for packaged food, or just tell me what you "
    "ate here and I'll log it. Workouts credit calories back to the day's road."
)

ANNAI_PERSONA = (
    "You are Annai (案内人), a warm, concise nutrition coach and guide inside the "
    "Harahachi app. Give practical, non-judgmental advice about food, macros and "
    "healthy habits (hara hachi bu — eat to about 80% full). Keep replies short. "
    "You are not a doctor; add a gentle caveat for medical questions."
)

ACTION_INSTRUCTIONS = (
    "When the user asks you to log a meal, log a workout, or change their goal, "
    "emit EXACTLY ONE fenced code block with language json containing an action, "
    "in ADDITION to your short natural reply. Supported actions:\n"
    '```json\n{"action":"log_meal","dish":str,"calories":int,"protein_g":int,'
    '"carbs_g":int,"fat_g":int}\n```\n'
    '```json\n{"action":"log_workout","kind":str,"minutes":int,"calories_burned":int}\n```\n'
    '```json\n{"action":"set_goal","goal":"cut"|"maintain"|"bulk"}\n```\n'
    "Estimate reasonable macros for described foods.\n"
    "INTENT MATTERS — decide whether to LOG or PROPOSE:\n"
    "• LOG (execute now): the user clearly states they ATE or HAD something in the "
    "past — 'I ate 2 idlis', 'had a dosa for lunch', 'log my paneer'. Emit the action "
    "WITHOUT a proposed field; the server logs it immediately.\n"
    '• PROPOSE (do NOT log): the user asks what they SHOULD eat, for a recommendation, '
    "suggestion, or meal idea — 'what should I eat', 'suggest a dinner', 'recommend "
    "something high protein'. Emit the SAME log_meal action but add \"proposed\": true. "
    "The server will NOT log it; the app shows a confirm chip so the user can accept it. "
    "NEVER mark a recommendation as logged.\n"
    "Emit the block only when an action or recommendation is clearly intended; "
    "otherwise reply normally with no code block."
)

# Small built-in nutrition table for the rule-based mock (per typical serving).
MOCK_FOODS = {
    "idli": (55, 2, 11, 0),
    "dosa": (170, 4, 28, 5),
    "masala dosa": (380, 8, 52, 15),
    "sambar": (120, 6, 18, 3),
    "vada": (140, 4, 16, 7),
    "poha": (270, 6, 46, 7),
    "upma": (250, 6, 40, 8),
    "roti": (110, 3, 20, 2),
    "chapati": (110, 3, 20, 2),
    "naan": (280, 8, 48, 6),
    "paratha": (260, 6, 36, 11),
    "rice": (210, 4, 45, 1),
    "dal": (180, 9, 22, 6),
    "rajma": (210, 11, 28, 6),
    "chole": (250, 12, 34, 7),
    "paneer": (280, 14, 10, 20),
    "paneer butter masala": (340, 14, 14, 26),
    "chicken curry": (280, 26, 8, 16),
    "biryani": (520, 24, 62, 18),
    "chicken biryani": (520, 24, 62, 18),
    "egg": (78, 6, 1, 5),
    "eggs": (78, 6, 1, 5),
    "curd": (90, 5, 7, 5),
    "raita": (80, 4, 8, 4),
    "banana": (105, 1, 27, 0),
    "apple": (95, 0, 25, 0),
    "milk": (120, 8, 12, 5),
    "poori": (150, 3, 18, 8),
    "puri": (150, 3, 18, 8),
    "samosa": (260, 5, 30, 14),
    "pakora": (180, 4, 16, 11),
    "khichdi": (280, 9, 45, 6),
}

_NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "half": 1,
}


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------
def _profile_and_targets(user_id: int) -> tuple[ProfileIn | None, Targets | None]:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return None, None
    p = ProfileIn(
        name=row["name"] or "", age=row["age"], sex=row["sex"], height_cm=row["height_cm"],
        weight_kg=row["weight_kg"], activity=row["activity"], goal=row["goal"],
        body_fat_pct=row["body_fat_pct"], body_fat_goal_pct=row["body_fat_goal_pct"],
    )
    return p, compute_targets(p)


def _today_bounds() -> tuple[str, str]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{today}T00:00:00", f"{today}T23:59:59.999999"


def today_totals(user_id: int) -> dict:
    start, end = _today_bounds()
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT COALESCE(SUM(calories),0) c, COALESCE(SUM(protein_g),0) p, "
            "COALESCE(SUM(carbs_g),0) cb, COALESCE(SUM(fat_g),0) f FROM meals "
            "WHERE user_id=? AND eaten_at>=? AND eaten_at<=?",
            (user_id, start, end),
        ).fetchone()
    return {"calories": row["c"], "protein_g": row["p"], "carbs_g": row["cb"], "fat_g": row["f"]}


def remaining(user_id: int) -> Targets | None:
    _p, targets = _profile_and_targets(user_id)
    if targets is None:
        return None
    tot = today_totals(user_id)
    return Targets(
        calories=max(0, targets.calories - tot["calories"]),
        protein_g=max(0, targets.protein_g - tot["protein_g"]),
        carbs_g=max(0, targets.carbs_g - tot["carbs_g"]),
        fat_g=max(0, targets.fat_g - tot["fat_g"]),
    )


def _history_14d(user_id: int) -> list[str]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT dish, calories, substr(eaten_at,1,10) d FROM meals "
            "WHERE user_id=? ORDER BY eaten_at DESC LIMIT 60",
            (user_id,),
        ).fetchall()
    return [f"{r['d']}: {r['dish']} ({r['calories']} kcal)" for r in rows[:40]]


def build_system_prompt(user_id: int) -> str:
    p, targets = _profile_and_targets(user_id)
    tot = today_totals(user_id)
    rem = remaining(user_id)
    parts = [ANNAI_PERSONA, "", APP_MANUAL, "", ACTION_INSTRUCTIONS, ""]
    if p and targets:
        parts.append(
            f"USER PROFILE: name={p.name or 'traveler'}, age={p.age}, sex={p.sex}, "
            f"height={p.height_cm}cm, weight={p.weight_kg}kg, activity={p.activity}, "
            f"goal={p.goal}"
            + (f", body_fat={p.body_fat_pct}%" if p.body_fat_pct is not None else "")
        )
        parts.append(
            f"DAILY TARGETS: {targets.calories} kcal, {targets.protein_g}g protein, "
            f"{targets.carbs_g}g carbs, {targets.fat_g}g fat"
        )
    else:
        parts.append("USER PROFILE: not set yet.")
    parts.append(
        f"TODAY SO FAR: {tot['calories']} kcal, {tot['protein_g']}g protein, "
        f"{tot['carbs_g']}g carbs, {tot['fat_g']}g fat"
    )
    if rem:
        parts.append(
            f"REMAINING TODAY: {rem.calories} kcal, {rem.protein_g}g protein, "
            f"{rem.carbs_g}g carbs, {rem.fat_g}g fat"
        )
    hist = _history_14d(user_id)
    if hist:
        parts.append("RECENT MEALS (last 14 days):")
        parts.extend("  " + h for h in hist)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_ANYFENCE_RE = re.compile(r"```[a-zA-Z]*\s*(\{.*?\})\s*```", re.DOTALL)


def parse_action(reply: str) -> tuple[str, dict | None]:
    """Extract ONE json action from the reply. Returns (clean_reply, action|None)."""
    m = _FENCE_RE.search(reply) or _ANYFENCE_RE.search(reply)
    if not m:
        return reply.strip(), None
    action = None
    try:
        candidate = json.loads(m.group(1))
        if isinstance(candidate, dict) and candidate.get("action"):
            action = candidate
    except Exception:
        action = None
    # Strip the whole fenced block from the reply.
    clean = (reply[: m.start()] + reply[m.end():]).strip()
    return clean, action


# ---------------------------------------------------------------------------
# Rule-based mock
# ---------------------------------------------------------------------------
def _parse_i_ate(text: str) -> dict | None:
    """Parse 'I ate 2 idlis and sambar' -> a log_meal action, or None."""
    low = text.lower()
    if not re.search(r"\b(i\s+ate|i\s+had|log(?:\s+my)?|just\s+ate|ate\s+a|had\s+a)\b", low):
        # Still allow bare "2 idlis and sambar"-style if a known food is present.
        if not any(f in low for f in MOCK_FOODS):
            return None

    # Find known foods (longest names first so "masala dosa" beats "dosa").
    found: list[tuple[str, int]] = []
    scan = low
    for food in sorted(MOCK_FOODS, key=len, reverse=True):
        for mm in re.finditer(r"(?:(\d+|a|an|one|two|three|four|five|six)\s+)?" + re.escape(food) + r"s?\b", scan):
            qty_raw = mm.group(1)
            if qty_raw is None:
                qty = 1
            elif qty_raw.isdigit():
                qty = int(qty_raw)
            else:
                qty = _NUM_WORDS.get(qty_raw, 1)
            found.append((food, qty))
        # Blank out matched food so shorter substrings don't double count.
        scan = re.sub(re.escape(food), "#" * len(food), scan)

    if not found:
        return None

    cal = prot = carb = fat = 0
    names = []
    for food, qty in found:
        c, p, cb, f = MOCK_FOODS[food]
        cal += c * qty
        prot += p * qty
        carb += cb * qty
        fat += f * qty
        label = food if qty == 1 else f"{qty} {food}"
        names.append(label)
    dish = ", ".join(names)
    return {
        "action": "log_meal", "dish": dish,
        "calories": cal, "protein_g": prot, "carbs_g": carb, "fat_g": fat,
    }


# Recommendation / suggestion intent — the user is asking what to eat, NOT
# reporting what they ate. These must PROPOSE a meal, never log it.
_RECOMMEND_RE = re.compile(
    r"\b(what\s+should\s+i\s+(?:eat|have|cook|make)|what\s+(?:can|do)\s+i\s+eat|"
    r"what\s+to\s+eat|suggest|recommend|meal\s+idea|any\s+ideas?|"
    r"give\s+me\s+(?:a|some)\b.*\b(?:meal|dish|food|dinner|lunch|breakfast)|"
    r"what\s+for\s+(?:dinner|lunch|breakfast))\b",
    re.IGNORECASE,
)


def _propose_meal(user_id: int | None) -> dict:
    """Build a PROPOSED log_meal action (not executed) from the user's history
    (best-fitting past meal) or a sensible protein-forward default."""
    from .suggestions import rank_candidates
    cand = None
    if user_id is not None:
        rem = remaining(user_id)
        if rem is not None:
            cands = rank_candidates(user_id, rem, limit=1)
            if cands:
                cand = cands[0]
    if cand is None:
        # Deterministic default when there's no usable history.
        cal, prot, carb, fat = MOCK_FOODS["dal"]
        cand = {"dish": "Dal with rice", "calories": cal + 210, "protein_g": prot + 4,
                "carbs_g": carb + 45, "fat_g": fat + 1}
    return {
        "action": "log_meal", "proposed": True, "dish": cand["dish"],
        "calories": int(cand["calories"]), "protein_g": int(cand["protein_g"]),
        "carbs_g": int(cand["carbs_g"]), "fat_g": int(cand["fat_g"]),
    }


def mock_reply(user_id: int | None, messages: list[dict]) -> tuple[str, dict | None]:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    low = last.lower()

    # 0) Recommendation intent — PROPOSE a meal, do NOT log it. Checked BEFORE the
    #    "I ate" parser so a mentioned food in "what should I eat, maybe dosa?" is
    #    suggested rather than silently logged.
    if _RECOMMEND_RE.search(low):
        action = _propose_meal(user_id)
        reply = (
            f"You could have {action['dish']} — about {action['calories']} kcal "
            f"({action['protein_g']}g protein). Tap to log it if you go for it."
        )
        return reply, action

    # 1) Try to parse a meal log.
    action = _parse_i_ate(last)
    if action:
        reply = (
            f"Logged {action['dish']} — about {action['calories']} kcal "
            f"({action['protein_g']}g protein). Your journey bar just advanced. "
            "Eat to about eight-tenths full and you'll land on target."
        )
        return reply, action

    # 2) set_goal
    for g in ("cut", "maintain", "bulk"):
        if re.search(rf"\b(set|change|switch).*\bgoal\b.*\b{g}\b", low) or \
           re.search(rf"\bgoal\s+to\s+{g}\b", low):
            return (f"Set your goal to {g}. I'll retune your targets accordingly.",
                    {"action": "set_goal", "goal": g})

    # 3) Remaining-macros question.
    if user_id is not None and re.search(r"\b(left|remaining|how much|what.?s left|budget)\b", low):
        rem = remaining(user_id)
        if rem is not None:
            return (
                f"You have {rem.calories} kcal left today — {rem.protein_g}g protein, "
                f"{rem.carbs_g}g carbs and {rem.fat_g}g fat to go. A protein-forward "
                "meal would round out the day nicely.",
                None,
            )
        return ("Set up your profile in Tegata and I can tell you exactly what's "
                "left for the day.", None)

    # 4) Generic.
    return (
        "I'm your guide, Annai. Tell me what you ate (e.g. \"I ate 2 idlis and "
        "sambar\") and I'll log it, ask \"what's left today?\", or say \"set my "
        "goal to cut\". " + (f'You said: "{last[:120]}".' if last else ""),
        None,
    )


# ---------------------------------------------------------------------------
# Backend path
# ---------------------------------------------------------------------------
async def backend_reply(user_id: int, messages: list[dict]) -> tuple[str, dict | None]:
    system = build_system_prompt(user_id)
    convo = [{"role": "system", "content": system}]
    convo.extend(messages)
    raw = await backends.chat_completion(convo, max_tokens=512, temperature=0.6)
    return parse_action(raw)
