"""Real places and real photos only.

* Nominatim geocodes the area the user typed.
* Overpass finds named venues, cafés, halls, parks, bakeries, parking and stays nearby.
* Wikimedia Commons / Wikidata supply photos, always with author and licence.

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
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel

from .schemas import PhotoHint, Point

log = logging.getLogger("waypoint.places")

USER_AGENT = "Waypoint-event-planner/0.1 (local, non-commercial; " + os.getenv(
    "WP_CONTACT", "hackathon demo") + ")"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
)
OVERPASS_TIMEOUT = float(os.getenv("WP_OVERPASS_TIMEOUT", "45"))
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
IMAGE_HOST = "upload.wikimedia.org"
# Commons may hand out thumbnails on thumb.wikimedia.org; the same path is served by IMAGE_HOST.
THUMB_HOSTS = {IMAGE_HOST, "thumb.wikimedia.org"}
THUMB_WIDTH = 960  # Wikimedia only renders a fixed set of standard widths

# Minimum seconds between two requests to the same host (Nominatim policy: 1/s).
MIN_GAP = {"nominatim.openstreetmap.org": 1.0, "overpass-api.de": 1.0, "lz4.overpass-api.de": 1.0,
           "z.overpass-api.de": 1.0, "commons.wikimedia.org": 0.1, "www.wikidata.org": 0.1}

LABELS = {
    "cafe": "Café", "restaurant": "Restaurant", "events_venue": "Event venue",
    "community_centre": "Community hall", "conference_centre": "Convention centre",
    "banquet_hall": "Banquet hall", "park": "Park", "garden": "Garden", "hotel": "Hotel",
    "resort": "Resort", "bakery": "Bakery", "confectionery": "Sweet shop", "pastry": "Pastry shop",
    "parking": "Parking", "fast_food": "Quick bites", "guest_house": "Guest house",
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
    photo: PhotoHint | None = None


class Photo(BaseModel):
    thumb: str
    title: str
    author: str
    licence: str
    licence_url: str | None = None
    page: str
    relation: str


class PlaceSet(BaseModel):
    center: Point | None = None
    places: list[Place] = []

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
photo_cache = _Cache(ttl=24 * 3600)
_last: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}


async def _get_json(url: str, *, params: dict | None = None, data: dict | None = None,
                    timeout: float = 10.0) -> dict | list | None:
    """Rate-limited GET/POST returning parsed JSON, or None on any failure."""
    host = urlparse(url).hostname or ""
    lock = _locks.setdefault(host, asyncio.Lock())
    try:
        async with lock:
            wait = MIN_GAP.get(host, 0.2) - (time.monotonic() - _last.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            _last[host] = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
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

async def geocode(area: str) -> Point | None:
    """Turn "Gachibowli, Hyderabad" into a point. Cached; None when unknown."""
    key = area.strip().lower()
    if not key:
        return None
    hit = geo_cache.get(key)
    if hit is not None:
        return hit or None
    rows = await _get_json(NOMINATIM, params={"q": area, "format": "jsonv2", "limit": 1,
                                              "accept-language": "en"}, timeout=8.0)
    point = None
    if isinstance(rows, list) and rows:
        try:
            point = Point(lat=float(rows[0]["lat"]), lon=float(rows[0]["lon"]))
        except (KeyError, TypeError, ValueError):
            point = None
    geo_cache.put(key, point or False, ttl=None if point else 600)
    return point


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


def parse_overpass(payload: dict | None, center: Point) -> list[Place]:
    """Overpass JSON -> named, de-duplicated places sorted by distance."""
    out: dict[str, Place] = {}
    for el in (payload or {}).get("elements", []) if isinstance(payload, dict) else []:
        tags = el.get("tags") or {}
        name = (tags.get("name:en") or tags.get("name") or "").strip()
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        kind = next((tags[k] for k in ("amenity", "leisure", "tourism", "shop") if tags.get(k) in LABELS), None)
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
            photo=_photo_from_tags(tags, float(lat), float(lon)),
        )
    rows, seen = [], set()
    for p in sorted(out.values(), key=lambda p: p.dist_km):
        if p.name.lower() not in seen:  # chains and duplicate nodes: keep the closest
            seen.add(p.name.lower())
            rows.append(p)
    return rows


async def _overpass(query: str) -> dict | None:
    """Try each official Overpass instance until one answers."""
    for url in OVERPASS:
        payload = await _get_json(url, data={"data": query}, timeout=OVERPASS_TIMEOUT)
        if isinstance(payload, dict) and "elements" in payload:
            return payload
    return None


def _venue_query(c: Point, radius: int) -> str:
    a = f"(around:{radius},{c.lat:.5f},{c.lon:.5f})"
    return (
        "[out:json][timeout:20];("
        f'nwr{a}["amenity"~"^(restaurant|cafe|events_venue|community_centre|conference_centre|banquet_hall)$"]["name"];'
        f'nwr{a}["leisure"~"^(park|garden)$"]["name"];'
        f'nwr{a}["tourism"~"^(hotel|resort)$"]["name"];'
        f'nwr{a}["shop"~"^(bakery|confectionery|pastry)$"]["name"];'
        ");out center tags 200;"
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


async def venues(area: str, guests: int | None) -> PlaceSet:
    """Candidate venues and bakeries around the area. Empty set when nothing is reachable.

    Concurrent calls for the same area share one lookup, so the browser can prefetch while
    the intake chat is still going and the plan request simply joins it.
    """
    key = (area.strip().lower(), (guests or 0) > 120)
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_venues(area, guests))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))
    return await asyncio.shield(task)


async def _venues(area: str, guests: int | None) -> PlaceSet:
    center = await geocode(area)
    if center is None:
        return PlaceSet()
    radius = 3000 if (guests or 0) <= 120 else 8000
    key = ("venues", round(center.lat, 3), round(center.lon, 3), radius)
    hit = place_cache.get(key)
    if hit is None:
        payload = await _overpass(_venue_query(center, radius))
        hit = parse_overpass(payload, center)
        place_cache.put(key, hit, ttl=None if payload else 120)
    return PlaceSet(center=center, places=hit)


async def nearby(lat: float, lon: float) -> list[Place]:
    """Food, parking and stays within 1.5 km of a point."""
    center = Point(lat=lat, lon=lon)
    key = ("nearby", round(lat, 3), round(lon, 3))
    hit = place_cache.get(key)
    if hit is None:
        payload = await _overpass(_nearby_query(center, 1500))
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
        place_cache.put(key, hit, ttl=None if payload else 120)
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


async def photos(hint: PhotoHint) -> list[Photo]:
    """Photos for a hint: a Commons file, a Wikidata item, a point, or a search phrase."""
    key = hint.model_dump_json()
    hit = photo_cache.get(key)
    if hit is not None:
        return hit
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
    if not result and hint.lat is not None and hint.lon is not None:
        payload = await _get_json(COMMONS, params={**_II, "generator": "geosearch", "ggsnamespace": 6,
                                                   "ggscoord": f"{hint.lat:.5f}|{hint.lon:.5f}",
                                                   "ggsradius": 1000, "ggslimit": 8})
        result = parse_imageinfo(payload if isinstance(payload, dict) else None, "near")
    if not result and hint.query:
        payload = await _get_json(COMMONS, params={**_II, "generator": "search", "gsrnamespace": 6,
                                                   "gsrsearch": f"{hint.query} filetype:bitmap", "gsrlimit": 8})
        result = relevant(parse_imageinfo(payload if isinstance(payload, dict) else None, "inspiration", 8),
                          hint.query)[:3]
    photo_cache.put(key, result, ttl=None if result else 900)
    return result
