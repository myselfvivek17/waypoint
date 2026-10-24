"""Over budget: "Where should the money go?" The rebalance maths is deterministic; the model only
rewords the options, and may not quote amounts."""

import pytest
from fastapi.testclient import TestClient

from app import choices, main, tradeoffs
from app.schemas import Plan

from .conftest import TODAY

client = TestClient(main.app)


@pytest.fixture
def over(no_places, aditya) -> dict:
    """The Aditya plan after "20 more guests", with the venue decided: ₹9,200 over a ₹40,000 budget."""
    plan = client.post("/api/plan", json={"profile": aditya.model_dump(mode="json")}).json()
    out = client.post("/api/adjust", json={"plan": plan, "request": "20 more guests", "decided": ["venue"]}).json()
    assert out["over"] > 0
    return {"base": plan, "rev": out}


def trade(over, priorities=(), **kw) -> dict:
    rev = over["rev"]
    r = client.post("/api/tradeoffs", json={"plan": rev["plan"], "chosen": rev["chosen"], "ripples": rev["ripples"],
                                            "priorities": list(priorities), "decided": ["venue"], **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_options_close_the_gap_and_say_what_they_give_up(over):
    t = trade(over)
    assert t["over"] == t["total"] - t["budget"] > 0
    assert 2 <= len(t["options"]) <= 3
    for o in t["options"]:
        assert o["fits"] and o["total"] <= t["budget"] and o["gives_up"] and o["line"]
    # the totals are real: applying an option (with the revision) gives exactly that total
    for o in t["options"]:
        a = client.post("/api/apply", json={"plan": over["base"], "ops": over["rev"]["ops"] + o["ops"],
                                            "decided": ["venue"]}).json()
        assert int(a["rows"][-1]["after"].replace("₹", "").replace(",", "")) == o["total"]


def test_priorities_are_protected(over):
    t = trade(over, ["food", "decor"])
    first = t["options"][0]
    assert first["title"] == "Protect food, decor" and first["keeps"] == ["Food as chosen", "Decor as chosen"]
    touched = {op["stop"] for op in first["ops"]}
    assert not touched & {"food", "theme"}
    # with no priorities the basics (food and venue) are protected by default
    default = trade(over)["options"][0]
    assert default["title"] == "Protect food, venue" and not {op["stop"] for op in default["ops"]} & {"food", "venue"}


def test_not_an_even_cut(over):
    """Least sacrifice: most stops stay exactly as they were."""
    t = trade(over)
    stops = len(over["rev"]["plan"]["stops"])
    assert all(len(o["ops"]) < stops for o in t["options"])


def test_fewer_guests_keeps_every_choice(over):
    t = trade(over)
    g = next(o for o in t["options"] if o["ops"][0]["op"] == "set_guests")
    assert g["title"] == "Keep every choice, invite 33" and g["gives_up"] == ["17 fewer guests (50 → 33)"]
    assert g["total"] <= 40000 and g["fits"]


def test_booked_and_quoted_stops_are_never_offered(over):
    t = trade(over, booked=["venue", "theme", "invites", "day"])
    assert all(op["stop"] not in ("venue", "theme", "invites", "day") for o in t["options"] for op in o["ops"])


def test_close_gap_least_sacrifice():
    from app.schemas import Option, Stop

    def stop(key, price):
        return Stop(key=key, label=key, rec=Option(id=key, kind="Recommended", name=key, price=price), options=[])
    a, b, c = stop("a", 0), stop("b", 0), stop("c", 0)
    moves = {"a": [tradeoffs.Move(a, None, 500)], "b": [tradeoffs.Move(b, None, 3000)], "c": [tradeoffs.Move(c, None, 1200)]}
    # one move covers 1000: the smallest that does (c), not the biggest (b)
    assert [m.stop.key for m in tradeoffs.close_gap(1000, moves, [{"a", "b", "c"}])] == ["c"]
    # nothing covers 4000 alone: biggest first, then the smallest that covers the rest
    assert [m.stop.key for m in tradeoffs.close_gap(4000, moves, [{"a", "b", "c"}])] == ["b", "c"]
    # a lower tier gives first even when a higher tier would be cheaper
    assert [m.stop.key for m in tradeoffs.close_gap(400, moves, [{"b"}, {"a", "c"}])] == ["b"]


def test_within_budget_has_no_options(no_places, aditya):
    plan = client.post("/api/plan", json={"profile": aditya.model_dump(mode="json")}).json()
    t = client.post("/api/tradeoffs", json={"plan": plan}).json()
    assert t["over"] == 0 and t["options"] == []


def test_model_only_rewords_and_cannot_quote_amounts(over, monkeypatch):
    async def fake(task, instructions, data, schema, fallback, **kw):
        assert task == "tradeoffs" and all("title" in o for o in data["options"])
        return choices.llm.Result(tradeoffs.Lines(lines=["Keep the food you love. Costs ₹9,000.",
                                                         "A smaller cake keeps the evening easy.",
                                                         "Fewer friends, every choice kept."]), "ai")
    monkeypatch.setattr(tradeoffs.llm, "ask", fake)
    t = trade(over)
    assert t["source"] == "ai"
    assert t["options"][0]["line"] == "Keep the food you love."  # the amount was dropped, the maths kept
    assert all(o["fits"] for o in t["options"])


def test_choose_op_is_validated(over):
    plan = Plan.model_validate(over["base"])
    assert choices.validate_ops([{"op": "choose", "stop": "cake", "item": "cake-1"}], plan)
    assert not choices.validate_ops([{"op": "choose", "stop": "cake", "item": "venue-1"}], plan)
    assert TODAY
