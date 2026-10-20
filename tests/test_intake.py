"""Deterministic parsing and turn logic of the intake chat (mock model)."""

import asyncio
import datetime as dt

import pytest

from app import intake
from app.schemas import Profile

from .conftest import TODAY


@pytest.mark.parametrize("text,expected", [
    ("15 Nov", dt.date(2026, 11, 15)),
    ("Nov 15th 2026", dt.date(2026, 11, 15)),
    ("2026-12-06", dt.date(2026, 12, 6)),
    ("15/11", dt.date(2026, 11, 15)),
    ("Aditya's 22nd birthday, 30 people, 15 Nov", dt.date(2026, 11, 15)),  # "22nd bir…" is not a month
    ("20 friends on 6th December", dt.date(2026, 12, 6)),
    ("3 March", dt.date(2027, 3, 3)),        # no year: the next 3 March
    ("3 March 2025", dt.date(2025, 3, 3)),   # explicit past year is kept (rules block it)
    ("20 October", TODAY),                   # today is not rolled forward
    ("tomorrow", TODAY + dt.timedelta(days=1)),
    ("31 Feb", None),
    ("sometime soon", None),
])
def test_parse_date(text, expected):
    assert intake.parse_date(text, TODAY) == expected


@pytest.mark.parametrize("text,loose,expected", [
    ("₹40k", False, 40000), ("Rs. 40,000", False, 40000), ("1.5 lakh", False, 150000),
    ("2 cr", False, 20000000), ("40000", False, None), ("40000", True, 40000), ("no idea", True, None),
])
def test_parse_money(text, loose, expected):
    assert intake.parse_money(text, loose=loose) == expected


@pytest.mark.parametrize("text,expected", [
    ("Aditya's 22nd birthday", "birthday"), ("our shaadi in Jaipur", "wedding"),
    ("griha pravesh puja", "house-warming"), ("colony Diwali night", "community"), ("a get-together", None),
])
def test_detect_type(text, expected):
    assert intake.detect_type(text) == expected


def test_opening_brief_fills_what_it_can():
    patch = intake.extract("Aditya's 22nd birthday, 30 people, ₹40k, in Gachibowli, Hyderabad, 15 Nov",
                           None, Profile(), TODAY)
    assert patch == {"type": "birthday", "title": "Aditya's 22nd birthday", "guests": 30, "budget": 40000,
                     "date": dt.date(2026, 11, 15), "area": "Gachibowli, Hyderabad"}


def test_model_patch_is_sanitised_field_by_field():
    out = intake.sanitize_patch({"guests": "30", "budget": "₹40,000", "date": "2026-11-15T00:00", "type": "party",
                                 "who": "x" * 500, "evil": "drop table", "area": None}, TODAY)
    assert out["guests"] == 30 and out["budget"] == 40000 and out["date"] == dt.date(2026, 11, 15)
    assert "type" not in out and "evil" not in out and "area" not in out
    assert len(out["who"]) <= 160


def run(inp):
    return asyncio.run(intake.chat_turn(inp, TODAY))


def test_one_question_at_a_time_until_done():
    out = run(intake.ChatIn(message="Aditya's 22nd birthday, 30 people, ₹40k, in Gachibowli, Hyderabad, 15 Nov"))
    assert not out.done and out.ask == "who"  # required fields inferred, so they are skipped
    transcript, asked, turns = [], out.ask, 1
    profile = out.profile
    while not out.done:
        out = run(intake.ChatIn(profile=profile, message="something", asked=asked, transcript=transcript))
        transcript.append(intake.Turn(field=asked, q="q", a="something"))
        profile, asked, turns = out.profile, out.ask, turns + 1
        assert turns <= intake.MAX_TURNS + 1
    assert profile.who == "something" and profile.guests == 30


def test_required_fields_are_asked_first():
    out = run(intake.ChatIn(message="a birthday party"))
    assert out.ask == "guests" and out.chips and out.source == "mock"


def test_impossible_budget_blocks():
    out = run(intake.ChatIn(message="birthday for 100 people with ₹500, in Hyderabad on 15 Nov"))
    assert out.block is not None and out.block.type == "budget" and out.block.ways


def test_past_date_blocks():
    out = run(intake.ChatIn(message="birthday for 20 people, ₹30k, in Pune on 3 March 2025"))
    assert out.block is not None and out.block.type == "date"


def test_editing_a_chip_never_calls_the_model(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("model called")
    monkeypatch.setattr(intake.llm, "ask", boom)
    out = run(intake.ChatIn(profile=Profile(guests=30), message="45", asked="guests", edit=True))
    assert out.profile.guests == 45


def test_summary_line_reads_like_a_sentence(aditya):
    line = intake.summary_line(aditya.model_copy(update={"have": "A family priest"}), TODAY)
    assert line.startswith("A birthday for Aditya with about 30 guests in Gachibowli, Hyderabad on 15 November")
    assert "₹40,000" in line and "veg + 2 jain guests" in line and "a family priest" in line
    assert line.endswith(".")


def test_first_insights_lead_with_the_budget(aditya):
    ins = intake.first_insights(aditya, TODAY)
    assert ins[0].family == "budget" and "a guest" in ins[0].why and len(ins) == 3


def test_chat_out_carries_understood_and_insights():
    out = asyncio.run(intake.chat_turn(intake.ChatIn(message="A house-warming for 40 people, ₹50k, in Pune on 6 Dec"),
                                       TODAY))
    assert out.understood.startswith("A house-warming with about 40 guests in Pune")
    assert out.understood_source == "mock" and out.insights and out.insights[0].family == "budget"


def test_editing_what_you_already_have_makes_no_question(aditya):
    out = asyncio.run(intake.chat_turn(intake.ChatIn(profile=aditya, message="Our family priest", asked="have",
                                                     edit=True), TODAY))
    assert out.profile.have == "Our family priest" and "our family priest" in out.understood
