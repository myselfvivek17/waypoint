"""Places: only real, verified places reach the plan; empty or failing services are handled."""

import asyncio
import datetime as dt

import httpx
import pytest
import respx

from app import places, planner, rules, templates
from app.schemas import DraftOption, DraftPlan, DraftStop, PhotoHint, Profile
from tests.conftest import GACHIBOWLI, osm

T = dt.date(2026, 10, 20)


def run(coro):
    return asyncio.run(coro)


def test_parse_overpass_keeps_named_unique_places_sorted_by_distance():
    payload = {"elements": [
        osm(1, "Far Cafe", 17.47, 78.36, amenity="cafe"),
        osm(2, "Near Cafe", 17.4437, 78.3520, amenity="cafe", wheelchair="yes"),
        osm(3, "Near Cafe", 17.4438, 78.3520, amenity="cafe"),               # duplicate name
        {"type": "node", "id": 4, "lat": 17.44, "lon": 78.35, "tags": {"amenity": "cafe"}},  # unnamed
        {"type": "way", "id": 5, "center": {"lat": 17.445, "lon": 78.353},
         "tags": {"name": "Hall", "amenity": "community_centre", "capacity": "120"}},
        osm(6, "Bank", 17.4437, 78.3520, amenity="bank"),                    # not a kind we use
    ]}
    rows = places.parse_overpass(payload, GACHIBOWLI)
    assert [p.name for p in rows] == ["Near Cafe", "Hall", "Far Cafe"]
    assert rows[0].id == "node/2" and rows[0].wheelchair == "yes" and rows[0].dist_km < 0.1
    assert rows[1].id == "way/5" and rows[1].capacity == 120


@pytest.mark.parametrize("tags,expected", [
    ({"wikimedia_commons": "File:Cafe niloufer.jpg"}, PhotoHint(file="File:Cafe niloufer.jpg", relation="place")),
    ({"image": "https://commons.wikimedia.org/wiki/File:Some_Hall.jpg"},
     PhotoHint(file="File:Some Hall.jpg", relation="place")),
    ({"image": "https://example.com/photo.jpg"}, None),            # not Commons: never used
    ({"wikidata": "Q6434103"}, PhotoHint(wikidata="Q6434103", relation="place")),
    ({"wikimedia_commons": "Category:Things"}, None),
    ({}, None),
])
def test_photo_hints_only_from_commons(tags, expected):
    assert places._photo_from_tags(tags, 0, 0) == expected


def test_empty_overpass_result_is_handled():
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(return_value=httpx.Response(200, json=[{"lat": "17.44", "lon": "78.35"}]))
        mock.post(url__regex=r"https://.*overpass-api\.de/.*").mock(
            return_value=httpx.Response(200, json={"elements": []}))
        ps = run(places.venues("Gachibowli, Hyderabad", 30))
    assert ps.center is not None and ps.places == []


def test_all_overpass_instances_failing_gives_an_empty_set():
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(return_value=httpx.Response(200, json=[{"lat": "17.44", "lon": "78.35"}]))
        route = mock.post(url__regex=r"https://.*overpass-api\.de/.*").mock(return_value=httpx.Response(504))
        ps = run(places.venues("Gachibowli, Hyderabad", 30))
    assert ps.places == [] and route.call_count == len(places.OVERPASS)


def test_unknown_area_and_geocoder_down():
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(return_value=httpx.Response(200, json=[]))
        assert run(places.venues("Nowhere at all", 30)).center is None
    places.geo_cache.data.clear()
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(side_effect=httpx.ConnectError("down"))
        assert run(places.geocode("Pune")) is None


def test_results_are_cached():
    with respx.mock as mock:
        geo = mock.get(places.NOMINATIM).mock(return_value=httpx.Response(200, json=[{"lat": "1", "lon": "2"}]))
        op = mock.post(url__regex=r"https://.*overpass-api\.de/.*").mock(
            return_value=httpx.Response(200, json={"elements": [osm(1, "A", 1.001, 2.001, amenity="cafe")]}))
        run(places.venues("Pune", 20))
        run(places.venues("Pune", 20))
    assert geo.call_count == 1 and op.call_count == 1


def test_imageinfo_keeps_only_wikimedia_hosts_and_cleans_attribution():
    payload = {"query": {"pages": {
        "1": {"title": "File:Hall front.jpg", "index": 1, "imageinfo": [{
            "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Hall.jpg/960px-Hall.jpg?utm=x",
            "mime": "image/jpeg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Hall_front.jpg",
            "extmetadata": {"Artist": {"value": "<a href='x'>Jane &amp; Co</a>"},
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]},
        "2": {"title": "File:Evil.jpg", "index": 2, "imageinfo": [{
            "thumburl": "https://evil.example.com/x.jpg", "mime": "image/jpeg"}]},
        "3": {"title": "File:City map.png", "index": 3, "imageinfo": [{
            "thumburl": "https://upload.wikimedia.org/x/map.png", "mime": "image/png"}]},
    }}}
    photos = places.parse_imageinfo(payload, "place")
    assert len(photos) == 1
    ph = photos[0]
    assert ph.thumb == "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Hall.jpg/960px-Hall.jpg"
    assert ph.author == "Jane & Co" and ph.licence == "CC BY-SA 4.0" and ph.title == "Hall front"


def test_photo_lookup_failure_is_empty():
    with respx.mock as mock:
        mock.get(places.COMMONS).mock(return_value=httpx.Response(503))
        mock.get(places.OPENVERSE).mock(return_value=httpx.Response(429))
        assert run(places.photos(PhotoHint(query="marigold"))) == []


OV_ID = "2e81a945-2a6f-41fd-945c-e1a9400b270d"


def ov(**over):
    row = {"id": OV_ID, "title": "Candid Arts Trust cafe interior", "creator": "Eithin", "license": "by",
           "license_version": "2.0", "license_url": "https://creativecommons.org/licenses/by/2.0/", "mature": False,
           "thumbnail": f"https://api.openverse.org/v1/images/{OV_ID}/thumb/",
           "foreign_landing_url": "https://www.flickr.com/photos/x/1"}
    return {**row, **over}


def test_openverse_results_are_credited_and_served_from_the_proxy():
    rows = places.parse_openverse({"results": [ov(), ov(id="not-a-uuid"), ov(mature=True),
                                               ov(id="11111111-2222-3333-4444-555555555555", license="cc0",
                                                  license_version=None, creator=None)]}, "similar")
    assert [r.licence for r in rows] == ["CC BY 2.0", "CC0"]
    a = rows[0]
    assert a.thumb == f"https://api.openverse.org/v1/images/{OV_ID}/thumb/" and a.page == f"https://openverse.org/image/{OV_ID}"
    assert a.author == "Eithin" and a.relation == "similar" and a.source == "openverse"
    assert rows[1].author == "Unknown author"


def test_venue_photos_put_the_place_first_then_similar_vibe():
    commons = {"query": {"pages": {"1": {"title": "File:Olive Kitchen.jpg", "index": 1, "imageinfo": [{
        "thumburl": "https://upload.wikimedia.org/a/olive.jpg", "mime": "image/jpeg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Olive_Kitchen.jpg", "extmetadata": {}}]}}}}
    with respx.mock as mock:
        mock.get(places.COMMONS).mock(return_value=httpx.Response(200, json=commons))
        mock.get(places.OPENVERSE).mock(return_value=httpx.Response(200, json={"results": [ov()]}))
        rows = run(places.photos(PhotoHint(file="File:Olive Kitchen.jpg", vibe="restaurant interior",
                                           relation="place")))
    assert [(r.relation, r.source) for r in rows] == [("place", "wikimedia"), ("similar", "openverse")]


def test_openverse_is_cached_for_the_small_anonymous_quota():
    with respx.mock as mock:
        route = mock.get(places.OPENVERSE).mock(return_value=httpx.Response(200, json={"results": [ov()]}))
        run(places.openverse("cafe interior"))
        run(places.openverse("Cafe Interior"))
    assert route.call_count == 1


def test_foursquare_is_skipped_without_a_key(monkeypatch):
    monkeypatch.delenv("FOURSQUARE_API_KEY", raising=False)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__startswith=places.FSQ_API)
        assert run(places.foursquare("Olive Kitchen", 17.444, 78.353)) == []
    assert route.call_count == 0


@pytest.mark.parametrize("name,lat,ok", [("Olive Kitchen", 17.4441, True), ("Olive Kitchen", 17.4600, False),
                                         ("Some Other Cafe", 17.4441, False)])
def test_foursquare_needs_a_confident_match(monkeypatch, name, lat, ok):
    monkeypatch.setenv("FOURSQUARE_API_KEY", "k")
    found = {"results": [{"fsq_place_id": "4b0588f7f964a5203ad622e3", "name": name, "latitude": lat,
                          "longitude": 78.353}]}
    pics = [{"prefix": "https://fastly.4sqi.net/img/general/", "suffix": "/123_abc.jpg"},
            {"prefix": "https://evil.example.com/", "suffix": "/x.jpg"}]
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{places.FSQ_API}/places/search").mock(return_value=httpx.Response(200, json=found))
        photos_route = mock.get(f"{places.FSQ_API}/places/4b0588f7f964a5203ad622e3/photos").mock(
            return_value=httpx.Response(200, json=pics))
        rows = run(places.foursquare("Olive Kitchen", 17.444, 78.353))
    if ok:
        assert [r.thumb for r in rows] == ["https://fastly.4sqi.net/img/general/800x600/123_abc.jpg"]
        assert rows[0].source == "foursquare" and rows[0].relation == "place"
        assert photos_route.calls[0].request.headers["authorization"] == "Bearer k"
    else:
        assert rows == [] and photos_route.call_count == 0


# ---------------------------------------------------------------- invented places


def _venue_draft(pid: str, name: str) -> DraftPlan:
    return DraftPlan(stops=[DraftStop(key="venue", pick=DraftOption(name=name, price=12000, place_id=pid,
                                                                     reason="Great spot."),
                                      options=[DraftOption(kind="Cheaper", name="Totally Real Banquets",
                                                           price=9000, place_id="node/424242")])])


def _assemble(draft, profile, ps):
    r = rules.evaluate(profile, T)
    cands = templates.rank_venues(places.venue_candidates(ps, profile.guests), profile, r)
    fb = templates.draft_plan(profile, r, cands, places.bakery_candidates(ps))
    return planner.assemble(draft, fb, profile, r, ps, T, "ai")


def test_invented_place_id_is_rejected_and_never_named(aditya, venue_set):
    plan = _assemble(_venue_draft("node/999999", "The Imaginary Rooftop"), aditya, venue_set)
    venue = plan.stops[0]
    names = [venue.rec.name] + [o.name for o in venue.options]
    assert "The Imaginary Rooftop" not in names and "Totally Real Banquets" not in names
    assert venue.rec.place_id is None
    assert all(o.place_id is None or venue_set.get(o.place_id) for o in [venue.rec, *venue.options])
    assert any("could not be verified" in n for n in plan.notes)


def test_valid_place_id_uses_the_real_name_not_the_models(aditya, venue_set):
    plan = _assemble(_venue_draft("node/1", "Olive Kitchen Rooftop Deluxe"), aditya, venue_set)
    rec = plan.stops[0].rec
    assert rec.place_id == "node/1" and rec.name == "Olive Kitchen" and rec.dist_km is not None
    assert (rec.lat, rec.lon) == (17.4440, 78.3530)


def test_no_places_gives_a_generic_unnamed_venue(aditya):
    plan = _assemble(_venue_draft("", "Sunset Terrace Club"), aditya, places.PlaceSet())
    rec = plan.stops[0].rec
    assert rec.name.startswith("A ") and "Sunset Terrace" not in rec.name and rec.place_id is None


def test_nearby_groups_pick_closest():
    rows = places.parse_overpass({"elements": [
        osm(1, "Tiffin", 17.4437, 78.352, amenity="restaurant"), osm(2, "Chai", 17.4438, 78.352, amenity="cafe"),
        osm(3, "Dosa", 17.4439, 78.352, amenity="fast_food"), osm(4, "Inn", 17.445, 78.352, tourism="hotel"),
        osm(5, "Lot", 17.444, 78.352, amenity="parking")]}, GACHIBOWLI)
    picked = places.pick_nearby(rows)
    assert [p.name for p in picked if places.NEARBY_GROUP[p.kind] == "food"] == ["Tiffin", "Chai"]
    assert {places.NEARBY_GROUP[p.kind] for p in picked} == {"food", "stay", "park"}


def test_venue_candidates_respect_guest_count(venue_set):
    small = places.venue_candidates(venue_set, 30)
    big = places.venue_candidates(venue_set, 300)
    assert {p.kind for p in small} <= {"cafe", "restaurant", "community_centre"}
    assert {p.kind for p in big} <= {"events_venue", "banquet_hall", "conference_centre", "hotel", "resort",
                                     "garden", "park"}
    assert places.bakery_candidates(venue_set)[0].name == "City Bakers"


def test_profile_fixture_is_valid(aditya):
    assert isinstance(aditya, Profile)


def _cake_draft(pid: str, name: str) -> DraftPlan:
    return DraftPlan(stops=[DraftStop(key="cake", pick=DraftOption(name=name, price=2400, place_id=pid))])


@pytest.mark.parametrize("pid,name", [("", "Karachi Bakery"), ("node/777", "Famous Bakers & Sons")])
def test_unverified_shop_name_on_other_stops_is_replaced(aditya, venue_set, pid, name):
    rec = next(s for s in _assemble(_cake_draft(pid, name), aditya, venue_set).stops if s.key == "cake").rec
    assert name not in rec.name and rec.name.lower().endswith(("cake, 2.5 kg eggless", "kg"))


def test_real_shop_named_without_id_is_attached_as_where(aditya, venue_set):
    rec = next(s for s in _assemble(_cake_draft("", "City Bakers"), aditya, venue_set).stops if s.key == "cake").rec
    assert rec.place_id == "node/4" and rec.where == "City Bakers" and "cake" in rec.name.lower()


def test_loose_search_hits_are_filtered_by_title():
    mk = lambda t: places.Photo(thumb="https://upload.wikimedia.org/x.jpg", title=t, author="a", licence="CC0",
                                page="https://commons.wikimedia.org/", relation="inspiration")
    rows = [mk("Avenue des Champs-Elysées, Paris"), mk("Cricket stadium at night"), mk("Fairy light garland")]
    assert [p.title for p in places.relevant(rows, "fairy lights")] == ["Fairy light garland"]
    assert [p.title for p in places.relevant(rows, "cricket stadium")] == ["Cricket stadium at night"]


def test_venue_reason_states_the_real_kind_and_no_stale_amounts(aditya, venue_set):
    draft = DraftPlan(stops=[DraftStop(key="venue", pick=DraftOption(
        name="Olive Kitchen", place_id="node/1", price=12000,
        reason="A cosy café with a rooftop. Fits your ₹40,000 budget. Great for cricket fans.",
        why="Within ₹12,000 for the venue."))])
    rec = _assemble(draft, aditya, venue_set).stops[0].rec
    assert rec.reason.startswith("Restaurant, ") and " km from Gachibowli." in rec.reason
    assert "café" not in rec.reason and "₹" not in rec.reason and "Great for cricket fans." in rec.reason
    assert "₹" not in rec.why
