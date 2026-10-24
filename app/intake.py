"""Conversational intake: one question at a time.

The model reads the user's words, updates the profile and picks the single most useful
next question, skipping anything it can infer. Code keeps the final say: numbers and
dates are parsed deterministically, required fields are enforced, the turn count is
capped, and impossible budgets or past dates become a deterministic "block".
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from . import llm, rules
from .schemas import PRIVATE, Block, EventType, Insight, Profile, Source, Strict
from .validators import CAPS, cap_list, clip

FieldName = Literal["guests", "budget", "area", "date", "who", "food", "access", "have", "type"]
ASK_ORDER: list[str] = ["guests", "budget", "area", "date", "who", "food", "access"]
REQUIRED = ["guests", "budget", "area", "date"]
MAX_TURNS = 7
GENERIC_TITLES = {"", "birthday", "wedding", "event", "house-warming", "community event", "other",
                  "celebration", "party"}

# field -> (question, placeholder, chips, wedding chips)
QUESTIONS: dict[str, tuple[str, str, list[str], list[str]]] = {
    "guests": ("About how many people are you inviting?", "e.g. 30",
               ["10", "20", "30", "50"], ["100", "250", "400"]),
    "budget": ("What budget feels comfortable for the whole day?", "e.g. 40000 or 12 lakh",
               ["₹20,000", "₹40,000", "₹75,000", "₹1.5 lakh"], ["₹8 lakh", "₹12 lakh", "₹20 lakh"]),
    "area": ("Which city or area should I look in?", "e.g. Gachibowli, Hyderabad",
             ["Gachibowli, Hyderabad", "Jubilee Hills, Hyderabad", "Koramangala, Bengaluru"],
             ["Jaipur, Rajasthan", "Udaipur, Rajasthan"]),
    "date": ("And the date?", "e.g. 15 Nov", ["15 Nov", "22 Nov", "6 Dec"], ["12 Dec", "20 Jan"]),
    "who": ("Who is it for, and what do they love?", "A name and a few things they enjoy",
            ["Aditya, 22 — cricket and retro Bollywood", "My mother — music and flowers",
             "Our family — good food, good company"],
            ["The couple and both families — folk music, Rajasthani food"]),
    "food": ("Any food needs I should plan around?", "e.g. vegetarian, a few Jain guests",
             ["Vegetarian", "Veg + 2 Jain guests", "Veg and non-veg", "No onion or garlic"],
             ["Vegetarian", "Veg and non-veg"]),
    "access": ("Does anyone need step-free access or extra seating?", "e.g. lift, ground floor",
               ["Lift or ground floor", "Seating for elders", "No special needs"],
               ["Seating for elders", "No special needs"]),
}

INSTRUCTIONS = """
You are Waypoint, a warm and concise assistant helping someone in India plan a celebration.
You are running a short intake chat. The data block has: profile (what is known so far),
asked (the field the user was just asked, or null for their opening message), message (their
latest words), already_asked (fields asked before) and today (YYYY-MM-DD).

1. Update the profile with anything the message states or clearly implies. Fields:
   type (birthday | wedding | house-warming | community | other), title (a short event name such as
   "Aditya's 22nd birthday"), guests (integer), budget (integer rupees for the whole event:
   "40k" = 40000, "1.5 lakh" = 150000), area (neighbourhood and city), date (YYYY-MM-DD; with no year,
   the next such date on or after today), who (who it is for and what they love), food (dietary
   needs), access (accessibility needs), vibe, have (things the host already has or will do
   themselves, such as "a family priest" or "my aunt is cooking"). Include only fields you are confident about.
2. Choose the ONE most useful next question and skip anything you can infer. Needed before
   planning: guests, budget, area, date. Also useful: who, food, access. Never ask about a field in
   already_asked. When nothing useful is left, set done to true.

3. Always write "understood": one warm sentence (at most 30 words) saying what you understood about
   the event so far, in your own words, without inventing anything.

Reply with JSON only:
{"profile": {changed fields only}, "done": false, "ask": "<one of guests|budget|area|date|who|food|access>",
 "reply": "<one short friendly question, at most 25 words>", "chips": ["up to 4 short tap answers"],
 "understood": "<one sentence>"}
or {"profile": {changed fields only}, "done": true, "reply": "<one short sentence>", "understood": "<one sentence>"}
"""


class Turn(Strict):
    field: str | None = Field(None, max_length=20)
    q: str = Field("", max_length=300)
    a: str = Field("", max_length=300)


class ChatIn(Strict):
    profile: Profile = Profile()
    message: str = Field("", max_length=500)
    asked: FieldName | None = None
    transcript: list[Turn] = Field(default_factory=list, max_length=16)
    edit: bool = False


class ChatOut(BaseModel):
    done: bool
    reply: str
    ask: str | None = None
    placeholder: str = ""
    chips: list[str] = []
    profile: Profile
    block: Block | None = None
    remaining: int = 0
    source: Source
    understood: str = ""            # one sentence: what the assistant took from the conversation
    understood_source: Source = "mock"
    insights: list[Insight] = []    # the rules engine's first reading of the details so far


class ChatDraft(BaseModel):
    """What the model is asked to return. Loose on purpose: code validates each field."""

    profile: dict = {}
    done: bool = False
    ask: str | None = None
    reply: str = ""
    chips: list = []
    understood: str = ""


# ------------------------------------------------------------------ deterministic parsing

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
CITY_WORDS = sorted(rules.TIER1 | rules.TIER2, key=len, reverse=True)


def detect_type(text: str) -> EventType | None:
    t = text.lower()
    if re.search(r"wedding|shaadi|marriage|mehendi|mehndi|sangeet|reception", t):
        return "wedding"
    if re.search(r"house[- ]?warming|griha|gruha|new home|puja|pooja", t):
        return "house-warming"
    if re.search(r"community|society|colony|festival|diwali|ganesh|navratri|durga|onam|pongal", t):
        return "community"
    if re.search(r"birthday|b'?day|turns? \d|\d+(st|nd|rd|th) bday", t):
        return "birthday"
    return None


NEGATIVE = re.compile(r"(?:^|[\s(:=])[-−–]\s*(?:₹|rs\.?|inr)?\s*\d|\bminus\s+\d", re.I)


def parse_money(text: str, loose: bool = False) -> int | None:
    """"₹40k", "40,000", "1.5 lakh", "2 cr", "fifty thousand" -> rupees. Bare numbers only when
    ``loose``. A negative amount ("-5000") is not money anyone can spend: None, never ₹5,000."""
    from .revise import spoken_amounts  # revise builds on this module
    if NEGATIVE.search(text):
        return None
    t = spoken_amounts(text.lower()).replace(",", "")
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d.]+)\s*(lakhs?|lacs?|l|k|thousand|crores?|cr)?\b", t) or \
        re.search(r"([\d.]+)\s*(lakhs?|lacs?|k|thousand|crores?|cr)\b", t)
    if not m and loose:
        m = re.search(r"([\d.]+)\s*(l)?\b", t)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    unit = (m.group(2) or "").rstrip("s")
    n *= {"lakh": 1e5, "lac": 1e5, "l": 1e5, "k": 1e3, "thousand": 1e3, "crore": 1e7, "cr": 1e7}.get(unit, 1)
    return int(n) if 0 < n <= 500_000_000 else None


def _candidates(t: str):
    """Yield (day, month, year or None) for every date-like phrase, in order of preference."""
    for m in re.finditer(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        yield int(m[3]), int(m[2]), int(m[1])
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?([a-z]{3})[a-z]*\.?(?:,?\s*(\d{4}))?", t):
        if m[2] in MONTHS:
            yield int(m[1]), MONTHS[m[2]], int(m[3]) if m[3] else None
    for m in re.finditer(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(\d{4}))?", t):
        if m[1] in MONTHS:
            yield int(m[2]), MONTHS[m[1]], int(m[3]) if m[3] else None
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        y = int(m[3]) if m[3] else None
        yield int(m[1]), int(m[2]), (y + 2000 if y is not None and y < 100 else y)


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
RELATIVE = re.compile(r"\bin\s+(?P<n>\d{1,3}|a|an|one|two|three|four|five|six)\s+(?P<u>days?|weeks?|months?)\b|"
                      r"\b(?P<n2>\d{1,3}|a|an|one|two|three|four|five|six)\s+(?P<u2>days?|weeks?|months?)\s+from\s+(?:now|today)\b")
SMALL = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _next_feb29(today: dt.date, year: int | None) -> dt.date | None:
    """29 Feb with no year: the next leap year's, said with its year by whoever shows it."""
    for y in ([year] if year else range(today.year, today.year + 8)):
        try:
            when = dt.date(y, 2, 29)
        except ValueError:
            continue
        if year or when >= today:
            return when
    return None


def parse_date(text: str, today: dt.date) -> dt.date | None:
    """"15 Nov", "Nov 15th 2026", "2026-11-15", "15/11", "today", "tomorrow", "next Sunday",
    "this Saturday", "in 3 weeks" -> date.

    With no year, the next such date on or after today ("29 Feb": the next leap year). An explicit
    past year is kept (the rules engine turns it into a "date in the past" block).
    """
    t = text.lower()
    for d, mo, y in _candidates(t):
        if (d, mo) == (29, 2):
            when = _next_feb29(today, y)
            if when is not None:
                return when
            continue
        try:
            when = dt.date(y or today.year, mo, d)
        except ValueError:
            continue
        if y is None and when < today:
            when = when.replace(year=today.year + 1)
        return when
    if re.search(r"\b(?:today|tonight)\b", t):
        return today
    if re.search(r"\b(?:tomorrow|tmrw)\b", t):
        return today + dt.timedelta(days=1)
    if m := re.search(r"\b(?:(next|this|coming)\s+)?(mon|tue|wed|thu|fri|sat|sun)(?:day|s|sday|nesday|rsday|urday)?\b", t):
        if m[1] or re.search(r"\bon\s+" + m[2], t) or t.strip().startswith(m[2]):
            ahead = (WEEKDAYS.index(m[2]) - today.weekday()) % 7 or 7  # the coming one, never today
            return today + dt.timedelta(days=ahead)
    if m := RELATIVE.search(t):
        n_raw, unit = m["n"] or m["n2"], m["u"] or m["u2"]
        n = int(n_raw) if n_raw.isdigit() else SMALL[n_raw]
        if unit.startswith("month"):
            y, mo = divmod(today.month - 1 + n, 12)
            for day in (today.day, 30, 29, 28):
                try:
                    return dt.date(today.year + y, mo + 1, day)
                except ValueError:
                    continue
        return today + dt.timedelta(days=n * (7 if unit.startswith("week") else 1))
    return None


GUEST_WORDS = r"(?:people|guests?|pax|persons?|friends|members|adults|folks|log|logon|lok|मेहमान|लोग|మంది|అతిథులు)"
MAX_GUESTS = 5000


def read_guests(text: str, asked: bool) -> int | None:
    """The head count as written, even when out of range (0, 100000, -5): ``parse_guests`` checks it.
    "30 people", "1 guest", "25 log" (Hinglish), "40 लोग", "30 మంది"; a bare number or a number word
    ("thirty") only when the question was the guest count."""
    from .revise import number
    t = text.lower().replace(",", "")
    m = re.search(rf"(?<![\d.])(-?\s*\d+)\s*{GUEST_WORDS}(?!\w)", t)
    if not m and asked:
        m = re.search(r"(?<![\d.])(-?\s*\d+)(?![\d.]*\s*(?:k|lakh|%))", t)
        if not m:
            n = number(re.sub(r"^(?:about|around|roughly|maybe|just|only)\s+", "", t.strip(" .!")))
            if n is None and t.strip(" .!") in ("zero", "none", "nobody", "no one"):
                n = 0
            return int(n) if n is not None else None
    return int(m.group(1).replace(" ", "")) if m else None


def parse_guests(text: str, asked: bool) -> int | None:
    n = read_guests(text, asked)
    return n if n is not None and 1 <= n <= MAX_GUESTS else None


def unread(asked: str | None, text: str) -> str:
    """When the answer to a guests, budget or date question couldn't be used: one plain line on why and
    what would work, so the same question never just comes back with no explanation."""
    shown = clip(" ".join(text.split()), 40)
    if asked == "guests":
        n = read_guests(text, True)
        if n is not None and n < 1:
            return "A plan needs at least 1 guest."
        if n is not None and n > MAX_GUESTS:
            return f"I can plan for up to {MAX_GUESTS:,} guests; {n:,} is more than that."
        return f"I couldn't find a number of guests in “{shown}”. Type a number, like 30."
    if asked == "budget":
        if NEGATIVE.search(text):
            return "A budget can't be below zero."
        if re.fullmatch(r"\s*(?:₹|rs\.?|inr)?\s*0+\s*", text.lower()):
            return "A budget of ₹0 can't pay for a celebration."
        return f"I couldn't read an amount in “{shown}”. Try 40000, ₹40k or 1.5 lakh."
    if asked == "date":
        if re.search(r"\byesterday\b|\blast\s+(?:week|month|year|mon|tue|wed|thu|fri|sat|sun)", text.lower()):
            return "That date has already passed."
        return f"I couldn't read a date in “{shown}”. Try a day and month, like 15 Nov, or next Saturday."
    return ""


NOT_PLACES = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|mon|tue|wed|thu|fri|sat|sun|"
                        r"the|my|our|a|an|next|this)", re.I)


def parse_area(text: str) -> str:
    for m in re.finditer(r"\b(?:in|at|near|around)\s+([A-Z][\w.'-]*(?:[ ,]+[A-Z][\w.'-]*){0,3})", text):
        if not NOT_PLACES.match(m.group(1)):
            return m.group(1).strip(" ,")
    low = text.lower()
    city = next((c for c in CITY_WORDS if re.search(rf"\b{re.escape(c)}\b", low)), None)
    return city.title() if city else ""


def derive_title(p: Profile) -> str:
    """"Aditya, 22 — cricket" + birthday -> "Aditya's birthday"."""
    kind = {"house-warming": "house-warming", "community": "community event"}.get(p.type, p.type)
    if p.who:
        name = re.split(r"[,—–\-:(]", p.who)[0].strip()
        if re.match(r"(?i)^(my|our)\s", name):
            return clip(f"{kind.capitalize()} for {re.sub(r'(?i)^(my|our)\s+', 'your ', name)}", 80)
        if 0 < len(name) <= 30 and p.type != "wedding":
            return clip(f"{name}'s {kind}", 80)
    return clip(p.title or kind.capitalize(), 80)


def extract(text: str, asked: str | None, profile: Profile, today: dt.date) -> dict:
    """Deterministic reading of one message. Used to check the model and as the fallback."""
    patch: dict = {}
    text = text.strip()
    if not text:
        return patch
    if asked is None:
        if t := detect_type(text):
            patch["type"] = t
        if re.search(r"[A-Z][a-z]+'s\s", text):  # "Aditya's 22nd birthday": a good title as typed
            patch["title"] = clip(re.split(r"[,.;]", text)[0].strip(), 80)
    guests = parse_guests(text, asked == "guests")
    budget = parse_money(text, loose=asked == "budget")
    date = parse_date(text, today) if asked in (None, "date") else None
    if guests and asked in (None, "guests"):
        patch["guests"] = guests
    if budget and asked in (None, "budget"):
        patch["budget"] = budget
    if date:
        patch["date"] = date
    if asked == "area":
        patch["area"] = clip(text, 120)
        if profile.point is not None and (profile.point.label or "") != patch["area"]:
            patch["point"], patch["from_me"] = None, False  # a typed area replaces the shared position
    elif asked is None and (area := parse_area(text)):
        patch["area"] = clip(area, 120)
    if asked in ("who", "food", "access", "have"):
        patch[asked] = clip(text, {"who": 160, "food": 120, "access": 120, "have": 160}[asked])
    if asked == "type" and (t := detect_type(text) or (text.lower() if text.lower() in TYPES else None)):
        patch["type"] = t
    return patch


def sanitize_patch(raw: dict, today: dt.date) -> dict:
    """Validate a model-proposed profile patch field by field; bad fields are dropped."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    limits = {"title": 80, "area": 120, "who": 160, "food": 120, "access": 120, "vibe": 120, "have": 160}
    for key, value in raw.items():
        if value in (None, "", []):
            continue
        try:
            if key in limits:
                out[key] = clip(value, limits[key])
            elif key == "type":
                out[key] = Profile(type=str(value).lower().strip()).type
            elif key in ("guests", "budget"):
                n = int(float(str(value).replace(",", "").replace("₹", "")))
                out[key] = getattr(Profile(**{key: n}), key)
            elif key == "date":
                out[key] = dt.date.fromisoformat(str(value)[:10])
        except (ValidationError, ValueError, TypeError):
            continue
    return out


# ------------------------------------------------------------------ turn logic

TYPES = {"birthday", "wedding", "house-warming", "community", "other"}
TYPE_WORDS = {"house-warming": "house-warming", "community": "community event", "other": "celebration"}


def summary_line(p: Profile, today: dt.date) -> str:
    """The deterministic "what I understood" sentence, used when no model wrote one."""
    kind = TYPE_WORDS.get(p.type, p.type)
    who = re.split(r"[,—–:(]", p.who)[0].strip() if p.who else ""
    who = re.sub(r"^(The|A|An) ", lambda m: m.group(1).lower() + " ", who)
    parts = [f"A {kind}" + (f" for {who}" if who and not re.match(r"(?i)^(my|our)\b", who) else "")]
    if p.guests:
        parts.append(f"with about {p.guests} guests")
    if p.area:
        parts.append(f"in {p.area}")
    if p.date:
        parts.append(f"on {p.date.day} {p.date:%B}" + (f" {p.date.year}" if p.date.year != today.year else ""))
    line = " ".join(parts)
    if p.budget:
        line += f", for {rules.inr(p.budget)} in all"
    extras = [x for x in (p.food, p.access) if x and not re.search(r"(?i)\bno (special )?needs?\b|\bnone\b", x)]
    if extras:
        line += f"; planning around: {', '.join(e.lower() for e in extras)}"
    if p.have:
        line += f"; you already have {p.have.lower()}"
    return clip(line + ".", 300)


def first_insights(p: Profile, today: dt.date, limit: int = 3) -> list[Insight]:
    """The rules' first reading of what is known (budget band, timing, season), most useful first."""
    r = rules.evaluate(p, today)
    order = {"budget": 0, "timing": 1, "season": 2, "size": 3, "needs": 4}
    return sorted(r.insights, key=lambda i: order[i.family])[:limit]


def _filled(p: Profile, f: str) -> bool:
    return getattr(p, f) not in (None, "")


def _question(field: str, p: Profile) -> tuple[str, str, list[str]]:
    text, ph, chips, wchips = QUESTIONS[field]
    return text, ph, list(wchips if p.type == "wedding" else chips)


def _clean_chips(raw: list) -> list[str]:
    chips = [clip(c, CAPS["chip"]) for c in cap_list(raw, 6) if isinstance(c, (str, int, float))]
    return [c for c in chips if c][:4]


def _fallback(inp: ChatIn, merged: Profile, asked_before: set[str]) -> ChatDraft:
    nxt = next((f for f in ASK_ORDER if f not in asked_before and not _filled(merged, f)), None)
    if nxt is None:
        return ChatDraft(done=True, reply="That's everything I need.")
    text, _, chips = _question(nxt, merged)
    return ChatDraft(done=False, ask=nxt, reply=text, chips=chips)


async def chat_turn(inp: ChatIn, today: dt.date) -> ChatOut:
    """Advance the intake by one turn."""
    det = extract(inp.message, inp.asked, inp.profile, today)
    asked_before = {t.field for t in inp.transcript if t.field} | ({inp.asked} if inp.asked else set())
    base = inp.profile.model_copy(update=det)

    if inp.edit:  # editing a chip on the event card: no model call, no new question
        result_source: Source = "mock"
        draft = _fallback(inp, base, asked_before)
        patch = {}
    else:
        data = {"profile": inp.profile.model_dump(mode="json", exclude_defaults=True, exclude=PRIVATE),
                "asked": inp.asked, "message": inp.message, "already_asked": sorted(asked_before),
                "today": today.isoformat()}
        res = await llm.ask("chat", INSTRUCTIONS, data, ChatDraft,
                            fallback=lambda: _fallback(inp, base, asked_before), max_tokens=500,
                            timeout=15)
        draft, result_source = res.value, res.source
        patch = sanitize_patch(draft.profile, today)

    # The model may add what it inferred; code-parsed numbers and dates win.
    merged = inp.profile.model_copy(update={**patch, **det})
    if merged.title.lower() in GENERIC_TITLES:
        merged.title = derive_title(merged)

    turns = len(asked_before)
    block = rules.budget_block(merged) or rules.date_block(merged, today)
    missing_req = [f for f in REQUIRED if not _filled(merged, f)]
    optional_left = [f for f in ASK_ORDER if f not in REQUIRED and f not in asked_before and not _filled(merged, f)]

    ask = draft.ask if draft.ask in ASK_ORDER and draft.ask not in asked_before and not _filled(merged, draft.ask) else None
    if block:
        ask = inp.asked
    elif missing_req:
        ask = ask if ask in missing_req else missing_req[0]
    elif draft.done or turns >= MAX_TURNS or not optional_left:
        ask = None
    else:
        ask = ask if ask in optional_left else optional_left[0]

    model_line = clip(draft.understood, CAPS["reply"]) if result_source == "ai" and not inp.edit else ""
    understood = {"understood": model_line or summary_line(merged, today),
                  "understood_source": "ai" if model_line else "mock",
                  "insights": [] if block else first_insights(merged, today)}
    if ask is None and not block:
        reply = clip(draft.reply, CAPS["reply"]) if draft.done and draft.reply else "That's everything I need."
        return ChatOut(done=True, reply=reply, profile=merged, remaining=0, source=result_source, **understood)

    text, ph, chips = _question(ask, merged) if ask else ("", "", [])
    if ask and draft.ask == ask and draft.reply and not inp.edit:
        text = clip(draft.reply, CAPS["reply"])
        chips = _clean_chips(draft.chips) or chips
    if ask and ask == inp.asked and inp.message.strip() and not block and not _filled(merged, ask):
        text = f"{unread(ask, inp.message)} {QUESTIONS[ask][0]}".strip()  # say why the question is back
    remaining = len(missing_req) + min(len(optional_left), max(0, MAX_TURNS - turns - len(missing_req)))
    return ChatOut(done=False, reply=text, ask=ask, placeholder=ph, chips=chips, profile=merged,
                   block=block, remaining=max(1, remaining), source=result_source, **understood)
