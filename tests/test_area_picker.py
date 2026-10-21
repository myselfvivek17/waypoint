"""Area namesakes: "Meerpet, Hyderabad" must not silently resolve to the first OSM hit."""

import asyncio

import httpx
import respx
from fastapi.testclient import TestClient

from app import main, places, planner
from app.schemas import Point

client = TestClient(main.app)

# Shapes as Nominatim (jsonv2, addressdetails=1) returns them for these queries.
UPPAL = {"lat": "17.4546432", "lon": "78.5715499", "category": "boundary", "type": "administrative",
         "addresstype": "suburb", "name": "Ward 4 Meerpet H.B Colony",
         "address": {"suburb": "Ward 4 Meerpet H.B Colony", "city_district": "Greater Hyderabad Municipal Corporation East Zone",
                     "city": "Hyderabad", "county": "Uppal mandal", "state_district": "Medchal–Malkajgiri",
                     "state": "Telangana", "postcode": "500040", "country_code": "in"}}
WARD_OFFICE = {"lat": "17.4580965", "lon": "78.5646487", "category": "building", "type": "public",
               "addresstype": "building", "name": "Meerpet Ward office",
               "address": {"building": "Meerpet Ward office", "suburb": "Ward 4 Meerpet H.B Colony", "city": "Hyderabad"}}
CLINIC = {"lat": "17.3376801", "lon": "78.5322366", "category": "amenity", "type": "veterinary",
          "addresstype": "amenity", "name": "Petyaari Pet Clinic Grooming and Diagnostics",
          "address": {"amenity": "Petyaari Pet Clinic", "suburb": "Karmanghat", "city": "Hyderabad",
                      "county": "Balapur mandal", "state_district": "Ranga Reddy", "postcode": "500079"}}


def road(lat, lon, suburb):
    return {"lat": lat, "lon": lon, "category": "highway", "type": "residential", "addresstype": "residential",
            "name": "Meerpet Road", "address": {"road": "Meerpet Road", "suburb": suburb, "county": "Balapur mandal",
                                                "state_district": "Ranga Reddy", "postcode": "500097"}}


SOUTH = [road("17.3223380", "78.5319055", "Jillelaguda"), road("17.3227773", "78.5290070", "Jillelaguda"),
         road("17.3221924", "78.5371798", "Almasguda")]
NAME_ONLY = [UPPAL, *SOUTH]


def nominatim(by_query: dict):
    def answer(request):
        return httpx.Response(200, json=by_query.get(request.url.params["q"], []))
    return answer


def run(coro):
    return asyncio.run(coro)


def test_meerpet_offers_both_namesakes_with_mandal_district_and_pincode():
    with respx.mock as mock:
        route = mock.get(places.NOMINATIM).mock(side_effect=nominatim(
            {"Meerpet, Hyderabad": [UPPAL, WARD_OFFICE, CLINIC], "Meerpet": NAME_ONLY}))
        opts = run(places.geocode_options("Meerpet, Hyderabad"))
    params = route.calls[0].request.url.params
    assert params["limit"] == "5" and params["addressdetails"] == "1" and params["countrycodes"] == "in"
    assert [(o.label, o.detail) for o in opts] == [
        ("Meerpet H.B Colony, Hyderabad", "Uppal mandal, Medchal–Malkajgiri · 500040"),
        ("Meerpet Road, Jillelaguda", "Balapur mandal, Ranga Reddy · 500097"),
    ]  # the three road segments are one area (< 2 km apart); the clinic and the office are dropped
    assert places.distance_km(opts[0].lat, opts[0].lon, opts[1].lat, opts[1].lon) > 2


def test_the_api_returns_options_instead_of_guessing():
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(side_effect=nominatim(
            {"Meerpet, Hyderabad": [UPPAL, WARD_OFFICE, CLINIC], "Meerpet": NAME_ONLY}))
        overpass = mock.post(url__startswith="https://").mock(return_value=httpx.Response(200, json={"elements": []}))
        r = client.get("/api/places", params={"area": "Meerpet, Hyderabad"})
    body = r.json()
    assert r.status_code == 200 and body["center"] is None and len(body["options"]) == 2
    assert not overpass.called  # no venue search around a guess


def test_single_clear_area_needs_no_picker():
    gachi = {"lat": "17.4436222", "lon": "78.3519638", "category": "place", "type": "suburb", "addresstype": "suburb",
             "name": "Gachibowli", "address": {"suburb": "Gachibowli", "city": "Hyderabad",
                                               "county": "Serilingampalle mandal", "postcode": "500032"}}
    with respx.mock as mock:
        route = mock.get(places.NOMINATIM).mock(side_effect=nominatim({"Gachibowli, Hyderabad": [gachi]}))
        mock.post(url__startswith="https://").mock(return_value=httpx.Response(200, json={"elements": []}))
        r = client.get("/api/places", params={"area": "Gachibowli, Hyderabad"})
    body = r.json()
    assert body["center"]["label"] == "Gachibowli, Hyderabad" and len(body["options"]) == 1
    assert route.call_count == 1  # a clean answer is not second-guessed with a name-only search


def test_clinics_and_buildings_are_filtered_out():
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(side_effect=nominatim({"Meerpet": [WARD_OFFICE, CLINIC, UPPAL]}))
        opts = run(places.geocode_options("Meerpet"))
    assert [o.label for o in opts] == ["Meerpet H.B Colony, Hyderabad"]


def test_a_venue_name_still_finds_its_spot():
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(side_effect=nominatim({"Petyaari Pet Clinic": [CLINIC]}))
        pt = run(places.geocode("Petyaari Pet Clinic"))
    assert pt is not None and pt.lat == 17.3376801


def test_a_district_is_not_a_second_choice_beside_its_city():
    city = {"lat": "26.9154576", "lon": "75.8189817", "category": "place", "type": "city", "addresstype": "city",
            "name": "Jaipur", "address": {"city": "Jaipur", "county": "Jaipur Tehsil", "postcode": "302001"}}
    district = {"lat": "26.9784335", "lon": "75.7122089", "category": "boundary", "type": "administrative",
                "addresstype": "state_district", "name": "Jaipur", "address": {"state_district": "Jaipur"}}
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(side_effect=nominatim({"Jaipur, Rajasthan": [city, district]}))
        opts = run(places.geocode_options("Jaipur, Rajasthan"))
    assert len(opts) == 1 and opts[0].lat == 26.9154576


def test_far_namesakes_from_the_name_only_search_are_ignored():
    elsewhere = road("26.9", "75.8", "Somewhere")  # a "Meerpet Road" in another state
    with respx.mock as mock:
        mock.get(places.NOMINATIM).mock(side_effect=nominatim(
            {"Meerpet, Hyderabad": [UPPAL, CLINIC], "Meerpet": [UPPAL, elsewhere]}))
        opts = run(places.geocode_options("Meerpet, Hyderabad"))
    assert len(opts) == 1


def test_the_picked_option_is_the_search_centre(aditya, monkeypatch):
    seen = {}

    async def fake_venues(area, guests, point=None):
        seen["point"] = point
        return places.PlaceSet(center=point)

    async def no_geocode(area):
        raise AssertionError("a picked area must not be geocoded again")
    monkeypatch.setattr(places, "venues", fake_venues)
    monkeypatch.setattr(places, "geocode", no_geocode)
    picked = Point(lat=17.322338, lon=78.5319055, label="Meerpet Road, Jillelaguda",
                   detail="Balapur mandal, Ranga Reddy · 500097")
    p = aditya.model_copy(update={"area": "Meerpet, Hyderabad", "point": picked})
    ps = run(planner.gather_places(p))
    assert seen["point"] == picked and ps.center.label == "Meerpet Road, Jillelaguda"


def test_csp_allows_osm_tiles_and_nothing_broader():
    csp = client.get("/api/health").headers["content-security-policy"]
    img = next(d for d in csp.split(";") if d.strip().startswith("img-src"))
    assert "https://tile.openstreetmap.org" in img and "*" not in img
