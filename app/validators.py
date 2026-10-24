"""Checks applied to everything the model produces before it reaches the browser.

The model is never trusted with arithmetic or dates: budgets are re-summed and scaled
here, dates are clamped here, and every string is trimmed to a fixed length.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Iterable, Sequence
from typing import TypeVar

from .schemas import Stop

T = TypeVar("T")

CAPS = {"name": 80, "reason": 240, "why": 400, "note": 200, "title": 80, "time": 12,
        "invite": 700, "reply": 300, "chip": 60, "label": 40, "query": 80, "ask": 110}

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WS = re.compile(r"[ \t]+")


def clip(value: object, limit: int, default: str = "") -> str:
    """Return ``value`` as clean text no longer than ``limit`` characters."""
    if value is None:
        return default
    text = _WS.sub(" ", _CTRL.sub("", str(value))).strip()
    if len(text) > limit:
        cut = text[: limit - 1]
        text = (cut.rsplit(" ", 1)[0] if " " in cut[limit // 2:] else cut).rstrip(" ,.;:") + "…"
    return text or default


_SENTENCE = re.compile(r"(?:\d\.(?=\d)|[^.!?])+[.!?]*")  # "0.39 km" is not two sentences
_MONEY = re.compile(r"₹|\brs\.?\s*\d|\binr\b|\d\s*(?:k|lakhs?|lacs?|crores?)\b|\brupees?\b", re.I)
KIND_WORDS = re.compile(r"(?i)\b(caf[eé]|restaurant|banquet|hall|garden|park|hotel|resort|lawn|terrace|clubhouse|"
                        r"dhaba|rooftop)s?\b")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.findall(text or "") if s.strip()]


def drop_money(text: str) -> str:
    """Remove sentences that quote amounts. Prices are shown from the numbers, which code keeps
    right; an amount inside prose goes stale as soon as the budget or a choice changes."""
    return " ".join(s for s in sentences(text) if not _MONEY.search(s))


_CLAIM = re.compile(r"(?i)\b(?:is|are|it's|they're)\s+(?:still\s+|now\s+)?(?:confirmed\s+and\s+)?available\b|"
                    r"\b(?:is|are)\s+(?:now\s+)?(?:confirmed|guaranteed|booked)\b|\bavailab(?:le|ility)\s+(?:on|for)\s+(?:your|the)\s+"
                    r"(?:date|day)|\b(?:confirmed|guaranteed)\s+(?:price|rate|booking|availability)|\bprice\s+is\s+confirmed|"
                    r"\b(?:booked|reserved|held)\s+for\s+you\b")


def drop_claims(text: str) -> str:
    """Remove sentences that claim availability or a confirmed price: Waypoint never contacts a
    vendor, so it cannot know either."""
    return " ".join(s for s in sentences(text) if not _CLAIM.search(s))


_TAG = re.compile(r"<[^>]{0,200}>|[<>`]")


def model_line(text: object, limit: int) -> str:
    """A model's free-text reply made fit to show: no markup, no amounts, no availability or price claims."""
    return clip(drop_claims(drop_money(_TAG.sub("", str(text or "")))), limit)


ASK_OFFERS = (("cheap", "Cheaper"), ("closer", "Closer"), ("vibe", "Different vibe"))


def clean_ask(text: str, kinds: set[str]) -> str:
    """The model's question that ends a proposal, or "" when it can't be shown as it is.

    It must be one short question with an "or" (its two answers are the two buttons), quote no
    amounts or numbers (they go stale), and offer only alternatives the stop really has.
    """
    t = clip(text, CAPS["ask"])
    low = t.lower()
    if (not t or t.endswith("…") or "?" not in t or not re.search(r"\bor\b", low) or re.search(r"\d|[<>{}\[\]]", t)
            or _MONEY.search(t) or len(t.split()) > 18):
        return ""
    if any(word in low and kind not in kinds for word, kind in ASK_OFFERS):
        return ""
    return t


def drop_wrong_kind(text: str, label: str) -> str:
    """Remove sentences that call a real place something it is not (a "café" that is a restaurant)."""
    own = label.lower().replace("é", "e")
    out = []
    for s in sentences(text):
        words = {w.lower().replace("é", "e") for w in KIND_WORDS.findall(s)}
        if all(w in own for w in words):
            out.append(s)
    return " ".join(out)


def cap_list(items: Iterable[T] | None, limit: int) -> list[T]:
    """First ``limit`` items of an iterable, tolerating ``None``."""
    return list(items or [])[:limit]


def require(data: dict, fields: Sequence[str]) -> list[str]:
    """Names of required fields that are missing or blank."""
    return [f for f in fields if data.get(f) in (None, "", [], {})]


def to_int(value: object, lo: int = 0, hi: int = 10**9) -> int | None:
    """Parse a model-supplied number ("₹12,000", 12000.0) and clamp it to [lo, hi]."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):  # NaN / Infinity from a model's JSON
        return None
    if isinstance(value, (int, float)):
        n = int(value)
    else:
        digits = re.sub(r"[^\d.]", "", str(value or ""))
        try:
            n = int(float(digits)) if digits else None
        except ValueError:
            n = None
    return None if n is None else max(lo, min(hi, n))


def line_total(price: int, per_guest: bool, guests: int) -> int:
    return price * guests if per_guest else price


def plan_total(stops: Sequence[Stop], guests: int) -> int:
    """Sum of the recommended option at every stop."""
    return sum(line_total(s.rec.price, s.rec.per_guest, guests) for s in stops)


def _round_down(n: float, step: int) -> int:
    return int(n // step * step)


def fit_budget(stops: list[Stop], budget: int, guests: int) -> bool:
    """Scale recommended prices down in place so their total is ≤ ``budget``.

    Fixed prices are rounded down to ₹100 (₹10 when small) and per-guest rates to ₹5, so
    rounding can only ever lower the total. Returns True when anything was changed.
    """
    total = plan_total(stops, guests)
    if total <= budget:
        return False
    factor = budget / total if total else 0
    for s in stops:
        r = s.rec
        if r.per_guest:
            r.price = _round_down(r.price * factor, 5)
        else:
            r.price = _round_down(r.price * factor, 100 if r.price >= 1000 else 10)
    # Rounding is always downward, but guard against pathological inputs anyway.
    while plan_total(stops, guests) > budget:
        biggest = max(stops, key=lambda s: line_total(s.rec.price, s.rec.per_guest, guests))
        biggest.rec.price = max(0, biggest.rec.price - (1 if biggest.rec.per_guest else 100))
    return True


def fix_option_kinds(stop: Stop, guests: int) -> None:
    """A "Cheaper" alternative must really be cheaper than the recommendation."""
    rec_total = line_total(stop.rec.price, stop.rec.per_guest, guests)
    for o in stop.options:
        if o.kind == "Cheaper" and line_total(o.price, o.per_guest, guests) >= rec_total:
            o.per_guest = stop.rec.per_guest
            step = 5 if o.per_guest else 100
            o.price = max(0, _round_down(stop.rec.price * 0.75, step))


def clamp_due(due: dt.date | None, today: dt.date, event: dt.date | None) -> tuple[dt.date | None, bool]:
    """Clamp a deadline into [today, event day]. Returns (date, was_late)."""
    if due is None:
        return None, False
    late = due < today
    due = max(due, today)
    if event is not None:
        due = min(due, event)
    return due, late


def valid_event_date(d: dt.date | None, today: dt.date, horizon_days: int = 3 * 365) -> bool:
    """An event date must be today or later, and not absurdly far away."""
    return d is not None and today <= d <= today + dt.timedelta(days=horizon_days)
