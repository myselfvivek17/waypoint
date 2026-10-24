"""Over budget: "Where should the money go?" Trade-offs instead of cutting everything evenly.

The user ranks what matters (food, venue, decor, photos, cake and entertainment). Code works out two
or three concrete rebalanced plans, each saying exactly what it gives up:

* protect every priority and take the money from everything else;
* protect only the top priority, taking from the lowest-ranked first;
* keep every choice and invite fewer people.

Within each, the gap is closed with the least sacrifice: when one move covers what is left, the
smallest such move; otherwise the biggest saving, and on to the next. Booked, quoted and user-priced
stops are never touched. The model only writes one plain sentence per option, after the maths, and
that sentence may not quote amounts.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pydantic import BaseModel, Field

from . import llm
from .choices import Ctx, Op, RippleState, _all, _take, priced
from .rules import inr
from .schemas import Plan, Source, Stop, Strict
from .validators import clip, drop_claims, drop_money, line_total

CATEGORIES: dict[str, tuple[str, set[str]]] = {
    "food": ("Food", {"food"}),
    "venue": ("Venue", {"venue"}),
    "decor": ("Decor", {"theme", "decor_lights"}),
    "photos": ("Photos", {"photographer"}),
    "fun": ("Cake & entertainment", {"cake", "music", "dj", "sound", "anchor"}),
}
SKIPPABLE = {"theme", "cake", "photographer", "dj", "decor_lights", "anchor", "gifts", "makeup", "mehendi", "transport"}
MAX_MOVES = 5
MIN_GUESTS = 4

INSTRUCTIONS = """
You explain budget trade-offs for an event plan in India. The data block has the event and up to three
options already worked out by code; each lists what it keeps and what it gives up. For each option, in
order, write one warm, plain sentence (at most 24 words) that tells the user what they keep and what they
give up. Do not quote amounts or numbers, do not add options, do not claim a price or availability.
Reply with JSON only: {"lines": ["", "", ""]}
"""


def category(key: str) -> str:
    return next((c for c, (_, keys) in CATEGORIES.items() if key in keys), "other")


class TradeIn(Strict):
    plan: Plan
    chosen: dict[str, str] = Field(default_factory=dict, max_length=12)
    ripples: list[RippleState] = Field(default_factory=list, max_length=30)
    booked: list[str] = Field(default_factory=list, max_length=20)
    decided: list[str] = Field(default_factory=list, max_length=20)
    priorities: list[str] = Field(default_factory=list, max_length=5)  # category ids, most important first


class TradeOption(BaseModel):
    id: str
    title: str
    line: str
    total: int
    fits: bool
    gives_up: list[str]
    keeps: list[str]
    ops: list[Op]


class Category(BaseModel):
    id: str
    label: str


class TradeOut(BaseModel):
    budget: int
    total: int
    over: int
    categories: list[Category]
    options: list[TradeOption]
    source: Source


class Lines(BaseModel):
    lines: list = []


@dataclass
class Move:
    stop: Stop
    to: object | None  # an Option, or None to skip the stop
    saving: int

    def op(self) -> Op:
        if self.to is None:
            return Op(op="skip_stop", stop=self.stop.key)
        return Op(op="choose", stop=self.stop.key, item=self.to.id)

    def words(self, cur_name: str) -> str:
        if self.to is None:
            return f"No {self.stop.label.lower()} (saves {inr(self.saving)})"
        return f"{self.stop.label}: {self.to.name} instead of {cur_name} ({inr(self.saving)} less)"


def _moves(c: Ctx) -> dict[str, list[Move]]:
    """Every way each changeable stop could cost less: a cheaper option, or leaving it out."""
    out: dict[str, list[Move]] = {}
    for s, item, price in priced(c.plan, c.guests, c.chosen, c.ripples):
        if c.locked(s) or price <= 0:
            continue
        ms = []
        for o in _all(s):
            if o.id == item.id or (o.cap and o.cap < c.guests):
                continue
            trial_c = {**c.chosen, s.key: o.id}
            trial_r = [x for x in c.ripples if x.src != s.key] + [
                RippleState(src=s.key, stop=x.stop, note=x.note, extra=x.extra) for x in o.ripple]
            after = sum(x[2] for x in priced(c.plan, c.guests, trial_c, trial_r))
            saving = sum(x[2] for x in priced(c.plan, c.guests, c.chosen, c.ripples)) - after
            if saving > 0:
                ms.append(Move(s, o, saving))
        if s.key in SKIPPABLE:
            ms.append(Move(s, None, price))
        if ms:
            out[s.key] = ms
    return out


def close_gap(gap: int, moves: dict[str, list[Move]], tiers: list[set[str]]) -> list[Move]:
    """Least sacrifice, lowest tier first: one move per stop, at most ``MAX_MOVES``."""
    picked: list[Move] = []
    for tier in tiers:
        pool = [m for k, ms in moves.items() if k in tier for m in ms]
        while gap > 0 and pool and len(picked) < MAX_MOVES:
            enough = [m for m in pool if m.saving >= gap]
            m = min(enough, key=lambda x: (x.saving, x.to is None)) if enough else max(pool, key=lambda x: x.saving)
            picked.append(m)
            gap -= m.saving
            pool = [x for x in pool if x.stop.key != m.stop.key]
        if gap <= 0:
            break
    return picked


def _tiers(keys: list[str], protect: set[str], ranked: list[str]) -> list[set[str]]:
    """Order in which stops give up money: the unranked first, then priorities from the lowest rank up."""
    free = [k for k in keys if category(k) not in protect]
    unranked = {k for k in free if category(k) not in ranked}
    tiers = [{k for k in unranked if category(k) == "other"}, {k for k in unranked if category(k) != "other"}]
    tiers += [{k for k in free if category(k) == cat} for cat in reversed(ranked)]
    return [t for t in tiers if t]


def _fewer_guests(c: Ctx) -> tuple[int, int] | None:
    """The most guests that fit the budget with every choice kept, and the total then. Per-guest stops
    scale with the head count, and so does an undecided cake (as the revision resizes it)."""
    rows = priced(c.plan, c.guests, c.chosen, c.ripples)
    total = sum(p for _, _, p in rows)
    per = sum(line_total(it.price, True, 1) for s, it, p in rows if it.per_guest and p and not c.locked(s))
    cake = next((p for s, _, p in rows if s.key == "cake" and not c.locked(s) and s.key not in c.decided), 0)
    fixed = total - per * c.guests - cake

    def at(g: int) -> int:
        return fixed + per * g + (max(100, int(round(cake * g / c.guests / 100) * 100)) if cake else 0)
    if per + cake / c.guests <= 0 or fixed >= c.budget:
        return None
    g = min(c.guests - 1, int((c.budget - fixed) // (per + cake / c.guests)))
    while g >= MIN_GUESTS and at(g) > c.budget:
        g -= 1
    return (g, at(g)) if g >= MIN_GUESTS else None


def options(inp: TradeIn, now: dt.date) -> tuple[TradeOut, list[TradeOption]]:
    """The rebalanced options, with deterministic wording (the model may rephrase it afterwards)."""
    plan, p = inp.plan, inp.plan.profile
    keys = [s.key for s in plan.stops]
    c = Ctx(plan=plan, now=now, guests=p.guests or 1, budget=p.budget or 0, date=p.date,
            chosen={k: v for k, v in inp.chosen.items() if k in keys},
            ripples=[x for x in inp.ripples if x.stop in keys], booked=set(inp.booked), decided=set(inp.decided))
    rows = priced(plan, c.guests, c.chosen, c.ripples)
    total = sum(x[2] for x in rows)
    gap = total - c.budget
    present = [cat for cat in CATEGORIES if any(category(k) == cat for k in keys)]
    ranked = [x for x in dict.fromkeys(inp.priorities) if x in present]
    cats = [Category(id=cat, label=CATEGORIES[cat][0]) for cat in present]
    out = TradeOut(budget=c.budget, total=total, over=max(0, gap), categories=cats, options=[], source="rules")
    if gap <= 0:
        return out, []
    moves = _moves(c)
    names = {s.key: it.name for s, it, _ in rows}
    labels = lambda cs: ", ".join(CATEGORIES[x][0].lower() for x in cs)  # noqa: E731
    plans: list[tuple[str, list[Move], list[str]]] = []
    protect_all = ranked or ["food", "venue"]
    protect_all = [x for x in protect_all if x in present]
    plans.append((f"Protect {labels(protect_all)}" if protect_all else "Take from the extras first",
                  close_gap(gap, moves, _tiers(keys, set(protect_all), ranked)), protect_all))
    if ranked:
        top = ranked[:1]
        plans.append((f"Protect only {labels(top)}", close_gap(gap, moves, _tiers(keys, set(top), ranked)), top))
    else:
        plans.append(("Least change overall", close_gap(gap, moves, [set(keys)]), []))
    result: list[TradeOption] = []
    seen: set[tuple] = set()
    for title, picked, kept in plans:
        sig = tuple(sorted((m.stop.key, getattr(m.to, "id", None)) for m in picked))
        if not picked or sig in seen:
            continue
        seen.add(sig)
        after = total - sum(m.saving for m in picked)
        fits = after <= c.budget
        result.append(TradeOption(
            id=f"t{len(result)}", title=title if fits else f"{title} (still {inr(after - c.budget)} over)",
            line="", total=after, fits=fits, gives_up=[m.words(names[m.stop.key]) for m in picked],
            keeps=[f"{CATEGORIES[x][0]} as chosen" for x in kept], ops=[m.op() for m in picked]))
    fewer = _fewer_guests(c)
    if fewer is not None:
        g, after = fewer
        result.append(TradeOption(
            id=f"t{len(result)}", title=f"Keep every choice, invite {g}", line="", total=after, fits=after <= c.budget,
            gives_up=[f"{c.guests - g} fewer guests ({c.guests} → {g})"], keeps=["Every stop as chosen"],
            ops=[Op(op="set_guests", amount=g, text=f"Invite {g} to keep every choice")]))
    for o in result:  # the wording when no model rephrases it
        n = len(o.gives_up)
        changes = f"{n} change{'s' if n > 1 else ''}"
        o.line = (f"Keeps {' and '.join(k.lower() for k in o.keeps)}, with {changes} elsewhere." if o.keeps
                  else f"The fewest rupees moved: {changes}.")
        if o.ops[0].op == "set_guests":
            o.line = "Every stop stays as you chose it; only the guest list gets shorter."
    out.options = result[:3]
    return out, out.options


async def tradeoffs(inp: TradeIn, now: dt.date) -> TradeOut:
    """The options from code, then (when a model is live) one sentence each in plainer words."""
    out, opts = options(inp, now)
    if not opts:
        return out
    data = {"event": {"type": inp.plan.profile.type, "guests": inp.plan.profile.guests},
            "options": [{"title": o.title, "keeps": o.keeps, "gives_up": o.gives_up} for o in opts]}
    res = await llm.ask("tradeoffs", INSTRUCTIONS, data, Lines, fallback=Lines, max_tokens=1200, timeout=20)
    lines = [clip(drop_claims(drop_money(str(x))), 160) for x in res.value.lines[:len(opts)]]
    if res.source == "ai" and len(lines) == len(opts) and all(lines):
        for o, line in zip(opts, lines):
            o.line = line
        out.source = "ai"
    else:
        out.source = res.source if res.source != "ai" else "fallback"
    return out
