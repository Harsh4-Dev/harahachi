"""Pydantic request/response models matching the API contract in API.md."""
from __future__ import annotations

import re
from datetime import date as _date, datetime as _datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ---- shared bounds (M4) ----
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DISH_MAX = 120
CHAT_MAX_MESSAGES = 20
CHAT_MAX_TOTAL_CHARS = 8000
MEAL_KCAL_MAX = 5000
WORKOUT_MINUTES_MAX = 600


def valid_date(v: str | None) -> bool:
    """True for a real calendar date in strict YYYY-MM-DD form."""
    if not isinstance(v, str) or not DATE_RE.fullmatch(v):
        return False
    try:
        _date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _check_date(v: str | None) -> str | None:
    if v is None:
        return v
    if not valid_date(v):
        raise ValueError("date must be YYYY-MM-DD")
    return v


# ---- Pairing ----
class PairClaimIn(BaseModel):
    token: str = Field(min_length=1, max_length=64)
    device_name: str = Field(default="phone", min_length=1, max_length=64)


class PairClaimOut(BaseModel):
    device_id: str
    device_key: str


# ---- Analyze ----
class Point(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class AnalysisItem(BaseModel):
    name: str = Field(max_length=200)
    calories: int = Field(ge=0, le=MEAL_KCAL_MAX)
    # Per-item macros are REQUIRED by the contract (API.md: one entry per plate
    # component, its own macros, top-level = sum). Default to 0 so lenient LLM
    # output still validates, but the mock always fills real numbers.
    protein_g: int = Field(default=0, ge=0, le=500)
    carbs_g: int = Field(default=0, ge=0, le=800)
    fat_g: int = Field(default=0, ge=0, le=400)
    # v3 extended nutrients (optional per-item; summed into the top-level totals).
    fiber_g: Optional[int] = Field(default=None, ge=0, le=300)
    sugar_g: Optional[int] = Field(default=None, ge=0, le=800)
    sodium_mg: Optional[int] = Field(default=None, ge=0, le=50000)
    # Optional normalized center of this component in the photo (API.md
    # "Complex/Indian meals") so the app can pin the label next to its part of
    # the dish. Invalid/out-of-range points are dropped rather than failing the
    # whole analysis.
    point: Optional[Point] = None

    @field_validator("point", mode="before")
    @classmethod
    def _drop_invalid_point(cls, v):
        if v is None:
            return None
        try:
            x = float(v["x"] if isinstance(v, dict) else v.x)
            y = float(v["y"] if isinstance(v, dict) else v.y)
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return None
        return {"x": x, "y": y}


class Analysis(BaseModel):
    dish: str = Field(max_length=200)
    calories: int = Field(ge=0, le=MEAL_KCAL_MAX)
    protein_g: int = Field(ge=0, le=500)
    carbs_g: int = Field(ge=0, le=800)
    fat_g: int = Field(ge=0, le=400)
    # v3 extended nutrients (optional; top-level = sum of the items' values).
    fiber_g: Optional[int] = Field(default=None, ge=0, le=300)
    sugar_g: Optional[int] = Field(default=None, ge=0, le=800)
    sodium_mg: Optional[int] = Field(default=None, ge=0, le=50000)
    confidence: float = Field(ge=0.0, le=1.0)
    question: Optional[str] = Field(default=None, max_length=500)
    items: list[AnalysisItem] = Field(default_factory=list, max_length=30)

    @field_validator("question", mode="after")
    @classmethod
    def _clear_question_if_confident(cls, v, info):
        conf = info.data.get("confidence")
        if conf is not None and conf >= 0.85:
            return None
        return v


# ---- Meals ----
class MealIn(BaseModel):
    dish: str = Field(min_length=1, max_length=DISH_MAX)
    calories: int = Field(ge=0, le=MEAL_KCAL_MAX)
    protein_g: int = Field(ge=0, le=1000)
    carbs_g: int = Field(ge=0, le=1000)
    fat_g: int = Field(ge=0, le=1000)
    # v3 extended nutrients (all optional).
    fiber_g: Optional[int] = Field(default=None, ge=0, le=500)
    sugar_g: Optional[int] = Field(default=None, ge=0, le=1000)
    sodium_mg: Optional[int] = Field(default=None, ge=0, le=100000)
    # ISO-8601 datetime (e.g. 2026-05-01T12:00:00 or ...Z), bounded.
    eaten_at: Optional[str] = Field(default=None, max_length=40)
    source: Literal["photo", "manual", "suggestion"] = "manual"

    @field_validator("eaten_at")
    @classmethod
    def _iso_datetime(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not DATE_RE.fullmatch(v[:10]):
            raise ValueError("eaten_at must be an ISO-8601 datetime")
        try:
            _datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("eaten_at must be an ISO-8601 datetime")
        return v


class Meal(BaseModel):
    id: int
    dish: str
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int
    fiber_g: Optional[int] = None
    sugar_g: Optional[int] = None
    sodium_mg: Optional[int] = None
    eaten_at: str
    source: str
    favorite: bool = False


class Totals(BaseModel):
    calories: int = 0
    protein_g: int = 0
    carbs_g: int = 0
    fat_g: int = 0
    burned: int = 0
    # v3 extended nutrients + water in day totals.
    fiber_g: int = 0
    sugar_g: int = 0
    sodium_mg: int = 0
    water_ml: int = 0
    water_target_ml: int = 0


class MealsOut(BaseModel):
    meals: list[Meal]
    totals: Totals


# ---- Profile ----
class ProfileIn(BaseModel):
    name: str = Field(default="", max_length=80)
    age: int = Field(ge=1, le=120)
    sex: Literal["m", "f", "x"]
    height_cm: int = Field(ge=50, le=260)
    weight_kg: float = Field(ge=20, le=400)
    activity: Literal["sedentary", "light", "moderate", "active"]
    goal: Literal["cut", "maintain", "bulk"]
    # Body composition (v2). When body_fat_pct is set, targets use Katch-McArdle.
    body_fat_pct: Optional[float] = Field(default=None, ge=1.0, le=75.0)
    body_fat_goal_pct: Optional[float] = Field(default=None, ge=1.0, le=75.0)
    # v3: goal weight + weekly pace. When both are set (with a direction that
    # matches the goal), targets recompute the deficit/surplus from the pace
    # (7700 kcal/kg) instead of the flat cut/bulk percentages.
    weight_goal_kg: Optional[float] = Field(default=None, ge=20.0, le=400.0)
    pace_kg_per_week: Optional[float] = Field(default=None, ge=0.0, le=1.5)


class Targets(BaseModel):
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int


class ProfileOut(ProfileIn):
    targets: Targets
    # v3: projected date to reach weight_goal_kg at pace_kg_per_week (null when
    # not applicable — no goal/pace, or already at goal).
    eta_date: Optional[str] = None


# =========================================================================
# v3: Weight / water
# =========================================================================
class WeightIn(BaseModel):
    weight_kg: float = Field(ge=20.0, le=400.0)
    date: Optional[str] = Field(default=None, max_length=10)

    @field_validator("date")
    @classmethod
    def _date_ok(cls, v: str | None) -> str | None:
        return _check_date(v)


class WeightEntry(BaseModel):
    date: str
    weight_kg: float


class TrendPoint(BaseModel):
    date: str
    avg_kg: float


class WeightOut(BaseModel):
    entries: list[WeightEntry]
    trend: list[TrendPoint]


class WaterIn(BaseModel):
    ml: int = Field(ge=-100000, le=100000)


class WaterOut(BaseModel):
    date: str
    water_ml: int
    water_target_ml: int


# ---- Workouts ----
class WorkoutIn(BaseModel):
    kind: str = Field(min_length=1, max_length=80)
    minutes: int = Field(ge=0, le=WORKOUT_MINUTES_MAX)
    calories_burned: int = Field(ge=0, le=MEAL_KCAL_MAX)
    note: Optional[str] = Field(default=None, max_length=280)


class Workout(BaseModel):
    id: int
    kind: str
    minutes: int
    calories_burned: int
    note: Optional[str] = None
    done_at: str


# ---- Suggestions ----
class Suggestion(BaseModel):
    dish: str
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int
    reason: str


class SuggestionsOut(BaseModel):
    remaining: Targets
    suggestions: list[Suggestion]


# ---- Calendar ----
class CalendarDay(BaseModel):
    date: str
    calories: int
    target: int
    burned: int
    status: Literal["under", "on", "over", "empty"]


class CalendarOut(BaseModel):
    days: list[CalendarDay]


# ---- Chat ----
class ChatMessage(BaseModel):
    # Only the conversation roles: a client-supplied "system" message could
    # override Annai's server-side system prompt (prompt injection).
    role: Literal["user", "assistant"]
    content: str = Field(max_length=CHAT_MAX_TOTAL_CHARS)


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=CHAT_MAX_MESSAGES)

    @model_validator(mode="after")
    def _bounded(self):
        if sum(len(m.content) for m in self.messages) > CHAT_MAX_TOTAL_CHARS:
            raise ValueError(f"messages exceed {CHAT_MAX_TOTAL_CHARS} characters in total")
        return self


class ChatOut(BaseModel):
    reply: str
    source: Literal["vllm", "mock"]


# ---- Barcode / packaged food ----
class MacroSet(BaseModel):
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int
    # v3 extended nutrients (optional; OFF often provides fiber/sugar/sodium).
    fiber_g: Optional[int] = None
    sugar_g: Optional[int] = None
    sodium_mg: Optional[int] = None


class BarcodeProduct(BaseModel):
    name: str
    brand: str = ""
    serving: str = ""
    per_serving: Optional[MacroSet] = None
    per_100g: Optional[MacroSet] = None


class BarcodeOut(BaseModel):
    found: bool
    code: str
    product: Optional[BarcodeProduct] = None
    source: Optional[Literal["openfoodfacts", "cache", "mock"]] = None


# =========================================================================
# v2: Accounts
# =========================================================================
PIN_RE = re.compile(r"^[0-9]{6,8}$")


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    pin: str = Field(min_length=6, max_length=8)
    profile: Optional[ProfileIn] = None

    @field_validator("username")
    @classmethod
    def _clean_username(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("username required")
        return v

    @field_validator("pin")
    @classmethod
    def _pin_digits(cls, v: str) -> str:
        if not PIN_RE.fullmatch(v):
            raise ValueError("pin must be 6-8 digits")
        return v


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    pin: str = Field(min_length=6, max_length=8)

    @field_validator("pin")
    @classmethod
    def _pin_digits(cls, v: str) -> str:
        if not PIN_RE.fullmatch(v):
            raise ValueError("pin must be 6-8 digits")
        return v


class UserOut(BaseModel):
    id: int
    username: str
    created_at: str


# =========================================================================
# v2: Body photo estimate
# =========================================================================
class BodyPhotoEstimate(BaseModel):
    body_fat_pct: float = Field(ge=1.0, le=75.0)
    range: list[float] = Field(min_length=2, max_length=2)
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""

    @field_validator("range")
    @classmethod
    def _clamp_range(cls, v: list[float]) -> list[float]:
        lo, hi = float(v[0]), float(v[1])
        lo = max(1.0, min(75.0, lo))
        hi = max(1.0, min(75.0, hi))
        if lo > hi:
            lo, hi = hi, lo
        return [round(lo, 1), round(hi, 1)]


# =========================================================================
# v2: Backend registry
# =========================================================================
BackendKind = Literal["vllm", "nvidia", "lmstudio", "openai"]


class BackendIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: BackendKind
    base_url: str = Field(min_length=1, max_length=400)
    api_key: Optional[str] = Field(default=None, max_length=400)
    model: str = Field(min_length=1, max_length=200)
    vision: bool = False
    enabled: bool = True


class BackendOut(BaseModel):
    id: int
    name: str
    kind: BackendKind
    base_url: str
    has_key: bool
    model: str
    vision: bool
    enabled: bool


# =========================================================================
# v2: Assistant chat (Annai) — POST /chat now returns reply + action
# =========================================================================
class ChatAction(BaseModel):
    action: Literal["log_meal", "log_workout", "set_goal"]
    # log_meal
    dish: Optional[str] = None
    calories: Optional[int] = None
    protein_g: Optional[int] = None
    carbs_g: Optional[int] = None
    fat_g: Optional[int] = None
    # log_workout
    kind: Optional[str] = None
    minutes: Optional[int] = None
    calories_burned: Optional[int] = None
    # set_goal
    goal: Optional[Literal["cut", "maintain", "bulk"]] = None


class ChatV2Out(BaseModel):
    reply: str
    action: Optional[dict] = None
    source: Literal["vllm", "mock"]


# =========================================================================
# v2: Push subscriptions
# =========================================================================
class PushSubscribeIn(BaseModel):
    # Shape/host/size are validated by push.validate_subscription (M1).
    subscription: dict
    tz_offset_min: int = Field(default=0, ge=-900, le=900)
