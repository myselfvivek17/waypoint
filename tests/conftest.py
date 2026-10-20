"""Shared fixtures. Tests never touch the network: the LLM runs in mock mode."""

import datetime as dt
import os

os.environ["WP_LLM_MOCK"] = "1"
os.environ["WP_NO_DOTENV"] = "1"
os.environ["WP_TODAY"] = "2026-10-20"
os.environ.pop("OPENROUTER_API_KEY", None)

import pytest  # noqa: E402

from app import llm, places  # noqa: E402
from app.schemas import Point, Profile  # noqa: E402

TODAY = dt.date(2026, 10, 20)


@pytest.fixture
def today() -> dt.date:
    return TODAY


@pytest.fixture
def aditya() -> Profile:
    return Profile(type="birthday", title="Aditya's 22nd birthday", guests=30, budget=40000,
                   area="Gachibowli, Hyderabad", date=dt.date(2026, 11, 15),
                   who="Aditya, 22 — loves cricket and retro Bollywood", food="Veg + 2 Jain guests",
                   access="Lift or ground floor")


@pytest.fixture
def wedding() -> Profile:
    return Profile(type="wedding", title="A wedding in Jaipur", guests=250, budget=1200000,
                   area="Jaipur, Rajasthan", date=dt.date(2026, 12, 12),
                   who="The couple and both families — folk music, Rajasthani food",
                   food="Vegetarian", access="Seating for elders")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Fresh caches and no rate-limit sleeps for every test."""
    llm.cache.clear()
    for c in (places.geo_cache, places.place_cache, places.photo_cache):
        c.data.clear()
    places._inflight.clear()
    monkeypatch.setattr(places, "MIN_GAP", {})
    from app.main import limiter
    limiter.reset()
    yield


@pytest.fixture
def live_llm(monkeypatch):
    """Pretend a key is configured so the live code path runs (HTTP is mocked by respx)."""
    monkeypatch.setenv("WP_LLM_MOCK", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("WP_LLM_MODEL", "openrouter/free")
    monkeypatch.setenv("WP_LLM_TIMEOUT", "2")


GACHIBOWLI = Point(lat=17.4436, lon=78.3519)


def osm(eid, name, lat, lon, **tags):
    return {"type": "node", "id": eid, "lat": lat, "lon": lon, "tags": {"name": name, **tags}}


@pytest.fixture
def venue_set() -> places.PlaceSet:
    payload = {"elements": [
        osm(1, "Olive Kitchen", 17.4440, 78.3530, amenity="restaurant", wheelchair="yes"),
        osm(2, "Rooftop Cafe", 17.4460, 78.3500, amenity="cafe", **{"diet:vegetarian": "yes"}),
        osm(3, "Lake Garden", 17.4500, 78.3600, leisure="garden"),
        osm(4, "City Bakers", 17.4437, 78.3521, shop="bakery"),
        osm(5, "Community Hall", 17.4450, 78.3525, amenity="community_centre"),
    ]}
    return places.PlaceSet(center=GACHIBOWLI, places=places.parse_overpass(payload, GACHIBOWLI))


@pytest.fixture
def no_places(monkeypatch):
    async def empty(area, guests):
        return places.PlaceSet()

    async def nowhere(area):
        return None
    monkeypatch.setattr(places, "venues", empty)
    monkeypatch.setattr(places, "geocode", nowhere)
