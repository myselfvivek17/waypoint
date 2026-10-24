"""v4b adversarial checks in the browser: corrupt storage, hostile text, odd navigation, failing
networks, small screens and maths that must agree everywhere. Run by run_e2e.py (WP_E2E_ONLY=breakit).

Each check names the finding it pins (ids from celebration-planner-notes/qa/v4b-breakit.md)."""

from __future__ import annotations

import json
import re

from playwright.sync_api import Page, expect

import run_e2e as E

BAD_TOKENS = ("NaN", "undefined", "null", "[object Object]", "₹-", "Infinity")


def clean_text(p: Page, where: str) -> None:
    """No NaN / undefined / null / ₹- anywhere a person can read, and exactly one h1."""
    text = p.locator("body").inner_text()
    found = [t for t in BAD_TOKENS if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w])", text)]
    assert not found, f"{where}: {found} rendered"
    assert p.locator("h1").count() == 1, f"{where}: {p.locator('h1').count()} h1 elements"


def corrupt_storage(r: E.Run) -> None:
    """B19: any corrupt saved value (edited, half-written, older version) blanked every screen."""
    p = r.page
    r.step("breakit: corrupt localStorage on every route")
    E.open_sample(p)
    good = json.loads(p.evaluate("localStorage.getItem('waypoint:v1')"))
    plan = good["plan"]
    corrupt = {
        "not json": "{oops", "null": "null", "array": "[]", "plan {}": json.dumps({**good, "plan": {}}),
        "plan string": json.dumps({**good, "plan": "abc"}),
        "stops null": json.dumps({**good, "plan": {**plan, "stops": None}}),
        "price text": json.dumps({**good, "plan": {**plan, "stops": [{**s, "rec": {**s["rec"], "price": "abc"}} for s in plan["stops"]]}}),
        "no days": json.dumps({**good, "plan": {**plan, "days": []}, "route": "day"}),
        "journey junk": json.dumps({**good, "j": {"chosen": "x", "decided": 5, "ripples": "r", "stopIdx": 999, "view": "nope"}}),
        "ticks null": json.dumps({**good, "ticks": None}), "profile null": json.dumps({**good, "profile": None}),
        "chat junk": json.dumps({**good, "chat": {"transcript": "x", "chips": 5}, "route": "chat"}),
        "route junk": json.dumps({**good, "route": "<img src=x>"}), "day tab": json.dumps({**good, "dayTab": 99, "route": "day"}),
    }
    for name, value in corrupt.items():
        for route in ("journey", "plan", "day", "invite", "chat", "welcome"):
            p.goto(E.BASE + "/#" + route)
            p.evaluate("([k, v]) => localStorage.setItem(k, v)", ["waypoint:v1", value])
            p.reload()
            p.wait_for_timeout(300)
            clean_text(p, f"storage '{name}' on #{route}")
    # a plan that fits keeps working, and a dropped one says so once
    p.goto(E.BASE + "/#plan")
    p.evaluate("([k, v]) => localStorage.setItem(k, v)", ["waypoint:v1", corrupt["plan {}"]])
    p.reload()
    expect(p.locator("#toast")).to_contain_text("starts fresh")
    p.goto(E.BASE + "/#plan")
    p.evaluate("([k, v]) => localStorage.setItem(k, v)", ["waypoint:v1", json.dumps({**good, "route": "plan"})])
    p.reload()
    expect(p.locator("#tasks-h")).to_be_visible()
    expect(p.locator("#toast")).to_be_hidden()


def run(r: E.Run, dark: bool) -> None:
    if not dark:  # storage handling does not depend on the theme
        corrupt_storage(r)
