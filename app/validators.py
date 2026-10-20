"""Checks applied to everything the model produces before it reaches the browser.

The model is never trusted with arithmetic or dates: budgets are re-summed and scaled
here, dates are clamped here, and every string is trimmed to a fixed length.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from typing import TypeVar

from .schemas import Stop

T = TypeVar("T")

CAPS = {"name": 80, "reason": 240, "why": 400, "note": 200, "title": 80, "time": 12,
        "invite": 700, "reply": 300, "chip": 60, "label": 40, "query": 80}

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
