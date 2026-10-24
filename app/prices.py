"""Where a price comes from, and how it is labelled. Mirrored by the browser's model.js.

Waypoint never contacts a vendor, so a price it proposes (from the rules or the model) is an
estimate, always shown as one. Only a number the user typed (what they already pay, what they
booked it for) or a quote they pasted and chose is shown without the "≈".
"""

from __future__ import annotations

from typing import Literal

from .rules import inr
from .schemas import Option, Stop
from .validators import line_total

PriceSource = Literal["estimate", "yours", "quoted", "none"]

LABEL: dict[str, str] = {"estimate": "Estimate", "yours": "Your number", "quoted": "Quoted"}
HELP: dict[str, str] = {
    "estimate": "An estimate from Waypoint, not a confirmed price. Confirm it with the vendor.",
    "yours": "A figure you typed in.",
    "quoted": "From a quote you entered. Waypoint hasn't contacted the vendor.",
}
NOTE = "Every price Waypoint proposes is an estimate, not a quote: confirm it with the vendor."


def source(s: Stop) -> PriceSource:
    """Who the stop's price comes from: nobody (skipped), the user, a quote, or Waypoint's estimate."""
    if s.status == "skip":
        return "none"
    if s.status in ("have", "own") or s.paid is not None:
        return "yours"
    if s.quote is not None:
        return "quoted"
    return "estimate"


def stop_price(s: Stop, item: Option, guests: int, extra: int) -> int:
    """What a stop costs: nothing when skipped; the user's own figure when they have it, do it their
    way or booked it; a chosen quote; otherwise the chosen option's estimate plus knock-on extras."""
    match source(s):
        case "none":
            return 0
        case "yours":
            return (s.paid if s.paid is not None and s.status == "open" else s.user_cost) or 0
        case "quoted":
            return s.quote.price if s.quote else 0
    return max(0, line_total(item.price, item.per_guest, guests) + extra)


def tag(amount: int, src: str) -> str:
    """'≈ ₹12,000 (estimate)', '₹12,000 (quoted)', '₹12,000 (your number)'."""
    if src == "estimate":
        return f"≈ {inr(amount)} (estimate)"
    return f"{inr(amount)} ({LABEL.get(src, 'Your number').lower()})" if src in LABEL else inr(amount)
