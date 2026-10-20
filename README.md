# Waypoint — plan a celebration one decision at a time

**Vertical: Celebration & Event Planner.** Waypoint is an AI planning assistant for birthdays, weddings,
house-warmings and community events in India. You describe the event in a sentence; it asks only the
questions it can't infer, then walks you along a route from *Today* to the event day, proposing **one
decision per stop** (venue, theme, food, cake or music, invites, the run-sheet) with the reason and the
rule behind it. Every venue is a real place from OpenStreetMap, every photo is a real, credited
Wikimedia Commons photo, and the budget always adds up.

## How it works

1. **Describe it** — "Aditya's 22nd birthday, 30 people, ₹40k, in Gachibowli, Hyderabad, 15 Nov".
2. **Conversational intake** — the AI reads the brief, fills the event card (editable chips), and picks the
   single most useful next question, skipping anything it could infer (max 7 turns).
3. **Rules engine** — deterministic decisions with plain-language "why" text: budget per guest vs event
   type and city tier, days left (what to book first, what is already late), season (monsoon, peak wedding
   season, summer heat), guest count (venue size, catering mode), dietary and accessibility must-haves.
   An impossible budget or a past date becomes a "let's make this work" screen with concrete ways out.
4. **One AI call drafts the whole plan** inside those rules: a pick plus 2–3 alternatives (cheaper /
   closer / different vibe) per stop, ranked from **real places fetched for that area**, a run-sheet and a
   WhatsApp invite. Code then validates and repairs everything.
5. **The journey** — one stop active at a time; later stops stay dimmed until you confirm. *Looks good*,
   *Show options* (a side sheet; picking one animates the budget bar and marks affected later stops),
   *Change* (cheaper / closer / different vibe or free text), *Why?* (the AI's reason **and** the
   deterministic rule), *Plan the rest for me*. Path, Map (with Nearby food, parking and stays) and List views.
6. **Event day** — a second timeline for the day itself (three functions for weddings).
7. **My plan** — the next task, a checklist with *book by* dates, budget planned vs spent, **Ask to adjust**
   ("cut the budget by 20%", "add 15 guests", "move it outdoors", "move it to 6 Dec") with a before/after
   diff, `.ics` export of deadlines, copy invite, print view.

State lives in the browser (localStorage) and is restored on reload. The server stores nothing.

## Architecture

```
 Browser (vanilla HTML/CSS/JS modules, no build, no CDN)
   │  #welcome → #chat → #journey → #day → #plan          state: localStorage only
   ▼
 FastAPI  app/main.py   size limit · per-IP rate limits · CSP + security headers · gzip
   ├─ POST /api/chat    intake.py ──┐
   ├─ POST /api/plan    planner.py ─┼──► rules.py       pure, table-driven decisions + "why"
   ├─ POST /api/adjust  planner.py ─┤    validators.py  budget sums, dates, caps, place ids
   ├─ GET  /api/places  places.py ──┤    templates.py   deterministic plan (fallback / gap filler)
   ├─ GET  /api/photo   places.py   │
   ├─ POST /api/ics     planner.py  └──► llm.py         the ONLY module that calls a model
   └─ GET  /api/health                     │
                                           ▼
                     OpenRouter (free models only)   OSM Nominatim + Overpass   Wikimedia Commons / Wikidata
```

**The split between rules and the model.** The rules engine decides *what must be true* (budget band,
urgency, venue size, must-haves, per-stop budget targets, lead times). The model decides *what to
propose and how to explain it* inside those limits, choosing only from places passed to it by id. Code
has the final say on every number, date, id and length:

| The model writes | Code enforces |
|---|---|
| Next intake question, chips, profile guesses | Numbers and dates parsed deterministically and preferred; required fields; turn cap; blocks |
| Picks, alternatives, reasons, run-sheet, invite | Place ids must exist in the fetched OSM set (otherwise a generic, unnamed suggestion); no unverified business names on any stop; prices clamped; per-guest totals fixed; recommended total scaled to fit the budget; deadlines clamped to today…event day; text length caps |
| Interpretation of "Ask to adjust" | The change itself is re-planned deterministically (cheapest fitting option, capacity checks), then diffed |

## Where GenAI is used

- **Intake** (`/api/chat`): understands free text ("my mom's 60th next month in Pune, around 40 people"),
  infers fields and chooses the next question.
- **Plan** (`/api/plan`): one call writes every stop's recommendation, alternatives and reasons, personalised
  to who the event is for (e.g. "Cricket night meets Retro Bollywood"), ranks real venues, writes the
  run-sheet and invite.
- **Adjust** (`/api/adjust`): turns a free-text change request into a structured action.

AI content is badged in the UI ("Proposed by AI · checked by rules", "Asked by AI"); rule output is badged
"Rules engine, not AI". If the model is unavailable the app keeps working with a deterministic plan built
from the same rules and real places, clearly labelled (**AI offline** banner).

`app/llm.py`: OpenRouter chat completions, JSON mode, pydantic validation, hard deadline per call, one
retry, in-memory LRU cache keyed by a hash of the request, at most 4 concurrent calls. **$0 is enforced in
code**: only `openrouter/free` or ids ending in `:free` are accepted, and a reply that reports a cost is
discarded.

## Security

- Secrets only in `.env` (gitignored); `.env.example` lists names only. The key is never logged.
- Prompt injection: user text only travels inside a delimited `<user_data>` JSON block (with `<` escaped so it
  can't close the block); the system prompt says to treat it as data; replies must match a schema and
  every value is re-validated; the model has no tools and can't name places that weren't fetched.
- Strict CSP (`default-src 'self'`, no inline script or style, images only from self and
  `upload.wikimedia.org`, `frame-ancestors 'none'`), plus nosniff, DENY framing, no-referrer,
  Permissions-Policy, COOP/CORP. The e2e run confirms even `eval` is blocked.
- All browser input validated with pydantic (length caps, ranges, enums) → friendly 422 that never echoes
  input; 64 KB request limit (413); per-IP sliding-window rate limits on AI and lookup routes (429 with
  `Retry-After`); generic 500s without stack traces; no API docs exposed.
- Front end builds the DOM with `textContent` only (`js/dom.js`); model and place text is never parsed
  as HTML; links are allowed only to `https://commons.wikimedia.org`; `null` is never rendered.
- Outbound calls: proper User-Agent, per-host rate limiting (Nominatim 1 req/s), timeouts, caching.

## Accessibility

- Semantic landmarks, one `h1` per screen, skip link, labels on every control, visible focus rings, 44 px
  targets, full keyboard use.
- Focus moves to the active stop's heading after every decision; the options sheet is a modal dialog with
  a focus trap, Escape to close and focus returned to *Show options*.
- `aria-live` announces AI questions and proposals; the **List view is the screen-reader equivalent** of the
  path and the map; the nearby places are also a plain list; budget bar segments are labelled buttons with
  a text list beside them.
- Light and dark themes (follows `prefers-color-scheme`, header toggle persisted, set before first paint,
  cross-fade), both WCAG AA. `prefers-reduced-motion` turns animations off.
- **axe-core: 0 violations** on welcome, chat, journey, options sheet, event day and plan, in both themes
  (run by the e2e script). Responsive from phones (390 px tested) to laptops; the route scrolls sideways on
  narrow screens with a visible cue.

## Efficiency

- One model call per plan; adjustments re-plan deterministically (a tiny model call only to read the request).
- LRU cache for model replies; venues, geocodes and photos cached in memory; the browser **prefetches
  venues as soon as the area is known**, while the chat is still going; concurrent lookups for the same
  area share one request.
- No framework, no build step, 5 small pinned runtime dependencies; self-hosted variable fonts (Latin subsets, ~140 KB),
  gzip on responses, immutable caching for fonts.

## Testing

`pytest` — **247 tests**, fully offline (mock model, `respx` for HTTP):

| File | What it covers |
|---|---|
| `test_rules.py` (62) | Table-driven cases for every rule family and their boundaries |
| `test_validators.py` (66) | Budget always ≤ total after fixing, rounding, dates in range, caps |
| `test_llm.py` (35) | Free-model enforcement, cost check, bad JSON → retry → fallback, cache hits, hard deadline, injection stays in the data block |
| `test_places.py` (23) | Invented place ids rejected and never named, empty Overpass result, unverified shop names replaced, photo filtering |
| `test_intake.py` (31) | Date/money parsing, one question at a time, blocks, chip edits make no model call |
| `test_api.py` (30) | chat → plan → adjust happy path, 409 blocks, 413, 422, 429, security headers, `.ics` |

`tests/e2e/run_e2e.py` — a headless Playwright run (not part of the pytest suite, so Playwright is not a
dependency): types a brief, answers the chat, edits a chip, confirms a stop, opens options and picks one,
checks Escape and focus, Map + Nearby, List, *Plan the rest*, event day, ticks a task, adjusts and applies,
copies the invite, downloads the `.ics`, reloads; then the four edge states and a phone viewport; the whole
flow again in dark mode; asserts no page or console errors; optionally runs axe-core. It works with the
mock model (default) or the real one (`--live`).

## Assumptions and limits

- India-first: rupees, Indian events (mehendi, sangeet, griha pravesh), city tiers for cost benchmarks,
  Indian seasons. Benchmarks in `rules.py` are reasonable estimates, not quotes.
- Places come from OpenStreetMap, so coverage and capacities vary; prices are estimates to check with the
  venue. Many places have no Commons photo, in which case Waypoint shows an illustrated "no photo" card
  rather than a stand-in.
- Free models can be slow or busy; replies are validated and fall back to the rule-based plan if needed.
  A first plan for a new area can take ~30–60 s (Overpass + model); repeat requests are cached.
- *Imagine it* (AI-generated decor preview) was not built: no free, key-less image-generation option was
  reliable enough, so the button is hidden.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # add OPENROUTER_API_KEY, or leave it empty for mock mode
.venv/bin/uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000
```

- `WP_LLM_MODEL` — a free model id or a comma-separated chain (see `.env.example`).
- `WP_LLM_MOCK=1` — deterministic offline mode. `WP_TODAY=2026-10-20` pins "today" for demos.
- Edge states: `/?demo=budget`, `/?demo=date`, `/?demo=offline`, `/?demo=nophoto`; `/?reset=1` clears saved state.

Tests:

```bash
.venv/bin/python -m pytest
python tests/e2e/run_e2e.py [--live] [--axe path/to/axe.min.js]   # any Python with Playwright
```

Fonts: Fraunces and Hanken Grotesk, SIL Open Font License (`app/static/fonts/OFL.txt`). Map and place
data © OpenStreetMap contributors (ODbL). Photos: Wikimedia Commons, credited individually.
