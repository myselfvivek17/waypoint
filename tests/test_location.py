"""Location: India-first geocoding, opt-in device position used only for the search."""

import asyncio
import json

import httpx
import respx
from fastapi.testclient import TestClient

from app import intake, main, places, planner, rules
from app.schemas import Point, Profile

from .conftest import TODAY

client = TestClient(main.app)
ADDR = {"suburb": "Gachibowli", "city": "Hyderabad", "state": "Telangana", "country": "India"}


def run(coro):
    return asyncio.run(coro)


def test_geocode_is_restricted_to_india_and_returns_a_short_label():
    with respx.mock as mock:
        route = mock.get(places.NOMINATIM).mock(return_value=httpx.Response(200, json=[
            {"lat": "17.44", "lon": "78.35", "address": ADDR, "display_name": "Gachibowli, Serilingampally, Hyderabad"}]))
        pt = run(places.geocode("Nearby area"))
    assert route.calls[0].request.url.params["countrycodes"] == "in"
    assert pt == Point(lat=17.44, lon=78.35, label="Gachibowli, Hyderabad")


def test_reverse_rounds_and_is_never_cached():
    with respx.mock as mock:
        route = mock.get(places.NOMINATIM_REVERSE).mock(
            return_value=httpx.Response(200, json={"address": ADDR, "display_name": "x"}))
        assert run(places.reverse(17.443612, 78.351934)) == "Gachibowli, Hyderabad"
        run(places.reverse(17.443612, 78.351934))
    assert route.call_count == 2
    assert route.calls[0].request.url.params["lat"] == "17.444"


def test_whereami_is_a_post_and_names_the_area():
    with respx.mock as mock:
        mock.get(places.NOMINATIM_REVERSE).mock(return_value=httpx.Response(200, json={"address": ADDR}))
        r = client.post("/api/whereami", json={"lat": 17.4436, "lon": 78.3519})
    assert r.status_code == 200 and r.json() == {"area": "Gachibowli, Hyderabad"}
    assert client.get("/api/whereami?lat=1&lon=2").status_code in (404, 405)  # coordinates never in a URL
    assert client.post("/api/whereami", json={"lat": 123, "lon": 0}).status_code == 422


def test_whereami_unknown_place_is_a_friendly_404():
    with respx.mock as mock:
        mock.get(places.NOMINATIM_REVERSE).mock(return_value=httpx.Response(200, json={"error": "Unable to geocode"}))
        r = client.post("/api/whereami", json={"lat": 0.0, "lon": 0.0})
    assert r.status_code == 404 and "type your area" in r.json()["error"]


def test_geolocation_is_allowed_for_this_site_only():
    assert "geolocation=(self)" in client.get("/api/health").headers["permissions-policy"]


def test_device_position_never_reaches_the_model(aditya):
    p = aditya.model_copy(update={"point": Point(lat=17.444, lon=78.352), "from_me": True})
    data = planner.plan_request(p, rules.evaluate(p, TODAY), [], [])
    assert "point" not in data["event"] and "from_me" not in data["event"]
    assert "17.444" not in json.dumps(data)


def test_planning_searches_around_the_shared_point(aditya, monkeypatch):
    seen = {}

    async def fake_venues(area, guests, point=None):
        seen["point"] = point
        return places.PlaceSet(center=point)

    async def no_geocode(area):
        raise AssertionError("the typed area must not be geocoded when a point was shared")
    monkeypatch.setattr(places, "venues", fake_venues)
    monkeypatch.setattr(places, "geocode", no_geocode)
    pt = Point(lat=17.444, lon=78.352, label="Gachibowli, Hyderabad")
    ps = run(planner.gather_places(aditya.model_copy(update={"point": pt, "from_me": True})))
    assert seen["point"] == pt and ps.center == pt


def test_typing_an_area_replaces_the_shared_position(aditya):
    p = aditya.model_copy(update={"point": Point(lat=17.444, lon=78.352, label="Gachibowli, Hyderabad"),
                                  "from_me": True})
    same = run(intake.chat_turn(intake.ChatIn(profile=p, message="Gachibowli, Hyderabad", asked="area", edit=True), TODAY))
    assert same.profile.point is not None and same.profile.from_me
    other = run(intake.chat_turn(intake.ChatIn(profile=p, message="Koramangala, Bengaluru", asked="area", edit=True), TODAY))
    assert other.profile.point is None and not other.profile.from_me and other.profile.area == "Koramangala, Bengaluru"


def test_profile_point_is_validated():
    assert Profile(point={"lat": 17.4, "lon": 78.3}).point.lat == 17.4
    r = client.post("/api/plan", json={"profile": {"point": {"lat": 300, "lon": 0}}})
    assert r.status_code == 422
