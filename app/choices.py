"""User choice and "Ask to adjust": the user shapes the plan, not only picks from it.

Everything here is deterministic. A request in words becomes a short list of operations (the
model proposes them, or the rules parser reads the common phrasings); code validates every
operation against the plan and applies it; the result is a before/after diff the user can
accept or not. Buttons in the UI (a stop's mode, add or remove a stop) send the same
operations directly.

Operations:
  stops     remove_stop, skip_stop, restore_stop, have (name, cost), own (text, cost), set_mode, add_stop
  event day remove_item, skip_item, have_item, own_item, move_item (time), add_item (time, text)
  plan      cut_budget, set_budget, add_guests, remove_guests, set_guests, outdoors, indoors, cheaper, move_date
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from . import llm, planner, prices, revise, rules, templates
from .intake import parse_date, parse_money
from .places import PlaceSet
from .rules import inr
from .schemas import DayItem, Mode, Option, Plan, Profile, Source, Stop, Strict, Task
from .validators import CAPS, clip, line_total, to_int

log = logging.getLogger("waypoint.choices")

STOP_OPS = {"remove_stop", "skip_stop", "restore_stop", "have", "own", "set_mode", "cheaper", "choose"}
ITEM_OPS = {"remove_item", "skip_item", "have_item", "own_item", "move_item"}
PLAN_OPS = {"add_stop", "add_item", "cut_budget", "set_budget", "add_guests", "remove_guests", "set_guests",
            "outdoors", "indoors", "move_date"}
OPS = STOP_OPS | ITEM_OPS | PLAN_OPS
MAX_OPS = 12
MAX_STOPS = 10

INSTRUCTIONS = """
You turn a request to change an event plan into a list of operations. The data block has request (the
user's words), event (a summary), stops (key, label, current choice, status, mode and the modes it can
take), day (the event-day moments: id, time, title) and can_add (stops that can be added).

Operations, referring to stops by key and moments by id:
 remove_stop {stop}  skip_stop {stop}  restore_stop {stop}  have {stop, name?, cost?}  own {stop, text, cost?}
 set_mode {stop, mode}  add_stop {name}  cheaper {stop}
 remove_item {item}  skip_item {item}  have_item {item, name?}  own_item {item, text}  move_item {item, time}
 add_item {time, text}
 cut_budget {amount: percent}  set_budget {amount: rupees}  add_guests {amount}  remove_guests {amount}
 set_guests {amount}  outdoors  indoors  move_date {date: YYYY-MM-DD}
"I don't want X" removes X's stop and its moments. "I already have X" or "X is sorted" is have.
"Cook at home" is set_mode food home. Keep the user's own words in text and name.
If nothing fits, return no operations and say in reply what you can do instead.
Reply with JSON only: {"ops": [{"op": "", "stop": null, "item": null, "mode": null, "name": null,
 "text": null, "cost": null, "amount": null, "date": null, "time": null}], "reply": "<one short sentence>"}
"""

CAN_DO = ("I can skip or remove a stop, mark one as already sorted, change how it's done (cook at home, a "
          "rented hall…), add a stop like a photographer, move or drop a moment on the day, change the "
          "budget, guests or date, or move it indoors or outdoors.")


# ------------------------------------------------------------------ shapes

class Op(BaseModel):
    """One change. Loose on input (the model writes these); ``validate_ops`` makes them safe."""

    op: str
    stop: str | None = Field(None, max_length=60)
    item: str | None = Field(None, max_length=80)
    mode: str | None = Field(None, max_length=30)
    name: str | None = Field(None, max_length=160)
    text: str | None = Field(None, max_length=160)
    cost: Any = None
    amount: Any = None
    date: str | None = Field(None, max_length=20)
    time: str | None = Field(None, max_length=20)

    @field_validator("stop", "item", "mode", "name", "text", "date", "time", mode="before")
    @classmethod
    def _short_text(cls, v: object) -> object:
        if v is None or isinstance(v, (dict, list)):
            return None
        return clip(v, 160) or None


class OpsDraft(BaseModel):
    ops: list = []
    reply: str = ""


class RippleState(Strict):
    src: str = Field("", max_length=40)
    stop: str = Field(max_length=40)
    note: str = Field("", max_length=200)
    extra: int = 0
    ack: bool = False


class AdjustIn(Strict):
    plan: Plan
    chosen: dict[str, str] = Field(default_factory=dict, max_length=12)
    ripples: list[RippleState] = Field(default_factory=list, max_length=30)
    booked: list[str] = Field(default_factory=list, max_length=20)  # stops already paid for: never swapped
    decided: list[str] = Field(default_factory=list, max_length=20)  # kept unless a rule now breaks them
    request: str = Field(min_length=1, max_length=300)


class ApplyIn(Strict):
    """Operations sent straight from the UI (a stop's mode, add or remove a stop)."""

    plan: Plan
    chosen: dict[str, str] = Field(default_factory=dict, max_length=12)
    ripples: list[RippleState] = Field(default_factory=list, max_length=30)
    booked: list[str] = Field(default_factory=list, max_length=20)
    decided: list[str] = Field(default_factory=list, max_length=20)
    ops: list[Op] = Field(min_length=1, max_length=MAX_OPS)


class DiffRow(BaseModel):
    label: str
    before: str
    after: str


class WayOut(BaseModel):
    """One way out of numbers that no longer work ("Keep ₹35,000, invite 58"), as operations that are
    re-run together with the revision."""

    title: str
    detail: str
    ops: list[Op]


class AdjustOut(BaseModel):
    understood: bool
    title: str
    rows: list[DiffRow]
    note: str
    profile: Profile
    chosen: dict[str, str]
    ripples: list[RippleState]
    plan: Plan
    source: Source
    ops: list[Op] = []
    suggest: Op | None = None       # when not understood: the closest thing I can do
    suggest_text: str = ""
    freed: int = 0                  # rupees freed by skipped or removed stops
    explain: list[str] = []         # what was kept, what a rule forced, what was assumed
    over: int = 0                   # rupees over budget after the change
    block_title: str = ""           # the new numbers are impossible: "let's make this work"
    block_body: str = ""
    ways: list[WayOut] = []


# ------------------------------------------------------------------ pricing (mirrors the browser's model.js)

def _all(stop: Stop) -> list[Option]:
    return [stop.rec, *stop.options]


stop_price = prices.stop_price


def priced(plan: Plan, guests: int, chosen: dict[str, str], ripples: list[RippleState]) -> list[tuple[Stop, Option, int]]:
    """(stop, chosen option, price) for every stop."""
    out = []
    for s in plan.stops:
        item = next((o for o in _all(s) if o.id == chosen.get(s.key)), s.rec)
        extra = sum(x.extra for x in ripples if x.stop == s.key)
        out.append((s, item, stop_price(s, item, guests, extra)))
    return out


def _take(stop: Stop, o: Option, chosen: dict[str, str], ripples: list[RippleState]) -> list[RippleState]:
    chosen[stop.key] = o.id
    kept = [x for x in ripples if x.src != stop.key]
    return kept + [RippleState(src=stop.key, stop=x.stop, note=x.note, extra=x.extra) for x in o.ripple]


# ------------------------------------------------------------------ finding things in the plan

def _items(plan: Plan) -> list[tuple[int, DayItem]]:
    return [(di, it) for di, d in enumerate(plan.days) for it in d.items]


def find_item(plan: Plan, ref: str | None) -> DayItem | None:
    """A moment by id, by the stop it depends on, or by words from its title."""
    if not ref:
        return None
    rows = [it for _, it in _items(plan)]
    hit = next((it for it in rows if it.id == ref), None)
    if hit:
        return hit
    low = ref.lower()
    key = rules.find_stop(low, [s.key for s in plan.stops] + list(rules.DAY_STOP.values()))
    if key:
        hit = next((it for it in rows if it.stop == key), None)
        if hit:
            return hit
    words = [w for w in re.findall(r"[a-z]{3,}", low) if w not in {"the", "and", "for", "our", "with"}]
    return next((it for it in rows if any(w in it.title.lower() for w in words)), None)


def resolve_stop(plan: Plan, ref: str | None) -> Stop | None:
    if not ref:
        return None
    keys = [s.key for s in plan.stops]
    key = ref if ref in keys else rules.find_stop(ref, keys)
    if key is None:  # by label ("Day-of schedule", an added stop's own label)
        key = next((s.key for s in plan.stops if s.label.lower() in ref.lower() or ref.lower() in s.label.lower()), None)
    return next((s for s in plan.stops if s.key == key), None)


def parse_time(text: str | None) -> str | None:
    """"8 pm", "8:30pm", "20:00" -> "8:30 PM"."""
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b", (text or "").lower())
    if m:
        h, mm = int(m[1]), int(m[2] or 0)
        return f"{h}:{mm:02d} {m[3].upper()}" if 1 <= h <= 12 and mm < 60 else None
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text or "")
    if m:
        h, mm = int(m[1]), int(m[2])
        return f"{(h - 1) % 12 + 1}:{mm:02d} {'PM' if h >= 12 else 'AM'}"
    return None


def minutes(t: str) -> int:
    m = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", t.upper())
    if not m:
        return 10**6
    return (int(m[1]) % 12 + (12 if m[3] == "PM" else 0)) * 60 + int(m[2])


def validate_ops(raw: list, plan: Plan) -> list[Op]:
    """Keep only operations that name a real thing in this plan, with safe values."""
    out: list[Op] = []
    for r in raw[:MAX_OPS] if isinstance(raw, list) else []:
        try:
            op = r if isinstance(r, Op) else Op.model_validate(r)
        except ValueError:
            continue
        op.op = (op.op or "").strip().lower()
        if op.op not in OPS:
            continue
        if op.op in STOP_OPS:
            s = resolve_stop(plan, op.stop or op.name)
            if s is None:
                continue
            op.stop = s.key
            if op.op == "set_mode" and not any(m.id == op.mode for m in s.modes):
                continue
            if op.op == "choose" and not any(o.id == op.item for o in _all(s)):
                continue
        if op.op in ITEM_OPS:
            it = find_item(plan, op.item or op.text)
            if it is None:
                continue
            op.item = it.id
            if op.op == "move_item" and not parse_time(op.time):
                continue
        if op.op == "add_stop" and not (op.name or op.text):
            continue
        if op.op == "add_item" and not (op.text and parse_time(op.time)):
            continue
        if op.op in ("own", "own_item") and not (op.text or op.name):
            continue
        out.append(op)
    return out


# ------------------------------------------------------------------ the rules parser

_NEG = re.compile(r"(?:don'?t|do not|dont)\s+(?:want|need)\s+(?:a |an |the |any )?(?P<a>.+)"
                  r"|^no\s+(?P<b>[a-z][a-z -]{2,30}?)(?:\s+please)?[.!]?$"
                  r"|\bwithout\s+(?:a |an |the |any )?(?P<c>.+)"
                  r"|\b(?:remove|cancel|drop|delete|scrap)\s+(?:the |a |an |our )?(?P<d>.+)")
_HAVE = re.compile(r"(?:already\s+(?:have|got|booked|arranged|found)|(?:we|i)\s+have\s+(?:our|my)\s+own|"
                   r"have\s+(?:our|my)\s+own)\s+(?:a |an |the |our |my )?(?P<x>.+)"
                   r"|(?P<y>[a-z][a-z ]{2,30}?)\s+is\s+(?:already\s+)?(?:sorted|taken care of|arranged|covered)")
_COOK = re.compile(r"\b(?:cook(?:ing)?\s+(?:it\s+|the food\s+)?(?:at|from)\s+home|home[- ]?cook(?:ed|ing)?|"
                   r"home[- ]?made food|cook(?:ing)? ourselves)\b")
_WHO_COOKS = re.compile(r"\b(?:my|our)\s+[a-z]+(?:\s+[a-z]+)?\s+(?:is|are|will be|will)\s+(?:cooking|cook|making the food|"
                        r"doing the food|handling the food)")
_ADD = re.compile(r"\b(?:add|include|also need|we need|i need|book)\s+(?:a |an |the |some )?(?P<x>[a-z][a-z &'/-]{2,40})")
_MOVE = re.compile(r"\b(?:move|shift|push|start|have)\s+(?:the\s+)?(?P<x>[a-z][a-z ]{2,30}?)\s+(?:to|at)\s+"
                   r"(?P<t>\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm))")
_FOOD_MODE = [(r"home caterer|home[- ]?chef", "home_caterer"), (r"hall'?s? cater|convention[- ]hall", "hall"),
              (r"(?:food|eat|dinner|lunch) at (?:a|the) restaurant|restaurant (?:food|dinner)", "restaurant")]
_VENUE_HOME = re.compile(r"\b(?:at|in|from)\s+(?:our|my)\s+(?:home|house|flat|place|apartment)\b|host(?:ing)? (?:it )?at home")


def regex_intent(text: str, now: dt.date) -> Op | None:
    """The whole-plan changes, read deterministically: budget, guests, indoors/outdoors, date."""
    t = text.lower()
    num = re.search(r"(\d+(?:\.\d+)?)", t.replace(",", ""))
    n = float(num.group(1)) if num else None
    if re.search(r"(cut|reduce|lower|trim|less|decrease)", t) and "%" in t:
        return Op(op="cut_budget", amount=n)
    if re.search(r"(cut|reduce|lower|trim|decrease).*budget|budget.*(cut|lower|less)|cheaper overall", t):
        return Op(op="cut_budget", amount=20)
    if re.search(r"budget.*(to|of)\s*₹?\s*\d|set budget", t):
        return Op(op="set_budget", amount=parse_money(t, loose=True))
    if re.search(r"\b(add|invite|more)\b", t) and n:
        return Op(op="add_guests", amount=n)
    if re.search(r"\b(remove|drop|fewer|less|cut|reduce)\b", t) and n:
        return Op(op="remove_guests", amount=n)
    if re.search(r"\b(?:for|plan for|make it)\s+\d+\s+(?:guests|people)", t) and n:
        return Op(op="set_guests", amount=n)
    if re.search(r"outdoor|outside|open[- ]?air|lawn", t):
        return Op(op="outdoors")
    if re.search(r"indoor|inside|covered|rain", t):
        return Op(op="indoors")
    if re.search(r"move|shift|postpone|prepone|change the date|reschedule", t):
        d = parse_date(t, now)
        return Op(op="move_date", date=d.isoformat() if d else None)
    for key in ("venue", "food", "theme", "cake", "music", "invites", "decor", "sound", "puja"):
        if key in t and re.search(r"cheap|less|save|lower", t):
            return Op(op="cheaper", stop="theme" if key == "decor" else key)
    return None


def _read_move(raw: str, t: str, plan: Plan, now: dt.date) -> list[Op]:
    m = _MOVE.search(t)
    return [Op(op="move_item", item=m["x"], time=m["t"])] if m else []


def _read_have(raw: str, t: str, plan: Plan, now: dt.date) -> list[Op]:
    m = _HAVE.search(t)
    if not m:
        return []
    what = (m["x"] or m["y"]).strip(" .!")
    if resolve_stop(plan, what):
        return [Op(op="have", stop=what, name=clip(raw, 160))]
    return [Op(op="have_item", item=what, name=clip(raw, 160))]


def _read_cook(raw: str, t: str, plan: Plan, now: dt.date) -> list[Op]:
    if _WHO_COOKS.search(t):  # "my aunt is cooking": cooked at home, in the user's own words
        return [Op(op="set_mode", stop="food", mode="home"), Op(op="own", stop="food", text=clip(raw, 160))]
    return [Op(op="set_mode", stop="food", mode="home")] if _COOK.search(t) else []


def _read_without(raw: str, t: str, plan: Plan, now: dt.date) -> list[Op]:
    m = _NEG.search(t)
    if not m:
        return []
    what = next(g for g in m.groups() if g).strip(" .!")
    if re.search(r"\d|guest|people|budget", what):
        return []
    s = resolve_stop(plan, what)
    return [Op(op="remove_stop", stop=s.key)] if s else [Op(op="remove_item", item=what)]


def _read_skip(raw: str, t: str, plan: Plan, now: dt.date) -> list[Op]:
    if not re.search(r"\bskip\b", t):
        return []
    what = re.sub(r"^.*?\bskip\s+(?:the\s+)?", "", t)
    s = resolve_stop(plan, what)
    return [Op(op="skip_stop", stop=s.key)] if s else [Op(op="skip_item", item=what)]


def _read_modes(raw: str, t: str, plan: Plan, now: dt.date) -> list[Op]:
    if _VENUE_HOME.search(t):
        return [Op(op="set_mode", stop="venue", mode="home")]
    mode = next((m for pat, m in _FOOD_MODE if re.search(pat, t)), None)
    return [Op(op="set_mode", stop="food", mode=mode)] if mode else []


def _read_add(raw: str, t: str, plan: Plan, now: dt.date) -> list[Op]:
    m = _ADD.search(t)
    if not m or re.search(r"\d|guest|people|budget", t):
        return []
    return [Op(op="add_stop", name=m["x"].strip())]


def _read_plan(raw: str, t: str, plan: Plan, now: dt.date) -> list[Op]:
    op = regex_intent(t, now)
    return [op] if op else []


READERS = (_read_move, _read_have, _read_cook, _read_without, _read_skip, _read_modes, _read_add, _read_plan)


def parse_ops(text: str, plan: Plan, now: dt.date) -> list[Op]:
    """The common phrasings, read without a model; the first reader that yields valid operations wins."""
    raw = text.strip()
    t = raw.lower().strip(" .!")
    for read in READERS:
        ops = validate_ops(read(raw, t, plan, now), plan)
        if ops:
            return ops
    return []


def closest(text: str, plan: Plan) -> tuple[Op | None, str]:
    """When a request can't be read, the nearest thing I can offer instead."""
    s = resolve_stop(plan, text)
    if s is not None:
        return Op(op="skip_stop", stop=s.key), f"Skip the {s.label.lower()}?"
    it = find_item(plan, text)
    if it is not None:
        return Op(op="skip_item", item=it.id), f"Leave out “{it.title}” on the day?"
    return None, ""


# ------------------------------------------------------------------ applying operations

@dataclass
class Ctx:
    plan: Plan
    now: dt.date
    guests: int
    budget: int
    date: dt.date | None
    chosen: dict[str, str]
    ripples: list[RippleState]
    booked: set[str]
    decided: set[str] = field(default_factory=set)
    titles: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    explain: list[str] = field(default_factory=list)
    budget_set: bool = False  # the user changed the budget: squeeze undecided stops to fit it
    orig: dict[str, str] = field(default_factory=dict)  # the choices before this change
    touched: set[str] = field(default_factory=set)      # stops already explained
    picked: set[str] = field(default_factory=set)       # stops the user changed in this same request
    _ps: PlaceSet | None = None

    def stop(self, key: str | None) -> Stop | None:
        return next((s for s in self.plan.stops if s.key == key), None)

    def profile(self) -> Profile:
        return self.plan.profile.model_copy(update={"guests": self.guests, "budget": self.budget, "date": self.date})

    def locked(self, s: Stop) -> bool:
        """Never swapped by a re-plan: booked, priced by the user or a quote, or not the plan's pick."""
        return s.key in self.booked or s.status != "open" or prices.source(s) != "estimate"

    def kept(self, s: Stop) -> bool:
        """Kept as it is unless a rule now breaks it: locked, decided, or just changed by the user."""
        return self.locked(s) or s.key in self.decided or s.key in self.picked

    def current(self, s: Stop) -> Option:
        return next((o for o in _all(s) if o.id == self.chosen.get(s.key)), s.rec)

    async def places(self) -> PlaceSet:
        if self._ps is None:
            self._ps = await planner.gather_places(self.profile())
        return self._ps

    def drop_choice(self, key: str) -> None:
        self.chosen.pop(key, None)
        self.ripples = [x for x in self.ripples if x.src != key]


def _later(plan: Plan, key: str) -> set[str]:
    keys = [s.key for s in plan.stops]
    return set(keys[keys.index(key) + 1:]) if key in keys else set()


async def rebuild(c: Ctx, s: Stop, mode: str) -> Stop | None:
    """Re-plan one stop from the rules for the current profile and a way of doing it."""
    row = rules.mode_row(s.key, mode)
    if row is None:
        return None
    p = c.profile()
    r = rules.evaluate(p, c.now)
    if s.key == "food":
        draft = templates.food_mode_stop(p, r, mode)
    elif s.key == "venue":
        ps = await c.places()
        cands = templates.rank_venues([x for x in ps.places if x.kind in sum(templates.VENUE_KINDS.values(), ())], p, r)
        draft = templates.venue_mode_stop(p, r, mode, cands)
    else:
        ps = await c.places()
        line = (p.budget or 0) * (r.split.get(s.key) or .08)
        draft = templates.puja_stop(p, r, s.key, [x for x in ps.places if x.kind == "place_of_worship"][:3], line)
    new = planner.build_stop(draft, draft, s.key, s.label, _later(c.plan, s.key), p, c._ps or PlaceSet(), r, [])
    new.mode, new.modes, new.custom, new.lead, new.verb = mode, s.modes, s.custom, row[3], row[4]
    new.rule = s.rule if s.custom else ""
    c.plan.stops = [new if x.key == s.key else x for x in c.plan.stops]
    c.drop_choice(s.key)
    return new


async def set_mode(c: Ctx, s: Stop, mode: str, cascade: bool = True) -> None:
    """Re-plan one stop for a way of doing it, then let dependent stops follow (explained)."""
    row = rules.mode_row(s.key, mode)
    if row is None:
        return
    c.titles.append(f"{s.label}: {row[1].lower()}")
    if row[0] == "have":  # "already have a poojari": covered, nothing to suggest
        s.mode, s.status, s.note = mode, "have", s.note or "Your own poojari"
        s.lead, s.verb = row[3], row[4]
        c.drop_choice(s.key)
        return
    await rebuild(c, s, mode)
    if not cascade:
        return
    for other, keep, to, why in rules.MODE_EFFECTS.get((s.key, mode), []):
        o = c.stop(other)
        if o is not None and o.mode not in keep and o.status == "open" and o.key not in c.booked:
            await set_mode(c, o, to, cascade=False)
            c.notes.append(why)

async def add_stop(c: Ctx, name: str) -> None:
    keys = [s.key for s in c.plan.stops]
    key = rules.extra_stop(name)
    if key is None:
        key = "x-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "extra")
    existing = c.stop(key) or (resolve_stop(c.plan, name) if key in rules.EXTRA_STOPS else None)
    if existing is not None:
        if existing.status == "skip":
            existing.status = "open"
            c.titles.append(f"Bring back the {existing.label.lower()}")
        else:
            c.notes.append(f"The plan already has a {existing.label.lower()} stop.")
        return
    if len(keys) >= MAX_STOPS:
        c.notes.append(f"A plan can have up to {MAX_STOPS} stops. Remove one first.")
        return
    p = c.profile()
    r = rules.evaluate(p, c.now)
    label, draft, lead, verb, per_guest = templates.extra_stop(key, name, p)
    ps = PlaceSet()
    if key == "poojari":
        ps = await c.places()
        draft = templates.puja_stop(p, r, key, [x for x in ps.places if x.kind == "place_of_worship"][:3],
                                    (p.budget or 0) * .08)
    st = planner.build_stop(draft, draft, key, label, set(), p, ps, r, [], per_guest=per_guest)
    st.custom, st.lead, st.verb = True, lead, verb
    st.rule = f"Added by you. {draft.pick.why} Book about {lead} days ahead.".replace("  ", " ")
    if rows := rules.modes_for(key):
        st.modes, st.mode = [Mode(id=m[0], label=m[1], why=m[5]) for m in rows], rows[0][0]
    at = keys.index("day") if "day" in keys else len(keys)
    c.plan.stops.insert(at, st)
    c.titles.append(f"Add {label.lower()}")


def drop_from_invite(invite: str, label: str) -> str:
    """Take a left-out thing out of the invite's lists: "cake, dinner and games" -> "dinner and games"."""
    word = re.escape(label.lower().split()[0].rstrip("s"))
    out = re.sub(rf"(?i)\b(?:a |the )?{word}s?\b(?:,\s*|\s+and\s+)", "", invite)
    out = re.sub(rf"(?i)(?:,\s*|\s+and\s+)(?:a |the )?{word}s?\b", "", out)
    return re.sub(r"[ \t]{2,}", " ", out)


def _day_of(plan: Plan, item: DayItem):
    return next(d for d in plan.days if any(x is item for x in d.items))


async def apply_op(c: Ctx, op: Op) -> None:
    """Apply one validated operation to the context."""
    s = c.stop(op.stop)
    it = find_item(c.plan, op.item) if op.op in ITEM_OPS else None
    if op.op in STOP_OPS and s is None or op.op in ITEM_OPS and it is None:
        return
    if op.op in STOP_OPS and s is not None:
        c.picked.add(s.key)
    if op.op in ("remove_stop", "skip_stop", "set_mode", "cheaper", "choose") and s.key in c.booked:
        c.notes.append(f"The {s.label.lower()} is already booked, so I left it as it is.")
        return
    match op.op:
        case "remove_stop":
            c.plan.stops = [x for x in c.plan.stops if x.key != s.key]
            c.drop_choice(s.key)
            c.ripples = [x for x in c.ripples if x.stop != s.key]
            gone = [x for _, x in _items(c.plan) if x.stop == s.key]
            for d in c.plan.days:
                d.items = [x for x in d.items if x.stop != s.key]
            c.plan.invite = drop_from_invite(c.plan.invite, s.label)
            c.titles.append(f"Without the {s.label.lower()}")
            if gone:
                c.notes.append(f"Also removed from the day: {', '.join(x.title for x in gone)}.")
        case "skip_stop":
            s.status, s.note, s.user_cost = "skip", "", None
            c.plan.invite = drop_from_invite(c.plan.invite, s.label)
            c.titles.append(f"Skip the {s.label.lower()}")
        case "restore_stop":
            s.status, s.note, s.user_cost = "open", "", None
            c.titles.append(f"Bring back the {s.label.lower()}")
        case "have":
            s.status, s.note, s.user_cost = "have", clip(op.name or op.text or "", 160), to_int(op.cost)
            if any(m.id == "have" for m in s.modes):
                s.mode = "have"
            c.drop_choice(s.key)
            c.titles.append(f"You already have the {s.label.lower()}")
        case "own":
            s.status, s.note, s.user_cost = "own", clip(op.text or op.name or "", 160), to_int(op.cost)
            c.drop_choice(s.key)
            c.titles.append(f"{s.label}: your way")
        case "set_mode":
            await set_mode(c, s, op.mode or "")
        case "cheaper":
            cur = next(it2 for st, it2, _ in priced(c.plan, c.guests, c.chosen, c.ripples) if st.key == s.key)
            cheaper = [o for o in _all(s) if line_total(o.price, o.per_guest, c.guests) <
                       line_total(cur.price, cur.per_guest, c.guests) and (o.cap is None or o.cap >= c.guests)]
            c.titles.append(f"A cheaper {s.label.lower()}")
            if cheaper and s.status == "open":
                c.ripples = _take(s, min(cheaper, key=lambda o: o.price), c.chosen, c.ripples)
            else:
                c.notes.append(f"This is already the cheapest {s.label.lower()} I have.")
        case "choose":  # a trade-off picked by the user: this option for this stop
            o = next(x for x in _all(s) if x.id == op.item)
            s.status = "open"
            c.ripples = _take(s, o, c.chosen, c.ripples)
            c.titles.append(f"{s.label}: {o.name}")
        case "add_stop":
            await add_stop(c, op.name or op.text or "")
        case "remove_item":
            d = _day_of(c.plan, it)
            d.items = [x for x in d.items if x is not it]
            c.titles.append(f"No “{it.title.lower()}” on the day")
        case "skip_item":
            it.status = "skip"
            c.titles.append(f"Skip “{it.title.lower()}” on the day")
        case "have_item":
            it.status, it.mine = "have", clip(op.name or op.text or "", 160)
            c.titles.append(f"“{it.title}” is sorted")
        case "own_item":
            it.status, it.mine = "own", clip(op.text or op.name or "", 160)
            c.titles.append(f"“{it.title}”, your way")
        case "move_item":
            it.time = parse_time(op.time) or it.time
            d = _day_of(c.plan, it)
            d.items.sort(key=lambda x: minutes(x.time))
            c.titles.append(f"Move “{it.title.lower()}” to {it.time}")
        case "add_item":
            d = c.plan.days[0] if c.plan.days else None
            if d is not None and len(d.items) < 12:
                n = sum(1 for x in d.items if x.id.startswith(f"{d.id}-u"))
                d.items.append(DayItem(time=parse_time(op.time) or "", title=clip(op.text, CAPS["title"]),
                                       id=f"{d.id}-u{n}", status="own", mine=clip(op.text, 160)))
                d.items.sort(key=lambda x: minutes(x.time))
                c.titles.append(f"Add “{clip(op.text, 40)}” at {parse_time(op.time)}")
        case _:
            await _plan_op(c, op)


def squeeze(c: Ctx, limit: int) -> None:
    """Swap undecided stops to cheaper options, biggest saving first, until the total fits ``limit``.
    Decided, booked and user-priced stops are never touched: the trade-off step asks about those."""
    for _ in range(12):
        cur = priced(c.plan, c.guests, c.chosen, c.ripples)
        if sum(x[2] for x in cur) <= limit:
            return
        best = None
        for s, item, _price in cur:
            if c.kept(s):
                continue
            for o in _all(s):
                if o.id == item.id or (o.cap and o.cap < c.guests):
                    continue
                trial_c = {**c.chosen, s.key: o.id}
                trial_r = [x for x in c.ripples if x.src != s.key] + [
                    RippleState(src=s.key, stop=x.stop, note=x.note, extra=x.extra) for x in o.ripple]
                save = sum(x[2] for x in cur) - sum(x[2] for x in priced(c.plan, c.guests, trial_c, trial_r))
                if save > 0 and (best is None or save > best[0]):
                    best = (save, s, o)
        if best is None:
            return
        c.ripples = _take(best[1], best[2], c.chosen, c.ripples)


def cheapest_fit(c: Ctx, s: Stop | None, pred) -> Option | None:
    """The cheapest option of ``s`` that passes ``pred`` and holds everyone (an unknown size counts)."""
    if s is None:
        return None
    fits = [o for o in _all(s) if pred(o) and (o.cap is None or o.cap >= c.guests)]
    return min(fits, key=lambda o: o.price) if fits else None


async def _plan_op(c: Ctx, op: Op) -> None:
    """Budget, guests, indoors/outdoors and date. Guests, budget and date only change the numbers here;
    ``settle`` then re-plans the stops they affect, once, after every operation has run."""
    amount = to_int(op.amount, 0, 500_000_000) if op.amount is not None else None
    try:
        amount_f = float(op.amount) if op.amount is not None else None
    except (TypeError, ValueError):
        amount_f = None
    venue = c.stop("venue")
    said = clip(op.text, 80) if op.text else ""
    if op.op in ("cut_budget", "set_budget"):
        if op.op == "cut_budget":
            pct = min(max(amount_f or 20, 1), 90)
            c.budget = int(round(c.budget * (1 - pct / 100) / 100) * 100)
            c.titles.append(said or f"Cut the budget by {pct:g}%")
        else:
            c.budget = int(min(max(amount or c.budget, 1000), 500_000_000))
            c.titles.append(said or f"Set the budget to {inr(c.budget)}")
        c.budget_set = True
    elif op.op in ("add_guests", "remove_guests", "set_guests"):
        k = int(amount or 10)
        c.guests = min(5000, {"add_guests": c.guests + k, "remove_guests": max(1, c.guests - k),
                              "set_guests": max(1, k)}[op.op])
        c.titles.append(said or f"{'Add' if op.op == 'add_guests' else 'Remove' if op.op == 'remove_guests' else 'Plan for'} "
                                f"{k if op.op != 'set_guests' else c.guests} guests")
    elif op.op in ("outdoors", "indoors"):
        want = op.op == "outdoors"
        c.titles.append("Move it outdoors" if want else "Move it indoors")
        cur = c.current(venue) if venue is not None else None
        if cur is not None and cur.outdoor == want:
            c.notes.append(f"The venue is already {'open-air' if want else 'covered'}.")
            if want:
                c.ripples = [x for x in c.ripples if x.src != "outdoors"] + [RippleState(
                    src="outdoors", stop="day", note="Rain cover on standby for the open-air venue.",
                    extra=int(round(c.budget * .03, -2)))]
                c.notes.append("I added a rain cover on standby.")
        elif venue is not None and not c.locked(venue) and (fit := cheapest_fit(c, venue, lambda o: o.outdoor == want)):
            c.ripples = _take(venue, fit, c.chosen, c.ripples)
        else:
            c.notes.append(f"I have no {'open-air' if want else 'covered'} venue among the options.")
    elif op.op == "move_date":
        try:
            new = dt.date.fromisoformat(op.date or "")
        except ValueError:
            new = None
        if new is None or new < c.now:
            c.notes.append("I couldn't find a future date in that. Try \"move it to 6 Dec\".")
        else:
            c.date = new
            c.titles.append(said or f"Move it to {new.day} {new:%b %Y}")


async def settle(c: Ctx, old: Profile) -> None:
    """After a change of guests, budget or date: re-plan only the stops it affects. Decided stops stay
    unless a rule now breaks them, and every such change is explained."""
    p = c.profile()
    if p.guests != old.guests:
        await _resize(c, old, p)
    if p.date != old.date and p.date and p.date.month in (6, 7, 8, 9):
        venue = c.stop("venue")
        if venue is not None and not c.locked(venue) and c.current(venue).outdoor:
            fit = cheapest_fit(c, venue, lambda o: not o.outdoor)
            if fit is not None:
                was = c.current(venue).name
                c.ripples = _take(venue, fit, c.chosen, c.ripples)
                c.explain.append(f"The new date is in the monsoon, so the open-air {was} became {fit.name}, which is covered.")
            else:
                c.explain.append("The new date is in the monsoon: plan a rain cover for the open-air venue.")
    if c.budget_set:
        was = dict(c.chosen)
        squeeze(c, c.budget)
        swapped = [s.label.lower() for s in c.plan.stops if c.chosen.get(s.key) != was.get(s.key)]
        if swapped:
            c.touched |= {s.key for s in c.plan.stops if c.chosen.get(s.key) != was.get(s.key)}
            c.explain.append(f"To fit {inr(c.budget)}, I picked cheaper options where you hadn't decided yet: "
                             f"{', '.join(swapped)}.")
    for s in c.plan.stops:  # say what stayed as decided, so nothing changes behind the user's back
        if (s.key in c.decided and s.status == "open" and not c.locked(s) and s.key not in c.touched
                and c.chosen.get(s.key) == c.orig.get(s.key)):
            c.explain.append(f"Kept your decided {s.label.lower()}: {c.current(s).name}.")


async def _resize(c: Ctx, old: Profile, p: Profile) -> None:
    """A new head count: the venue must still hold everyone (a rule that can override a decision),
    an undecided venue follows the size the rules now call for, and an undecided cake is resized."""
    c.touched |= {"venue", "cake", "food"}
    venue = c.stop("venue")
    if venue is not None and not c.locked(venue):
        cur = c.current(venue)
        size_was, size_now = rules.size(old)[0], rules.size(p)[0]
        if cur.cap and cur.cap < c.guests:
            fit = cheapest_fit(c, venue, lambda o: o.id != cur.id)
            if fit is not None:
                c.ripples = _take(venue, fit, c.chosen, c.ripples)
                c.explain.append(f"{cur.name} holds about {cur.cap}, fewer than {c.guests} guests, so the venue is now "
                                 f"{fit.name}" + (" (this overrides your decision: the rule is that everyone must fit)."
                                                  if venue.key in c.decided else "."))
            elif venue.mode and (new := await rebuild(c, venue, venue.mode)) is not None:
                c.explain.append(f"{cur.name} holds about {cur.cap}, fewer than {c.guests} guests, and no option was big "
                                 f"enough, so I looked again: {new.rec.name}.")
        elif size_was != size_now and not c.kept(venue) and venue.mode:
            new = await rebuild(c, venue, venue.mode)
            if new is not None:
                c.explain.append(f"{c.guests} guests need {size_now} rather than {size_was}, so I re-planned the "
                                 f"venue: {new.rec.name}.")
        elif size_was != size_now:
            c.explain.append(f"{c.guests} guests usually need {size_now}. You decided on {cur.name}: check it holds "
                             f"everyone comfortably.")
    cake = c.stop("cake")
    if cake is not None and old.guests and not c.locked(cake) and cake.key not in c.decided:
        ratio = c.guests / old.guests
        for o in _all(cake):
            o.price = max(100, int(round(o.price * ratio / 100) * 100))
        c.explain.append(f"The cake is resized for {c.guests} guests (about 1 kg for every 12).")
    elif cake is not None and cake.key in c.decided and cake.status == "open":
        c.explain.append(f"Your cake was sized for {old.guests}; ask the baker for one that serves {c.guests}.")
    food = c.stop("food")
    if food is not None and food.key in c.decided and not c.locked(food) and c.current(food).per_guest:
        item = c.current(food)
        c.explain.append(f"Kept your food choice; at {inr(item.price)} a guest it is now about "
                         f"{inr(item.price * c.guests)} for {c.guests}.")

def stop_rule(s: Stop, r: rules.RulesResult, p: Profile) -> str:
    """The rule behind a stop, including the way the user chose to do it."""
    base = s.rule if s.custom else r.stop_rule(s.key, p)
    row = rules.mode_row(s.key, s.mode)
    if row and f"Way: {row[1]}." not in base:
        base = f"{base} Way: {row[1]}. {row[5]}".strip()
    return base


def replan(plan: Plan, p: Profile, now: dt.date) -> Plan:
    """Same stops, refreshed rules, explanations and deadlines for a changed profile."""
    r = rules.evaluate(p, now)
    old = plan.profile.guests
    stops = [s.model_copy(update={"rule": stop_rule(s, r, p)}, deep=True) for s in plan.stops]
    if old and p.guests and old != p.guests:  # keep head counts in the prose in step with the profile
        pat = re.compile(rf"\b{old}( guests)\b")
        for s in stops:
            for o in _all(s):
                o.reason = pat.sub(rf"{p.guests}\1", o.reason)
                o.why = pat.sub(rf"{p.guests}\1", o.why)
    return plan.model_copy(update={"profile": p, "title": p.title or plan.title, "stops": stops,
                                   "insights": r.insights, "tasks": planner.make_tasks(stops, p, r, now), "today": now})


def _describe(s: Stop, item: Option, price: int) -> str:
    """One side of a diff row; every amount says whether it is an estimate, a quote or the user's."""
    src = prices.source(s)
    if s.status == "skip":
        return "skipped"
    if s.status == "have":
        return "you have it" + (f": {s.note}" if s.note else "") + (f" · {prices.tag(price, src)}" if price else "")
    if s.status == "own":
        return f"your way: “{s.note}”" + (f" · {prices.tag(price, src)}" if price else "")
    name = f"{s.quote.vendor}'s quote" if src == "quoted" and s.quote else item.name
    return f"{name} · {prices.tag(price, src)}"


URGENT_DAYS = 3  # a task due within this many days (or already late) is urgent
MAX_URGENT_ROWS = 4


def _urgent(t: Task, now: dt.date) -> bool:
    return t.late or (t.due is not None and (t.due - now).days <= URGENT_DAYS)


def _due_text(t: Task, now: dt.date) -> str:
    if t.late:
        return "late: do it today"
    if t.due is None:
        return "no date yet"
    n = (t.due - now).days
    return f"by {t.due.day} {t.due:%b}" + (" (today)" if n == 0 else f" (in {n} day{'s' if n != 1 else ''})")


def timing_rows(before_plan: Plan, after_plan: Plan, p: Profile, new_p: Profile, booked: set[str] = frozenset()) -> list[DiffRow]:
    """Per-guest figure, days to go, book-by dates that moved, and tasks that are newly urgent."""
    rows: list[DiffRow] = []
    now = after_plan.today
    if (p.guests, p.budget) != (new_p.guests, new_p.budget) and p.guests and new_p.guests:
        rows.append(DiffRow(label="Per guest", before=inr((p.budget or 0) // p.guests),
                            after=inr((new_p.budget or 0) // new_p.guests)))
    if p.date != new_p.date and p.date and new_p.date:
        rows.append(DiffRow(label="Days to go", before=str((p.date - now).days), after=str((new_p.date - now).days)))
        old = {t.id: t for t in before_plan.tasks}
        moved = [t for t in after_plan.tasks if t.id in old and t.due != old[t.id].due]
        if moved:
            first_b = min((old[t.id].due for t in moved if old[t.id].due), default=None)
            first_a = min((t.due for t in moved if t.due), default=None)
            if first_b and first_a:
                rows.append(DiffRow(label="Book-by dates", before=f"first by {first_b.day} {first_b:%b}",
                                    after=f"first by {first_a.day} {first_a:%b} · {len(moved)} moved"))
    old = {t.id: t for t in before_plan.tasks}
    urgent = []
    for t in after_plan.tasks:
        if (t.stop or t.id) in booked or not _urgent(t, now):
            continue
        was = old.get(t.id)
        if was is None or not _urgent(was, now):
            urgent.append(DiffRow(label=f"Now urgent: {clip(t.text, 60)}", before=_due_text(was, now) if was else "—",
                                  after=_due_text(t, now)))
    if len(urgent) > MAX_URGENT_ROWS + 1:
        more = len(urgent) - MAX_URGENT_ROWS
        urgent = urgent[:MAX_URGENT_ROWS] + [DiffRow(label="Now urgent", before="—",
                                                     after=f"{more} more tasks: see My plan")]
    return rows + urgent


def diff(before_plan: Plan, after_plan: Plan, before: list, after: list, p: Profile, new_p: Profile,
         booked: set[str] = frozenset()) -> tuple[list[DiffRow], int]:
    rows: list[DiffRow] = []
    if new_p.guests != p.guests:
        rows.append(DiffRow(label="Guests", before=str(p.guests), after=str(new_p.guests)))
    if new_p.budget != p.budget:
        rows.append(DiffRow(label="Budget", before=inr(p.budget or 0), after=inr(new_p.budget or 0)))
    if new_p.date != p.date and p.date and new_p.date:
        rows.append(DiffRow(label="Date", before=f"{p.date.day} {p.date:%b}", after=f"{new_p.date.day} {new_p.date:%b}"))
    b_by = {s.key: (s, it, pr) for s, it, pr in before}
    a_by = {s.key: (s, it, pr) for s, it, pr in after}
    freed = 0
    for key in list(b_by) + [k for k in a_by if k not in b_by]:
        b, a = b_by.get(key), a_by.get(key)
        if a is None:
            rows.append(DiffRow(label=b[0].label, before=_describe(*b), after="removed"))
            freed += b[2]
        elif b is None:
            rows.append(DiffRow(label=a[0].label, before="not in the plan", after=_describe(*a)))
        elif (a[1].id, a[2], a[0].status, a[0].note, a[0].mode, a[1].name, a[0].quote, a[0].paid) != \
                (b[1].id, b[2], b[0].status, b[0].note, b[0].mode, b[1].name, b[0].quote, b[0].paid):
            rows.append(DiffRow(label=a[0].label, before=_describe(*b), after=_describe(*a)))
            if a[0].status == "skip" and b[0].status != "skip":
                freed += b[2]
    rows += timing_rows(before_plan, after_plan, p, new_p, booked)
    b_items = {it.id: it for _, it in _items(before_plan)}
    a_items = {it.id: it for _, it in _items(after_plan)}
    for iid, it in b_items.items():
        na = a_items.get(iid)
        if na is None:
            rows.append(DiffRow(label="On the day", before=f"{it.time} {it.title}", after="removed"))
        elif (na.time, na.status, na.mine, na.title) != (it.time, it.status, it.mine, it.title):
            after_txt = {"skip": "skipped", "have": f"sorted: {na.mine}" if na.mine else "sorted",
                         "own": f"your way: “{na.mine}”"}.get(na.status, f"{na.time} {na.title}")
            rows.append(DiffRow(label="On the day", before=f"{it.time} {it.title}",
                                after=after_txt if na.status != "keep" else f"{na.time} {na.title}"))
    for iid, it in a_items.items():
        if iid not in b_items:
            rows.append(DiffRow(label="On the day", before="—", after=f"{it.time} {it.title}"))
    return rows, freed


PLAN_FIELDS = {"cut_budget", "set_budget", "add_guests", "remove_guests", "set_guests", "move_date"}


def ways_out(new_p: Profile, ops: list[Op]) -> tuple[str, str, list[WayOut]]:
    """The rules' "let's make this work" ways for numbers below the floor, as operations to re-run
    with the revision (a later set_guests or set_budget replaces the earlier one)."""
    block = rules.budget_block(new_p)
    if block is None:
        return "", "", []
    ways = []
    for w in block.ways:
        extra: list[Op] = []
        a = w.apply or {}
        if "guests" in a:
            extra.append(Op(op="set_guests", amount=a["guests"], text=w.title))
        if "budget" in a:
            extra.append(Op(op="set_budget", amount=a["budget"], text=w.title if "guests" not in a else None))
        if "food" in a:
            extra.append(Op(op="own", stop="food", text=a["food"]))
        ways.append(WayOut(title=w.title, detail=w.detail, ops=[o for o in ops if o.op not in PLAN_FIELDS or
                                                               not any(e.op == o.op for e in extra)] + extra))
    return block.title, block.body, ways


async def run_ops(plan: Plan, chosen: dict[str, str], ripples: list[RippleState], booked: list[str], ops: list[Op],
                  now: dt.date, request: str, source: Source, decided: list[str] = (),
                  assumed: list[str] = (), unread: list[str] = ()) -> AdjustOut:
    """Apply validated operations and describe the result as ONE diff.

    Guests, budget and date are applied first, then the stop and event-day operations (with the new
    numbers), then ``settle`` re-plans the stops the new numbers affect, once, so "50 more guests, ₹35k,
    two weeks earlier and no cake" is one revision with one diff."""
    p = plan.profile
    keys = [s.key for s in plan.stops]
    chosen = {k: v for k, v in chosen.items() if k in keys}
    ripples = [x for x in ripples if x.stop in keys]
    before = priced(plan, p.guests or 1, chosen, ripples)
    c = Ctx(plan=plan.model_copy(deep=True), now=now, guests=p.guests or 1, budget=p.budget or 0, date=p.date,
            chosen=dict(chosen), ripples=list(ripples), booked=set(booked), decided=set(decided) & set(keys),
            orig=dict(chosen))
    ops = sorted(ops, key=lambda o: o.op not in PLAN_FIELDS)  # stable: the numbers first
    for op in ops:
        await apply_op(c, op)
    if (c.guests, c.budget, c.date) != (p.guests or 1, p.budget or 0, p.date):
        await settle(c, p)
    new_p = p.model_copy(update={"guests": c.guests, "budget": c.budget, "date": c.date})
    new_plan = replan(c.plan, new_p, now)
    after = priced(new_plan, c.guests, c.chosen, c.ripples)
    rows, freed = diff(replan(plan, p, now), new_plan, before, after, p, new_p, set(booked))
    tb, ta = sum(x[2] for x in before), sum(x[2] for x in after)
    estimated = any(prices.source(x[0]) == "estimate" for x in after)
    rows.append(DiffRow(label="Total (includes estimates)" if estimated else "Total", before=inr(tb), after=inr(ta)))
    old_band, new_band = rules.budget_band(p)[0], rules.budget_band(new_p)[0]
    if old_band != new_band and old_band and new_band:
        rows.append(DiffRow(label="Budget feel", before=old_band, after=new_band))
    note = " ".join(c.notes)
    if freed:
        note += f" {inr(freed)} freed and kept as savings."
    gap = ta - c.budget
    block_title, block_body, ways = ways_out(new_p, ops) if new_band == "impossible" else ("", "", [])
    if not block_title:  # when blocked, the ways out say it
        note += (f" Still {inr(gap)} over budget: choose where the money should go." if gap > 0
                 else f" Fits the budget with {inr(-gap)} to spare.")
    explain = list(assumed) + c.explain + [f"I didn't act on “{clip(u, 80)}”: {CAN_DO}" for u in unread]
    title = "; ".join(dict.fromkeys(c.titles)) or "Nothing to change"
    return AdjustOut(understood=bool(c.titles), title=clip(title[0].upper() + title[1:], 160), rows=rows if c.titles else [],
                     note=note.strip(), profile=new_p, chosen=c.chosen, ripples=c.ripples, plan=new_plan,
                     source=source, ops=ops, freed=freed, explain=explain, over=max(0, gap) if not block_title else 0,
                     block_title=block_title, block_body=block_body, ways=ways)


# ------------------------------------------------------------------ the two entry points

def _model_data(inp: AdjustIn, request: str | None = None) -> dict:
    plan, p = inp.plan, inp.plan.profile
    cur = priced(plan, p.guests or 1, inp.chosen, inp.ripples)
    return {
        "request": request or inp.request,
        "event": {"type": p.type, "guests": p.guests, "budget": p.budget, "date": p.date.isoformat() if p.date else None},
        "stops": [{"key": s.key, "label": s.label, "current": it.name, "status": s.status, "mode": s.mode,
                   "modes": [m.id for m in s.modes]} for s, it, _ in cur],
        "day": [{"id": it.id, "time": it.time, "title": it.title} for _, it in _items(plan)][:16],
        "can_add": [row[0] for row in rules.EXTRA_STOPS.values()],
    }


async def _revision(inp: AdjustIn, rev: revise.Revision, now: dt.date) -> AdjustOut:
    """Guests, budget and date read by code; any other clause goes to the rules parser, then the model."""
    plan = inp.plan
    ops = [Op.model_validate(o) for o in rev.ops()]
    source: Source = "rules"
    unread: list[str] = []
    for clause in rev.rest:
        got = parse_ops(clause, plan, now)
        ops += got
        if not got:
            unread.append(clause)
    if unread:
        res = await llm.ask("adjust", INSTRUCTIONS, _model_data(inp, "; ".join(unread)), OpsDraft,
                            fallback=lambda: OpsDraft(), max_tokens=400, timeout=15)
        extra = [o for o in validate_ops(res.value.ops, plan) if o.op not in PLAN_FIELDS]  # code already read those
        if extra:
            ops, source, unread = ops + extra, res.source, []
    return await run_ops(plan, inp.chosen, inp.ripples, inp.booked, ops[:MAX_OPS], now, inp.request, source,
                         inp.decided, rev.assumed + rev.problems, unread)


async def adjust(inp: AdjustIn, now: dt.date) -> AdjustOut:
    """A request in words -> operations -> a re-planned diff.

    Guests, budget and date are read deterministically first ("50 more guests, ₹35k, two weeks earlier");
    anything else goes to the model, else the rules parser. Never silently does nothing: a request
    neither can read gets an honest list of what I can do, plus the closest operation as a one-tap
    suggestion.
    """
    plan = inp.plan
    rev = revise.read(inp.request, plan.profile, now)
    if rev.found:
        return await _revision(inp, rev, now)
    res = await llm.ask("adjust", INSTRUCTIONS, _model_data(inp), OpsDraft,
                        fallback=lambda: OpsDraft(), max_tokens=400, timeout=15)
    ops, source = validate_ops(res.value.ops, plan), res.source
    if not ops:
        ops = parse_ops(inp.request, plan, now)
        if ops:  # the rules parser understood what the model did not
            source = "fallback" if source == "ai" else source
    if not ops:
        reply = clip(res.value.reply, CAPS["reply"]) if res.source == "ai" else ""
        sug, sug_text = closest(inp.request, plan)
        note = " ".join(rev.problems + ([reply] if reply else []) + [CAN_DO])
        return AdjustOut(understood=False, title="", rows=[], note=note,
                         profile=plan.profile, chosen=inp.chosen, ripples=inp.ripples, plan=plan, source=source,
                         suggest=sug, suggest_text=sug_text)
    return await run_ops(plan, inp.chosen, inp.ripples, inp.booked, ops, now, inp.request, source, inp.decided)


async def apply(inp: ApplyIn, now: dt.date) -> AdjustOut:
    """Operations straight from the UI (a stop's mode, the Details page, a way out, a trade-off).
    Validated exactly like the model's."""
    ops = validate_ops(inp.ops, inp.plan)
    if not ops:
        return AdjustOut(understood=False, title="", rows=[], note="That change doesn't fit this plan.",
                         profile=inp.plan.profile, chosen=inp.chosen, ripples=inp.ripples, plan=inp.plan,
                         source="rules")
    return await run_ops(inp.plan, inp.chosen, inp.ripples, inp.booked, ops, now, "", "rules", inp.decided)
