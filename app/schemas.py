"""Typed data shapes shared by the API, the rules engine and the planner.

Inputs that come from the browser are validated strictly (oversized text is a 422).
Shapes that come from the model are built through ``validators`` first, which trims
and clamps, so a chatty model can never break the response contract.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["birthday", "wedding", "house-warming", "community", "other"]
Source = Literal["ai", "mock", "fallback"]

MAX_TEXT = 500


class Strict(BaseModel):
    """Base for request bodies: unknown keys are ignored, strings are stripped."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Profile(Strict):
    """What we know about the event. Every field is optional until intake is done."""

    type: EventType = "birthday"
    title: str = Field("", max_length=80)
    guests: int | None = Field(None, ge=1, le=5000)
    budget: int | None = Field(None, ge=0, le=500_000_000)
    area: str = Field("", max_length=120)
    date: dt.date | None = None
    who: str = Field("", max_length=160)
    food: str = Field("", max_length=120)
    access: str = Field("", max_length=120)
    vibe: str = Field("", max_length=120)


# ---------- rules ----------

Family = Literal["budget", "timing", "season", "size", "needs"]
Level = Literal["info", "warn", "block"]


class Way(BaseModel):
    """One concrete way out of a blocking problem; ``apply`` patches the profile."""

    title: str
    detail: str
    apply: dict | None = None


class Insight(BaseModel):
    """One explainable decision made by the rules engine."""

    id: str
    family: Family
    level: Level
    title: str
    why: str
    suggestions: list[str] = []


class Block(BaseModel):
    """A problem the user must resolve before planning (impossible budget, past date)."""

    type: Literal["budget", "date"]
    title: str
    body: str
    ways: list[Way]


class ChecklistItem(BaseModel):
    id: str
    text: str
    why: str
    stop: str | None = None


# ---------- plan ----------


class Ripple(BaseModel):
    """A knock-on effect on a later stop when an option is chosen."""

    stop: str
    note: str = Field(max_length=200)
    extra: int = 0


class PhotoHint(BaseModel):
    """Where real photos for an option may be found (all optional)."""

    file: str | None = None
    wikidata: str | None = None
    lat: float | None = None
    lon: float | None = None
    query: str | None = None
    relation: Literal["place", "near", "inspiration"] = "inspiration"


class Option(BaseModel):
    id: str
    kind: str
    name: str
    price: int = Field(ge=0)
    per_guest: bool = False
    cap: int | None = None
    outdoor: bool = False
    reason: str = ""
    why: str = ""
    place_id: str | None = None
    dist_km: float | None = None
    ripple: list[Ripple] = []
    photo: PhotoHint | None = None
    tone: str = "dusk"


class Stop(BaseModel):
    key: str
    label: str
    rule: str = ""
    nophoto_msg: str = ""
    rec: Option
    options: list[Option]


class DayItem(BaseModel):
    time: str
    title: str
    note: str = ""
    art: Literal["arrive", "games", "cake", "dinner", "music", "ritual"] = "arrive"


class Day(BaseModel):
    id: str
    label: str
    title: str
    items: list[DayItem]


class Task(BaseModel):
    id: str
    stop: str | None = None
    text: str
    why: str = ""
    due: dt.date | None = None
    late: bool = False
    mandatory: bool = False


class Point(BaseModel):
    lat: float
    lon: float


class Plan(BaseModel):
    title: str
    profile: Profile
    stops: list[Stop]
    days: list[Day]
    tasks: list[Task]
    invite: str
    insights: list[Insight]
    source: Source
    today: dt.date
    area_point: Point | None = None
    notes: list[str] = []
