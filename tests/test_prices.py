"""Honest prices: every price Waypoint proposes is an estimate; only the user's own numbers and the
quotes they chose are shown without the "≈"."""

import pytest
from fastapi.testclient import TestClient

from app import main, prices
from app.schemas import Option, Quote, Stop
from app.validators import drop_claims

client = TestClient(main.app)


def _stop(**kw) -> Stop:
    rec = Option(id="food-0", kind="Recommended", name="Veg buffet", price=500, per_guest=True)
    return Stop(key="food", label="Food", rec=rec, options=[], **kw)


@pytest.mark.parametrize("kw, src, price", [
    ({}, "estimate", 15000),                                                   # 500 a guest × 30
    ({"status": "skip"}, "none", 0),
    ({"status": "have", "user_cost": 4000}, "yours", 4000),
    ({"status": "own", "note": "my aunt cooks"}, "yours", 0),
    ({"paid": 13500}, "yours", 13500),                                         # booked for
    ({"quote": Quote(vendor="Annapurna Caterers", price=14200)}, "quoted", 14200),
    ({"quote": Quote(vendor="A", price=14200), "paid": 14000}, "yours", 14000),  # what was paid beats the quote
    ({"status": "skip", "paid": 900}, "none", 0),
])
def test_source_and_price(kw, src, price):
    s = _stop(**kw)
    assert prices.source(s) == src
    assert prices.stop_price(s, s.rec, 30, 0) == price


@pytest.mark.parametrize("src, text", [
    ("estimate", "≈ ₹12,000 (estimate)"),
    ("quoted", "₹12,000 (quoted)"),
    ("yours", "₹12,000 (your number)"),
    ("none", "₹12,000"),
])
def test_tag(src, text):
    assert prices.tag(12000, src) == text


@pytest.mark.parametrize("text, kept", [
    ("A warm café. It is available on your date.", "A warm café."),
    ("Great food. Confirmed price for 30 guests.", "Great food."),
    ("Booked for you already! Lovely lawn.", "Lovely lawn."),
    ("Parking nearby. Ask if the terrace is free that evening.", "Parking nearby. Ask if the terrace is free that evening."),
])
def test_model_prose_never_claims_availability_or_a_confirmed_price(text, kept):
    assert drop_claims(text) == kept


def test_plan_says_prices_are_estimates(no_places, aditya):
    plan = client.post("/api/plan", json={"profile": aditya.model_dump(mode="json")}).json()
    assert prices.NOTE in plan["notes"]
    assert all(s.get("quote") is None and s.get("paid") is None for s in plan["stops"])


def test_diff_marks_estimates_and_user_numbers(no_places, aditya):
    plan = client.post("/api/plan", json={"profile": aditya.model_dump(mode="json")}).json()
    out = client.post("/api/apply", json={"plan": plan, "ops": [
        {"op": "have", "stop": "cake", "name": "My sister bakes it", "cost": 900}]}).json()
    row = next(r for r in out["rows"] if r["label"] == "Cake")
    assert "(estimate)" in row["before"] and row["before"].count("≈") == 1
    assert row["after"].endswith("₹900 (your number)")
    assert out["rows"][-1]["label"] == "Total (includes estimates)"


def test_quoted_and_paid_prices_flow_into_the_totals(no_places, aditya):
    plan = client.post("/api/plan", json={"profile": aditya.model_dump(mode="json")}).json()
    venue = next(s for s in plan["stops"] if s["key"] == "venue")
    venue["quote"] = {"vendor": "Olive Kitchen", "price": 11111, "includes": ["chairs"], "clarify": ["GST"]}
    cake = next(s for s in plan["stops"] if s["key"] == "cake")
    cake["paid"] = 777
    out = client.post("/api/apply", json={"plan": plan, "ops": [{"op": "skip_stop", "stop": "invites"}]}).json()
    rows = {r["label"]: r for r in out["rows"]}
    total = int(out["rows"][-1]["after"].replace("₹", "").replace(",", ""))
    est = sum(s["rec"]["price"] * (30 if s["rec"]["per_guest"] else 1) for s in out["plan"]["stops"]
              if s["key"] not in ("venue", "cake", "invites"))
    assert total == est + 11111 + 777
    assert "Venue" not in rows  # unchanged stops are not in the diff, but their quoted price counts
