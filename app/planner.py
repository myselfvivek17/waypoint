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

from pydantic import Field

from . import llm, places, prices, rules, templates
from .places import Place, PlaceSet
from .rules import RulesResult, inr
from .schemas import (PRIVATE, Block, Day, DayItem, Mode, DraftOption, DraftPlan, DraftStop, Option, PhotoHint, Plan, Profile,
                      Ripple, Source, Stop, Strict, Task)
from .validators import (CAPS, cap_list, clamp_due, clean_ask, clip, drop_claims, drop_money, drop_wrong_kind, fit_budget,
                         fix_option_kinds, line_total, sentences, to_int)

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
# Openverse phrases for "similar vibe" photos of a kind of place.
VIBE = {"cafe": "cafe interior", "restaurant": "restaurant interior", "events_venue": "event hall party",
        "community_centre": "community hall event", "conference_centre": "convention hall",
        "banquet_hall": "banquet hall", "park": "park picnic", "garden": "garden party", "hotel": "hotel banquet hall",
        "resort": "resort lawn party"}
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
ask (pick only): one short, warm question under 14 words that offers the pick and what the alternatives
would bring, with "or" between the two, e.g. "Want this café? Or I can show cheaper ones." No amounts.
Also write "days": the event-day run-sheet with 4 to 6 items (weddings: up to 3 functions), and
"invite": a warm WhatsApp invite under 70 words with the real date and area, no placeholders.

JSON shape:
{"stops": [{"key": "venue", "pick": {"name": "", "price": 0, "place_id": "", "reason": "", "why": "",
 "photo_query": "", "outdoor": false, "ask": ""}, "options": [{"kind": "Cheaper", "name": "", "price": 0,
 "place_id": "", "reason": "", "outdoor": false, "photo_query": ""}]}],
 "days": [{"label": "", "title": "", "items": [{"time": "6:00 PM", "title": "", "note": "",
 "art": "arrive|games|cake|dinner|music|ritual"}]}],
 "invite": ""}
For per-guest stops, price is per guest. Prices are your estimates: never say a price is confirmed or that
a place or vendor is available, because nobody has contacted them.
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
        if place is not None:  # its own photos first, then openly licensed photos of similar places
            photo = (place.photo or PhotoHint()).model_copy(update={
                "lat": place.lat, "lon": place.lon, "name": place.name, "vibe": VIBE.get(place.kind), "relation": "place"})
        else:
            photo = PhotoHint(vibe=generic_vibe(p, name))
    elif key not in NO_PHOTO:
        q = _query(d.photo_query) or _query(fallback.photo_query)
        photo = PhotoHint(query=q, relation="inspiration") if q else None
    reason, why = drop_claims(drop_money(d.reason)), drop_claims(drop_money(d.why))
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
        place_id=place.id if place else None, place_kind=place.kind if place else None, where=where,
        dist_km=place.dist_km if place else None,
        lat=place.lat if place else None, lon=place.lon if place else None,
        photo=photo, tone=_tone(oid + name),
    )


def generic_vibe(p: Profile, name: str) -> str:
    """A photo phrase for a venue that is described, not named (no verified place)."""
    low = name.lower()
    for word, phrase in (("home", "housewarming party home"), ("terrace", "rooftop terrace party"),
                         ("garden", "garden party"), ("lawn", "wedding lawn"), ("clubhouse", "clubhouse party"),
                         ("café", "cafe interior"), ("cafe", "cafe interior"), ("restaurant", "restaurant interior"),
                         ("hall", "banquet hall")):
        if word in low:
            return phrase
    return "banquet hall" if (p.guests or 0) > 40 else "restaurant interior"


def _ripples(d: DraftOption, later: set[str], budget: int) -> list[Ripple]:
    if d.ripple is None or d.ripple.stop not in later:
        return []
    extra = to_int(abs(to_int(d.ripple.extra, -10**9) or 0), 0, int(budget * .2)) or 0
    sign = -1 if (to_int(d.ripple.extra, -10**9) or 0) < 0 else 1
    note = clip(d.ripple.note, CAPS["note"])
    return [Ripple(stop=d.ripple.stop, note=note, extra=sign * extra)] if note else []


def build_stop(ds: DraftStop, fb: DraftStop, key: str, label: str, later: set[str], p: Profile,
               ps: PlaceSet, r: RulesResult, rejected: list[str], per_guest: bool | None = None) -> Stop:
    per_guest = key == "food" if per_guest is None else per_guest
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
    set_asks(stop, ds.pick.ask)
    return stop


def set_asks(stop: Stop, model_ask: str = "") -> None:
    """End every proposal with its choice in plain words: the model's line for the pick when it
    passes validation, otherwise (and for the alternatives) the rules' line."""
    kinds = [o.kind for o in stop.options]
    stop.rec.ask = (clean_ask(model_ask, set(kinds)) if kinds else "") or \
        templates.choice_line(stop.key, stop.label, stop.rec.place_kind, kinds)
    for o in stop.options:
        o.ask = templates.choice_line(stop.key, stop.label, o.place_kind, kinds, recommended=False)


def make_tasks(stops: list[Stop], p: Profile, r: RulesResult, now: dt.date,
               chosen: dict[str, str] | None = None) -> list[Task]:
    """Deadlines from the rules' lead times (or a mode's), clamped to [today, event day].

    A task names the option chosen for its stop (``chosen``), else the stop's pick. A skipped stop
    has no task; one the user already has becomes "confirm with …"; one done their own way keeps
    their words.
    """
    chosen = chosen or {}
    order = [k for k in r.book_order] + [s.key for s in stops if s.key not in r.book_order]
    by_key = {s.key: s for s in stops}
    tasks: list[Task] = []
    dues: dict[str, dt.date | None] = {}
    for key in order:
        s = by_key.get(key)
        if s is None:
            continue
        lead = s.lead or r.lead.get(key, 14)
        raw = p.date - dt.timedelta(days=lead) if p.date else None
        due, late = clamp_due(raw, now, p.date)
        dues[key] = due
        if s.status == "skip":
            continue
        verb = s.verb or rules.TASK_VERBS.get(key, "Book")
        item = next((o for o in [s.rec, *s.options] if o.id == chosen.get(key)), s.rec)
        text = f"{verb}: {item.name}"
        if s.status == "have":
            verb, text = "Confirm", f"Confirm with {s.note or 'your own'}: {s.label.lower()}"
        elif s.status == "own":
            verb, text = "", clip(s.note, 200) or s.label
        tasks.append(Task(id=key, stop=key, verb=verb, text=text, due=due, late=late,
                          why=f"Usually booked about {lead} days ahead."))
    for c in r.mandatory:
        tasks.append(Task(id=c.id, stop=c.stop, text=c.text, why=c.why, due=dues.get(c.stop or ""),
                          mandatory=True))
    far = dt.date.max
    return sorted(tasks, key=lambda t: (t.due or far, t.mandatory))


def set_modes(stops: list[Stop], p: Profile) -> None:
    """Offer the ways of doing each stop that has them, starting from what the plan proposes."""
    venue = next((s for s in stops if s.key == "venue"), None)
    kind = venue.rec.place_kind if venue is not None else None
    for s in stops:
        rows = rules.modes_for(s.key)
        if rows and s.mode is None:
            s.modes = [Mode(id=m[0], label=m[1], why=m[5]) for m in rows]
            s.mode = templates.default_mode(s.key, p, kind)
            row = rules.mode_row(s.key, s.mode)
            if row:
                s.rule = f"{s.rule} Way: {row[1]}. {row[5]}".strip()


def apply_have(stops: list[Stop], p: Profile) -> list[str]:
    """Mark stops the host already has ("a family priest, my aunt is cooking") as covered."""
    marked = []
    for phrase in re.split(r",|;|\band\b|\+", p.have or ""):
        phrase = phrase.strip(" .")
        key = rules.find_stop(phrase, [s.key for s in stops]) if phrase else None
        s = next((x for x in stops if x.key == key), None)
        if s is not None and s.status == "open":
            s.status, s.note = "have", clip(phrase[0].upper() + phrase[1:], 160)
            if s.modes and any(m.id == "have" for m in s.modes):
                s.mode = "have"
            marked.append(s.label)
    return marked


def day_ids(days: list[Day], keys: list[str]) -> None:
    """Give every moment an id, the stop it depends on, and other ways to do it."""
    for d in days:
        for n, it in enumerate(d.items):
            it.id = it.id or f"{d.id}-{n}"
            dep = rules.DAY_STOP.get(it.art)
            if it.stop is None and dep in keys and (it.art != "dinner" or "food" in keys):
                it.stop = dep
            if it.art == "cake" or re.search(r"(?i)\bcake\b", it.title):
                it.stop = "cake" if "cake" in keys else it.stop
            if not it.alts:
                it.alts = list(rules.DAY_ALTS.get(it.art, []))


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
    notes: list[str] = [prices.NOTE]
    if rejected:
        log.info("rejected %d place id(s) not in the fetched set", len(rejected))
        notes.append("A suggested place could not be verified, so it was replaced with a generic suggestion.")
    if p.budget is not None and fit_budget(stops, p.budget, p.guests or 1):
        notes.append(f"Prices were scaled down so the plan fits your {inr(p.budget)} budget.")
    if ps.places:
        notes.append("Venues and bakeries come from OpenStreetMap; check prices and availability with them.")
    elif ps.source == "not-found":
        notes.append(f"I couldn't find “{clip(p.area, 60)}” on the map (I look in India only), so venues are described, "
                     "not named. Check the area on Details.")
    elif ps.source:
        notes.append("No named venues were found on OpenStreetMap near there, so venues are described, not named.")
    elif p.area:
        notes.append("Live place listings were unavailable, so venues are described, not named.")
    invite = clip(draft.invite.replace("null", "").replace("None", ""), CAPS["invite"]) or fb.invite
    if "{" in invite or "[" in invite:  # unfilled template slots from the model
        invite = fb.invite
    set_modes(stops, p)
    covered = apply_have(stops, p)
    if covered:
        notes.append(f"You already have: {', '.join(covered).lower()}. No suggestions are shown for those.")
    days = clean_days(draft.days, fb.days)
    day_ids(days, [s.key for s in stops])
    return Plan(title=p.title or templates.DEFAULT_THEME[p.type][0], profile=p, stops=stops,
                days=days, tasks=make_tasks(stops, p, r, now), invite=invite,
                insights=r.insights, source=source, today=now, area_point=ps.center, notes=notes)


# ------------------------------------------------------------------ build

async def gather_places(p: Profile) -> PlaceSet:
    """Venues around the user's point (if they shared it) or the area they typed, waiting at most
    ``PLACE_WAIT`` seconds (the browser's prefetch usually makes this quick)."""
    if not p.area and p.point is None:
        return PlaceSet()
    try:
        return await asyncio.wait_for(places.venues(p.area, p.guests, p.point), timeout=PLACE_WAIT)
    except TimeoutError:
        return PlaceSet(center=p.point or await places.geocode(p.area))


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
        "event": p.model_dump(mode="json", exclude_defaults=True, exclude=PRIVATE),
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
    fb = templates.draft_plan(p, r, venues, bakeries, places.temple_candidates(ps))
    if not use_ai:
        return assemble(fb, fb, p, r, ps, now, "fallback")
    res = await llm.ask("plan", INSTRUCTIONS, plan_request(p, r, venues, bakeries), DraftPlan,
                        fallback=lambda: fb, max_tokens=3500)
    return assemble(res.value, fb, p, r, ps, now, res.source)


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
