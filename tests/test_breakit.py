"""v4b adversarial QA: inputs a confused or hostile user sends, each pinned after a fix.

Every case here was a real failure once (see celebration-planner-notes/qa/v4b-breakit.md for the ids)."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import intake, main

from .conftest import TODAY

client = TestClient(main.app, raise_server_exceptions=False)
BASE = {"type": "birthday", "guests": 30, "budget": 40000, "area": "Gachibowli, Hyderabad", "date": "2026-11-15"}


def answer(field: str, text: str) -> dict:
    prof = {k: v for k, v in BASE.items() if k != field}
    r = client.post("/api/chat", json={"message": text, "asked": field, "profile": prof})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- B01-B03 intake numbers and dates

@pytest.mark.parametrize("text, guests", [
    ("100000", None), ("0", None), ("-5", None), ("zero", None), ("five", 5), ("1 guest", 1), ("1", 1),
    ("thirty", 30), ("about 40", 40), ("5000", 5000), ("5001", None),
])
def test_guest_answers_are_read_whole_or_refused(text, guests):
    """B01: "100000" was silently read as 1000 and "-5" as 5."""
    out = answer("guests", text)
    assert out["profile"]["guests"] == guests, out
    if guests is None:
        assert out["ask"] == "guests" and out["reply"] != intake.QUESTIONS["guests"][0]


@pytest.mark.parametrize("text, why", [
    ("100000", "up to 5,000 guests"), ("0", "at least 1 guest"), ("-5", "at least 1 guest"), ("lots", "Type a number"),
])
def test_an_unreadable_guest_answer_says_why(text, why):
    """B02: the same question came back with no explanation."""
    assert why in answer("guests", text)["reply"]


@pytest.mark.parametrize("text, budget, why", [
    ("-5000", None, "below zero"), ("0", None, "₹0"), ("₹0", None, "₹0"), ("lots of money", None, "40000"),
    ("fifty thousand", 50000, ""), ("₹1,50,000", 150000, ""), ("1.5 lakh", 150000, ""), ("35k", 35000, ""),
    ("₹10 crore", 100000000, ""),
])
def test_budget_answers(text, budget, why):
    """B03: "-5000" was read as ₹5,000; "fifty thousand" was not read at all."""
    out = answer("budget", text)
    assert out["profile"]["budget"] == budget, out
    assert why in out["reply"]


@pytest.mark.parametrize("text, date", [
    ("today", "2026-10-20"), ("tomorrow", "2026-10-21"), ("next Sunday", "2026-10-25"), ("Saturday", "2026-10-24"),
    ("on sat", "2026-10-24"), ("29 Feb", "2028-02-29"), ("in 3 weeks", "2026-11-10"), ("2 weeks from now", "2026-11-03"),
    ("15 Nov 2029", "2029-11-15"), ("asap", None), ("yesterday", None),
])
def test_date_answers(text, date):
    """B04: "today", "next Sunday", "Saturday" and "29 Feb" were not read; the question repeated silently."""
    out = answer("date", text)
    assert out["profile"]["date"] == date, out
    if date is None:
        assert out["reply"].startswith(("I couldn't read a date", "That date has already passed"))


def test_a_name_is_not_a_weekday():
    assert intake.parse_date("Sunita's 60th birthday, Satish is hosting", TODAY) is None
    assert intake.parse_date("Monica's party", TODAY) is None


@pytest.mark.parametrize("text, guests", [("beta ka bday hai 25 log", 25), ("मेरी बेटी का जन्मदिन, 40 लोग", 40),
                                          ("నా కొడుకు పుట్టినరోజు 30 మంది", 30), ("BIRTHDAY FOR 30 PEOPLE", 30)])
def test_hinglish_hindi_telugu_and_capitals_head_counts(text, guests):
    """B05: an opening message in Hinglish, Hindi or Telugu lost its head count offline."""
    r = client.post("/api/chat", json={"message": text})
    assert r.json()["profile"]["guests"] == guests


@pytest.mark.parametrize("budget", [1, 10, 250])
def test_a_token_budget_gets_honest_ways_out(budget):
    """B06: ₹1 for 30 guests offered "Keep ₹1, invite 4: about ₹0 a head"."""
    out = answer("budget", f"₹{budget}")
    block = out["block"]
    assert block and "₹0 a head" not in block["title"] and "won't cover 30 guests" in block["title"]
    assert [w["title"] for w in block["ways"]][-1] == "Keep both, go potluck" and len(block["ways"]) == 2
    assert all("₹0" not in w["detail"] for w in block["ways"])
    assert "two ways" in block["body"]


# ---------------------------------------------------------------- B07 lavish budgets and tiny parties

def _plan(**kw) -> dict:
    r = client.post("/api/plan", json={"profile": {**BASE, **kw}})
    assert r.status_code == 200, r.text
    return r.json()


def _line(s: dict, guests: int) -> int:
    return s["rec"]["price"] * (guests if s["rec"]["per_guest"] else 1)


@pytest.mark.parametrize("guests, budget, per_guest_max", [(30, 100_000_000, 6000), (1, 40000, 6000), (2, 5_000_000, 6000)])
def test_estimates_stop_at_a_lavish_ceiling(no_places, guests, budget, per_guest_max):
    """B07: ₹10 crore for 30 guests priced a WhatsApp invite at ₹40 lakh; one guest's food at ₹14,400."""
    plan = _plan(guests=guests, budget=budget)
    total = sum(_line(s, guests) for s in plan["stops"])
    assert total <= guests * per_guest_max, total
    invites = next(s for s in plan["stops"] if s["key"] == "invites")
    assert invites["rec"]["price"] <= 20000
    venue = next(s for s in plan["stops"] if s["key"] == "venue")
    assert "the rest of your budget stays spare" in venue["rule"] and "0% of the budget" not in venue["rule"]
    assert any("stays spare" in i["why"] for i in plan["insights"])


def test_an_ordinary_budget_is_still_shared_out_in_full(no_places):
    plan = _plan()
    assert "% of the budget" in next(s for s in plan["stops"] if s["key"] == "venue")["rule"]
    assert not any("stays spare" in i["why"] for i in plan["insights"])
