"""Revising a plan in one sentence: "50 more guests, budget down to ₹35k and the party is two weeks earlier".

The parser is deterministic; the revision keeps what the user decided unless a rule now breaks it,
recomputes every book-by date, shows one diff, and routes impossible numbers to the ways out."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import choices, main, revise
from app.schemas import Profile

from .conftest import TODAY

client = TestClient(main.app)
P = Profile(type="birthday", guests=30, budget=40000, area="Gachibowli, Hyderabad", date=dt.date(2026, 11, 15))
EXPLAINER = "50 more guests, budget down to ₹35k and the party is two weeks earlier"


@pytest.mark.parametrize("text, guests, budget, date", [
    (EXPLAINER, 80, 35000, "2026-11-01"),
    ("actually we have 50 more guests, our budget decreased a bit, and the event is two weeks earlier", 80, 36000, "2026-11-01"),
    ("+50 people", 80, None, None),
    ("add 20 guests", 50, None, None),
    ("invite twenty-five more friends", 55, None, None),
    ("10 fewer guests", 20, None, None),
    ("10 people can't come", 20, None, None),
    ("reduce guests by 5", 25, None, None),
    ("make it 80 people", 80, None, None),
    ("guests down to 25", 25, None, None),
    ("double the guests", 60, None, None),
    ("budget 35k", None, 35000, None),
    ("budget ₹1.5 lakh", None, 150000, None),
    ("Rs 50,000 budget", None, 50000, None),
    ("cut 20%", None, 32000, None),
    ("budget up by 10%", None, 44000, None),
    ("reduce the budget by 5k", None, 35000, None),
    ("budget up by ₹10,000", None, 50000, None),
    ("half the budget", None, 20000, None),
    ("2 weeks earlier", None, None, "2026-11-01"),
    ("postpone by a week", None, None, "2026-11-22"),
    ("push it back 3 days", None, None, "2026-11-18"),
    ("bring it forward by 4 days", None, None, "2026-11-11"),
    ("on 1 Nov", None, None, "2026-11-01"),
    ("move it to 6 Dec", None, None, "2026-12-06"),
    ("the date is 2026-11-08", None, None, "2026-11-08"),
    ("for 60 guests on 8 Nov with ₹60k", 60, 60000, "2026-11-08"),
    ("2 weeks later and 20% more budget", None, 48000, "2026-11-29"),
])
def test_parser_table(text, guests, budget, date):
    rev = revise.read(text, P, TODAY)
    assert (rev.guests, rev.budget, rev.date.isoformat() if rev.date else None) == (guests, budget, date), text
    assert rev.rest == []


@pytest.mark.parametrize("text", ["I don't want a cake", "add a photographer", "move dinner to 9 pm", "sing me a song"])
def test_other_requests_are_left_for_the_other_readers(text):
    rev = revise.read(text, P, TODAY)
    assert not rev.found and rev.rest == [text]


def test_vague_amounts_are_assumed_out_loud_and_past_dates_refused():
    rev = revise.read("our budget decreased a bit", P, TODAY)
    assert rev.budget == 36000 and "10%" in rev.assumed[0]
    rev = revise.read("move it 6 weeks earlier", P, TODAY)  # 4 Oct: already gone
    assert rev.date is None and "already passed" in rev.problems[0]


def test_mixed_sentence_keeps_the_other_clause_verbatim():
    rev = revise.read("We are now 45 people and My aunt is cooking", P, TODAY)
    assert rev.guests == 45 and rev.rest == ["My aunt is cooking"]


# ---------------------------------------------------------------- the revision itself

@pytest.fixture
def plan(no_places, aditya) -> dict:
    r = client.post("/api/plan", json={"profile": aditya.model_dump(mode="json")})
    assert r.status_code == 200
    return r.json()


def adjust(plan, text, **kw) -> dict:
    r = client.post("/api/adjust", json={"plan": plan, "request": text, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def rows(out) -> dict:
    return {r["label"]: r for r in out["rows"]}


def test_explainer_sentence_is_one_diff_with_every_figure(plan):
    out = adjust(plan, EXPLAINER, decided=["venue", "theme"])
    assert out["understood"] and out["source"] == "rules"
    assert out["title"].startswith("50 more guests (30 → 80); budget down to ₹35,000; 2 weeks earlier (1 Nov)")
    r = rows(out)
    assert (r["Guests"]["before"], r["Guests"]["after"]) == ("30", "80")
    assert (r["Budget"]["before"], r["Budget"]["after"]) == ("₹40,000", "₹35,000")
    assert (r["Date"]["before"], r["Date"]["after"]) == ("15 Nov", "1 Nov")
    assert (r["Per guest"]["before"], r["Per guest"]["after"]) == ("₹1,333", "₹437")
    assert (r["Days to go"]["before"], r["Days to go"]["after"]) == ("26", "12")
    assert any(k.startswith("Now urgent: Book the venue") and v["after"] == "late: do it today" for k, v in r.items())
    p = out["plan"]["profile"]
    assert (p["guests"], p["budget"], p["date"]) == (80, 35000, "2026-11-01")


def test_impossible_numbers_route_to_the_ways_out(plan):
    out = adjust(plan, EXPLAINER)
    assert out["block_title"] == "₹35,000 for 80 guests is ₹437 a head." and len(out["ways"]) == 3
    fewer, more, potluck = out["ways"]
    assert fewer["title"] == "Keep ₹35,000, invite 58" and more["title"] == "Keep 80 guests, raise to ₹48,000"
    # a way re-runs the whole revision with its own numbers: the date move stays in
    again = client.post("/api/apply", json={"plan": plan, "ops": fewer["ops"]}).json()
    p = again["plan"]["profile"]
    assert (p["guests"], p["budget"], p["date"]) == (58, 35000, "2026-11-01")
    assert not again["block_title"] and again["understood"]
    pot = client.post("/api/apply", json={"plan": plan, "ops": potluck["ops"]}).json()
    food = next(s for s in pot["plan"]["stops"] if s["key"] == "food")
    assert food["status"] == "own" and "Potluck" in food["note"]


def test_revision_keeps_decided_stops(plan):
    venue, theme = plan["stops"][0], plan["stops"][1]
    out = adjust(plan, "budget down to ₹30k", decided=["venue", "theme"])
    after = {s["key"]: s for s in out["plan"]["stops"]}
    assert "venue" not in out["chosen"] and "theme" not in out["chosen"]
    assert after["venue"]["rec"]["id"] == venue["rec"]["id"] and after["theme"]["rec"]["id"] == theme["rec"]["id"]
    assert any("Kept your decided venue" in e for e in out["explain"])
    assert any(e.startswith("To fit ₹30,000, I picked cheaper options") for e in out["explain"])


def test_a_rule_that_breaks_overrides_a_decision_and_says_so(plan):
    plan["stops"][0]["rec"]["cap"] = 35          # the decided venue holds 35
    plan["stops"][0]["options"][0]["cap"] = 120  # an alternative holds everyone
    out = adjust(plan, "50 more guests", decided=["venue"])
    assert out["chosen"].get("venue") == plan["stops"][0]["options"][0]["id"]
    assert any("holds about 35" in e and "overrides your decision" in e for e in out["explain"])


def test_booked_and_quoted_stops_never_move(plan):
    plan["stops"][2]["quote"] = {"vendor": "Annapurna", "price": 15000}
    out = adjust(plan, "budget down 30%", booked=["venue"])
    assert "venue" not in out["chosen"] and "food" not in out["chosen"]


def test_date_move_recomputes_every_deadline(plan):
    out = adjust(plan, "a week earlier")
    before = {t["id"]: t["due"] for t in plan["tasks"]}
    after = {t["id"]: t["due"] for t in out["plan"]["tasks"]}
    moved = [k for k in after if before.get(k) and after[k] != before[k]]
    assert moved and all(dt.date.fromisoformat(after[k]) <= dt.date.fromisoformat(before[k]) for k in moved)
    assert all(dt.date.fromisoformat(d) <= dt.date(2026, 11, 8) for d in after.values() if d)
    assert "Book-by dates" in rows(out)


def test_guests_resize_the_cake_and_scale_per_guest_food(plan):
    out = adjust(plan, "+30 people")
    cake_b = next(s for s in plan["stops"] if s["key"] == "cake")["rec"]["price"]
    cake_a = next(s for s in out["plan"]["stops"] if s["key"] == "cake")["rec"]["price"]
    assert cake_a == round(cake_b * 2 / 100) * 100
    assert out["over"] > 0 and "choose where the money should go" in out["note"]


def test_other_clauses_ride_along_and_unread_ones_are_reported(plan):
    out = adjust(plan, "10 more guests, no cake")
    assert out["profile"]["guests"] == 40 and not any(s["key"] == "cake" for s in out["plan"]["stops"])
    assert not any("cake" in e.lower() for e in out["explain"])  # nothing said about a stop that is gone
    out = adjust(plan, "10 more guests and sing me a song")
    assert out["profile"]["guests"] == 40 and any("sing me a song" in e for e in out["explain"])


def test_details_page_edits_take_the_same_path(plan):
    said = adjust(plan, EXPLAINER)
    ops = [{"op": "set_guests", "amount": 80}, {"op": "set_budget", "amount": 35000}, {"op": "move_date", "date": "2026-11-01"}]
    edited = client.post("/api/apply", json={"plan": plan, "ops": ops}).json()
    assert edited["plan"]["profile"] == said["plan"]["profile"]
    assert [r for r in edited["rows"]] == [r for r in said["rows"]]
    assert edited["ways"] and edited["block_title"] == said["block_title"]


def test_model_is_only_asked_about_the_clauses_code_could_not_read(plan, monkeypatch):
    seen = {}

    async def fake(task, instructions, data, schema, fallback, **kw):
        seen["request"] = data["request"]
        return choices.llm.Result(choices.OpsDraft(ops=[{"op": "add_stop", "name": "balloon artist"},
                                                        {"op": "set_guests", "amount": 999}]), "ai")
    monkeypatch.setattr(choices.llm, "ask", fake)
    out = adjust(plan, "20 more guests and we'd love a balloon artist")
    assert seen["request"] == "we'd love a balloon artist"
    assert out["profile"]["guests"] == 50  # the model's set_guests is ignored: code read the guests
    assert any(s["label"].lower().startswith("balloon") for s in out["plan"]["stops"]) and out["source"] == "ai"
