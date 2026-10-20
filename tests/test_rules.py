"""Table-driven tests for every rule family, including the boundaries."""

import datetime as dt

import pytest

from app import rules
from app.schemas import Profile

T = dt.date(2026, 10, 20)


def P(**kw) -> Profile:
    return Profile(**kw)


@pytest.mark.parametrize("area,tier", [
    ("Gachibowli, Hyderabad", 1), ("Bandra, MUMBAI", 1), ("Jaipur, Rajasthan", 2),
    ("Kochi", 2), ("Shillong", 3), ("", rules.DEFAULT_TIER), ("Hyderabadi biryani house, Nellore", 3),
])
def test_city_tier(area, tier):
    assert rules.city_tier(area) == tier


@pytest.mark.parametrize("etype,area,guests,budget,band", [
    # birthday, tier 1: floor 600, typical 1500, generous 3000 (per guest)
    ("birthday", "Hyderabad", 30, 30 * 599, "impossible"),
    ("birthday", "Hyderabad", 30, 30 * 600, "tight"),
    ("birthday", "Hyderabad", 30, 30 * 1499, "tight"),
    ("birthday", "Hyderabad", 30, 30 * 1500, "comfortable"),
    ("birthday", "Hyderabad", 30, 30 * 2999, "comfortable"),
    ("birthday", "Hyderabad", 30, 30 * 3000, "generous"),
    # wedding, tier 2: floor 1200, typical 4000
    ("wedding", "Jaipur", 250, 1200000, "comfortable"),
    ("wedding", "Jaipur", 250, 250 * 1199, "impossible"),
    ("community", "Shillong", 100, 100 * 150, "tight"),
    ("house-warming", "Pune", 40, 40 * 2500, "generous"),
])
def test_budget_band(etype, area, guests, budget, band):
    got, ins = rules.budget_band(P(type=etype, area=area, guests=guests, budget=budget))
    assert got == band
    assert ins and "a guest" in ins.why
    assert ins.level == ("block" if band == "impossible" else "warn" if band == "tight" else "info")


def test_budget_band_needs_guests_and_budget():
    assert rules.budget_band(P(budget=5000)) == (None, None)
    assert rules.budget_band(P(guests=10)) == (None, None)


def test_budget_block_offers_three_ways_that_fit():
    p = P(type="birthday", guests=100, budget=5000, area="Hyderabad")
    b = rules.budget_block(p)
    assert b and b.type == "budget" and len(b.ways) == 3
    fewer, more, potluck = b.ways
    assert fewer.apply["guests"] >= 4 and 5000 / fewer.apply["guests"] >= 0
    assert more.apply["budget"] >= 100 * 600
    assert potluck.apply["food"].startswith("Potluck")
    assert rules.budget_block(P(type="birthday", guests=30, budget=40000, area="Hyderabad")) is None


@pytest.mark.parametrize("days,level", [
    (0, "rush"), (6, "rush"), (7, "tight"), (20, "tight"), (21, "on-track"), (59, "on-track"),
    (60, "relaxed"), (300, "relaxed"),
])
def test_urgency_levels(days, level):
    got, order, late, ins = rules.urgency(P(type="birthday", date=T + dt.timedelta(days=days)), T)
    assert got == level
    assert order[0] == "venue"  # longest lead first
    assert ins and f"{days} days to go" in ins.why


def test_urgency_marks_late_items_and_book_first():
    _, order, late, ins = rules.urgency(P(type="wedding", date=T + dt.timedelta(days=53)), T)
    assert late == ["venue", "food", "theme", "music"]
    assert "invites first" in ins.why
    assert ins.level == "warn"


def test_urgency_past_and_missing_date():
    assert rules.urgency(P(date=T - dt.timedelta(days=1)), T)[0] == "past"
    assert rules.urgency(P(), T) == (None, [], [], None)


@pytest.mark.parametrize("month,flags", [
    (1, ["peak"]), (2, ["peak"]), (3, []), (4, ["heat"]), (5, ["heat"]), (6, ["monsoon"]),
    (9, ["monsoon"]), (10, []), (11, ["peak"]), (12, ["peak"]),
])
def test_season(month, flags):
    got, ins = rules.season(P(date=dt.date(2027, month, 10)))
    assert got == flags
    assert len(ins) == len(flags)


def test_season_monsoon_is_a_warning():
    _, ins = rules.season(P(date=dt.date(2027, 7, 1)))
    assert ins[0].level == "warn" and "covered" in ins[0].suggestions[0]


@pytest.mark.parametrize("guests,venue_part,catering_part", [
    (1, "home", "home-cooked"), (15, "home", "home-cooked"), (16, "café", "thali"), (40, "café", "thali"),
    (41, "small banquet", "buffet"), (120, "small banquet", "buffet"), (121, "banquet hall or lawn", "live"),
    (400, "banquet hall or lawn", "live"), (401, "convention", "crew"),
])
def test_size(guests, venue_part, catering_part):
    venue, catering, ins = rules.size(P(guests=guests))
    assert venue_part in venue and catering_part in catering
    assert f"{guests} guests" in ins.why


@pytest.mark.parametrize("food,access,expected", [
    ("Veg + 2 Jain guests", "", {"Separate Jain counter and utensils", "Pure-veg kitchen confirmed with the caterer"}),
    ("Veg and non-veg", "", {"Separate veg and non-veg counters"}),
    ("No onion or garlic", "", {"Label satvik dishes (no onion or garlic)"}),
    ("vegan, one nut allergy", "", {"At least two vegan mains and a vegan dessert", "Allergen labels on every dish"}),
    ("", "Lift or ground floor", {"Step-free entrance and a lift confirmed"}),
    ("", "Seating for elders", {"Reserved seating for elders away from speakers"}),
    ("", "No special needs", set()),
    ("", "gold-themed decor", set()),
    ("", "", set()),
])
def test_needs_mandatory_items(food, access, expected):
    items, ins = rules.needs(P(food=food, access=access))
    assert {i.text for i in items} == expected
    assert len(ins) == len(items) and all(i.why for i in ins)


def test_split_rows_keep_a_buffer_and_match_stops():
    for etype, split in rules.SPLIT.items():
        assert sum(split.values()) == pytest.approx(1 - rules.BUFFER)
        assert set(split) == {k for k, _ in rules.STOPS[etype]} == set(rules.LEAD[etype])


def test_date_block():
    b = rules.date_block(P(date=dt.date(2026, 10, 3)), T)
    assert b and b.type == "date" and b.ways[0].apply == {"date": "2027-10-03"}
    assert rules.date_block(P(date=T), T) is None


def test_evaluate_aggregates_and_explains(aditya):
    r = rules.evaluate(aditya, T)
    assert r.band == "tight" and r.per_guest == 1333 and r.tier == 1
    assert r.days_left == 26 and r.urgency == "on-track" and r.late == []
    assert r.season == ["peak"]
    assert {i.family for i in r.insights} == {"budget", "timing", "season", "size", "needs"}
    assert not r.blocks
    why = r.stop_rule("venue", aditya)
    assert "₹1,333 a guest is tight" in why and "Step-free" not in why and "step-free" in why.lower()


def test_inr_formatting():
    assert rules.inr(0) == "₹0"
    assert rules.inr(1333) == "₹1,333"
    assert rules.inr(1200000) == "₹12,00,000"
    assert rules.inr(-2500) == "-₹2,500"
