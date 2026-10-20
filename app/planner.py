"""Profile -> rules -> one model call -> validators -> Plan. Also "Ask to adjust" and .ics.

The model writes the plan (names, reasons, the run-sheet, the invite) inside limits set by
the rules engine. Code then repairs everything it cannot trust: stop keys, place ids,
prices, budget totals, deadlines and text lengths. Missing pieces come from the
deterministic draft, so a partial reply still yields a complete plan.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import os
import re
from typing import Literal

from pydantic import BaseModel, Field

from . import llm, places, rules, templates
from .intake import parse_date, parse_money
from .places import Place, PlaceSet
from .rules import RulesResult, inr
from .schemas import (Block, Day, DayItem, DraftOption, DraftPlan, DraftStop, Option, PhotoHint, Plan, Profile,
                      Ripple, Source, Stop, Strict, Task)
from .validators import (CAPS, cap_list, clamp_due, clip, drop_money, drop_wrong_kind, fit_budget, fix_option_kinds,
                         line_total, sentences, to_int)

log = logging.getLogger("waypoint.planner")

KINDS = ("Cheaper", "Closer", "Different vibe")
CLOSER_OK = {"venue", "food", "cake"}  # "closer" only means something where distance matters
ARTS = {"arrive", "games", "cake", "dinner", "music", "ritual"}
TONES = ("dusk", "garden", "blush", "night", "mustard", "sea")
NO_PHOTO = {
    "invites": "Invites are a design, not a place. I'd rather show nothing than something that isn't real.",
    "day": "A run-sheet has nothing to photograph. You'll see it on the event-day screen.",
}
PLACE_WAIT = float(os.getenv("WP_PLACES_WAIT", "50"))
# Words that make a name look like a specific business; such names need a verified place id.
BUSINESS = re.compile(r"(?i)\b(bakery|bakers|bakehouse|patisserie|caterers?|catering co|sweets|mithai(?:wala)?|"
                      r"restaurant|caf[eé]|dhaba|hotel|banquets?|studios?|pvt|ltd|& sons|enterprises)\b")

INSTRUCTIONS = """
You are Waypoint's planner for celebrations in India; all prices are Indian rupees.
The data block has: event (the profile), rules (decisions already made by a deterministic engine:
follow them), stops (each with key, label, target budget and whether its price is per guest),
venue_places and bakeries (REAL places from OpenStreetMap, each with an id).

For every stop, give one recommendation ("pick") and 2 or 3 alternatives ("options") whose kind is
"Cheaper", "Closer" or "Different vibe". Keep the picks' total within the event budget and close to each
stop's target. For the venue stop you MUST use a place_id from venue_places (name it exactly as given);
you may use a bakery's place_id for the cake. Never invent the name of a business: without a place_id,
describe the option generically. Personalise names and ideas to who the event is for and what they love,
and respect every must-have in the rules. reason: at most 20 words. why: at most 40 words, naming the
rule or the person it comes from. photo_query: 2-4 plain words to search photos of the idea.
Also write "days": the event-day run-sheet with 4 to 6 items (weddings: up to 3 functions), and
"invite": a warm WhatsApp invite under 70 words with the real date and area, no placeholders.

JSON shape:
{"stops": [{"key": "venue", "pick": {"name": "", "price": 0, "place_id": "", "reason": "", "why": "",
 "photo_query": "", "outdoor": false}, "options": [{"kind": "Cheaper", "name": "", "price": 0,
 "place_id": "", "reason": "", "outdoor": false, "photo_query": ""}]}],
 "days": [{"label": "", "title": "", "items": [{"time": "6:00 PM", "title": "", "note": "",
 "art": "arrive|games|cake|dinner|music|ritual"}]}],
 "invite": ""}
For per-guest stops, price is per guest.
"""

ADJUST_INSTRUCTIONS = """
You turn a request to change an event plan into one structured action. The data block has request
(the user's words), event (a summary) and stops (keys with current choices).
Actions: cut_budget (amount = percent to cut), set_budget (amount = new total in rupees),
add_guests (amount = how many more), remove_guests (amount = how many fewer), set_guests (amount = new count),
outdoors, indoors, cheaper (stop = which stop key to make cheaper), move_date (date = YYYY-MM-DD),
unknown (when it is none of these; then explain in reply what you can do).
Reply with JSON only: {"action": "", "amount": null, "stop": null, "date": null, "reply": "<one short sentence>"}
"""


class Blocked(Exception):
    """Planning cannot start: the rules found a blocking problem."""

    def __init__(self, block: Block):
        super().__init__(block.title)
        self.block = block


def today() -> dt.date:
    """Today's date; ``WP_TODAY`` pins it for demos and tests."""
    pinned = os.getenv("WP_TODAY", "")
    return dt.date.fromisoformat(pinned) if pinned else dt.date.today()


# ------------------------------------------------------------------ assembly helpers

def _tone(seed: str) -> str:
    return TONES[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(TONES)]


def _kind(raw: str) -> str | None:
    low = raw.lower()
    if "cheap" in low or "budget" in low or "save" in low:
        return "Cheaper"
    if "close" in low or "near" in low:
        return "Closer"
    if "vibe" in low or "different" in low or "style" in low or "alternative" in low:
        return "Different vibe"
    return None


def _query(text: str) -> str | None:
    q = re.sub(r"[^\w\s'-]", " ", text or "", flags=re.UNICODE)
    q = re.sub(r"\s+", " ", q).strip()[: CAPS["query"]]
    return q or None


def make_option(d: DraftOption, *, key: str, oid: str, kind: str, p: Profile, ps: PlaceSet,
                fallback: DraftOption, per_guest: bool, rejected: list[str]) -> Option:
    """Turn one draft option into a validated Option. Place ids must exist in ``ps``."""
    guests = p.guests or 1
    price = to_int(d.price)
    if price is None:
        price = to_int(fallback.price) or 0
    if per_guest and price > max(1, (p.budget or 0)) // guests and price > 5000:
        price //= guests  # the model gave a total for a per-guest line
    place: Place | None = None
    pid = (d.place_id or "").strip()
    if pid:
        place = ps.get(pid)
        if place is None:
            rejected.append(pid)
    name = clip(d.name, CAPS["name"])
    if place is None and name:  # a real place named without its id: attach it, never trust the bare name
        place = next((x for x in ps.places if x.name.lower() == name.lower()), None)
    where = None
    if key != "venue" and (place is not None and name.lower() == place.name.lower()
                           or place is None and BUSINESS.search(name)):
        name = clip(fallback.name, CAPS["name"])  # describe the item; the shop goes in ``where``
    if key == "venue":
        if place is not None:
            name = place.name
        elif p.type != "house-warming":
            # Without a verified place the name may be invented: use the generic fallback name,
            # unless the fallback itself names a real place that is not this one.
            fb_place = ps.get(fallback.place_id)
            name = clip(fallback.name, CAPS["name"]) if fb_place is None else \
                f"A {fb_place.label.lower()} near {templates.area_short(p)}"
    elif place is not None:
        where = place.name
    if not name:
        name = clip(fallback.name, CAPS["name"]) or "To be decided"
    photo: PhotoHint | None = None
    if key == "venue":
        if place is not None and place.photo is not None:
            photo = place.photo
        elif place is not None:
            photo = PhotoHint(lat=place.lat, lon=place.lon, relation="near")
        elif ps.center is not None:
            photo = PhotoHint(lat=ps.center.lat, lon=ps.center.lon, relation="near")
    elif key not in NO_PHOTO:
        q = _query(d.photo_query) or _query(fallback.photo_query)
        photo = PhotoHint(query=q, relation="inspiration") if q else None
    reason, why = drop_money(d.reason), drop_money(d.why)
    if key == "venue" and place is not None:
        # The facts about a real place come from OSM, not from the model's prose.
        facts = f"{place.label}, {place.dist_km} km from {templates.area_short(p)}."
        prose = " ".join(x for x in sentences(reason) if " km" not in x)  # distance is in the facts
        reason = " ".join(x for x in (facts, drop_wrong_kind(prose, place.label)) if x)
        why = drop_wrong_kind(why, place.label)
    return Option(
        id=oid, kind=kind, name=name, price=price, per_guest=per_guest,
        cap=place.capacity if place else None, outdoor=bool(place.outdoor if place else d.outdoor),
        reason=clip(reason, CAPS["reason"]) or clip(drop_money(fallback.reason), CAPS["reason"]),
        why=clip(why, CAPS["why"]) or clip(drop_money(fallback.why), CAPS["why"]),
        place_id=place.id if place else None, where=where, dist_km=place.dist_km if place else None,
        lat=place.lat if place else None, lon=place.lon if place else None,
        photo=photo, tone=_tone(oid + name),
    )


def _ripples(d: DraftOption, later: set[str], budget: int) -> list[Ripple]:
    if d.ripple is None or d.ripple.stop not in later:
        return []
    extra = to_int(abs(to_int(d.ripple.extra, -10**9) or 0), 0, int(budget * .2)) or 0
    sign = -1 if (to_int(d.ripple.extra, -10**9) or 0) < 0 else 1
    note = clip(d.ripple.note, CAPS["note"])
    return [Ripple(stop=d.ripple.stop, note=note, extra=sign * extra)] if note else []


def build_stop(ds: DraftStop, fb: DraftStop, key: str, label: str, later: set[str], p: Profile,
               ps: PlaceSet, r: RulesResult, rejected: list[str]) -> Stop:
    per_guest = key == "food"
    rec = make_option(ds.pick, key=key, oid=f"{key}-0", kind="Recommended", p=p, ps=ps, fallback=fb.pick,
                      per_guest=per_guest, rejected=rejected)
    options: list[Option] = []
    seen = {rec.name.lower()}
    sources = [(o, _kind(o.kind)) for o in cap_list(ds.options, 5)] + [(o, _kind(o.kind)) for o in fb.options]
    used_kinds: set[str] = set()
    for i, (o, kind) in enumerate(sources):
        if kind is None or kind in used_kinds or len(options) >= 3 or (kind == "Closer" and key not in CLOSER_OK):
            continue
        fb_same = next((f for f in fb.options if _kind(f.kind) == kind), fb.pick)
        opt = make_option(o, key=key, oid=f"{key}-{len(options) + 1}", kind=kind, p=p, ps=ps, fallback=fb_same,
                          per_guest=per_guest, rejected=rejected)
        if opt.name.lower() in seen:
            continue
        if kind == "Closer" and key == "venue" and (opt.dist_km is None or rec.dist_km is None
                                                     or opt.dist_km >= rec.dist_km):
            continue  # "closer" must be measurably closer
        opt.ripple = _ripples(o, later, p.budget or 0)
        seen.add(opt.name.lower())
        used_kinds.add(kind)
        options.append(opt)
    stop = Stop(key=key, label=label, rule=r.stop_rule(key, p), nophoto_msg=NO_PHOTO.get(key, ""),
                rec=rec, options=options)
    fix_option_kinds(stop, p.guests or 1)
    return stop


def make_tasks(stops: list[Stop], p: Profile, r: RulesResult, now: dt.date) -> list[Task]:
    """Booking deadlines from the rules' lead times, clamped to [today, event day]."""
    by_key = {s.key: s for s in stops}
    tasks: list[Task] = []
    dues: dict[str, dt.date | None] = {}
    for key in r.book_order:
        if key not in by_key:
            continue
        lead = r.lead[key]
        raw = p.date - dt.timedelta(days=lead) if p.date else None
        due, late = clamp_due(raw, now, p.date)
        dues[key] = due
        verb = rules.TASK_VERBS.get(key, "Book")
        tasks.append(Task(id=key, stop=key, verb=verb, text=f"{verb}: {by_key[key].rec.name}", due=due, late=late,
                          why=f"Usually booked about {lead} days ahead."))
    for c in r.mandatory:
        tasks.append(Task(id=c.id, stop=c.stop, text=c.text, why=c.why, due=dues.get(c.stop or ""),
                          mandatory=True))
    far = dt.date.max
    return sorted(tasks, key=lambda t: (t.due or far, t.mandatory))


def clean_days(draft: list, fallback: list) -> list[Day]:
    out: list[Day] = []
    for i, d in enumerate(cap_list(draft, 3)):
        items = [DayItem(time=clip(it.time, CAPS["time"]), title=clip(it.title, CAPS["title"]),
                         note=clip(it.note, CAPS["note"]), art=it.art if it.art in ARTS else "arrive")
                 for it in cap_list(d.items, 8) if clip(it.title, 80)]
        if len(items) >= 2:
            out.append(Day(id=f"d{i}", label=clip(d.label, CAPS["label"]) or "The day",
                           title=clip(d.title, CAPS["title"]) or "The day, hour by hour", items=items))
    if out:
        return out
    return clean_days(fallback, []) if fallback else []


def assemble(draft: DraftPlan, fb: DraftPlan, p: Profile, r: RulesResult, ps: PlaceSet, now: dt.date,
             source: Source) -> Plan:
    """Validate and repair a draft into a Plan whose recommended total fits the budget."""
    rejected: list[str] = []
    order = rules.stops_for(p.type)
    stops: list[Stop] = []
    for i, (key, label) in enumerate(order):
        fbs = next(s for s in fb.stops if s.key == key)
        ds = next((s for s in draft.stops if s.key == key), fbs)
        later = {k for k, _ in order[i + 1:]}
        stops.append(build_stop(ds, fbs, key, label, later, p, ps, r, rejected))
    notes: list[str] = []
    if rejected:
        log.info("rejected %d place id(s) not in the fetched set", len(rejected))
        notes.append("A suggested place could not be verified, so it was replaced with a generic suggestion.")
    if p.budget is not None and fit_budget(stops, p.budget, p.guests or 1):
        notes.append(f"Prices were scaled down so the plan fits your {inr(p.budget)} budget.")
    if ps.places:
        notes.append("Venues and bakeries come from OpenStreetMap; check prices and availability with them.")
    elif p.area:
        notes.append("Live place listings were unavailable, so venues are described, not named.")
    invite = clip(draft.invite.replace("null", "").replace("None", ""), CAPS["invite"]) or fb.invite
    if "{" in invite or "[" in invite:  # unfilled template slots from the model
        invite = fb.invite
    return Plan(title=p.title or templates.DEFAULT_THEME[p.type][0], profile=p, stops=stops,
                days=clean_days(draft.days, fb.days), tasks=make_tasks(stops, p, r, now), invite=invite,
                insights=r.insights, source=source, today=now, area_point=ps.center, notes=notes)


# ------------------------------------------------------------------ build

async def gather_places(p: Profile) -> PlaceSet:
    """Venues for the area, waiting at most ``PLACE_WAIT`` seconds (prefetch makes this quick)."""
    if not p.area:
        return PlaceSet()
    try:
        return await asyncio.wait_for(places.venues(p.area, p.guests), timeout=PLACE_WAIT)
    except TimeoutError:
        return PlaceSet(center=await places.geocode(p.area))


def _place_brief(pl: Place) -> dict:
    out = {"id": pl.id, "name": pl.name, "kind": pl.label, "km": pl.dist_km}
    if pl.wheelchair:
        out["wheelchair"] = pl.wheelchair
    if pl.veg:
        out["veg"] = True
    if pl.capacity:
        out["capacity"] = pl.capacity
    return out


def plan_request(p: Profile, r: RulesResult, venues: list[Place], bakeries: list[Place]) -> dict:
    """The data block sent to the model: the profile, the rules and the real places."""
    budget = p.budget or 0
    stops = []
    for key, label in rules.stops_for(p.type):
        line = budget * r.split[key]
        item = {"key": key, "label": label, "target": round(line, -2)}
        if key == "food" and p.guests:
            item = {"key": key, "label": label, "target_per_guest": int(line / p.guests), "per_guest": True}
        stops.append(item)
    return {
        "event": p.model_dump(mode="json", exclude_defaults=True),
        "rules": {
            "budget_band": r.band, "per_guest": r.per_guest, "days_left": r.days_left, "urgency": r.urgency,
            "late": r.late, "season": r.season, "venue_size": r.venue_size, "catering": r.catering,
            "must_have": [c.text for c in r.mandatory],
            "advice": [i.why for i in r.insights][:6],
        },
        "stops": stops,
        "venue_places": [_place_brief(x) for x in venues[:10]],
        "bakeries": [_place_brief(x) for x in bakeries[:3]],
    }


async def build_plan(p: Profile, now: dt.date, use_ai: bool = True) -> Plan:
    """The full draft plan: rules, real places, one model call, validators.

    ``use_ai=False`` skips the model and returns the labelled rule-based plan (offline demo).
    """
    r = rules.evaluate(p, now)
    if r.blocks:
        raise Blocked(r.blocks[0])
    ps = await gather_places(p)
    venues = templates.rank_venues(places.venue_candidates(ps, p.guests), p, r)
    bakeries = places.bakery_candidates(ps)
    fb = templates.draft_plan(p, r, venues, bakeries)
    if not use_ai:
        return assemble(fb, fb, p, r, ps, now, "fallback")
    res = await llm.ask("plan", INSTRUCTIONS, plan_request(p, r, venues, bakeries), DraftPlan,
                        fallback=lambda: fb, max_tokens=3500)
    return assemble(res.value, fb, p, r, ps, now, res.source)


# ------------------------------------------------------------------ adjust

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
    request: str = Field(min_length=1, max_length=300)


Action = Literal["cut_budget", "set_budget", "add_guests", "remove_guests", "set_guests", "outdoors",
                 "indoors", "cheaper", "move_date", "unknown"]


class Intent(BaseModel):
    action: str = "unknown"
    amount: float | None = None
    stop: str | None = None
    date: str | None = None
    reply: str = ""


class DiffRow(BaseModel):
    label: str
    before: str
    after: str


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


def regex_intent(text: str, now: dt.date) -> Intent:
    """Deterministic reading of common requests (fallback and mock)."""
    t = text.lower()
    num = re.search(r"(\d+(?:\.\d+)?)", t.replace(",", ""))
    n = float(num.group(1)) if num else None
    if re.search(r"(cut|reduce|lower|trim|less|decrease)", t) and "%" in t:
        return Intent(action="cut_budget", amount=n)
    if re.search(r"(cut|reduce|lower|trim|decrease).*budget|budget.*(cut|lower|less)|cheaper overall", t):
        return Intent(action="cut_budget", amount=20)
    if re.search(r"budget.*(to|of)\s*₹?\s*\d|set budget", t):
        return Intent(action="set_budget", amount=parse_money(t, loose=True))
    if re.search(r"\b(add|invite|more)\b", t) and n:
        return Intent(action="add_guests", amount=n)
    if re.search(r"\b(remove|drop|fewer|less|cut|reduce)\b", t) and n:
        return Intent(action="remove_guests", amount=n)
    if re.search(r"outdoor|outside|open[- ]?air|garden|lawn", t):
        return Intent(action="outdoors")
    if re.search(r"indoor|inside|covered|rain", t):
        return Intent(action="indoors")
    if re.search(r"move|shift|postpone|prepone|change the date|reschedule", t):
        d = parse_date(t, now)
        return Intent(action="move_date", date=d.isoformat() if d else None)
    for key in ("venue", "food", "theme", "cake", "music", "invites", "decor"):
        if key in t and re.search(r"cheap|less|save|lower", t):
            return Intent(action="cheaper", stop="theme" if key == "decor" else key)
    return Intent(action="unknown")


def _all(stop: Stop) -> list[Option]:
    return [stop.rec, *stop.options]


def priced(plan: Plan, guests: int, chosen: dict[str, str], ripples: list[RippleState]) -> list[tuple[Stop, Option, int]]:
    """(stop, chosen option, price incl. knock-on extras) for every stop."""
    out = []
    for s in plan.stops:
        item = next((o for o in _all(s) if o.id == chosen.get(s.key)), s.rec)
        extra = sum(x.extra for x in ripples if x.stop == s.key)
        out.append((s, item, max(0, line_total(item.price, item.per_guest, guests) + extra)))
    return out


def _take(stop: Stop, o: Option, chosen: dict[str, str], ripples: list[RippleState]) -> list[RippleState]:
    chosen[stop.key] = o.id
    kept = [x for x in ripples if x.src != stop.key]
    return kept + [RippleState(src=stop.key, stop=x.stop, note=x.note, extra=x.extra) for x in o.ripple]


def replan(plan: Plan, p: Profile, now: dt.date) -> Plan:
    """Same stops, refreshed rules, explanations and deadlines for a changed profile."""
    r = rules.evaluate(p, now)
    old = plan.profile.guests
    stops = [s.model_copy(update={"rule": r.stop_rule(s.key, p)}, deep=True) for s in plan.stops]
    if old and p.guests and old != p.guests:  # keep head counts in the prose in step with the profile
        pat = re.compile(rf"\b{old}( guests)\b")
        for s in stops:
            for o in _all(s):
                o.reason = pat.sub(rf"{p.guests}\1", o.reason)
                o.why = pat.sub(rf"{p.guests}\1", o.why)
    return plan.model_copy(update={"profile": p, "title": p.title or plan.title, "stops": stops,
                                   "insights": r.insights, "tasks": make_tasks(stops, p, r, now), "today": now})


async def adjust(inp: AdjustIn, now: dt.date) -> AdjustOut:
    """Interpret a free-text change (model), then re-plan it deterministically and diff."""
    plan, p = inp.plan, inp.plan.profile
    keys = [s.key for s in plan.stops]
    data = {"request": inp.request, "event": {"type": p.type, "guests": p.guests, "budget": p.budget,
                                               "date": p.date.isoformat() if p.date else None},
            "stops": [{"key": s.key, "current": it.name} for s, it, _ in priced(plan, p.guests or 1, inp.chosen,
                                                                                  inp.ripples)]}
    res = await llm.ask("adjust", ADJUST_INSTRUCTIONS, data, Intent,
                        fallback=lambda: regex_intent(inp.request, now), max_tokens=200,
                        timeout=15)
    intent, source = res.value, res.source
    if intent.action not in Action.__args__ or intent.action == "unknown":  # type: ignore[attr-defined]
        det = regex_intent(inp.request, now)
        if det.action != "unknown" or intent.action not in Action.__args__:  # type: ignore[attr-defined]
            # The rules parser understood what the model did not: say so instead of doing nothing.
            intent, source = det, ("fallback" if source == "ai" else source)
    guests, budget, date = p.guests or 1, p.budget or 0, p.date
    chosen = {k: v for k, v in inp.chosen.items() if k in keys}
    ripples = [x for x in inp.ripples if x.stop in keys]
    before = priced(plan, guests, chosen, ripples)
    amount = intent.amount if intent.amount and intent.amount > 0 else None
    title, note = "What changes", ""
    venue = next((s for s in plan.stops if s.key == "venue"), None)

    booked = set(inp.booked)

    def cheapest_fit(pred) -> Option | None:
        if venue is None or venue.key in booked:
            return None
        fits = [o for o in _all(venue) if pred(o) and (o.cap is None or o.cap >= guests)]
        return min(fits, key=lambda o: o.price) if fits else None

    def squeeze(limit: int) -> None:
        nonlocal ripples
        for _ in range(12):
            cur = priced(plan, guests, chosen, ripples)
            if sum(x[2] for x in cur) <= limit:
                return
            best = None
            for s, item, price in cur:
                if s.key in booked:
                    continue
                for o in _all(s):
                    if o.id == item.id or (o.cap and o.cap < guests):
                        continue
                    trial_c = {**chosen, s.key: o.id}
                    trial_r = [x for x in ripples if x.src != s.key] + [
                        RippleState(src=s.key, stop=x.stop, note=x.note, extra=x.extra) for x in o.ripple]
                    save = sum(x[2] for x in cur) - sum(x[2] for x in priced(plan, guests, trial_c, trial_r))
                    if save > 0 and (best is None or save > best[0]):
                        best = (save, s, o)
            if best is None:
                return
            ripples = _take(best[1], best[2], chosen, ripples)

    act = intent.action
    if act in ("cut_budget", "set_budget"):
        if act == "cut_budget":
            pct = min(max(amount or 20, 1), 90)
            budget = int(round(budget * (1 - pct / 100) / 100) * 100)
            title = f"Cut the budget by {pct:g}%"
        else:
            budget = int(min(max(amount or budget, 1000), 500_000_000))
            title = f"Set the budget to {inr(budget)}"
        squeeze(budget)
    elif act in ("add_guests", "remove_guests", "set_guests"):
        k = int(amount or 10)
        guests = {"add_guests": guests + k, "remove_guests": max(1, guests - k), "set_guests": max(1, k)}[act]
        guests = min(guests, 5000)
        title = f"{'Add' if act == 'add_guests' else 'Remove' if act == 'remove_guests' else 'Plan for'} " \
                f"{k if act != 'set_guests' else guests} guests"
        cur_venue = priced(plan, guests, chosen, ripples)[0][1] if venue else None
        if cur_venue is not None and cur_venue.cap and cur_venue.cap < guests:
            fit = cheapest_fit(lambda o: True)
            if fit:
                ripples = _take(venue, fit, chosen, ripples)  # type: ignore[arg-type]
    elif act in ("outdoors", "indoors"):
        want = act == "outdoors"
        title = "Move it outdoors" if want else "Move it indoors"
        cur_venue = priced(plan, guests, chosen, ripples)[0][1] if venue else None
        if cur_venue is not None and cur_venue.outdoor == want:
            note = f"The venue is already {'open-air' if want else 'covered'}. "
            if want:
                ripples = [x for x in ripples if x.src != "outdoors"] + [RippleState(
                    src="outdoors", stop="day", note="Rain cover on standby for the open-air venue.",
                    extra=int(round(budget * .03, -2)))]
                note += "I added a rain cover on standby. "
        else:
            fit = cheapest_fit(lambda o: o.outdoor == want)
            if fit and venue is not None:
                ripples = _take(venue, fit, chosen, ripples)
            else:
                note = f"I have no {'open-air' if want else 'covered'} venue among the options. "
    elif act == "cheaper" and intent.stop in booked:
        s = next(x for x in plan.stops if x.key == intent.stop)
        title, note = f"A cheaper {s.label.lower()}", f"The {s.label.lower()} is already booked, so I left it alone. "
    elif act == "cheaper" and intent.stop in keys:
        s = next(x for x in plan.stops if x.key == intent.stop)
        cur = next(it for st, it, _ in before if st.key == s.key)
        cheaper = [o for o in _all(s) if line_total(o.price, o.per_guest, guests) <
                   line_total(cur.price, cur.per_guest, guests) and (o.cap is None or o.cap >= guests)]
        title = f"A cheaper {s.label.lower()}"
        if cheaper:
            ripples = _take(s, min(cheaper, key=lambda o: o.price), chosen, ripples)
        else:
            note = f"This is already the cheapest {s.label.lower()} I have. "
    elif act == "move_date":
        try:
            new = dt.date.fromisoformat(intent.date or "")
        except ValueError:
            new = None
        if new is None or new < now:
            note = "I couldn't find a future date in that. Try \"move it to 6 Dec\". "
        else:
            date = new
            title = f"Move it to {new.day} {new:%b %Y}"
    else:
        reply = clip(intent.reply, CAPS["reply"])
        return AdjustOut(understood=False, title="", rows=[], note=reply or (
            "I can cut or set the budget, add or remove guests, move it outdoors or indoors, make one stop "
            "cheaper, or move the date."), profile=p, chosen=inp.chosen, ripples=inp.ripples, plan=plan,
            source=source)

    new_p = p.model_copy(update={"guests": guests, "budget": budget, "date": date})
    after = priced(plan, guests, chosen, ripples)
    rows: list[DiffRow] = []
    if guests != p.guests:
        rows.append(DiffRow(label="Guests", before=str(p.guests), after=str(guests)))
    if budget != p.budget:
        rows.append(DiffRow(label="Budget", before=inr(p.budget or 0), after=inr(budget)))
    if date != p.date and p.date and date:
        rows.append(DiffRow(label="Date", before=f"{p.date.day} {p.date:%b}", after=f"{date.day} {date:%b}"))
    for (s, b_item, b_price), (_, a_item, a_price) in zip(before, after):
        if a_item.id != b_item.id or a_price != b_price:
            rows.append(DiffRow(label=s.label, before=f"{b_item.name} · {inr(b_price)}",
                                after=f"{a_item.name} · {inr(a_price)}"))
    tb, ta = sum(x[2] for x in before), sum(x[2] for x in after)
    rows.append(DiffRow(label="Total", before=inr(tb), after=inr(ta)))
    old_band, new_band = rules.budget_band(p)[0], rules.budget_band(new_p)[0]
    if old_band != new_band and old_band and new_band:
        rows.append(DiffRow(label="Budget feel", before=old_band, after=new_band))
    gap = ta - budget
    note += (f"Still {inr(gap)} over budget. Try cutting a stop or the guest list." if gap > 0
             else f"Fits the budget with {inr(-gap)} to spare.")
    return AdjustOut(understood=True, title=clip(title, 80), rows=rows, note=note, profile=new_p, chosen=chosen,
                     ripples=ripples, plan=replan(plan, new_p, now), source=source)


# ------------------------------------------------------------------ calendar

class IcsTask(Strict):
    text: str = Field(min_length=1, max_length=200)
    due: dt.date


class IcsIn(Strict):
    title: str = Field("Waypoint plan", max_length=80)
    tasks: list[IcsTask] = Field(max_length=40)


def _ics_text(s: str) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s)
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def _fold(line: str) -> str:
    """RFC 5545: lines over 75 octets continue on the next line after a space."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 74:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def ics(data: IcsIn, now: dt.datetime) -> str:
    """An all-day VEVENT per deadline."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Waypoint//Event planner//EN", "CALSCALE:GREGORIAN"]
    for i, t in enumerate(data.tasks):
        day = t.due.strftime("%Y%m%d")
        nxt = (t.due + dt.timedelta(days=1)).strftime("%Y%m%d")
        uid = hashlib.sha1(f"{data.title}|{t.text}|{day}|{i}".encode()).hexdigest()[:16]
        lines += ["BEGIN:VEVENT", f"UID:{uid}@waypoint.local", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{day}",
                  f"DTEND;VALUE=DATE:{nxt}", _fold(f"SUMMARY:{_ics_text(t.text)}"),
                  _fold(f"DESCRIPTION:{_ics_text(data.title)}"), "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
