"""Live check of the places chain against the real services (not part of pytest).

    .venv/bin/python -m scripts.live_places "Gachibowli, Hyderabad" "Meerpet Road, Jillelaguda"

Prints which source answered (Overpass, else the Nominatim fallback) and the names found.
"""

import asyncio
import logging
import sys
import time

from app import places


async def main(areas: list[str]) -> None:
    for area in areas:
        t = time.monotonic()
        ps = await places.venues(area, 30)
        near = places.pick_nearby(await places.nearby(ps.center.lat, ps.center.lon)) if ps.center else []
        where = ps.center.label if ps.center else "not found"
        print(f"\n{area} -> {where}: {len(ps.places)} places via {ps.source or 'nothing (all sources failed)'}"
              f" in {time.monotonic() - t:.1f} s")
        for p in ps.places[:15]:
            print(f"  {p.label:<16} {p.name}  ({p.dist_km} km)")
        print("  around the venue:", ", ".join(f"{p.name} ({p.label})" for p in near) or "none")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  log: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main(sys.argv[1:] or ["Gachibowli, Hyderabad", "Meerpet Road, Jillelaguda"]))
