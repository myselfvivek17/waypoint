"""Real places and real photos only.

* Nominatim geocodes the area the user typed.
* Overpass finds named venues, cafés, halls, parks, bakeries, parking and stays nearby. Each
  instance fails fast and a dead one is skipped for a while (circuit breaker).
* When every Overpass instance is down, Nominatim's bounded POI search is the fallback: one
  request per category around the event point, results merged, de-duplicated and cached.
* Wikimedia Commons / Wikidata supply photos of the place itself, always with author and licence.
* Foursquare (optional, only with ``FOURSQUARE_API_KEY``) adds real photos of the venue.
* Openverse supplies openly licensed "similar vibe" photos, which are never presented as the venue.

Every call has a timeout, a per-host rate limit, an in-memory cache and a graceful empty
result, so a slow or missing service never breaks planning. Nothing here invents a place.
"""

from __future__ import annotations

import asyncio
import html
import logging
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel

from .schemas import PhotoHint, Point

log = logging.getLogger("waypoint.places")

USER_AGENT = "Waypoint-event-planner/0.1 (local, non-commercial; " + os.getenv(
    "WP_CONTACT", "hackathon demo") + ")"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
COUNTRIES = os.getenv("WP_COUNTRIES", "in")  # India-first: "Nearby area" must not resolve to South Carolina
OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
# Fail fast: a refused or hung instance must not hold the plan up. The budget covers all
# instances together; a failed instance is skipped for OVERPASS_COOLDOWN seconds.
OVERPASS_CONNECT = 3.0
OVERPASS_BUDGET = float(os.getenv("WP_OVERPASS_TIMEOUT", "8"))
OVERPASS_COOLDOWN = 600.0
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
OPENVERSE = "https://api.openverse.org/v1/images/"
OPENVERSE_HOST = "api.openverse.org"
FSQ_API = "https://places-api.foursquare.com"
FSQ_VERSION = "2025-06-17"
FSQ_IMAGE_HOST = "fastly.4sqi.net"
IMAGE_HOST = "upload.wikimedia.org"
# Commons may hand out thumbnails on thumb.wikimedia.org; the same path is served by IMAGE_HOST.
THUMB_HOSTS = {IMAGE_HOST, "thumb.wikimedia.org"}
THUMB_WIDTH = 960  # Wikimedia only renders a fixed set of standard widths

# Minimum seconds between two requests to the same host (Nominatim policy: 1/s).
MIN_GAP = {"nominatim.openstreetmap.org": 1.0, "overpass-api.de": 1.0, "lz4.overpass-api.de": 1.0,
           "z.overpass-api.de": 1.0, "maps.mail.ru": 1.0, "overpass.private.coffee": 1.0,
           "commons.wikimedia.org": 0.1, "www.wikidata.org": 0.1,
           "api.openverse.org": 3.1, "places-api.foursquare.com": 0.2}  # Openverse: 20/min without a key
# The only hosts this module ever calls; anything else is refused before a socket is opened.
ALLOWED_HOSTS = frozenset(MIN_GAP)

LABELS = {
    "cafe": "Café", "restaurant": "Restaurant", "events_venue": "Event venue",
    "community_centre": "Community hall", "conference_centre": "Convention centre",
    "banquet_hall": "Banquet hall", "park": "Park", "garden": "Garden", "hotel": "Hotel",
    "resort": "Resort", "bakery": "Bakery", "confectionery": "Sweet shop", "pastry": "Pastry shop",
    "parking": "Parking", "fast_food": "Quick bites", "guest_house": "Guest house",
    "place_of_worship": "Temple",
}
OUTDOOR = {"park", "garden"}
NEARBY_GROUP = {"restaurant": "food", "cafe": "food", "fast_food": "food", "parking": "park",
                "hotel": "stay", "guest_house": "stay"}

# Which kinds of place suit how many guests: (max_guests, kinds in order of preference).
VENUE_FIT = [
    (15, ("restaurant", "cafe")),
    (40, ("cafe", "restaurant", "community_centre")),
    (120, ("restaurant", "events_venue", "banquet_hall", "community_centre", "hotel")),
    (10**9, ("events_venue", "banquet_hall", "conference_centre", "hotel", "resort", "garden", "park")),
]


class Place(BaseModel):
    id: str
    name: str
    kind: str
    label: str
    lat: float
    lon: float
    dist_km: float
    outdoor: bool = False
    wheelchair: str | None = None
    veg: bool = False
    cuisine: str | None = None
    capacity: int | None = None
    website: str | None = None
    photo: PhotoHint | None = None


class Photo(BaseModel):
    thumb: str
    title: str
    author: str
    licence: str
    licence_url: str | None = None
    page: str
    relation: str  # place | near | inspiration | similar
    source: str = "wikimedia"  # wikimedia | foursquare | openverse


class PlaceSet(BaseModel):
    center: Point | None = None
    places: list[Place] = []
    # "overpass" or "nominatim" when a source answered (even with nothing nearby); "" when
    # every source failed, which is the only case the plan calls listings "unavailable".
    source: str = ""

    def get(self, place_id: str | None) -> Place | None:
        return next((p for p in self.places if p.id == place_id), None) if place_id else None


# ------------------------------------------------------------------ plumbing

@dataclass
class _Cache:
    ttl: float
    data: dict = field(default_factory=dict)

    def get(self, key):
        hit = self.data.get(key)
        if hit and time.monotonic() - hit[0] < hit[2]:
            return hit[1]
        self.data.pop(key, None)
        return None

    def put(self, key, value, ttl: float | None = None):
        if len(self.data) > 500:
            self.data.clear()
        self.data[key] = (time.monotonic(), value, self.ttl if ttl is None else ttl)


geo_cache = _Cache(ttl=24 * 3600)
place_cache = _Cache(ttl=6 * 3600)
POI_TTL = 24 * 3600  # Nominatim fallback results
photo_cache = _Cache(ttl=24 * 3600)
_last: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}


async def _get_json(url: str, *, params: dict | None = None, data: dict | None = None,
                    timeout: float | httpx.Timeout = 10.0, headers: dict | None = None) -> dict | list | None:
    """Rate-limited GET/POST returning parsed JSON, or None on any failure."""
    host = urlparse(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        log.warning("refused a call to a host that is not allowed")
        return None
    lock = _locks.setdefault(host, asyncio.Lock())
    try:
        async with lock:
            wait = MIN_GAP.get(host, 0.2) - (time.monotonic() - _last.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            _last[host] = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT, **(headers or {})}) as client:
            if data is not None:
                resp = await client.post(url, data=data)
            else:
                resp = await client.get(url, params=params)
        if resp.status_code != 200:
            log.info("%s answered %s", host, resp.status_code)
            return None
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("%s failed: %s", host, type(exc).__name__)
        return None
    except RuntimeError:  # lock used across event loops in tests
        _locks.pop(host, None)
        return None


def distance_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ------------------------------------------------------------------ geocode + overpass

def short_label(addr: dict, display: str = "") -> str:
    """"Gachibowli, Hyderabad" from a Nominatim address (neighbourhood, then city)."""
    local = next((addr[k] for k in ("suburb", "neighbourhood", "quarter", "city_district", "village", "town")
                  if addr.get(k)), "")
    city = next((addr[k] for k in ("city", "town", "county", "state_district", "state") if addr.get(k)), "")
    parts = [x for x in (local, city) if x]
    if len(parts) == 2 and parts[0] == parts[1]:
        parts = parts[:1]
    label = ", ".join(parts) or ", ".join(display.split(", ")[:3])
    return label[:160]


# Nominatim results that name an area people can meet in. Shops, clinics, buildings and other
# amenities are dropped: "Meerpet, Hyderabad" must not resolve to a pet clinic that mentions it.
AREA_CATEGORIES = {"place", "boundary", "highway"}
AREA_LANDUSE = {"residential"}
DISTINCT_KM = 2.0   # two hits closer than this are the same area
REGION_KM = 60.0    # a name-only follow-up search must stay in the region of the first hit
MAX_OPTIONS = 4
# Districts and states share names with their main town ("Jaipur" the district holds Jaipur the
# city): they are never a separate choice when a finer area of that name lies in the same region.
COARSE = {"state", "state_district", "county", "district", "region"}


def _is_area(row: dict) -> bool:
    cat = row.get("category")
    if cat is None:  # older responses without a category: trust the geocoder
        return True
    return cat in AREA_CATEGORIES or (cat == "landuse" and row.get("type") in AREA_LANDUSE)


def _option(row: dict) -> Point | None:
    """One Nominatim row -> a point labelled for a picker: "Meerpet Road, Jillelaguda" with
    "Balapur mandal, Ranga Reddy · 500097" as its detail."""
    try:
        lat, lon = float(row["lat"]), float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    addr = row.get("address") or {}
    name = re.sub(r"^(?:Ward|Zone|Circle|Division)\s+\d+\s+", "", str(row.get("name") or "")).strip()
    if not name:
        label = short_label(addr, str(row.get("display_name", "")))
    else:
        road = row.get("category") == "highway"
        near = next((addr[k] for k in (("suburb", "neighbourhood", "city", "town", "village") if road else
                                       ("city", "town", "village", "county", "state_district")) if addr.get(k)), "")
        label = name if not near or near.lower() in name.lower() else f"{name}, {near}"
    where = next((addr[k] for k in ("county", "city_district", "town", "city") if addr.get(k)), "")
    region = ", ".join(x for x in (where, addr.get("state_district", "")) if x and x not in label)
    detail = " · ".join(x for x in (region, str(addr.get("postcode") or "")) if x)
    return Point(lat=lat, lon=lon, label=label[:160] or None, detail=detail[:160] or None)


async def _search(q: str) -> list[dict]:
    params = {"q": q, "format": "jsonv2", "limit": 5, "accept-language": "en", "addressdetails": 1}
    if COUNTRIES:
        params["countrycodes"] = COUNTRIES
    rows = await _get_json(NOMINATIM, params=params, timeout=8.0)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def distinct_areas(rows: list[dict], near: Point | None = None) -> list[Point]:
    """Area-like rows as points, one per place: hits within ``DISTINCT_KM`` of an earlier one are
    the same area. With ``near``, only hits within ``REGION_KM`` of it are kept."""
    hits = []
    for row in rows:
        pt = _option(row) if _is_area(row) else None
        if pt is not None and not (near and distance_km(near.lat, near.lon, pt.lat, pt.lon) > REGION_KM):
            hits.append((pt, row.get("addresstype") in COARSE))
    out: list[Point] = []
    for pt, coarse in hits:
        if coarse and any(not c and distance_km(o.lat, o.lon, pt.lat, pt.lon) <= REGION_KM for o, c in hits):
            continue
        if all(distance_km(o.lat, o.lon, pt.lat, pt.lon) > DISTINCT_KM for o in out):
            out.append(pt)
    return out[:MAX_OPTIONS]


async def geocode_options(area: str) -> list[Point]:
    """Every distinct area the text could mean, the geocoder's best first. Cached.

    OSM often has several places with one name ("Meerpet" is a ward in Uppal and a town near
    Balapur). When "Name, City" finds one area padded out with shops or clinics that mention the
    name, the geocoder was unsure: the name alone is searched too, and areas in the same region
    are offered, so the user can pick instead of us guessing. When
    nothing area-like is found, the top hit is still used (someone may type a venue's name).
    """
    key = area.strip().lower()
    if not key:
        return []
    hit = geo_cache.get(("options", key))
    if hit is not None:
        return hit
    rows = await _search(area)
    found = distinct_areas(rows)
    name = area.split(",")[0].strip()
    anchor = found[0] if found else _option(rows[0]) if rows else None
    unsure = any(not _is_area(r) for r in rows)
    if len(found) < 2 and unsure and anchor and name and name.lower() != key:
        found = distinct_areas(rows + await _search(name), near=anchor)
    if not found and anchor:
        found = [anchor]
    geo_cache.put(("options", key), found, ttl=None if found else 600)
    return found


async def geocode(area: str) -> Point | None:
    """The geocoder's best point for "Gachibowli, Hyderabad", with its own name for it."""
    found = await geocode_options(area)
    return found[0] if found else None


async def reverse(lat: float, lon: float) -> str | None:
    """The area name for a device position. Deliberately not cached or logged: the position is
    used for this one lookup and then forgotten."""
    row = await _get_json(NOMINATIM_REVERSE, params={"lat": f"{lat:.3f}", "lon": f"{lon:.3f}", "format": "jsonv2",
                                                     "zoom": 14, "accept-language": "en", "addressdetails": 1},
                          timeout=8.0)
    if not isinstance(row, dict) or "address" not in row:
        return None
    return short_label(row.get("address") or {}, str(row.get("display_name", ""))) or None


def _photo_from_tags(tags: dict, lat: float, lon: float) -> PhotoHint | None:
    """A photo hint from a place's own tags; only Commons-hosted images are accepted."""
    commons = tags.get("wikimedia_commons", "")
    if commons.startswith("File:"):
        return PhotoHint(file=commons[:200], relation="place")
    image = tags.get("image", "")
    m = re.search(r"(?:commons\.wikimedia\.org/wiki/|upload\.wikimedia\.org/.+/)(File:)?([^/?#]+\.(?:jpe?g|png|webp))",
                  image, re.I)
    if m:
        return PhotoHint(file="File:" + unquote(m.group(2)).replace("_", " ")[:200], relation="place")
    if re.fullmatch(r"Q\d{1,12}", tags.get("wikidata", "")):
        return PhotoHint(wikidata=tags["wikidata"], relation="place")
    return None


def display_name(raw: str) -> str:
    """An OSM name as people would write it: one space between words, and a hyphen spaced on one side
    only closed up ("Am- Pm cafe" -> "Am-Pm cafe", "Sri -Sai" -> "Sri-Sai"). A dash spaced on both sides
    ("Cafe - Bakery") is left as it is. Only the shown name changes; ids and links stay OSM's."""
    name = " ".join(raw.split())
    name = re.sub(r"(?<=\w)\s+-(?=\w)|(?<=\w)-\s+(?=\w)", "-", name)
    return name.strip(" -")


def parse_overpass(payload: dict | None, center: Point) -> list[Place]:
    """Overpass JSON -> named, de-duplicated places sorted by distance."""
    out: dict[str, Place] = {}
    for el in (payload or {}).get("elements", []) if isinstance(payload, dict) else []:
        tags = el.get("tags") or {}
        name = display_name(str(tags.get("name:en") or tags.get("name") or ""))
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        kind = next((tags[k] for k in ("amenity", "leisure", "tourism", "shop") if tags.get(k) in LABELS), None)
        if kind == "place_of_worship" and tags.get("religion") != "hindu":
            continue
        if not name or lat is None or lon is None or not kind or not el.get("id"):
            continue
        pid = f"{el.get('type', 'node')}/{el['id']}"
        cap = tags.get("capacity", "")
        out[pid] = Place(
            id=pid, name=name[:80], kind=kind, label=LABELS[kind], lat=float(lat), lon=float(lon),
            dist_km=round(distance_km(center.lat, center.lon, float(lat), float(lon)), 2),
            outdoor=kind in OUTDOOR or tags.get("outdoor_seating") == "yes" and kind == "cafe",
            wheelchair=tags.get("wheelchair"), veg=tags.get("diet:vegetarian") in ("yes", "only"),
            cuisine=(tags.get("cuisine") or None), capacity=int(cap) if cap.isdigit() else None,
            website=_website(tags), photo=_photo_from_tags(tags, float(lat), float(lon)),
        )
    return merge_places(list(out.values()))


def _website(tags: dict) -> str | None:
    url = str(tags.get("website") or tags.get("contact:website") or "").strip()
    return url if re.fullmatch(r"https?://[^\s\"'<>]{3,200}", url) else None


def merge_places(*lists: list[Place]) -> list[Place]:
    """Places from any number of sources as one list, closest first. The same OSM object
    (same id) or the same name (chains, a node and a way for one building) is kept once."""
    rows: list[Place] = []
    ids: set[str] = set()
    names: set[str] = set()
    for p in sorted((p for rows_ in lists for p in rows_), key=lambda p: p.dist_km):
        name = re.sub(r"\W+", " ", unicodedata.normalize("NFKD", p.name.lower())
                      .encode("ascii", "ignore").decode()).strip()
        if p.id not in ids and name not in names:
            ids.add(p.id)
            names.add(name)
            rows.append(p)
    return rows


_down: dict[str, float] = {}  # Overpass instance -> monotonic time until which it is skipped


async def _overpass(query: str) -> dict | None:
    """Try each public Overpass instance until one answers, within ``OVERPASS_BUDGET`` seconds
    in total. An instance that fails is skipped for ``OVERPASS_COOLDOWN`` seconds."""
    deadline = time.monotonic() + OVERPASS_BUDGET
    for url in OVERPASS:
        now = time.monotonic()
        left = deadline - now
        if _down.get(url, 0.0) > now:
            continue
        if left <= 0.5:
            break
        timeout = httpx.Timeout(left, connect=min(OVERPASS_CONNECT, left))
        try:
            payload = await asyncio.wait_for(_get_json(url, data={"data": query}, timeout=timeout), left)
        except TimeoutError:
            payload = None
        if isinstance(payload, dict) and "elements" in payload:
            _down.pop(url, None)
            return payload
        _down[url] = time.monotonic() + OVERPASS_COOLDOWN
        log.info("overpass instance skipped for %.0f s", OVERPASS_COOLDOWN)
    return None


# Nominatim fallback: free-text category searches inside a box around the point. A name hit
# whose OSM tag is not one we know ("Sri Sai Function Hall" tagged only as a building) gets
# the searched kind, but only when its name says so.
POI_VENUE = (("cafe", None), ("restaurant", None), ("banquet hall", "banquet_hall"),
             ("function hall", "banquet_hall"), ("convention centre", "conference_centre"),
             ("community hall", "community_centre"), ("park", None), ("bakery", None))
POI_NEARBY = (("restaurant", None), ("cafe", None), ("parking", None), ("hotel", None))
POI_NAME_HINT = re.compile(r"hall|banquet|convention|function|kalyana|mandap|garden|palace", re.I)
POI_LOOSE_CATEGORIES = {"amenity", "building", "leisure", "tourism", "shop"}


def _viewbox(c: Point, radius: int) -> str:
    dlat = radius / 111_320
    dlon = radius / (111_320 * max(math.cos(math.radians(c.lat)), 0.01))
    return f"{c.lon - dlon:.5f},{c.lat + dlat:.5f},{c.lon + dlon:.5f},{c.lat - dlat:.5f}"


def nominatim_elements(rows: list | None, fallback_kind: str | None = None) -> list[dict]:
    """Nominatim POI rows -> Overpass-shaped elements (same ``node/123`` ids and tags), so one
    parser serves both sources. Extra tags (capacity, cuisine, website, photos) are kept."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or r.get("osm_type") not in ("node", "way", "relation") or not r.get("osm_id"):
            continue
        cat, typ, name = r.get("category"), r.get("type"), str(r.get("name") or "").strip()
        tags = {k: str(v) for k, v in (r.get("extratags") or {}).items() if isinstance(k, str)}
        if name:
            tags["name"] = name
        if cat in ("amenity", "leisure", "tourism", "shop") and typ in LABELS:
            tags[cat] = typ
        elif fallback_kind and cat in POI_LOOSE_CATEGORIES and POI_NAME_HINT.search(name):
            tags["amenity"] = fallback_kind
        else:
            continue
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"type": r["osm_type"], "id": r["osm_id"], "lat": lat, "lon": lon, "tags": tags})
    return out


async def _poi_search(term: str, c: Point, radius: int) -> list[dict] | None:
    """One bounded Nominatim search, cached for a day. None when Nominatim failed."""
    box = _viewbox(c, radius)
    key = ("poi", term, box)
    hit = place_cache.get(key)
    if hit is not None:
        return hit
    rows = await _get_json(NOMINATIM, params={"q": term, "format": "jsonv2", "viewbox": box, "bounded": 1,
                                              "limit": 20, "addressdetails": 1, "extratags": 1,
                                              "accept-language": "en"}, timeout=8.0)
    if not isinstance(rows, list):
        return None
    place_cache.put(key, rows, ttl=POI_TTL)
    return rows


async def _nominatim_pois(c: Point, radius: int, terms: tuple) -> dict | None:
    """Every category in ``terms`` as one Overpass-shaped payload; None when every search
    failed. Requests run one after another: the per-host gap keeps them ≥ 1 s apart."""
    found: dict[tuple, dict] = {}
    answered = False
    for term, kind in terms:
        rows = await _poi_search(term, c, radius)
        answered = answered or rows is not None
        for el in nominatim_elements(rows, kind):
            # The box is a square: drop its corners so the radius means the same for both sources.
            if distance_km(c.lat, c.lon, el["lat"], el["lon"]) <= radius / 1000:
                seen = found.setdefault((el["type"], el["id"]), el)  # found by two searches: keep all tags
                seen["tags"] = {**el["tags"], **seen["tags"]}
    return {"elements": list(found.values())} if answered else None


async def _lookup(overpass_query: str, c: Point, radius: int, terms: tuple) -> tuple[dict | None, str]:
    """Overpass first, then the Nominatim fallback: (payload, source), source "" when both failed."""
    payload = await _overpass(overpass_query)
    if payload is not None:
        return payload, "overpass"
    payload = await _nominatim_pois(c, radius, terms)
    if payload is not None:
        log.info("overpass unavailable; %d places from nominatim", len(payload["elements"]))
        return payload, "nominatim"
    return None, ""


def _venue_query(c: Point, radius: int) -> str:
    a = f"(around:{radius},{c.lat:.5f},{c.lon:.5f})"
    return (
        "[out:json][timeout:20];("
        f'nwr{a}["amenity"~"^(restaurant|cafe|events_venue|community_centre|conference_centre|banquet_hall)$"]["name"];'
        f'nwr{a}["leisure"~"^(park|garden)$"]["name"];'
        f'nwr{a}["tourism"~"^(hotel|resort)$"]["name"];'
        f'nwr{a}["shop"~"^(bakery|confectionery|pastry)$"]["name"];'
        f'nwr{a}["amenity"="place_of_worship"]["religion"="hindu"]["name"];'
        ");out center tags 240;"
    )


def _nearby_query(c: Point, radius: int) -> str:
    a = f"(around:{radius},{c.lat:.5f},{c.lon:.5f})"
    return (
        "[out:json][timeout:15];("
        f'nwr{a}["amenity"~"^(restaurant|cafe|fast_food)$"]["name"];'
        f'nwr{a}["amenity"="parking"];'
        f'nwr{a}["tourism"~"^(hotel|guest_house)$"]["name"];'
        ");out center tags 120;"
    )


_inflight: dict[tuple, asyncio.Task] = {}


async def venues(area: str, guests: int | None, point: Point | None = None) -> PlaceSet:
    """Candidate venues and bakeries around the area (or a shared point). Empty set when
    nothing is reachable.

    Concurrent calls for the same area share one lookup, so the browser can prefetch while
    the intake chat is still going and the plan request simply joins it.
    """
    where = f"{point.lat:.3f},{point.lon:.3f}" if point else area.strip().lower()
    key = (where, (guests or 0) > 120)
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_venues(area, guests, point))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))
    return await asyncio.shield(task)


async def _venues(area: str, guests: int | None, point: Point | None = None) -> PlaceSet:
    center = point or await geocode(area)
    if center is None:
        return PlaceSet()
    radius = 3000 if (guests or 0) <= 120 else 8000
    key = ("venues", round(center.lat, 3), round(center.lon, 3), radius)
    hit = place_cache.get(key)
    if hit is None:
        payload, source = await _lookup(_venue_query(center, radius), center, radius, POI_VENUE)
        hit = (parse_overpass(payload, center), source)
        place_cache.put(key, hit, ttl=None if source == "overpass" else POI_TTL if source else 120)
    return PlaceSet(center=center, places=hit[0], source=hit[1])


async def nearby(lat: float, lon: float) -> list[Place]:
    """Food, parking and stays within 1.5 km of a point."""
    center = Point(lat=lat, lon=lon)
    key = ("nearby", round(lat, 3), round(lon, 3))
    hit = place_cache.get(key)
    if hit is None:
        payload, source = await _lookup(_nearby_query(center, 1500), center, 1500, POI_NEARBY)
        rows = parse_overpass(payload, center)
        # Unnamed parking is common and still useful; parse_overpass drops unnamed places,
        # so label parking explicitly from the raw payload.
        for el in (payload or {}).get("elements", []):
            tags = el.get("tags") or {}
            if tags.get("amenity") == "parking" and not tags.get("name"):
                lat2 = el.get("lat", (el.get("center") or {}).get("lat"))
                lon2 = el.get("lon", (el.get("center") or {}).get("lon"))
                if lat2 is not None and lon2 is not None and el.get("id"):
                    rows.append(Place(id=f"{el.get('type', 'node')}/{el['id']}", name="Public parking",
                                      kind="parking", label="Parking", lat=lat2, lon=lon2,
                                      dist_km=round(distance_km(lat, lon, lat2, lon2), 2)))
        rows.sort(key=lambda p: p.dist_km)
        hit = rows
        place_cache.put(key, hit, ttl=None if source == "overpass" else POI_TTL if source else 120)
    return hit


def pick_nearby(rows: list[Place], per_group: int = 2) -> list[Place]:
    """The closest couple of places in each group (food, parking, stay)."""
    chosen: list[Place] = []
    counts: dict[str, int] = {}
    for p in rows:
        g = NEARBY_GROUP.get(p.kind)
        if g and counts.get(g, 0) < per_group:
            counts[g] = counts.get(g, 0) + 1
            chosen.append(p)
    return chosen


def venue_candidates(ps: PlaceSet, guests: int | None, limit: int = 12) -> list[Place]:
    """Places that suit the guest count, closest first, mixing kinds."""
    kinds = next(k for lim, k in VENUE_FIT if (guests or 0) <= lim)
    rows = [p for p in ps.places if p.kind in kinds and (p.capacity is None or p.capacity >= (guests or 0))]
    rows.sort(key=lambda p: (kinds.index(p.kind) > 2, p.dist_km))
    return rows[:limit]


def temple_candidates(ps: PlaceSet, limit: int = 3) -> list[Place]:
    """Temples near the area, where the host can ask for a poojari (never a named person)."""
    return [p for p in ps.places if p.kind == "place_of_worship"][:limit]


def bakery_candidates(ps: PlaceSet, limit: int = 4) -> list[Place]:
    return [p for p in ps.places if p.kind in ("bakery", "pastry", "confectionery")][:limit]


# ------------------------------------------------------------------ photos

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", text or ""))).strip()[:limit]


def parse_imageinfo(payload: dict | None, relation: str, limit: int = 3) -> list[Photo]:
    """Commons imageinfo -> photos with attribution; anything not hosted on Commons is dropped."""
    pages = ((payload or {}).get("query") or {}).get("pages") or {}
    items = pages.values() if isinstance(pages, dict) else pages
    out: list[Photo] = []
    for page in sorted(items, key=lambda p: p.get("index", 0)):
        info = (page.get("imageinfo") or [{}])[0]
        url = urlparse(info.get("thumburl") or "")
        if url.scheme != "https" or url.hostname not in THUMB_HOSTS or not str(info.get("mime", "")).startswith(
                ("image/jpeg", "image/png", "image/webp")):
            continue
        title = page.get("title", "")
        if re.search(r"logo|map|icon|flag|diagram|signature|police|hospital|office|court|bank|school|"
                     r"institute|college|station|accident|crowd|protest", title, re.I):
            continue
        meta = info.get("extmetadata") or {}
        author = _clean((meta.get("Artist") or {}).get("value", "")) or "Unknown author"
        licence = _clean((meta.get("LicenseShortName") or {}).get("value", ""), 40) or "See source"
        lic_url = (meta.get("LicenseUrl") or {}).get("value") or None
        nice = re.sub(r"^File:|\.[a-z0-9]+$", "", title, flags=re.I).replace("_", " ")
        thumb = f"https://{IMAGE_HOST}{url.path}"
        page_url = str(info.get("descriptionurl") or "")
        if urlparse(page_url).hostname != "commons.wikimedia.org" or not page_url.startswith("https://"):
            page_url = "https://commons.wikimedia.org/"
        out.append(Photo(thumb=thumb, title=nice[:100], author=author, licence=licence,
                         licence_url=lic_url if lic_url and lic_url.startswith("https://") else None,
                         page=page_url, relation=relation))
        if len(out) >= limit:
            break
    return out


def relevant(rows: list[Photo], query: str) -> list[Photo]:
    """Keep search hits whose title shares a real word with the query (Commons search is loose)."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 3]
    if not words:
        return rows
    stems = {w[:-1] if w.endswith("s") else w for w in words}
    return [p for p in rows if any(st in p.title.lower() for st in stems)]


_II = {"prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": THUMB_WIDTH, "format": "json",
       "action": "query", "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl"}


# ---- Openverse: openly licensed photos of similar places and ideas (no key needed)

LICENCE_NAMES = {"cc0": "CC0", "pdm": "Public domain"}


def parse_openverse(payload: dict | None, relation: str, limit: int = 3) -> list[Photo]:
    """Openverse search results -> credited photos served from the Openverse thumbnail proxy."""
    out: list[Photo] = []
    for r in ((payload or {}).get("results") or []) if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or r.get("mature") or not re.fullmatch(r"[0-9a-f-]{36}", str(r.get("id", ""))):
            continue
        thumb = f"https://{OPENVERSE_HOST}/v1/images/{r['id']}/thumb/"
        code = str(r.get("license", "")).lower()
        licence = LICENCE_NAMES.get(code) or f"CC {code.upper()} {r.get('license_version') or ''}".strip()
        lic_url = str(r.get("license_url") or "")
        out.append(Photo(thumb=thumb, title=_clean(r.get("title") or "Untitled", 100) or "Untitled",
                         author=_clean(r.get("creator") or "", 80) or "Unknown author", licence=licence[:40],
                         licence_url=lic_url if lic_url.startswith("https://creativecommons.org/") else None,
                         page=f"https://openverse.org/image/{r['id']}", relation=relation, source="openverse"))
        if len(out) >= limit:
            break
    return out


async def openverse(query: str, relation: str = "similar", limit: int = 3) -> list[Photo]:
    """Photos for a phrase like "cafe interior". Cached for a day: the anonymous quota is small."""
    key = ("openverse", query.lower(), relation)
    hit = photo_cache.get(key)
    if hit is not None:
        return hit
    payload = await _get_json(OPENVERSE, params={"q": query, "page_size": 8, "mature": "false"}, timeout=20.0)
    rows = parse_openverse(payload if isinstance(payload, dict) else None, relation, limit)
    photo_cache.put(key, rows, ttl=None if rows else 120)  # a slow or busy Openverse is retried soon
    return rows


# ---- Foursquare: real venue photos, only when the host configured a key

def fsq_key() -> str:
    return os.getenv("FOURSQUARE_API_KEY", "").strip()


def _same_place(a: str, b: str) -> bool:
    x, y = re.sub(r"\W+", " ", a.lower()).strip(), re.sub(r"\W+", " ", b.lower()).strip()
    return bool(x and y) and (x in y or y in x)


async def foursquare(name: str, lat: float, lon: float, limit: int = 3) -> list[Photo]:
    """Photos of a named place from Foursquare Places. Empty without a key or a confident match:
    the match must share the name and lie within 150 m of the OSM point."""
    key = fsq_key()
    if not key:
        return []
    headers = {"Authorization": f"Bearer {key}", "X-Places-Api-Version": FSQ_VERSION, "Accept": "application/json"}
    found = await _get_json(f"{FSQ_API}/places/search", params={
        "query": name, "ll": f"{lat:.5f},{lon:.5f}", "radius": 150, "limit": 3}, headers=headers, timeout=8.0)
    match = None
    for r in (found or {}).get("results", []) if isinstance(found, dict) else []:
        try:
            near = distance_km(lat, lon, float(r["latitude"]), float(r["longitude"])) <= 0.15
        except (KeyError, TypeError, ValueError):
            near = False
        if near and _same_place(str(r.get("name", "")), name) and re.fullmatch(r"\w{8,40}", str(r.get("fsq_place_id", ""))):
            match = r
            break
    if match is None:
        return []
    pid = match["fsq_place_id"]
    pics = await _get_json(f"{FSQ_API}/places/{pid}/photos", params={"limit": limit}, headers=headers, timeout=8.0)
    out: list[Photo] = []
    for ph in pics if isinstance(pics, list) else []:
        prefix, suffix = str(ph.get("prefix", "")), str(ph.get("suffix", ""))
        url = urlparse(prefix)
        if url.scheme != "https" or url.hostname != FSQ_IMAGE_HOST or not re.fullmatch(r"/[\w./-]+\.(?:jpe?g|png)", suffix):
            continue
        out.append(Photo(thumb=f"{prefix}800x600{suffix}", title=_clean(match.get("name", name), 100), author="Foursquare",
                         licence="Foursquare", page=f"https://foursquare.com/v/{pid}", relation="place",
                         source="foursquare"))
    return out[:limit]


# ---- the pipeline

async def _commons(hint: PhotoHint) -> list[Photo]:
    result: list[Photo] = []
    if hint.wikidata and not hint.file:
        claims = await _get_json(WIKIDATA, params={"action": "wbgetclaims", "entity": hint.wikidata,
                                                   "property": "P18", "format": "json"})
        try:
            name = claims["claims"]["P18"][0]["mainsnak"]["datavalue"]["value"]  # type: ignore[index]
            hint = hint.model_copy(update={"file": "File:" + str(name)})
        except (KeyError, IndexError, TypeError):
            pass
    if hint.file:
        payload = await _get_json(COMMONS, params={**_II, "titles": hint.file})
        result = parse_imageinfo(payload if isinstance(payload, dict) else None, "place", 1)
    return result


async def photos(hint: PhotoHint) -> list[Photo]:
    """Photos for an option, the place's own first.

    1. Wikimedia Commons / Wikidata photos of this place (OSM tags).
    2. Foursquare photos of this place, when a key is configured and the match is confident.
    3. Commons photos taken near the point, only when nothing better is asked for.
    4. Openverse "similar vibe" photos for ``vibe`` (always labelled as not this place).
    5. Commons, then Openverse, inspiration photos for an idea in ``query``.
    """
    key = hint.model_dump_json()
    hit = photo_cache.get(key)
    if hit is not None:
        return hit
    result = await _commons(hint)
    if hint.name and hint.lat is not None and hint.lon is not None and not result:
        result += await foursquare(hint.name, hint.lat, hint.lon)
    if not result and not hint.vibe and not hint.query and hint.lat is not None and hint.lon is not None:
        payload = await _get_json(COMMONS, params={**_II, "generator": "geosearch", "ggsnamespace": 6,
                                                   "ggscoord": f"{hint.lat:.5f}|{hint.lon:.5f}",
                                                   "ggsradius": 1000, "ggslimit": 8})
        result = parse_imageinfo(payload if isinstance(payload, dict) else None, "near")
    if hint.vibe:
        result += await openverse(hint.vibe, "similar")
    if hint.query:
        payload = await _get_json(COMMONS, params={**_II, "generator": "search", "gsrnamespace": 6,
                                                   "gsrsearch": f"{hint.query} filetype:bitmap", "gsrlimit": 8})
        found = relevant(parse_imageinfo(payload if isinstance(payload, dict) else None, "inspiration", 8),
                         hint.query)[:3]
        if len(found) < 3:
            found += (await openverse(hint.query, "inspiration"))[: 3 - len(found)]
        result += found
    result = result[:6]
    photo_cache.put(key, result, ttl=None if result else 120)
    return result
