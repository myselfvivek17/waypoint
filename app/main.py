"""FastAPI entry point: the JSON API under /api and the static single-page app.

Hardening lives here: request size limit, per-IP rate limits on the AI routes, security
headers with a strict Content-Security-Policy, and error handlers that never leak
stack traces or echo input back.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ROOT = Path(__file__).resolve().parent
if os.getenv("WP_NO_DOTENV") != "1":
    load_dotenv(ROOT.parent / ".env")

from . import intake, llm, places, planner  # noqa: E402  (env must be loaded first)
from .schemas import PhotoHint, Plan, Profile, Strict  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("waypoint")

MAX_BODY = 64 * 1024
# Photos load straight from Wikimedia and the Openverse thumbnail proxy (and Foursquare's image host
# only when a Foursquare key is configured); everything else is same-origin.
IMG_HOSTS = "https://upload.wikimedia.org https://api.openverse.org" + (
    " https://fastly.4sqi.net" if os.getenv("FOURSQUARE_API_KEY", "").strip() else "")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
       f"img-src 'self' data: blob: {IMG_HOSTS}; connect-src 'self'; object-src 'none'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
# requests per minute per IP, by route group
LIMITS = {
    "chat": int(os.getenv("WP_RATE_CHAT", "30")),
    "plan": int(os.getenv("WP_RATE_PLAN", "8")),
    "adjust": int(os.getenv("WP_RATE_ADJUST", "15")),
    "places": int(os.getenv("WP_RATE_PLACES", "40")),
}


class Guard:
    """Pure ASGI middleware: body size limit plus security headers on every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
            await _reply(send, 413, b'{"error":"Request is too large."}')
            return
        seen = 0

        async def limited_receive() -> Message:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > MAX_BODY:
                    raise HTTPException(413, "Request is too large.")
            return msg

        async def send_with_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                headers = list(msg.get("headers") or [])
                headers += [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
                path = scope.get("path", "")
                if path.startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                elif path.startswith("/fonts/"):
                    headers.append((b"cache-control", b"public, max-age=31536000, immutable"))
                else:
                    headers.append((b"cache-control", b"no-cache"))
                msg = {**msg, "headers": headers}
            await send(msg)

        await self.app(scope, limited_receive, send_with_headers)


async def _reply(send: Send, status: int, body: bytes) -> None:
    headers = [(b"content-type", b"application/json")] + [
        (k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class RateLimiter:
    """Sliding one-minute window per (client IP, route group). In memory, single process."""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, request: Request, group: str) -> None:
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        q = self.hits[(ip, group)]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= LIMITS[group]:
            retry = int(60 - (now - q[0])) + 1
            raise HTTPException(429, "Too many requests. Please wait a moment.", headers={"Retry-After": str(retry)})
        q.append(now)
        if len(self.hits) > 10_000:  # bound memory under abuse
            self.hits.clear()

    def reset(self) -> None:
        self.hits.clear()


limiter = RateLimiter()
app = FastAPI(title="Waypoint", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(Guard)


@app.exception_handler(RequestValidationError)
async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = sorted({".".join(str(x) for x in e.get("loc", ())[1:]) or "body" for e in exc.errors()})
    return JSONResponse({"error": "Some details look wrong.", "fields": fields[:10]}, status_code=422)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(Exception)
async def crashed(_: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled %s", type(exc).__name__)
    return JSONResponse({"error": "Something went wrong on our side. Please try again."}, status_code=500)


# ------------------------------------------------------------------ routes

@app.get("/api/health")
def health() -> dict:
    cfg = llm.settings()
    return {"ok": True, "llm": cfg.mode, "models": list(cfg.models) if cfg.mode == "live" else [],
            "today": planner.today().isoformat()}


@app.post("/api/chat", response_model=intake.ChatOut)
async def chat(body: intake.ChatIn, request: Request) -> intake.ChatOut:
    if not body.edit:
        limiter.check(request, "chat")
    return await intake.chat_turn(body, planner.today())


class PlanIn(Strict):
    profile: Profile
    ai: bool = True  # False: the rule-based plan only (the "AI offline" state)


@app.post("/api/plan", response_model=Plan)
async def plan(body: PlanIn, request: Request) -> Plan | JSONResponse:
    limiter.check(request, "plan")
    p = body.profile
    missing = [f for f in intake.REQUIRED if getattr(p, f) in (None, "")]
    if missing:
        return JSONResponse({"error": "Some details are missing.", "fields": missing}, status_code=422)
    try:
        return await planner.build_plan(p, planner.today(), use_ai=body.ai)
    except planner.Blocked as b:
        return JSONResponse({"error": b.block.title, "block": b.block.model_dump(mode="json")}, status_code=409)


@app.post("/api/adjust", response_model=planner.AdjustOut)
async def adjust(body: planner.AdjustIn, request: Request) -> planner.AdjustOut:
    limiter.check(request, "adjust")
    return await planner.adjust(body, planner.today())


class NearbyPlace(BaseModel):
    id: str
    name: str
    label: str
    group: str
    lat: float
    lon: float
    dist_km: float


@app.get("/api/places")
async def get_places(request: Request, area: str | None = Query(None, min_length=2, max_length=120),
                     guests: int | None = Query(None, ge=1, le=5000),
                     lat: float | None = Query(None, ge=-90, le=90),
                     lon: float | None = Query(None, ge=-180, le=180)) -> dict:
    """``?area=`` warms the venue cache (prefetch); ``?lat=&lon=`` lists nearby food, parking and stays."""
    limiter.check(request, "places")
    if lat is not None and lon is not None:
        rows = places.pick_nearby(await places.nearby(lat, lon))
        return {"places": [NearbyPlace(group=places.NEARBY_GROUP[p.kind], **p.model_dump(
            include={"id", "name", "label", "lat", "lon", "dist_km"})) for p in rows]}
    if area:
        ps = await places.venues(area, guests)
        return {"center": ps.center, "count": len(ps.places)}
    raise HTTPException(422, "Give an area, or a latitude and longitude.")


FILE_RE = re.compile(r"^File:[^/\\<>#?|{}\[\]]{1,200}\.(?:jpe?g|png|webp)$", re.I)
QUERY_RE = re.compile(r"^[\w\s,'-]{2,80}$", re.UNICODE)
NAME_RE = re.compile(r"^[^<>{}\[\]\\|`]{1,80}$")


@app.get("/api/photo")
async def photo(request: Request, file: str | None = Query(None, max_length=210),
                wikidata: str | None = Query(None, pattern=r"^Q\d{1,12}$"),
                lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
                q: str | None = Query(None, max_length=80), name: str | None = Query(None, max_length=80),
                vibe: str | None = Query(None, max_length=80)) -> dict:
    """Credited photos: of a place (Commons file, Wikidata item, name + point), near a point,
    of similar places (``vibe``, always labelled as not this place), or of an idea (``q``)."""
    limiter.check(request, "places")
    if file is not None and not FILE_RE.match(file):
        raise HTTPException(422, "That file name is not allowed.")
    if any(x is not None and not QUERY_RE.match(x) for x in (q, vibe)):
        raise HTTPException(422, "That search is not allowed.")
    if name is not None and not NAME_RE.match(name):
        raise HTTPException(422, "That name is not allowed.")
    if not (file or wikidata or q or vibe or (lat is not None and lon is not None)):
        raise HTTPException(422, "Nothing to look up.")
    relation = "place" if (file or wikidata or name) else "near" if lat is not None else "inspiration"
    hint = PhotoHint(file=file, wikidata=wikidata, lat=lat, lon=lon, name=name, vibe=vibe, query=q, relation=relation)
    return {"photos": [p.model_dump() for p in await places.photos(hint)]}


@app.post("/api/ics")
def calendar(body: planner.IcsIn) -> Response:
    text = planner.ics(body, dt.datetime.now(dt.UTC))
    return Response(text, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="waypoint-deadlines.ics"'})


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
