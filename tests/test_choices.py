"""User choice: keep / skip / already have / write my own, modes, added stops, and "Ask to adjust" as
operations. All offline: mock model (so the rules parser reads requests) and no live place listings."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import choices, llm, main, places, planner, rules
from app.schemas import Plan

from .conftest import TODAY

client = TestClient(main.app)


def plan_for(profile) -> dict:
    r = client.post("/api/plan", json={"profile": profile.model_dump(mode="json")})
    assert r.status_code == 200, r.text
    return r.json()


def adjust(plan: dict, text: str, **extra) -> dict:
    r = client.post("/api/adjust", json={"plan": plan, "request": text, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def apply(plan: dict, *ops, **extra) -> dict:
    r = client.post("/api/apply", json={"plan": plan, "ops": list(ops), **extra})
    assert r.status_code == 200, r.text
    return r.json()


def stop(plan: dict, key: str) -> dict | None:
    return next((s for s in plan["stops"] if s["key"] == key), None)


def moments(plan: dict) -> list[dict]:
    return [it for d in plan["days"] for it in d["items"]]


def total(out: dict) -> int:
    return int(out["rows"][-1]["after"].replace("₹", "").replace(",", ""))


@pytest.fixture
def birthday(no_places, aditya) -> dict:
    return plan_for(aditya)


@pytest.fixture
def housewarming(no_places, aditya) -> dict:
    return plan_for(aditya.model_copy(update={"type": "house-warming", "title": "Our house-warming", "guests": 40,
                                              "budget": 50000, "who": "Our family"}))


# ---------------------------------------------------------------- the five scenarios from the review

def test_i_dont_want_cake_removes_it_everywhere_and_frees_budget(birthday):
    cake = stop(birthday, "cake")
    assert cake and any(it["stop"] == "cake" for it in moments(birthday))
    out = adjust(birthday, "I don't want cake")
    assert out["understood"] and out["title"].startswith("Without the cake")
    new = out["plan"]
    assert stop(new, "cake") is None
    assert not any(it["stop"] == "cake" or "cake" in it["title"].lower() for it in moments(new))
    assert out["freed"] == cake["rec"]["price"] and "freed" in out["note"]
    labels = [(r["label"], r["after"]) for r in out["rows"]]
    assert ("Cake", "removed") in labels and ("On the day", "removed") in labels
    assert total(out) == int(out["rows"][-1]["before"].replace("₹", "").replace(",", "")) - cake["rec"]["price"]
    assert not any(t["stop"] == "cake" for t in new["tasks"])


@pytest.mark.parametrize("text", ["We already have a poojari", "already have our own pandit", "the priest is sorted"])
def test_already_have_a_poojari(housewarming, text):
    out = adjust(housewarming, text)
    puja = stop(out["plan"], "puja")
    assert out["understood"] and puja["status"] == "have" and puja["mode"] == "have"
    assert puja["note"]  # the user's own words
    task = next(t for t in out["plan"]["tasks"] if t["stop"] == "puja")
    assert task["text"].startswith("Confirm with")


def test_cook_at_home_changes_food_budget_tasks_and_explains(birthday):
    food = stop(birthday, "food")
    assert food["mode"] in ("caterer", "restaurant") and {m["id"] for m in food["modes"]} >= {"home", "hall"}
    out = adjust(birthday, "We'll cook at home")
    new = stop(out["plan"], "food")
    assert new["mode"] == "home" and new["rec"]["price"] < food["rec"]["price"]
    assert "Cook at home" in new["rule"] and "groceries" in new["rule"]
    task = next(t for t in out["plan"]["tasks"] if t["stop"] == "food")
    assert task["text"].startswith("Plan the menu and buy groceries") and task["due"] >= "2026-11-12"


def test_my_aunt_is_cooking_is_kept_verbatim(birthday):
    out = adjust(birthday, "My aunt is cooking")
    food = stop(out["plan"], "food")
    assert food["status"] == "own" and food["note"] == "My aunt is cooking" and food["mode"] == "home"


def test_add_photographer(birthday):
    out = adjust(birthday, "Add a photographer")
    new = out["plan"]
    keys = [s["key"] for s in new["stops"]]
    assert "photographer" in keys and keys[-1] == "day"  # inserted before the day-of schedule
    ph = stop(new, "photographer")
    assert ph["custom"] and ph["rec"]["price"] > 0 and len(ph["options"]) == 2
    assert any(t["stop"] == "photographer" and t["text"].startswith("Book the photographer") for t in new["tasks"])
    assert ("Photographer", "not in the plan") in [(r["label"], r["before"]) for r in out["rows"]]


def test_skip_invites(birthday):
    out = adjust(birthday, "skip the invites")
    inv = stop(out["plan"], "invites")
    assert inv["status"] == "skip" and not any(t["stop"] == "invites" for t in out["plan"]["tasks"])
    assert any(r["label"] == "Invites" and r["after"] == "skipped" for r in out["rows"])


# ---------------------------------------------------------------- more operations

def test_set_mode_cascades_with_a_reason(birthday):
    out = apply(birthday, {"op": "set_mode", "stop": "venue", "mode": "home"})
    venue, food = stop(out["plan"], "venue"), stop(out["plan"], "food")
    assert venue["mode"] == "home" and venue["rec"]["name"].startswith("Your home")
    if food["mode"] not in ("caterer", "home", "home_caterer"):
        raise AssertionError(food["mode"])
    assert out["source"] == "rules"


def test_food_at_a_restaurant_makes_the_venue_a_restaurant(birthday):
    out = apply(birthday, {"op": "set_mode", "stop": "food", "mode": "restaurant"})
    assert stop(out["plan"], "venue")["mode"] == "restaurant" and "restaurant is the venue" in out["note"]


def test_unknown_mode_or_stop_is_rejected(birthday):
    r = client.post("/api/apply", json={"plan": birthday, "ops": [{"op": "set_mode", "stop": "food", "mode": "teleport"}]})
    assert r.status_code == 200 and not r.json()["understood"]
    r = client.post("/api/apply", json={"plan": birthday, "ops": [{"op": "rm -rf", "stop": "food"}]})
    assert not r.json()["understood"]


def test_remove_any_stop_and_add_a_custom_one(birthday):
    out = apply(birthday, {"op": "remove_stop", "stop": "theme"}, {"op": "add_stop", "name": "Balloon artist"})
    keys = [s["key"] for s in out["plan"]["stops"]]
    assert "theme" not in keys and "x-balloon-artist" in keys
    custom = stop(out["plan"], "x-balloon-artist")
    assert custom["label"] == "Balloon artist" and "estimate" in custom["rec"]["why"]


def test_booked_stops_are_never_removed(birthday):
    out = apply(birthday, {"op": "remove_stop", "stop": "venue"}, booked=["venue"])
    assert stop(out["plan"], "venue") and "already booked" in out["note"]


def test_have_with_name_and_cost_counts_in_the_total(birthday):
    out = apply(birthday, {"op": "have", "stop": "cake", "name": "Aunt Meera's bakery", "cost": 1500})
    cake = stop(out["plan"], "cake")
    assert cake["status"] == "have" and cake["user_cost"] == 1500
    assert any(r["after"] == "you have it: Aunt Meera's bakery · ₹1,500" for r in out["rows"])


def test_day_moments_move_skip_and_own(birthday):
    games = next(it for it in moments(birthday) if it["art"] == "games")
    out = adjust(birthday, "move the games to 8:30 pm")
    moved = next(it for it in moments(out["plan"]) if it["id"] == games["id"])
    assert moved["time"] == "8:30 PM"
    times = [choices.minutes(it["time"]) for it in out["plan"]["days"][0]["items"]]
    assert times == sorted(times)
    out = apply(birthday, {"op": "own_item", "item": games["id"], "text": "Cousins run a cricket quiz"})
    own = next(it for it in moments(out["plan"]) if it["id"] == games["id"])
    assert own["status"] == "own" and own["mine"] == "Cousins run a cricket quiz"


def test_have_from_the_details_page_marks_stops_at_plan_time(no_places, aditya):
    plan = plan_for(aditya.model_copy(update={"have": "a cake from my aunt, our own speaker"}))
    assert stop(plan, "cake")["status"] == "have" and stop(plan, "cake")["note"] == "A cake from my aunt"
    assert any("You already have" in n for n in plan["notes"])


def test_unknown_request_is_honest_with_the_closest_offer(birthday):
    out = adjust(birthday, "the cake should sing")
    assert not out["understood"] and "I can" in out["note"]
    assert out["suggest"] == {**out["suggest"], "op": "skip_stop", "stop": "cake"} and out["suggest_text"]
    out = adjust(birthday, "make it magical")
    assert not out["understood"] and out["suggest"] is None and "I can" in out["note"]


def test_model_ops_are_validated(birthday, monkeypatch):
    async def model(*a, **k):
        return llm.Result(choices.OpsDraft(ops=[{"op": "remove_stop", "stop": "Cake"},
                                                {"op": "remove_stop", "stop": "unicorn"},
                                                {"op": "format_disk"}, "junk"], reply="Done."), "ai")
    monkeypatch.setattr(choices.llm, "ask", model)
    out = adjust(birthday, "no cake please")
    assert out["source"] == "ai" and [o["op"] for o in out["ops"]] == ["remove_stop"] and out["ops"][0]["stop"] == "cake"


@pytest.mark.parametrize("text,op", [
    ("I don't want a cake", "remove_stop"), ("no cake", "remove_stop"), ("without the cake", "remove_stop"),
    ("skip the invites", "skip_stop"), ("we already have a caterer", "have"), ("cook at home", "set_mode"),
    ("add a mehendi artist", "add_stop"), ("add 15 guests", "add_guests"), ("remove 5 guests", "remove_guests"),
    ("cut the budget by 10%", "cut_budget"), ("move it to 6 Dec", "move_date"), ("host it at home", "set_mode"),
    ("move dinner to 9 pm", "move_item"), ("make the cake cheaper", "cheaper"),
])
def test_rules_parser_reads_common_phrasings(birthday, text, op):
    ops = choices.parse_ops(text, Plan.model_validate(birthday), TODAY)
    assert ops and ops[0].op == op, (text, ops)


def test_parse_time():
    assert choices.parse_time("8 pm") == "8:00 PM" and choices.parse_time("at 20:15") == "8:15 PM"
    assert choices.parse_time("8.30pm") == "8:30 PM" and choices.parse_time("noonish") is None


def test_poojari_mode_uses_real_temples_and_never_names_a_person(aditya, monkeypatch):
    from tests.conftest import GACHIBOWLI, osm
    payload = {"elements": [osm(9, "Sri Venkateswara Temple", 17.445, 78.353, amenity="place_of_worship",
                                religion="hindu"),
                            osm(10, "St Mary's Church", 17.446, 78.354, amenity="place_of_worship", religion="christian")]}
    ps = places.PlaceSet(center=GACHIBOWLI, places=places.parse_overpass(payload, GACHIBOWLI))

    async def venues(area, guests, point=None):
        return ps
    monkeypatch.setattr(places, "venues", venues)
    p = aditya.model_copy(update={"type": "house-warming", "guests": 40, "budget": 50000})
    plan = asyncio.run(planner.build_plan(p, TODAY, use_ai=False))
    puja = next(s for s in plan.stops if s.key == "puja")
    assert puja.mode == "need" and puja.rec.where == "Sri Venkateswara Temple"
    assert "Ask at Sri Venkateswara Temple" in puja.rec.reason and "Church" not in puja.model_dump_json()
    assert [m.id for m in puja.modes] == ["need", "have"]
    assert rules.mode_row("poojari", "need")  # an added poojari stop shares the puja modes


@pytest.mark.parametrize("text,expected", [
    ("Join us for cake, dinner and games!", "Join us for dinner and games!"),
    ("Dinner and cake at 8.", "Dinner at 8."),
    ("Music, dancing and a cake await.", "Music, dancing await."),
    ("Dinner at 8.", "Dinner at 8."),
])
def test_drop_from_invite(text, expected):
    assert choices.drop_from_invite(text, "Cake") == expected


def test_removing_the_cake_takes_it_out_of_the_invite(birthday):
    plan = {**birthday, "invite": "Join us for cake, dinner and games on 15 Nov!"}
    out = adjust(plan, "no cake")
    assert "cake" not in out["plan"]["invite"].lower() and "dinner and games" in out["plan"]["invite"]
