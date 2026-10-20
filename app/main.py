"""FastAPI entry point: JSON API under /api and the static single-page app."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Waypoint", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
