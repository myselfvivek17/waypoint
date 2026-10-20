"""Validators: budgets always fit, dates are clamped, text is capped."""

import datetime as dt
import random

import pytest

from app import validators as v
from app.validators import drop_money, drop_wrong_kind
from app.schemas import Option, Stop

T = dt.date(2026, 10, 20)


def stop(key, price, per_guest=False, options=()):
    rec = Option(id=f"{key}-rec", kind="Recommended", name=key, price=price, per_guest=per_guest)
    return Stop(key=key, label=key, rec=rec, options=list(options))


@pytest.mark.parametrize("seed", range(40))
def test_fit_budget_always_sums_within_budget(seed):
    rnd = random.Random(seed)
    guests = rnd.randint(1, 600)
    budget = rnd.randint(1000, 5_000_000)
    stops = [stop(f"s{i}", rnd.randint(0, budget), per_guest=rnd.random() < .3) for i in range(6)]
    v.fit_budget(stops, budget, guests)
    assert v.plan_total(stops, guests) <= budget
    assert all(s.rec.price >= 0 for s in stops)


def test_fit_budget_leaves_fitting_plans_alone():
    stops = [stop("a", 10000), stop("b", 450, per_guest=True)]
    assert v.fit_budget(stops, 40000, 30) is False
    assert [s.rec.price for s in stops] == [10000, 450]


def test_fit_budget_rounds_down_not_up():
    stops = [stop("a", 33333), stop("b", 33333), stop("c", 33334)]
    assert v.fit_budget(stops, 99999, 1)
    assert v.plan_total(stops, 1) <= 99999
    assert all(s.rec.price % 100 == 0 for s in stops)


def test_cheaper_option_is_really_cheaper():
    s = stop("venue", 12000, options=[Option(id="o", kind="Cheaper", name="x", price=15000)])
    v.fix_option_kinds(s, 30)
    assert s.options[0].price < 12000


@pytest.mark.parametrize("due,event,expected,late", [
    (dt.date(2026, 10, 1), dt.date(2026, 11, 15), T, True),
    (dt.date(2026, 11, 1), dt.date(2026, 11, 15), dt.date(2026, 11, 1), False),
    (dt.date(2026, 12, 1), dt.date(2026, 11, 15), dt.date(2026, 11, 15), False),
    (T, T, T, False),
    (None, None, None, False),
])
def test_clamp_due(due, event, expected, late):
    assert v.clamp_due(due, T, event) == (expected, late)


@pytest.mark.parametrize("d,ok", [
    (T, True), (T - dt.timedelta(days=1), False), (T + dt.timedelta(days=365), True),
    (T + dt.timedelta(days=4 * 365), False), (None, False),
])
def test_valid_event_date(d, ok):
    assert v.valid_event_date(d, T) is ok


@pytest.mark.parametrize("raw,limit,out", [
    ("  hello   world ", 50, "hello world"),
    ("a\x00b\x1fc", 50, "abc"),
    ("word " * 40, 30, None),
    (None, 10, ""),
    (12345, 10, "12345"),
])
def test_clip(raw, limit, out):
    got = v.clip(raw, limit)
    assert len(got) <= limit
    if out is not None:
        assert got == out
    else:
        assert got.endswith("…")


@pytest.mark.parametrize("raw,expected", [
    (12000, 12000), ("₹12,000", 12000), (1.5e5, 150000), ("abc", None), (True, None), (-5, 0), (10**12, 10**9),
])
def test_to_int(raw, expected):
    assert v.to_int(raw) == expected


def test_caps_and_required():
    assert v.cap_list(range(10), 3) == [0, 1, 2]
    assert v.cap_list(None, 3) == []
    assert v.require({"a": 1, "b": "", "c": None}, ["a", "b", "c", "d"]) == ["b", "c", "d"]


@pytest.mark.parametrize("text,expected", [
    ("Fits your ₹40,000 budget. Lovely terrace.", "Lovely terrace."),
    ("About Rs 500 a head. Good food.", "Good food."),
    ("Keeps it under 40k. Warm lights!", "Warm lights!"),
    ("Costs 2 lakh rupees.", ""),
    ("Seats 30 guests near the lake.", "Seats 30 guests near the lake."),
])
def test_drop_money_removes_sentences_with_amounts(text, expected):
    assert drop_money(text) == expected


@pytest.mark.parametrize("text,label,expected", [
    ("A cosy café with fairy lights. Great for groups.", "Restaurant", "Great for groups."),
    ("A cosy café with fairy lights.", "Café", "A cosy café with fairy lights."),
    ("The banquet hall has a stage.", "Banquet hall", "The banquet hall has a stage."),
    ("Open lawn and garden seating.", "Hotel", ""),
])
def test_drop_wrong_kind(text, label, expected):
    assert drop_wrong_kind(text, label) == expected
