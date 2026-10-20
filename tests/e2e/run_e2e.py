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
    if dark:
        p.click("#theme")
        assert p.evaluate("document.documentElement.dataset.theme") == "dark"
    r.shot("welcome" + suffix)

    r.step("type a brief → conversational intake")
    p.fill("#w-in", "Aditya's 22nd birthday, 30 people, ₹40k, in Gachibowli, Hyderabad, 15 Nov")
    p.click("#view >> text=Begin")
    expect(p.locator("#q-h")).to_be_visible(timeout=40000)
    expect(p.locator(".evchips")).to_contain_text("30 guests")
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
    p.click("#view >> text=Start the journey")
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
    r.shot("journey" + suffix)

    r.step("Why? reveals the rule")
    p.click("#view >> text=Why?")
    expect(p.locator("#why")).to_contain_text("The rule behind it")

    r.step("confirm the venue")
    p.click("#view >> text=Looks good")
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

    r.step("map + nearby, list view")
    p.click("button.tgl")
    expect(p.locator(".mapwrap")).to_be_visible()
    p.wait_for_timeout(1500)
    r.shot("map" + suffix)
    p.click("button.sg >> text=List")
    expect(p.locator(".slist li")).to_have_count(6)
    p.click("button.sg >> text=Path")

    r.step("plan the rest")
    btn = p.locator("#view >> text=Plan the rest for me")
    expect(btn).to_be_enabled()
    btn.click()
    expect(p.locator("#fin-h")).to_be_visible(timeout=30000)
    expect(btn).to_be_disabled()
    r.shot("journey-done" + suffix)

    r.step("event day")
    p.click("#view >> text=See the event day")
    expect(p.locator(".tli").first).to_be_visible()
    r.shot("day" + suffix)

    r.step("my plan: tick, adjust, diff, apply")
    p.click("nav >> text=My plan")
    expect(p.locator(".task").first).to_be_visible()
    p.locator(".task input").first.check()
    check_totals_match(r, "after booking a task")
    p.click("#view >> text=Cut the budget by 20%")
    expect(p.locator("#diff-h")).to_be_visible(timeout=40000)
    r.shot("plan-diff" + suffix)
    p.click("#view >> text=Apply changes")
    expect(p.locator("#diff-h")).to_have_count(0)
    check_totals_match(r, "after an adjustment")
    assert "32,000" in p.locator("[data-budget-line]").first.inner_text()
    p.click("#view >> text=Copy invite")
    expect(p.locator(".inv")).to_be_visible()
    with p.expect_download() as dl:
        p.click("#view >> text=Add deadlines to calendar")
    assert dl.value.suggested_filename.endswith(".ics")
    r.shot("plan" + suffix)
    assert_no_null_text(p)

    r.step("reload restores state")
    p.reload()
    expect(p.locator("h1")).to_contain_text("22nd")


def edges(r: Run) -> None:
    p = r.page
    r.step("edge: impossible budget")
    p.goto(BASE + "/?demo=budget&reset=1")
    expect(p.locator(".ways .way")).to_have_count(3, timeout=40000)
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

    r.step("reduced motion: the overlay rests, fully drawn")
    rm = p.context.browser.new_context(reduced_motion="reduce", viewport={"width": 1280, "height": 900})
    q = rm.new_page()
    q.goto(BASE + "/?reset=1")
    q.evaluate("import('/js/ui.js').then((m) => m.think('Testing the overlay…'))")
    q.wait_for_selector("#busy:not([hidden])")
    offset = q.evaluate("getComputedStyle(document.querySelector('.bm-path')).strokeDashoffset")
    assert offset in ("0", "0px"), offset
    q.screenshot(path=str(r.shots / "16-loading-reduced-motion.png"))
    rm.close()

    r.step("mobile layout")
    p.set_viewport_size({"width": 390, "height": 844})
    p.goto(BASE + "/#journey")
    expect(p.locator("#prop-h")).to_be_visible(timeout=20000)
    r.shot("mobile-journey")
    p.set_viewport_size({"width": 1280, "height": 900})


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
        page.click("#view >> text=Aditya's 22nd birthday")
        page.wait_for_selector("#prop-h", timeout=160000)
        for route in ("journey", "day", "plan", "chat", "welcome"):
            page.evaluate(f"location.hash = '{route}'")
            page.wait_for_timeout(900)
            page.add_script_tag(content=src)
            res = page.evaluate("axe.run(document, {resultTypes: ['violations']})")
            for v in res["violations"]:
                results.append((theme, route, v["impact"], v["id"], len(v["nodes"]), v["help"]))
        page.evaluate("location.hash = 'journey'")
        page.wait_for_timeout(600)
        page.click("#opt-btn")
        page.wait_for_timeout(900)  # let the slide-in finish; mid-fade colours are not the real ones
        page.add_script_tag(content=src)
        res = page.evaluate("axe.run(document, {resultTypes: ['violations']})")
        for v in res["violations"]:
            results.append((theme, "sheet", v["impact"], v["id"], len(v["nodes"]), v["help"],
                            [(n["target"], n.get("failureSummary", "")[-120:]) for n in v["nodes"][:4]]))
    ctx.close()
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use the real model from .env")
    ap.add_argument("--shots", default=str(ROOT.parent / "celebration-planner-notes" / "shots" / "v2"))
    ap.add_argument("--axe", default="")
    ap.add_argument("--chrome", default=CHROME)
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
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
                r.n = 20 if dark else 0
                try:
                    flow(r, dark)
                    if not dark:
                        edges(r)
                except Exception as exc:  # report and keep going so the summary is complete
                    failures.append(f"{'dark' if dark else 'light'} flow: {exc}")
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
