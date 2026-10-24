"""Headless end-to-end run of the Waypoint UI with Playwright.

Not collected by pytest (Playwright is not a project dependency). Run it with any Python
that has Playwright installed:

    python tests/e2e/run_e2e.py [--live] [--shots DIR] [--axe PATH/TO/axe.min.js]

It starts its own server (mock model unless --live), drives the main flow, checks for page
errors, saves screenshots, optionally runs axe-core, and stops the server by its PID.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.getenv("WP_E2E_PORT", "8791"))
BASE = f"http://127.0.0.1:{PORT}"
CHROME = os.getenv("WP_CHROME", "")
LIVE = False  # --live: real model answers, so waits are longer and AI wording is not fixed


def start_server(live: bool) -> subprocess.Popen:
    env = {**os.environ, "WP_TODAY": "2026-10-20", "WP_RATE_PLAN": "100", "WP_RATE_CHAT": "200",
           "WP_RATE_ADJUST": "100", "WP_RATE_PLACES": "400"}
    if not live:
        env.update({"WP_LLM_MOCK": "1"})
    py = ROOT / ".venv" / "bin" / "python"
    proc = subprocess.Popen([str(py), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT),
                             "--log-level", "warning"], cwd=ROOT, env=env)
    for _ in range(60):
        if proc.poll() is not None:
            raise SystemExit(f"server exited (port {PORT} busy?)")
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=1) as r:
                body = json.loads(r.read())
                if r.status == 200 and "llm" in body:
                    print("server pid", proc.pid, body)
                    return proc
        except OSError:
            pass
        time.sleep(0.25)
    proc.kill()
    raise SystemExit("server did not start")


class Run:
    def __init__(self, page: Page, shots: Path):
        self.page, self.shots, self.errors, self.n = page, shots, [], 0
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and self.errors.append(f"console: {m.text}"))

    def shot(self, name: str, full: bool = True) -> None:
        self.n += 1
        self.page.wait_for_timeout(700)
        path = self.shots / f"{self.n:02d}-{name}.png"
        self.page.screenshot(path=str(path), full_page=full)
        print("  shot", path.name)

    def step(self, msg: str) -> None:
        print("-", msg)


def assert_no_null_text(page: Page) -> None:
    text = page.locator("body").inner_text()
    for bad in ("null", "undefined", "NaN", "[object Object]"):
        assert bad not in text, f"'{bad}' rendered in the page"


def more(p: Page, item: str, menu: str = "#more-stop") -> None:
    """Pick an action from a "More ▾" menu."""
    p.click(menu)
    p.click(item)


def one_next_step(p: Page, where: str) -> str:
    """Exactly one primary action on the screen, and a guide line under the title that names it."""
    found = p.evaluate("""() => {
        const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        const next = [...document.querySelectorAll('#view [data-next]')].filter(vis);
        const mari = [...document.querySelectorAll('#view .btn.mari')].filter(vis);
        const g = document.getElementById('guide');
        return { next: next.map((e) => e.textContent.trim()), stray: mari.filter((e) => !e.hasAttribute('data-next')).length,
                 guide: g ? g.textContent : '' }; }""")
    assert len(found["next"]) == 1, f"{where}: expected one primary action, got {found['next']}"
    assert not found["stray"], f"{where}: a marigold button that is not the primary"
    label = found["next"][0]
    assert found["guide"], f"{where}: no guide line"
    if p.locator("#view .way[data-next]").count() == 0:
        assert label.split(":")[0] in found["guide"], f"{where}: guide {found['guide']!r} does not name {label!r}"
    print(f"  next step on {where}: {label!r} · {found['guide']}")
    return label


def screen_totals(p: Page, nav: str) -> tuple[str, dict]:
    """The budget line and per-stop prices as one screen shows them."""
    p.click(f"nav >> text={nav}")
    line = p.locator("[data-budget-line]").first
    expect(line).to_be_visible()
    stops = p.evaluate("""() => Object.fromEntries([...document.querySelectorAll('[data-stop][data-price]')]
        .map((e) => [e.dataset.stop, Number(e.dataset.price)]))""")
    return line.inner_text(), stops


def check_totals_match(r: Run, when: str) -> None:
    """Journey and My plan derive from one plan state, so their numbers must agree exactly."""
    p = r.page
    d_line, _ = screen_totals(p, "Details")
    j_line, j_stops = screen_totals(p, "Journey")
    p_line, p_stops = screen_totals(p, "My plan")  # ends on My plan
    assert j_line == p_line == d_line, f"{when}: journey {j_line!r}, My plan {p_line!r}, details {d_line!r}"
    assert j_stops == p_stops and j_stops, f"{when}: per-stop prices differ {j_stops} vs {p_stops}"
    assert sum(j_stops.values()) == int(re.sub(r"\D", "", j_line.split(" of ")[0])), f"{when}: total != sum of stops"
    print(f"  totals match ({when}):", j_line)


def flow(r: Run, dark: bool) -> None:
    p = r.page
    suffix = "-dark" if dark else ""
    p.goto(BASE + "/?reset=1")
    expect(p.locator("h1")).to_have_text("Tell me what you're celebrating")
    # Code from strings, as page script would run it (DevTools evaluation itself is exempt from CSP):
    blocked = p.evaluate("""() => new Promise((done) => {
        window.addEventListener('securitypolicyviolation', (e) => done(e.violatedDirective), { once: true });
        window.__ran = false; setTimeout('window.__ran = true', 0);
        setTimeout(() => done(window.__ran ? 'ran' : 'silent'), 800); })""")
    assert blocked.startswith("script-src"), f"the CSP must block code from strings, got {blocked}"
    p.wait_for_timeout(200)  # the browser also logs the block: that one console error is expected
    r.errors = [e for e in r.errors if "Evaluating a string as JavaScript violates" not in e]
    if dark:
        p.click("#theme")
        assert p.evaluate("document.documentElement.dataset.theme") == "dark"
    one_next_step(p, "welcome")
    r.shot("welcome" + suffix)

    r.step("type a brief → conversational intake")
    p.fill("#w-in", "Aditya's 22nd birthday, 30 people, ₹40k, in Gachibowli, Hyderabad, 15 Nov")
    p.click("#view button >> text=Begin")
    expect(p.locator("#q-h")).to_be_visible(timeout=40000)
    expect(p.locator(".evchips")).to_contain_text("30 guests")
    one_next_step(p, "intake question")
    r.shot("chat" + suffix)
    done = p.locator("#view >> text=That's everything I need.")
    ready = p.locator(".chips .chip:not([disabled])")
    for _ in range(8):                                  # answer with chips until the AI is done
        expect(p.locator("#q-h")).to_be_visible(timeout=40000)
        expect(p.locator("#view button[disabled]")).to_have_count(0, timeout=40000)
        if done.count():
            break
        ready.first.click()
        p.wait_for_timeout(300)
    expect(done).to_be_visible(timeout=40000)
    r.step("details page: what I understood, first insights, grouped fields")
    expect(p.locator(".details h1")).to_contain_text("22nd")
    expect(p.locator(".under blockquote")).to_contain_text("30 guests")
    expect(p.locator(".dins li").first).to_contain_text("a guest")
    expect(p.locator(".dgroup")).to_have_count(7)
    assert one_next_step(p, "details") == "Start the journey"
    r.step("edit a field on the details page")
    p.click("#ed-guests")
    p.fill("#ed-in", "32")
    p.press("#ed-in", "Enter")
    expect(p.locator(".dfields")).to_contain_text("32 guests", timeout=20000)
    expect(p.locator(".under blockquote")).to_contain_text("32 guests")
    p.click("#ed-have")
    p.fill("#ed-in", "Our own speaker")
    p.press("#ed-in", "Enter")
    expect(p.locator(".dfields")).to_contain_text("Our own speaker", timeout=20000)
    r.shot("chat-done" + suffix)

    r.step("start the journey → plan, with the working-on-it overlay")
    p.click("#view button >> text=Start the journey")
    try:
        p.wait_for_selector("#busy:not([hidden])", timeout=4000)
        text = p.locator("#busy-tx").inner_text()
        assert text.startswith("Finding ") and "Gachibowli" in text, text
        assert p.evaluate("document.getElementById('view').inert"), "page behind the overlay must be inert"
        box = p.locator(".busyin").bounding_box()
        assert abs(box["x"] + box["width"] / 2 - 640) < 4, "overlay must be centred"
        r.shot("loading" + suffix, full=False)
    except PlaywrightTimeout:
        print("  (plan came back before the overlay delay)")
    expect(p.locator("#prop-h")).to_be_visible(timeout=160000)
    assert p.evaluate("document.activeElement.id") == "prop-h", "focus should move to the active stop"
    expect(p.locator(".node.now")).to_have_count(1)
    expect(p.locator(".node")).to_have_count(6)
    assert one_next_step(p, "journey") == "Looks good"
    expect(p.locator("#guide")).to_contain_text("Stop 1 of 6: choose a venue")
    r.shot("journey" + suffix)

    r.step("venue card: distance from the area, OpenStreetMap link, labelled photos")
    p.wait_for_selector(".prop .fig, .prop .nophoto", timeout=40000)
    where = p.locator(".pwhere")
    if where.count():
        expect(where).to_contain_text("km from Gachibowli")
        link = where.locator("a")
        if link.count():
            assert link.get_attribute("href").startswith("https://www.openstreetmap.org/")
    if p.locator(".prop .fig").count():
        label = p.locator(".prop .rel").inner_text()
        assert label in ("Photo of this place", "Similar vibe · not this venue"), label
        expect(p.locator(".prop .credit").first).to_contain_text("Photo: ")
        tags = p.locator(".pstrip .ptag").all_inner_texts()
        print("  photo strip:", tags)
        assert all(t in ("This place", "Similar vibe") for t in tags)
    r.shot("venue-card" + suffix, full=False)

    r.step("Why? reveals the rule")
    p.click("#view button >> text=Why?")
    expect(p.locator("#why")).to_contain_text("The rule behind it")

    r.step("confirm the venue")
    p.click("#view button >> text=Looks good")
    expect(p.locator(".node.done")).to_have_count(1)
    expect(p.locator("#prop-h")).to_be_focused()

    r.step("show options → pick one")
    before = p.locator(".budtop span").first.inner_text()
    p.click("#opt-btn")
    expect(p.locator("#sheet")).to_be_visible()
    expect(p.locator("#sheet-x")).to_be_focused()
    r.shot("options" + suffix, full=False)
    p.locator("#sheet .alt button:not([disabled])").first.click()
    expect(p.locator("#sheet")).to_have_count(0)
    toast = p.locator("#toast")
    expect(toast).to_be_visible()
    p.wait_for_timeout(400)
    tb = toast.bounding_box()
    assert abs(tb["x"] + tb["width"] / 2 - 640) < 4, f"toast must be centred, got {tb}"
    p.wait_for_timeout(900)
    after = p.locator(".budtop span").first.inner_text()
    print("  budget", before, "→", after)
    check_totals_match(r, "after picking an option")
    p.click("nav >> text=Journey")

    r.step("Escape closes the sheet and returns focus")
    p.click("#opt-btn")
    p.keyboard.press("Escape")
    expect(p.locator("#sheet")).to_have_count(0)
    expect(p.locator("#opt-btn")).to_be_focused()

    r.step("user choice: skip, a stop's way of doing it, already have, write my own, add a stop")
    expect(p.locator("#prop-h")).to_be_visible()
    more(p, "#ch-skip")                                   # the theme
    expect(p.locator(".budlist")).to_contain_text("skipped")
    expect(p.locator(".bud")).to_contain_text("freed by skipping")
    p.click("#mode-btn")                                  # the food
    p.locator(".modes button", has_text="Cook at home").click()
    expect(p.locator(".modes button[aria-pressed=true]")).to_have_text("Cook at home", timeout=30000)
    expect(p.locator(".modewhy")).to_contain_text("groceries")
    r.shot("food-cook-at-home" + suffix, full=False)
    more(p, "#ch-have")
    p.fill("#ch-note", "Meera aunty's kitchen")
    p.fill("#ch-cost", "3000")
    p.click("#choice-form >> text=Mark as mine")
    expect(p.locator("#prop-h")).not_to_have_text("Meera aunty's kitchen")   # moved on to the cake
    more(p, "#ch-own")
    p.fill("#ch-note", "My cousin bakes a chocolate cake")
    p.click("#choice-form >> text=Use my words")
    p.click("#add-btn")
    p.locator("#addp button", has_text="Photographer").click()
    expect(p.locator("#prop-h")).to_contain_text("Photographer", timeout=30000)
    expect(p.locator(".node")).to_have_count(7)
    r.shot("choices" + suffix)
    check_totals_match(r, "after skip / way / have / own / add")
    p.click("nav >> text=My plan")
    expect(p.locator(".tasks")).to_contain_text("Confirm with Meera aunty's kitchen")
    expect(p.locator(".tasks")).to_contain_text("My cousin bakes a chocolate cake")
    expect(p.locator(".tasks")).not_to_contain_text("Order the decor")
    p.click("nav >> text=Journey")

    r.step("map + around the venue, list view")
    expect(p.locator("button.tgl")).to_have_text("Around the venue")
    p.click("button.tgl")
    expect(p.locator(".mapwrap")).to_be_visible()
    expect(p.locator(".around")).to_contain_text("Searching around:")
    assert not re.search(r"\b(from|near) you\b", p.locator("body").inner_text()), "never 'from you' unless shared"
    p.wait_for_timeout(1500)
    r.shot("map" + suffix)
    p.click("button.sg >> text=List")
    expect(p.locator(".slist li")).to_have_count(7)
    p.click("button.sg >> text=Path")

    r.step("plan the rest")
    btn = p.locator("#view button >> text=Plan the rest for me")
    expect(btn).to_be_enabled()
    btn.click()
    expect(p.locator("#fin-h")).to_be_visible(timeout=30000)
    assert one_next_step(p, "finale") == "See the event day"
    expect(btn).to_have_count(0)  # nothing left to plan
    r.shot("journey-done" + suffix)

    r.step("event day: every moment can be skipped, covered, swapped or rewritten")
    p.click("#view button >> text=See the event day")
    expect(p.locator(".tli").first).to_be_visible()
    p.locator(".tli").nth(1).locator(".morebtn").click()
    p.locator("[role=menuitem]", has_text="Skip this moment").click()
    expect(p.locator(".tli.skip")).to_have_count(1)
    p.locator(".tli").nth(0).locator(".dctl button", has_text="Show options").click()
    p.locator(".dalts button").first.click()
    p.locator(".tli").nth(3).locator(".morebtn").click()
    p.locator("[role=menuitem]", has_text="Write my own").click()
    p.fill("#dd-in", "Antakshari with the cousins")
    p.press("#dd-in", "Enter")
    expect(p.locator(".tl")).to_contain_text("Antakshari with the cousins")
    assert one_next_step(p, "event day") == "Open my plan"
    expect(p.locator("#guide")).to_contain_text("1 skipped")
    r.shot("day" + suffix)

    r.step("my plan: tick, adjust, diff, apply")
    p.click("nav >> text=My plan")
    expect(p.locator(".task").first).to_be_visible()
    assert one_next_step(p, "my plan") in ("Mark as booked", "Mark as done")
    p.locator(".task input").first.check()
    check_totals_match(r, "after booking a task")
    p.click("#view button >> text=Cut the budget by 20%")
    expect(p.locator("#diff-h")).to_be_visible(timeout=40000)
    assert one_next_step(p, "my plan with a diff") == "Apply changes"
    r.shot("plan-diff" + suffix)
    p.click("#view button >> text=Apply changes")
    expect(p.locator("#diff-h")).to_have_count(0)
    check_totals_match(r, "after an adjustment")
    assert "32,000" in p.locator("[data-budget-line]").first.inner_text()
    p.click("#view button >> text=Copy invite")
    expect(p.locator(".inv")).to_be_visible()
    with p.expect_download() as dl:
        p.click("#view button >> text=Add deadlines to calendar")
    assert dl.value.suggested_filename.endswith(".ics")
    r.shot("plan" + suffix)
    assert_no_null_text(p)

    r.step("invite: AI text options, long names, emoji and Hindi, PNG download, photo stays local")
    p.click("nav >> text=Invite")
    expect(p.locator(".tone")).to_have_count(3, timeout=40000)
    card = p.locator("#card")
    expect(card).to_have_attribute("data-fit", "true")  # the plan's own text fits the card
    assert one_next_step(p, "invite") == "Download PNG"
    p.locator(".tone").nth(1).click()
    p.fill("#inv-head", "Aadhya Venkatalakshmi Subramaniam-Ramachandran's very first birthday 🎂🎉👶🏽")
    p.locator(".chips button[lang=hi]").click()
    expect(p.locator(".tone").first).to_contain_text("15", timeout=40000)
    hindi = p.locator("#inv-msg").input_value()
    assert re.search(r"[\u0900-\u097F]", hindi), hindi
    p.wait_for_timeout(500)
    assert card.get_attribute("data-fit") == "true", "long name + emoji + Hindi must still fit"
    assert int(card.get_attribute("data-lines")) >= 4
    painted = p.evaluate("""() => { const c = document.getElementById('card'), d = c.getContext('2d')
        .getImageData(0, 0, c.width, c.height).data; let ink = 0;
        for (let i = 0; i < d.length; i += 4 * 97) if (d[i] !== d[0] || d[i + 1] !== d[1]) ink++; return ink; }""")
    assert painted > 500, "the card should have decor and text drawn on it"
    r.shot("invite" + suffix)
    with p.expect_download() as dl:
        p.click("#dl-btn")
    png = r.shots / "_downloaded-invite.png"
    dl.value.save_as(png)
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and dl.value.suggested_filename.endswith("-invite.png")
    sent = []
    p.on("request", lambda req: sent.append(req.url) if req.method != "GET" else None)
    p.set_input_files("#inv-photo", str(png))
    expect(p.locator("#photo-x")).to_be_visible()
    p.wait_for_timeout(600)
    assert not sent, f"the photo must stay in the browser, but saw {sent}"
    r.shot("invite-photo" + suffix, full=False)
    p.fill("#inv-msg", "Long message. " * 42)
    p.wait_for_timeout(400)
    if card.get_attribute("data-fit") == "false":
        expect(p.locator("#fit-warn")).to_be_visible()
    p.click("#photo-x")

    r.step("reload restores state")
    p.reload()
    expect(p.locator("h1")).to_be_visible()


def mobile(r: Run, dark: bool) -> None:
    """Every screen at phone width, in the current theme: no sideways scrolling, controls reachable."""
    p = r.page
    suffix = "-dark" if dark else ""
    r.step("mobile: every screen at 390 px" + (" (dark)" if dark else ""))
    p.set_viewport_size({"width": 390, "height": 844})
    for route, ready in (("chat", ".details h1"), ("journey", "#prop-h, #fin-h"), ("day", ".tli"), ("plan", ".task"),
                         ("invite", "#card")):
        p.evaluate(f"location.hash = '{route}'")
        expect(p.locator(ready).first).to_be_visible(timeout=20000)
        p.wait_for_timeout(500)
        over = p.evaluate("document.scrollingElement.scrollWidth - innerWidth")
        assert over <= 1, f"{route}: {over}px of sideways scroll on a phone"
        r.shot(f"mobile-{route}{suffix}")
    p.set_viewport_size({"width": 1280, "height": 900})


def edges(r: Run) -> None:
    p = r.page
    r.step("edge: impossible budget")
    p.goto(BASE + "/?demo=budget&reset=1")
    expect(p.locator(".ways .way")).to_have_count(3, timeout=40000)
    one_next_step(p, "impossible budget")
    r.shot("edge-budget")
    p.locator(".way").first.click()
    expect(p.locator(".ways")).to_have_count(0, timeout=20000)

    r.step("edge: date in the past")
    p.goto(BASE + "/?demo=date&reset=1")
    expect(p.locator("#view >> text=has already passed")).to_be_visible(timeout=40000)
    r.shot("edge-date")

    r.step("edge: AI offline")
    p.goto(BASE + "/?demo=offline&reset=1")
    expect(p.locator(".offb")).to_contain_text("AI offline", timeout=160000)
    r.shot("edge-offline")

    r.step("edge: no photo")
    p.goto(BASE + "/?demo=nophoto&reset=1")
    expect(p.locator(".nophoto")).to_be_visible(timeout=160000)
    r.shot("edge-nophoto")

    r.step("ask to adjust: \"I don't want cake\" removes it from the stops, the day and the budget")
    p.goto(BASE + "/?reset=1")
    p.click("#view button >> text=Aditya's 22nd birthday")
    expect(p.locator("#prop-h")).to_be_visible(timeout=160000)
    expect(p.locator(".node .nl", has_text="Cake")).to_have_count(1)
    p.click("nav >> text=My plan")
    p.fill("#adj-in", "I don't want cake")
    p.click("#view button >> text=Show me")
    expect(p.locator("#diff-h")).to_contain_text("Without the cake", timeout=40000)
    expect(p.locator(".diff")).to_contain_text("removed")
    expect(p.locator(".chgmsg").first).to_contain_text("freed")
    r.shot("adjust-no-cake")
    p.click("#view button >> text=Apply changes")
    check_totals_match(r, "after removing the cake")
    p.click("nav >> text=Journey")
    expect(p.locator(".node .nl", has_text="Cake")).to_have_count(0)
    p.click("nav >> text=Party day")
    expect(p.locator(".tl")).not_to_contain_text("Cake")

    r.step("use my location: asked only on click, named, and correctable")
    geo = p.context.browser.new_context(geolocation={"latitude": 17.4401, "longitude": 78.3489},
                                        permissions=["geolocation"], viewport={"width": 1280, "height": 900})
    g = geo.new_page()
    g.on("pageerror", lambda e: r.errors.append(f"pageerror: {e}"))
    g.goto(BASE + "/?reset=1")
    g.click("#view button >> text=A wedding in Jaipur")
    expect(g.locator("#prop-h")).to_be_visible(timeout=160000)
    g.click("nav >> text=Details")
    expect(g.locator("#loc-btn")).to_be_visible()
    expect(g.locator(".loc")).to_contain_text("never stores it")
    g.click("#loc-btn")
    expect(g.locator(".areanote")).to_contain_text("From your location", timeout=30000)
    where = g.locator(".dgroup[aria-label=Where] dd").first.inner_text()
    print("  resolved area:", where.splitlines()[0])
    assert "Jaipur" not in where
    expect(g.locator(".offb.dirty")).to_have_count(0)  # details page itself offers the re-plan
    expect(g.locator("#view button >> text=Re-plan with these details")).to_be_visible()
    g.screenshot(path=str(r.shots / "17-use-my-location.png"), full_page=True)
    geo.close()

    r.step("reduced motion: the overlay rests, fully drawn")
    rm = p.context.browser.new_context(reduced_motion="reduce", viewport={"width": 1280, "height": 900})
    q = rm.new_page()
    q.goto(BASE + "/?reset=1")
    ring = q.evaluate("(() => { const s = getComputedStyle(document.querySelector('[data-next]')); return [s.animationName, s.boxShadow]; })()")
    assert ring[0] == "none" and ring[1] != "none", f"reduced motion: the next step should be a still ring, got {ring}"
    q.evaluate("import('/js/ui.js').then((m) => m.think('Testing the overlay…'))")
    q.wait_for_selector("#busy:not([hidden])")
    offset = q.evaluate("getComputedStyle(document.querySelector('.bm-path')).strokeDashoffset")
    assert offset in ("0", "0px"), offset
    q.screenshot(path=str(r.shots / "16-loading-reduced-motion.png"))
    rm.close()



def tour_clear_of_target(p: Page, where: str) -> None:
    """The popover sits beside the highlight ring and never overlaps it; the rest of the page is
    dimmed by the ring's scrim."""
    p.wait_for_timeout(350)  # the ring glides between steps
    gap = p.evaluate("""() => { const a = document.getElementById('tour').getBoundingClientRect(),
        b = document.getElementById('tour-ring').getBoundingClientRect();
        return Math.max(b.top - a.bottom, a.top - b.bottom, b.left - a.right, a.left - b.right); }""")
    assert gap >= 0, f"{where}: the tour covers its target by {-gap}px"
    shadow = p.evaluate("getComputedStyle(document.getElementById('tour-ring')).boxShadow")
    assert "rgba" in shadow and "0px 0px 0px" in shadow, f"{where}: no scrim around the ring ({shadow})"


def tour_points_at_buttons(p: Page, where: str) -> None:
    """Step 2: the arrow's tip is over the Looks good button, and the ring hugs the three buttons
    (not the wider action row)."""
    m = p.evaluate("""() => {
        const a = document.querySelector('#tour .tourarrow').getBoundingClientRect(),
          ok = document.getElementById('ok-btn').getBoundingClientRect(),
          ring = document.getElementById('tour-ring').getBoundingClientRect(),
          bs = [...document.querySelectorAll('.prop .pact > button, .prop .pact > .mmenu > .morebtn')]
            .map((b) => b.getBoundingClientRect());
        const r = Math.max(...bs.map((b) => b.right)), l = Math.min(...bs.map((b) => b.left));
        return { tip: a.left + a.width / 2, okL: ok.left, okR: ok.right, n: bs.length,
                 slackL: l - ring.left, slackR: ring.right - r }; }""")
    assert m["n"] == 3, f"{where}: expected 3 buttons, found {m['n']}"
    assert m["okL"] <= m["tip"] <= m["okR"], f"{where}: the arrow does not point at Looks good: {m}"
    assert 0 <= m["slackL"] <= 10 and 0 <= m["slackR"] <= 10, f"{where}: the ring is not tight: {m}"


def both_widths(r: Run, name: str, at: str = "", full: bool = False) -> None:
    """One screenshot at laptop width and one at phone width, of the screen as it is now, with the
    element ``at`` (if given) scrolled into the middle of the view."""
    p = r.page
    for width, height, tail in ((1280, 900, ""), (390, 844, "-390")):
        p.set_viewport_size({"width": width, "height": height})
        p.wait_for_timeout(400)
        if at:
            p.locator(at).first.evaluate("(e) => e.scrollIntoView({ block: 'center' })")
        r.shot(name + tail, full=full)
    p.set_viewport_size({"width": 1280, "height": 900})
    p.wait_for_timeout(300)


def guided(r: Run, dark: bool) -> None:
    """v3 guidance: the tour, the next step, the choice question, "you are here", the hint, the hurry
    button and the More menu. Takes the per-item screenshots in the current theme."""
    p = r.page
    sfx = "-dark" if dark else ""
    r.step("guidance" + (" (dark)" if dark else ""))
    p.goto(BASE + "/?reset=1&tour=1")
    if dark:
        p.evaluate("localStorage.setItem('waypoint:theme', 'dark')")
        p.reload()
    p.click("#view button >> text=Aditya's 22nd birthday")
    expect(p.locator("#prop-h")).to_be_visible(timeout=160000)

    r.step("tour: appears on the first visit, three steps beside their targets")
    tour = p.locator("#tour")
    expect(tour).to_be_visible(timeout=5000)
    expect(tour).to_have_attribute("role", "dialog")
    expect(p.locator("#tour-next")).to_be_focused()
    expect(tour.locator(".step")).to_contain_text("Tour 1/3")
    tour_clear_of_target(p, "tour step 1")
    both_widths(r, "v3-item2-tour-path" + sfx)
    p.keyboard.press("Tab")
    p.keyboard.press("Tab")
    p.keyboard.press("Tab")
    assert p.evaluate("document.getElementById('tour').contains(document.activeElement)"), "focus must stay in the tour"
    p.click("#tour-next")
    expect(tour.locator(".step")).to_contain_text("Tour 2/3")
    expect(p.locator(".tour-on")).to_have_count(1)
    tour_clear_of_target(p, "tour step 2")
    tour_points_at_buttons(p, "tour step 2")
    expect(tour.locator("#tour-b b")).to_have_text(["Looks good", "Show options", "More"])
    both_widths(r, "v3-item2-tour-actions" + sfx)
    p.set_viewport_size({"width": 390, "height": 844})
    p.wait_for_timeout(400)
    tour_clear_of_target(p, "tour step 2 at 390 px")
    tour_points_at_buttons(p, "tour step 2 at 390 px")
    p.set_viewport_size({"width": 1280, "height": 900})
    p.wait_for_timeout(300)
    p.click("#tour-back")
    expect(tour.locator(".step")).to_contain_text("Tour 1/3")
    p.keyboard.press("ArrowRight")
    p.keyboard.press("ArrowRight")
    expect(tour.locator(".step")).to_contain_text("Tour 3/3")
    tour_clear_of_target(p, "tour step 3")
    both_widths(r, "v3-item2-tour-budget" + sfx)
    p.keyboard.press("Escape")
    expect(tour).to_have_count(0)

    r.step("tour: not shown on the second visit; ? replays it")
    p.reload()
    expect(p.locator("#prop-h")).to_be_visible(timeout=20000)
    p.wait_for_timeout(1500)
    expect(tour).to_have_count(0)
    p.click("#help")
    expect(tour).to_be_visible()
    expect(tour.locator(".step")).to_contain_text("Tour 1/3")
    p.click("#tour-skip")
    expect(tour).to_have_count(0)
    expect(p.locator("#help")).to_be_focused()

    r.step("one clear next step: the guide line names the single glowing primary action")
    assert one_next_step(p, "journey") == "Looks good"
    both_widths(r, "v3-item1-next-step-journey" + sfx, at="#guide")

    r.step("tour: a short one on My plan and Invite, and ? points at the next step elsewhere")
    p.click("nav >> text=My plan")
    expect(tour).to_be_visible(timeout=5000)
    expect(tour.locator(".step")).to_contain_text("Tour 1/2")
    tour_clear_of_target(p, "plan tour")
    both_widths(r, "v3-item2-tour-plan" + sfx)
    p.keyboard.press("Escape")
    one_next_step(p, "my plan")
    both_widths(r, "v3-item1-next-step-plan" + sfx, at="#guide")
    p.click("nav >> text=Invite")
    expect(tour).to_be_visible(timeout=90000 if LIVE else 8000)  # the tour waits for the three versions
    p.keyboard.press("Escape")
    p.click("nav >> text=Party day")
    p.wait_for_timeout(1000)
    expect(tour).to_have_count(0)
    p.click("#help")
    expect(tour).to_contain_text("Your next step")
    expect(tour).to_contain_text("Open my plan")
    p.keyboard.press("Escape")
    p.click("nav >> text=Journey")
    expect(p.locator("#prop-h")).to_be_visible()

    r.step("the proposal ends with its choice in plain words, answered by the two buttons")
    ask = p.locator("#ask-q")
    text = ask.inner_text()
    if LIVE:  # the model's line passed validators.clean_ask, or the rules' line replaced it
        source = p.evaluate("import('/js/store.js').then((m) => m.state.plan.source)")
        own = not re.fullmatch(r"Want this [\w ]+\? Or (I can show [\w ]+|tell me what you would change)\.", text)
        print(f"  choice line ({'written by the model' if own else 'rules line'}; plan source {source}): {text}")
        assert "?" in text and re.search(r"\bor\b", text, re.I) and not re.search(r"\d", text), text
        assert len(text.split()) <= 18, text
    else:
        assert text.startswith("Want ") and "? Or I can show " in text, text
    expect(p.locator("#ok-btn")).to_have_attribute("aria-describedby", "ask-q")
    both_widths(r, "v3-item3-choice-line" + sfx, at="#ask-q")
    p.click("#opt-btn")
    p.locator("#sheet .alt button:not([disabled])").first.click()
    expect(ask).to_contain_text("Happy with ")
    expect(ask).to_contain_text("the other options again")

    r.step("you are here: a marker on the active stop, the next undecided stop pulses, decided stops tick")
    p.click("#ok-btn")
    now = p.locator(".node.now")
    expect(now.locator(".here")).to_have_text("You are here")
    expect(now.locator(".nl")).to_have_text("Theme · 2 of 6")
    expect(now).to_have_attribute("aria-label", re.compile(r"^You are here: Stop 2 of 6, Theme"))
    expect(p.locator(".node.upnext")).to_have_count(1)
    expect(p.locator(".node.upnext")).to_have_attribute("data-i", "2")
    expect(p.locator(".node").first.locator(".nb")).to_have_text("✓")
    both_widths(r, "v3-item4-you-are-here" + sfx, at=".scenewrap")
    p.click("button.sg >> text=List")
    expect(p.locator(".slist li.now .heretag")).to_have_text("You are here")
    expect(p.locator(".slist li.upnext .heretag")).to_have_text("Up next")
    expect(p.locator(".slist li").first.locator(".sltag")).to_contain_text("✓ Venue · 1 of 6 · Decided")
    both_widths(r, "v3-item4-list" + sfx, at=".slist li.now")
    p.click("button.sg >> text=Path")

    r.step("hesitation: after ~8 s without a click or key press, a hint appears under the buttons, once")
    hint = p.locator("#hint")
    expect(hint).to_have_attribute("aria-live", "polite")
    expect(hint).to_be_empty()
    p.wait_for_timeout(5000)
    expect(hint).to_be_empty()
    expect(hint).to_contain_text("Not sure? Tap Looks good. You can change it later.", timeout=6000)
    overlap = p.evaluate("""() => { const a = document.getElementById('hint').getBoundingClientRect();
        return [...document.querySelectorAll('#view button, #view a, #view img, #view h2, #view p')]
          .filter((e) => !document.getElementById('hint').contains(e) && e.offsetParent)
          .map((e) => e.getBoundingClientRect())
          .filter((b) => b.width && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom).length; }""")
    assert overlap == 0, f"the hint overlaps {overlap} element(s)"
    both_widths(r, "v3-item5-hint" + sfx, at="#hint")
    p.click("#hint-x")
    expect(hint).to_be_empty()
    expect(p.locator("#ok-btn")).to_be_focused()
    p.wait_for_timeout(9000)
    expect(hint).to_be_empty()  # once per stop

    r.step("in a hurry: visible early, right above the path, says everything stays editable")
    p.evaluate("window.scrollTo(0, 0)")
    hurry = p.locator(".hurry")
    expect(hurry).to_contain_text("In a hurry?")
    expect(hurry).to_contain_text("Everything stays editable.")
    box, scene = hurry.bounding_box(), p.locator(".scenewrap").bounding_box()
    assert box["y"] + box["height"] < 900, "the hurry button should be in the first screenful"
    assert 0 <= scene["y"] - (box["y"] + box["height"]) < 40, "it should sit right above the path"
    both_widths(r, "v3-item6-hurry" + sfx, at=".hurry")

    r.step("fewer buttons: Looks good and Show options, everything else under More (keyboard menu)")
    pact = p.locator(".prop .pact").first
    shown = [t.split("\n")[0] for t in pact.locator("button:visible").all_inner_texts()]
    assert shown == ["Looks good", "Show options", "More"], shown
    expect(p.locator(".prop .chip:visible")).to_have_count(0)
    btn = p.locator("#more-stop")
    expect(btn).to_have_attribute("aria-haspopup", "menu")
    btn.focus()
    p.keyboard.press("Enter")
    expect(btn).to_have_attribute("aria-expanded", "true")
    expect(p.locator("#more-stop-m")).to_have_attribute("role", "menu")
    expect(p.locator("#ch-skip")).to_be_focused()
    p.keyboard.press("ArrowDown")
    expect(p.locator("#ch-have")).to_be_focused()
    p.keyboard.press("End")
    expect(p.locator("#rm-btn")).to_be_focused()
    p.keyboard.press("ArrowDown")
    expect(p.locator("#ch-skip")).to_be_focused()
    p.keyboard.press("ArrowUp")
    expect(p.locator("#rm-btn")).to_be_focused()
    p.keyboard.press("Escape")
    expect(p.locator("#more-stop-m")).to_have_count(0)
    expect(btn).to_be_focused()
    p.keyboard.press("ArrowDown")
    expect(p.locator("#ch-skip")).to_be_focused()
    p.keyboard.press("Escape")
    p.click("#more-stop")
    both_widths(r, "v3-item7-more-menu" + sfx, at="#more-stop-m")
    p.mouse.click(5, 300)                                  # a press outside closes it
    expect(p.locator("#more-stop-m")).to_have_count(0)
    one_next_step(p, "journey with fewer buttons")

    r.step("More menu actions still work: skip, I already have this, write my own, remove")
    more(p, "#ch-skip")                                    # the theme
    expect(p.locator(".budlist")).to_contain_text("Theme skipped")
    expect(p.locator(".node.now .nl")).to_contain_text("Food")
    more(p, "#ch-have")
    p.fill("#ch-note", "Meera aunty's kitchen")
    p.click("#choice-form button[type=submit]")
    expect(p.locator(".node.now .nl")).to_contain_text("Cake")
    more(p, "#ch-own")
    p.fill("#ch-note", "My cousin bakes a chocolate cake")
    p.click("#choice-form button[type=submit]")
    expect(p.locator(".node.now .nl")).to_contain_text("Invites")
    more(p, "#rm-btn")
    expect(p.locator("#rm-yes")).to_be_focused()
    both_widths(r, "v3-item7-remove-confirm" + sfx, at="#rm-yes")
    p.click("#rm-yes")
    expect(p.locator(".node")).to_have_count(5, timeout=30000)
    expect(p.locator(".node .nl", has_text="Invites")).to_have_count(0)
    p.click("nav >> text=My plan")
    expect(p.locator(".tasks")).to_contain_text("Confirm with Meera aunty's kitchen")
    expect(p.locator(".tasks")).to_contain_text("My cousin bakes a chocolate cake")
    p.click("nav >> text=Journey")

    r.step("plan the rest: every stop decided, and each can still be reopened")
    p.click("#rest-btn")
    expect(p.locator("#fin-h")).to_be_visible(timeout=30000)
    expect(p.locator(".hurry")).to_have_count(0)
    p.locator(".node").nth(0).click()
    expect(p.locator("#ok-btn")).to_have_text("Keep and continue")

    r.step("event-day moments: Show options visible, the rest under More")
    p.click("nav >> text=Party day")
    first = p.locator(".tli").first
    shown = [t.split("\n")[0] for t in first.locator(".dctl button:visible").all_inner_texts()]
    assert shown in (["Show options", "More"], ["More"]), shown
    first.locator(".morebtn").click()
    expect(p.locator("[role=menu] [role=menuitem]")).to_have_count(3)
    both_widths(r, "v3-item7-day-more" + sfx, at=".tli .menu")
    p.locator("[role=menuitem]", has_text="Skip this moment").click()
    expect(p.locator(".tli.skip")).to_have_count(1)
    expect(first.locator("button", has_text="Keep it as planned")).to_be_focused()

    if not dark:
        empties(r)


def empties(r: Run) -> None:
    """Screens with nothing in them say what to do first (state emptied in the page, then re-drawn)."""
    p = r.page
    r.step("empty states: an empty run-sheet, no tasks, no stops")
    redraw = "import('/js/ui.js').then((u) => u.rerender({ focus: null }))"
    p.evaluate("import('/js/store.js').then((m) => { m.state.plan.days = m.state.plan.days.map((d) => ({ ...d, items: [] })); })")
    p.evaluate(redraw)
    expect(p.locator("#view .empty")).to_contain_text("Nothing is on this run-sheet yet")
    one_next_step(p, "empty run-sheet")
    r.shot("v3-empty-day", full=False)
    p.evaluate("import('/js/store.js').then((m) => { m.state.plan.stops = []; m.state.plan.tasks = []; })")
    p.click("nav >> text=My plan")
    expect(p.locator("#view .empty").first).to_contain_text("No tasks yet")
    expect(p.locator(".next")).to_contain_text("Nothing to book yet")
    one_next_step(p, "empty plan")
    r.shot("v3-empty-plan", full=False)
    p.click("nav >> text=Journey")
    expect(p.locator("#view .empty")).to_contain_text("Nothing is on your route")
    assert one_next_step(p, "empty journey") == "Add a stop"
    r.shot("v3-empty-journey", full=False)
    p.click("#view .fin button >> text=Add a stop")
    expect(p.locator("#add-in")).to_be_focused()


def run_axe(browser, axe_path: Path, shots: Path) -> list:
    """axe-core on each screen; the CSP is bypassed only to inject the checker."""
    ctx = browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    src = axe_path.read_text()
    results = []
    for theme in ("light", "dark"):
        page.goto(BASE + "/?reset=1")
        page.evaluate(f"localStorage.setItem('waypoint:theme', '{theme}')")
        page.goto(BASE + "/")
        page.click("#view button >> text=Aditya's 22nd birthday")
        page.wait_for_selector("#prop-h", timeout=160000)
        for route in ("journey", "day", "plan", "invite", "chat", "welcome"):
            page.evaluate(f"location.hash = '{route}'")
            page.wait_for_timeout(2500 if route == "invite" else 900)  # invite: let the texts arrive
            page.add_script_tag(content=src)
            res = page.evaluate("axe.run(document, {resultTypes: ['violations']})")
            for v in res["violations"]:
                results.append((theme, route, v["impact"], v["id"], len(v["nodes"]), v["help"],
                                [(n["target"], n.get("failureSummary", "")[-120:]) for n in v["nodes"][:3]]))
        page.evaluate("location.hash = 'journey'")
        page.wait_for_timeout(600)
        page.click("#opt-btn")
        page.wait_for_timeout(900)  # let the slide-in finish; mid-fade colours are not the real ones
        page.add_script_tag(content=src)
        res = page.evaluate("axe.run(document, {resultTypes: ['violations']})")
        for v in res["violations"]:
            results.append((theme, "sheet", v["impact"], v["id"], len(v["nodes"]), v["help"],
                            [(n["target"], n.get("failureSummary", "")[-120:]) for n in v["nodes"][:4]]))
        page.keyboard.press("Escape")
        # the v3 guidance states: the tour popover, the More menu on a stop and on a moment
        for name, open_it, close_it in (
                ("tour", lambda: page.click("#help"), "#tour-skip"),
                ("more-menu", lambda: page.click("#more-stop"), "#more-stop"),
                ("day-menu", lambda: (page.evaluate("location.hash = 'day'"), page.wait_for_timeout(900),
                                      page.locator(".tli .morebtn").first.click()), None)):
            open_it()
            page.wait_for_timeout(700)
            assert page.locator("#tour, [role=menu]").count() == 1, f"{name} did not open"
            page.add_script_tag(content=src)
            res = page.evaluate("axe.run(document, {resultTypes: ['violations']})")
            for v in res["violations"]:
                results.append((theme, name, v["impact"], v["id"], len(v["nodes"]), v["help"],
                                [(n["target"], n.get("failureSummary", "")[-120:]) for n in v["nodes"][:4]]))
            if close_it:
                page.click(close_it)
    ctx.close()
    return results


# "Meerpet, Hyderabad" has namesakes on OpenStreetMap; the browser gets the two areas the server
# would offer (pinned here so the run does not depend on live geocoder results).
MEERPET = [
    {"lat": 17.4546432, "lon": 78.5715499, "label": "Meerpet H.B Colony, Hyderabad",
     "detail": "Uppal mandal, Medchal–Malkajgiri · 500040"},
    {"lat": 17.322338, "lon": 78.5319055, "label": "Meerpet Road, Jillelaguda",
     "detail": "Balapur mandal, Ranga Reddy · 500097"},
]
# Map tiles are stubbed with a plain grid so the run makes no tile requests to OpenStreetMap.
TILE_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" '
            'fill="#EFE3C8"/><path d="M0 128H256M128 0V256" stroke="#FFFBF2" stroke-width="10"/>'
            '<path d="M0 0H256V256" fill="none" stroke="#D9CBAA"/></svg>')


def area_picker(r: Run, dark: bool) -> None:
    """Namesakes: "Which Meerpet?" in the chat, a pick, then pin on map from the details page."""
    p = r.page
    suffix = "-dark" if dark else ""
    tiles: list[str] = []
    p.route(lambda u: "/api/places?" in u and "area=Meerpet" in u, lambda route: route.fulfill(
        json={"center": None, "count": 0, "options": MEERPET}))
    p.route("https://tile.openstreetmap.org/**", lambda route: (tiles.append(route.request.url), route.fulfill(
        body=TILE_SVG, content_type="image/svg+xml"))[-1])
    p.route("**/api/whereami", lambda route: route.fulfill(json={"area": "Jillelaguda, Hyderabad"}))
    p.goto(BASE + "/?reset=1")
    if dark:
        p.click("#theme")
    r.step("area with namesakes → 'Which Meerpet?' picker in the chat")
    p.fill("#w-in", "A birthday for 30 people, ₹40,000, in Meerpet, Hyderabad on 15 Nov")
    p.click("#view button >> text=Begin")
    expect(p.locator("#apick-h")).to_have_text("Which Meerpet?", timeout=40000)
    expect(p.locator(".apicks .way")).to_have_count(2)
    expect(p.locator(".apicks")).to_contain_text("Balapur mandal, Ranga Reddy · 500097")
    expect(p.locator(".apicks")).to_contain_text("Uppal mandal, Medchal–Malkajgiri · 500040")
    assert_no_null_text(p)
    r.shot("v3b-picker-chat" + suffix, full=False)
    p.click("#apick-1")
    expect(p.locator(".apick")).to_have_count(0)
    assert p.evaluate("import('/js/store.js').then((m) => m.state.profile.point.label)") == "Meerpet Road, Jillelaguda"
    done = p.locator("#view >> text=That's everything I need.")
    for _ in range(8):
        expect(p.locator("#view button[disabled]")).to_have_count(0, timeout=40000)
        if done.count():
            break
        p.locator(".chips .chip:not([disabled])").first.click()
        p.wait_for_timeout(300)
    expect(done).to_be_visible(timeout=40000)
    where = p.locator(".dgroup[aria-label='Where']")
    expect(where).to_contain_text("Searching around: Meerpet Road, Jillelaguda · Balapur mandal")

    r.step("details: choose again, pin the exact spot on the map")
    where.locator("#area-change").click()
    expect(p.locator("#apick-h")).to_be_focused()
    expect(p.locator("#apick-1")).to_have_attribute("aria-pressed", "true")
    expect(p.locator("#toast")).to_be_hidden(timeout=8000)
    p.locator(".apick").scroll_into_view_if_needed()
    r.shot("v3b-picker-details" + suffix, full=False)
    p.click("#apick-pin")
    expect(p.locator("#pm-frame")).to_be_focused()
    expect(p.locator(".pmmark")).to_have_count(2)
    expect(p.locator("#pm-use")).to_be_disabled()
    assert tiles and all(re.fullmatch(r"https://tile\.openstreetmap\.org/\d+/\d+/\d+\.png", t) for t in tiles)
    assert len(set(tiles)) <= 30, f"{len(set(tiles))} tiles for one small map"
    p.locator(".pinmap").scroll_into_view_if_needed()
    expect(p.locator("#toast")).to_be_hidden(timeout=8000)
    box = p.locator("#pm-frame").bounding_box()
    p.mouse.click(box["x"] + box["width"] * 0.5 - 30, box["y"] + box["height"] * 0.5 + 40)
    expect(p.locator(".pmpin")).to_have_count(1)
    p.keyboard.press("ArrowRight")  # keyboard nudges the pin; focus stays on the map
    expect(p.locator("#pm-frame")).to_be_focused()
    expect(p.locator("#pm-use")).to_be_enabled()
    p.locator(".pinmap").scroll_into_view_if_needed()
    r.shot("v3b-pin-map" + suffix, full=False)
    p.click("#pm-use")
    expect(where).to_contain_text("Searching around: Pinned spot near Jillelaguda, Hyderabad")
    pt = p.evaluate("import('/js/store.js').then((m) => m.state.profile.point)")
    assert 17.3 < pt["lat"] < 17.5 and 78.5 < pt["lon"] < 78.6 and pt["lat"] == round(pt["lat"], 4), pt
    assert_no_null_text(p)
    if not dark:  # the plan itself is searched around the pin (one live run is enough)
        r.step("plan → map view says where it searched")
        p.click("#view button >> text=Start the journey")
        expect(p.locator("#prop-h")).to_be_visible(timeout=150000)
        p.click("button.sg >> text=Map")
        # The plan is searched around the pin; the map then centres on the pin, or on the real venue
        # found near it ("…, the venue") when there is one.
        searched = p.evaluate("import('/js/store.js').then((m) => [m.state.plan.area_point.label,"
                              " m.state.plan.stops[0].rec.dist_km])")
        assert searched[0] == "Pinned spot near Jillelaguda, Hyderabad", searched
        assert searched[1] is None or searched[1] <= 3, searched
        expect(p.locator(".around")).to_contain_text(re.compile(
            r"Searching around: (Pinned spot near Jillelaguda, Hyderabad|.+, the venue)"))
    p.unroute_all()


def open_sample(p: Page, dark: bool = False) -> None:
    p.goto(BASE + "/?reset=1")
    if (p.evaluate("document.documentElement.dataset.theme") == "dark") != dark:
        p.click("#theme")
    p.click("#view button >> text=Aditya's 22nd birthday")
    expect(p.locator("#prop-h")).to_be_visible(timeout=160000)


def v4_prices(r: Run, dark: bool) -> None:
    """Item 1: every Waypoint price is labelled an estimate; the user's own numbers are not."""
    p = r.page
    suffix = "-dark" if dark else ""
    r.step("honest prices: estimates are labelled everywhere, your numbers and quotes are not" + (" (dark)" if dark else ""))
    open_sample(p, dark)
    helper = p.evaluate("""() => import('/js/model.js').then((m) => {
        const s = (kw) => Object.assign({ status: 'open', rec: { price: 500, per_guest: true } }, kw);
        const row = (kw) => [m.priceSource(s(kw)), m.stopPrice(s(kw), s(kw).rec, 30, 0)];
        return { est: row({}), skip: row({ status: 'skip' }), have: row({ status: 'have', user_cost: 4000 }),
                 paid: row({ paid: 13500 }), quoted: row({ quote: { vendor: 'A', price: 14200 } }),
                 text: [m.priceText(12000, 'estimate'), m.priceText(12000, 'quoted'), m.priceText(12000, 'yours')] }; })""")
    assert helper == {"est": ["estimate", 15000], "skip": ["none", 0], "have": ["yours", 4000], "paid": ["yours", 13500],
                      "quoted": ["quoted", 14200], "text": ["≈ ₹12,000", "₹12,000", "₹12,000"]}, helper
    price = p.locator(".pprice")
    expect(price).to_contain_text("≈ ₹")
    expect(price.locator(".ptag.price.estimate")).to_have_text(re.compile(r"^Estimate"))
    assert "confirm it with the vendor" in price.locator(".ptag").get_attribute("title").lower()
    expect(p.locator(".budtop [data-budget-line]")).to_contain_text("(all estimates)")
    expect(p.locator(".legend")).to_contain_text("≈ estimate, not a quote")
    p.click("#opt-btn")
    expect(p.locator(".alt .altp").first).to_contain_text("≈ ₹")
    expect(p.locator(".alt .ptag.price.estimate").first).to_be_visible()
    r.shot("v4-estimate-sheet" + suffix, full=False)
    p.keyboard.press("Escape")
    r.shot("v4-estimate-journey" + suffix, full=False)
    p.click("nav >> text=My plan")
    expect(p.locator(".task .tcost").first).to_contain_text("≈ ₹")
    expect(p.locator(".brow .ptag.price.estimate").first).to_be_visible()
    first = p.locator(".task").filter(has=p.locator(".tcost")).first
    key = first.locator("input").get_attribute("id")[3:]
    first.locator("input").check()
    p.click("#pd-" + key)
    p.fill("#pd-in", "4321")
    p.click(".paidf button[type=submit]")
    expect(p.locator(f".brow[data-stop='{key}'] .ptag.price.yours")).to_have_text(re.compile("^Your number"))
    expect(p.locator(f".brow[data-stop='{key}']")).to_contain_text("₹4,321")
    expect(p.locator("#bud-h + p [data-budget-line]")).to_contain_text("₹4,321 quoted or yours")
    check_totals_match(r, "after a booked amount")
    r.shot("v4-estimate-plan" + suffix)
    assert_no_null_text(p)


EXPLAINER = "actually we have 50 more guests, our budget decreased a bit, and the event is two weeks earlier"


def diff_rows(p: Page) -> dict:
    return p.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.diff tbody tr')].map((tr) =>
        [tr.cells[0].textContent, [tr.cells[1].textContent, tr.cells[2].textContent]]))""")


def v4_revision(r: Run, dark: bool) -> None:
    """Item 2: the explainer's sentence on the Aditya plan -> one diff -> a way out -> apply -> plan updated."""
    p = r.page
    suffix = "-dark" if dark else ""
    r.step("revision: the explainer's sentence, one diff, ways out, apply" + (" (dark)" if dark else ""))
    open_sample(p, dark)
    p.click("#ok-btn")  # decide the venue: the revision must keep it
    expect(p.locator("#prop-h")).to_be_visible()
    p.click("nav >> text=My plan")
    p.fill("#adj-in", EXPLAINER)
    p.click("#view button >> text=Show me")
    expect(p.locator("#diff-h")).to_contain_text("50 more guests (30 → 80)", timeout=40000)
    rows = diff_rows(p)
    assert rows["Guests"] == ["30", "80"] and rows["Budget"] == ["₹40,000", "₹36,000"], rows
    assert rows["Date"][1] == "1 Nov" and rows["Per guest"] == ["₹1,333", "₹450"] and "Days to go" in rows, rows
    assert any(k.startswith("Now urgent") for k in rows), rows
    expect(p.locator(".explain")).to_contain_text("so I took 10%")
    expect(p.locator(".explain")).to_contain_text("You decided on")
    expect(p.locator(".ways-out .way")).to_have_count(3)
    assert one_next_step(p, "a revision that no longer adds up").startswith("Keep ₹36,000, invite 60")
    r.shot("v4-revision-diff" + suffix)
    p.locator(".ways-out .way").first.click()
    expect(p.locator(".ways-out")).to_have_count(0, timeout=40000)
    assert diff_rows(p)["Guests"] == ["30", "60"]
    assert one_next_step(p, "the revision after a way out") == "Apply changes"
    r.shot("v4-revision-way" + suffix, full=False)
    p.click("#view button >> text=Apply changes")
    expect(p.locator("#diff-h")).to_have_count(0)
    prof = p.evaluate("import('/js/store.js').then((m) => [m.state.plan.profile, m.state.profile])")
    assert prof[0] == prof[1] and (prof[0]["guests"], prof[0]["budget"], prof[0]["date"]) == (60, 36000, "2026-11-01"), prof
    expect(p.locator("#bud-h + p [data-budget-line]")).to_contain_text("of ₹36,000")
    check_totals_match(r, "after the explainer's revision")
    p.click("nav >> text=Details")
    expect(p.locator(".dmeta")).to_contain_text("1 November 2026")

    r.step("details page: editing the budget revises the plan, it doesn't start again")
    p.click("#ed-budget")
    p.fill("#ed-in", "45000")
    p.click(".drow.editing button[type=submit]")
    expect(p.locator("#view button >> text=Show what changes")).to_be_visible(timeout=40000)
    one_next_step(p, "details after an edit")
    p.click("#view button >> text=Show what changes")
    expect(p.locator("#diff-h")).to_contain_text("Set the budget to ₹45,000", timeout=40000)
    assert diff_rows(p)["Per guest"] == ["₹600", "₹750"]
    p.click("#view button >> text=Apply changes")
    assert p.evaluate("import('/js/store.js').then((m) => [m.state.plan.profile.budget, m.state.planDirty])") == [45000, False]
    expect(p.locator(".offb.dirty")).to_have_count(0)
    assert_no_null_text(p)


def v4_tradeoffs(r: Run, dark: bool) -> None:
    """Item 3: over budget after a revision -> "Where should the money go?" -> priorities -> an option."""
    p = r.page
    suffix = "-dark" if dark else ""
    r.step("trade-offs: rank what matters, pick a rebalanced option, apply" + (" (dark)" if dark else ""))
    open_sample(p, dark)
    p.click("#ok-btn")
    expect(p.locator("#prop-h")).to_be_visible()
    p.click("nav >> text=My plan")
    p.fill("#adj-in", "20 more guests")
    p.click("#view button >> text=Show me")
    expect(p.locator("#diff-h")).to_contain_text("20 more guests", timeout=40000)
    expect(p.locator(".chgmsg").first).to_contain_text("over budget")
    p.click("#trade-btn")
    expect(p.locator("#trade-h")).to_be_focused()
    expect(p.locator(".tradeo").first).to_be_visible(timeout=30000)
    expect(p.locator(".tradeo").first).to_contain_text("Protect food, venue")
    p.click("#pr-food")
    p.click("#pr-decor")
    expect(p.locator("#pr-food .apn")).to_have_text("1")
    expect(p.locator("#pr-decor")).to_have_attribute("aria-pressed", "true")
    p.click("#view button >> text=Update the options")
    expect(p.locator(".tradeo").first).to_contain_text("Protect food, decor", timeout=30000)
    expect(p.locator(".tradeo").first).to_contain_text("Gives up")
    assert one_next_step(p, "the trade-off step").startswith("Use this")
    r.shot("v4-tradeoffs" + suffix)
    p.locator(".tradeo").first.locator("button").click()
    expect(p.locator(".trade")).to_have_count(0, timeout=40000)
    expect(p.locator(".chgmsg").first).to_contain_text("Fits the budget")
    p.click("#view button >> text=Apply changes")
    expect(p.locator("#diff-h")).to_have_count(0)
    line = p.locator("#bud-h + p").inner_text()
    assert "over" not in line, line
    check_totals_match(r, "after a trade-off")
    assert_no_null_text(p)


QUOTE_A = ("Annapurna Caterers", "", "Veg buffet ₹420 per plate for 30 guests, includes 2 starters, dal, paneer, rice, roti, "
           "dessert and serving staff. GST 5% extra. Live dosa counter extra ₹3,000.")
QUOTE_B = ("Home Chef Meera", "9000", "Thali for 25 people, delivered. Jain thali available. 50% advance.")


def v4_vendors(r: Run, dark: bool) -> None:
    """Item 4: what to ask a caterer, compare two pasted quotes, choose one -> "Quoted" in the budget."""
    p = r.page
    suffix = "-dark" if dark else ""
    r.step("vendors: questions to ask, compare quotes, the chosen quote becomes the budget line" + (" (dark)" if dark else ""))
    open_sample(p, dark)
    p.click("#ok-btn")
    p.click("#ok-btn")
    expect(p.locator("#prop-h")).to_be_visible()
    expect(p.locator(".step").filter(has_text="Food")).to_have_count(1)
    more(p, "#ch-vendor")
    expect(p.locator("#vd-h")).to_be_focused()
    expect(p.locator(".vnote")).to_have_text("Waypoint hasn't contacted anyone. Availability and prices are yours to confirm.")
    expect(p.locator(".vqs li").first).to_be_visible(timeout=30000)
    n = p.locator(".vqs li").count()
    assert 5 <= n <= 8, n
    expect(p.locator(".vqs")).to_contain_text("Jain")
    p.locator(".vendor").scroll_into_view_if_needed()
    r.shot("v4-vendor-questions" + suffix, full=False)
    p.click("#view button >> text=Copy the questions")
    expect(p.locator("#toast")).to_contain_text("copied")
    p.click("#view button >> text=I have quotes: compare them")
    for i, (name, price, text) in enumerate((QUOTE_A, QUOTE_B)):
        p.fill(f"#vq-{i}-n", name)
        if price:
            p.fill(f"#vq-{i}-p", price)
        p.fill(f"#vq-{i}-t", text)
    p.click("#vq-go")
    expect(p.locator(".vtable tbody tr")).to_have_count(2, timeout=40000)
    expect(p.locator(".vtable")).to_contain_text("₹15,600")
    expect(p.locator(".vtable")).to_contain_text("Covers 25 guests; you have 30.")
    expect(p.locator(".vtable")).to_contain_text("Advance and cancellation terms")
    p.locator(".vres").scroll_into_view_if_needed()
    r.shot("v4-vendor-quotes" + suffix, full=False)
    p.click("#view button >> text=Choose Annapurna Caterers's quote")
    expect(p.locator(".vendor")).to_have_count(0)
    p.click("button.sg >> text=List")
    p.locator(".slist button", has_text="Food").click()
    expect(p.locator(".pprice")).to_contain_text("₹15,600")
    expect(p.locator(".pprice .ptag.price.quoted")).to_contain_text("Quoted")
    expect(p.locator(".budtop [data-budget-line]")).to_contain_text("₹15,600 quoted or yours")
    check_totals_match(r, "after choosing a quote")
    assert_no_null_text(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use the real model from .env")
    ap.add_argument("--shots", default=str(ROOT.parent / "celebration-planner-notes" / "shots" / "v4"))
    ap.add_argument("--axe", default="")
    ap.add_argument("--chrome", default=CHROME)
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    global LIVE
    LIVE = args.live
    proc = start_server(args.live)
    failures: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=args.chrome or None, headless=True)
            for dark in (() if os.getenv("WP_E2E_AXE_ONLY") else (False, True)):
                ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True,
                                          color_scheme="light")
                ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
                r = Run(ctx.new_page(), shots)
                r.n = 50 if dark else 0
                only = os.getenv("WP_E2E_ONLY", "")  # e.g. "guided" while working on one part
                try:
                    if only in ("", "flow"):
                        flow(r, dark)
                        expect(r.page.locator("#tour")).to_have_count(0)  # automated runs: no tour unless asked
                        mobile(r, dark)
                        if not dark:
                            edges(r)
                    if only in ("", "v4"):
                        v4_prices(r, dark)
                        v4_revision(r, dark)
                        v4_tradeoffs(r, dark)
                        v4_vendors(r, dark)
                    if only in ("", "picker"):
                        area_picker(r, dark)
                    if only in ("", "guided"):
                        guided(r, dark)  # last: it asks for tours, which then stay on for this session
                except Exception as exc:  # report and keep going so the summary is complete
                    import traceback
                    traceback.print_exc()
                    failures.append(f"{'dark' if dark else 'light'} flow: {exc!r}"[:2000])
                    r.shot("failure")
                failures += r.errors
                ctx.close()
            if args.axe:
                found = run_axe(browser, Path(args.axe), shots)
                serious = [f for f in found if f[2] in ("serious", "critical")]
                for f in found:
                    print("  axe", *f)
                print(f"axe: {len(found)} violation groups, {len(serious)} serious/critical")
                failures += [f"axe {f}" for f in serious]
            browser.close()
    finally:
        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:  # an outbound call is still open
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        print("server stopped", proc.pid)
    print("\nFAILURES:" if failures else "\nE2E OK: no page errors")
    for f in failures:
        print(" ", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
