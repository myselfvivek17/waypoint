"""Typed data shapes shared by the API, the rules engine and the planner.

Inputs that come from the browser are validated strictly (oversized text is a 422).
Shapes that come from the model are built through ``validators`` first, which trims
and clamps, so a chatty model can never break the response contract.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

EventType = Literal["birthday", "wedding", "house-warming", "community", "other"]
Source = Literal["ai", "mock", "fallback"]

MAX_TEXT = 500


class Strict(BaseModel):
    """Base for request bodies: unknown keys are ignored, strings are stripped."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str | None = Field(None, max_length=160)  # what the geocoder calls it, for the user to confirm


# Fields that never go to a model: the device position is used only for the place search.
PRIVATE = {"point", "from_me"}


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
    have: str = Field("", max_length=160)  # what the host already has: "a family priest, my aunt cooks"
    # Set only when the user taps "Use my location": search around this point instead of the area
    # text. Rounded on the device, used per request and never stored or logged by the server.
    point: Point | None = None
    from_me: bool = False


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
    """Where real photos for an option may be found (all optional).

    ``file``/``wikidata`` and ``name`` + point find photos of THIS place (Wikimedia, Foursquare);
    ``vibe`` finds openly licensed photos of similar places (Openverse), always labelled as such;
    ``query`` finds inspiration photos for an idea (a theme, a dish).
    """

    file: str | None = None
    wikidata: str | None = None
    lat: float | None = None
    lon: float | None = None
    name: str | None = None
    vibe: str | None = None
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
    where: str | None = None
    dist_km: float | None = None
    lat: float | None = None
    lon: float | None = None
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
    verb: str = ""
    text: str
    why: str = ""
    due: dt.date | None = None
    late: bool = False
    mandatory: bool = False



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


# ---------- model drafts ----------
# What the model is asked to return. Every field is lenient (wrong types become blanks)
# because the planner validates and repairs each value before building a Plan.

def _text(v: object) -> str:
    return "" if v is None or isinstance(v, (dict, list)) else str(v)


def _flag(v: object) -> bool:
    return v is True or str(v).strip().lower() in ("true", "yes", "1")


def _objects(v: object) -> list:
    return [x for x in v if isinstance(x, (dict, BaseModel))] if isinstance(v, list) else []


Text = Annotated[str, BeforeValidator(_text)]
Flag = Annotated[bool, BeforeValidator(_flag)]


class Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DraftRipple(Lenient):
    stop: Text = ""
    note: Text = ""
    extra: Any = 0


class DraftOption(Lenient):
    kind: Text = ""
    name: Text = ""
    price: Any = None
    place_id: Text = ""
    reason: Text = ""
    why: Text = ""
    outdoor: Flag = False
    photo_query: Text = ""
    ripple: DraftRipple | None = None

    @field_validator("ripple", mode="before")
    @classmethod
    def _ripple(cls, v: object) -> object:
        return v if isinstance(v, (dict, BaseModel)) else None


class DraftStop(Lenient):
    key: Text = ""
    pick: DraftOption = DraftOption()

    @field_validator("pick", mode="before")
    @classmethod
    def _pick(cls, v: object) -> object:
        return v if isinstance(v, (dict, BaseModel)) else {}
    options: Annotated[list[DraftOption], BeforeValidator(_objects)] = []


class DraftDayItem(Lenient):
    time: Text = ""
    title: Text = ""
    note: Text = ""
    art: Text = "arrive"


class DraftDay(Lenient):
    label: Text = ""
    title: Text = ""
    items: Annotated[list[DraftDayItem], BeforeValidator(_objects)] = []


class DraftPlan(Lenient):
    stops: Annotated[list[DraftStop], BeforeValidator(_objects)] = []
    days: Annotated[list[DraftDay], BeforeValidator(_objects)] = []
    invite: Text = ""
