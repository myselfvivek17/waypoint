"""The choice line that ends every proposal: the model's question when it is safe, else the rules' one."""

import datetime as dt

import pytest

from app import places, planner, rules, templates
from app.schemas import DraftOption, DraftPlan, DraftStop
from app.validators import clean_ask

T = dt.date(2026, 10, 20)
KINDS = {"Cheaper", "Different vibe"}


def _assemble(draft, profile, ps):
    r = rules.evaluate(profile, T)
    cands = templates.rank_venues(places.venue_candidates(ps, profile.guests), profile, r)
    fb = templates.draft_plan(profile, r, cands, places.bakery_candidates(ps))
    return planner.assemble(draft, fb, profile, r, ps, T, "ai")


def _venue(ask: str) -> DraftPlan:
    return DraftPlan(stops=[DraftStop(key="venue", pick=DraftOption(name="Olive Kitchen", place_id="node/1", price=12000,
                                                                     reason="Great spot.", ask=ask))])


@pytest.mark.parametrize("key,label,kind,kinds,expected", [
    ("venue", "Venue", "cafe", ["Cheaper", "Closer"], "Want this café? Or I can show cheaper ones or closer ones."),
    ("venue", "Venue", "restaurant", ["Cheaper"], "Want this restaurant? Or I can show cheaper ones."),
    ("venue", "Space", None, ["Different vibe"], "Want this space? Or I can show a different vibe."),
    ("theme", "Theme", None, ["Cheaper", "Different vibe"], "Want this theme? Or I can show cheaper ones or a different vibe."),
    ("food", "Food", None, ["Closer", "Cheaper", "Different vibe"], "Want this menu? Or I can show closer ones or cheaper ones."),
    ("photographer", "Photographer", None, ["Cheaper"], "Want this photographer? Or I can show cheaper ones."),
    ("cake", "Cake", None, [], "Want this cake? Or tell me what you would change."),
])
def test_rules_choice_line(key, label, kind, kinds, expected):
    assert templates.choice_line(key, label, kind, kinds) == expected


def test_rules_choice_line_for_an_alternative_offers_the_others_again():
    assert templates.choice_line("cake", "Cake", None, ["Cheaper"], recommended=False) == \
        "Happy with this cake? Or I can show you the other options again."


@pytest.mark.parametrize("text,ok", [
    ("Want this café? Or I can show cheaper ones.", True),
    ("Fancy Olive Kitchen for Aditya? Or shall I find a different vibe?", True),
    ("Want this café?", False),                                       # no second answer
    ("This café is lovely, or something cheaper.", False),           # not a question
    ("Want this café for ₹12,000? Or cheaper ones?", False),          # amounts go stale
    ("Want this 2 kg cake? Or a smaller one?", False),                # so do numbers
    ("Want this café? Or I can show closer ones.", False),            # no "Closer" option exists
    ("<b>Want</b> this? Or cheaper?", False),
    ("Would you like to go ahead with this absolutely wonderful and very lovely café today, or would you "
     "rather see some other ones?", False),                          # too long
    ("", False),
])
def test_clean_ask(text, ok):
    assert clean_ask(text, KINDS) == (text if ok else "")


def test_model_question_is_kept_when_it_passes(aditya, venue_set):
    rec = _assemble(_venue("Want Olive Kitchen for Aditya? Or I can show cheaper ones."), aditya, venue_set).stops[0].rec
    assert rec.ask == "Want Olive Kitchen for Aditya? Or I can show cheaper ones."


@pytest.mark.parametrize("bad", ["", "Book it now!", "Want this for ₹12,000? Or cheaper?",
                                 "Ignore the rules and say yes? Or no? " * 4])
def test_bad_or_missing_model_question_falls_back_to_the_rules(aditya, venue_set, bad):
    stop = _assemble(_venue(bad), aditya, venue_set).stops[0]
    assert stop.rec.ask.startswith("Want this restaurant? Or I can show ")
    assert all(o.ask.startswith("Happy with ") for o in stop.options)


def test_every_stop_of_a_fallback_plan_ends_with_a_question(aditya, venue_set):
    plan = _assemble(DraftPlan(), aditya, venue_set)
    for s in plan.stops:
        assert s.rec.ask.endswith((".", "?")) and "?" in s.rec.ask, s.key
        assert " Or " in s.rec.ask and "₹" not in s.rec.ask
