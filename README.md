# Waypoint — plan a celebration one decision at a time

**Vertical: Celebration & Event Planner.** Waypoint is an AI planning assistant for birthdays, weddings,
house-warmings and community events in India. You describe the event in a sentence; it asks only the
questions it can't infer, then walks you along a route from *Today* to the event day, proposing **one
decision per stop** (venue, theme, food, cake or music, invites, the run-sheet) with the reason and the
rule behind it. **You shape the plan, not just pick from it**: skip any stop, say you already have it,
write your own, change how it's done (cook at home, a rented hall…), add or remove stops, or simply ask
("I don't want a cake"). Every venue is a real place from OpenStreetMap, photos are real and credited,
the budget always adds up, and at the end it designs the invitation card.

## How it works

1. **Describe it** — "Aditya's 22nd birthday, 30 people, ₹40k, in Gachibowli, Hyderabad, 15 Nov".
2. **Conversational intake** — the AI reads the brief, fills the event card and picks the single most useful
   next question, skipping anything it could infer (max 7 turns). The area can be typed or taken from
   **Use my location** (opt-in, see *Location and privacy*).
3. **Details** — a calm profile page: the event at a glance, *What I understood* (one sentence from the
   AI), the rules' first insights ("₹1,333 a guest is comfortable for a birthday in a tier-1 city"), and
   editable fields grouped as who / when / where / how many / budget / food & needs / **what you already
   have** ("a family priest, my aunt is cooking" — those stops start as covered).
4. **Rules engine** — deterministic decisions with plain-language "why" text: budget per guest vs event
   type and city tier, days left (what to book first, what is already late), season (monsoon, peak
   wedding season, summer heat), guest count (venue size, catering mode), dietary and accessibility
   must-haves, and the **ways of doing a stop** (below). An impossible budget or a past date becomes a
   "let's make this work" screen with concrete ways out.
5. **One AI call drafts the whole plan** inside those rules: a pick plus 2–3 alternatives (cheaper /
   closer / different vibe) per stop, ranked from **real places fetched around that area**, a run-sheet
   and an invite. Code then validates and repairs everything.
6. **The journey** — one stop active at a time, ending with its choice in plain words ("Want this café?
   Or I can show cheaper ones."). *Looks good* and *Show options* are the two answers; *More ▾* holds the
   choice controls below; *Why?* shows the AI's reason **and** the deterministic rule; *In a hurry? Plan
   the rest for me* sits by the path. Path, Map (with **Around the venue**: food, parking and stays within
   1.5 km) and List views.
7. **Event day** — a second timeline for the day itself (three functions for weddings). Every moment can
   be kept, skipped, marked as sorted, swapped for an alternative, or rewritten.
8. **My plan** — the next task, a checklist with *book by* dates, budget planned vs booked, **Ask to
   adjust** with a before/after diff, `.ics` export of deadlines, copy invite, print view.
9. **Invite** — three AI-written invitations (warm / fun / formal) in English, हिन्दी or తెలుగు, drawn
   on a card in the browser: six templates, five colourways, your photo, *Download PNG*, *Print / PDF*,
   *Copy text*, *Share*.

While the AI works, a calm full-screen overlay shows the journey motif drawing itself and one line
saying what is happening ("Finding cafés and restaurants around Gachibowli…", "Re-planning without a
cake…"). State lives in the browser (localStorage) and is restored on reload. The server stores nothing.

## Guided by design

A first-time user (or a judge) should never wonder what to click next:

- **One next step per screen.** Exactly one primary action is marigold and glows softly (a still ring
  under reduced motion); everything else is secondary. A line under each title names it, built from the
  state so it is always true: *"Stop 2 of 6: pick a theme. Tap Looks good, or Show options to compare."*,
  *"Next up: Book the venue (book by 25 Oct). Tap Mark as booked once it is done."*
- **A three-step tour** on the first visit to the Journey (the path, the stop's buttons, the budget bar),
  and short ones on My plan and Invite: small popovers placed beside their target, never on it, with
  Next / Back / Skip, arrow keys, Esc and a focus trap. A soft scrim dims the page around a ring drawn
  tightly round the target; the arrow points at the button the step names, and the step's copy names the
  buttons in bold (**Looks good** keeps it · **Show options** compares · **More** has Skip, I already have
  this, Write my own). The counter reads *Tour 2/3*, so it isn't confused with *Stop 1 of 6*. Shown once per screen; the header's **?** replays
  it (elsewhere it points at the screen's next step). Not shown under `?demo=` or in automated browsers
  unless `?tour=1` asks for it.
- **The choice in plain words.** The model writes a short question with each pick; code keeps it only if
  it is one question with an "or", quotes no amounts or numbers, and offers only alternatives the stop
  really has. Otherwise a rules line is used (*"Want this restaurant? Or I can show cheaper ones."*;
  for a chosen alternative, *"Happy with this cake? Or I can show you the other options again."*).
- **You are here.** The active stop carries a *You are here* marker and *Food · 3 of 6*; the next
  undecided stop pulses gently; decided stops show a tick. List view and the screen-reader labels say
  the same.
- **A gentle hint after hesitation.** After about 8 s with no click or key press on a stop, *"Not sure?
  Tap Looks good. You can change it later."* appears under its buttons — in the page flow, so it never
  covers anything. Once per stop, dismissible, announced politely.
- **In a hurry? Plan the rest for me** sits right above the path from the first stop, saying how many
  stops it will confirm and that everything stays editable.
- **Fewer buttons at once.** A stop shows *Looks good* and *Show options*; *Skip*, *I already have this*,
  *Write my own*, *Describe a change* and *Remove stop* are in a **More ▾** menu (a real menu button:
  arrow keys, Home/End, Esc returns focus). Event-day moments work the same way. Nothing was removed.
- **Empty states** say what to do first: a route with no stops offers *Add a stop*, an empty run-sheet
  or task list says how to fill it.

## The choice model

Every stop has **Looks good / Show options** and, under **More ▾**, **Skip / I already have this / Write
my own / Describe a change / Remove stop** (removing asks once):

| Choice | What happens |
|---|---|
| **Skip** | The stop is greyed as *skipped*, leaves the budget (the money freed is shown as savings) and its task disappears. *Bring it back* undoes it. |
| **I already have this** | Covered: optional *who or what* and *what it costs you*; no suggestions are shown; the task becomes "Confirm with …". |
| **Write my own** | Your words replace the suggestion **verbatim** ("my aunt is cooking") and become the task. |
| **Ways of doing it** | Food: caterer · cook at home · home caterer · restaurant · convention-hall catering. Venue: rented hall · at home · restaurant · outdoor. Rituals: *need a poojari near me* (real temples from OpenStreetMap to ask at — a person is never named) · *already have one*. Each comes from a rules table with its budget share, lead time, task wording and reason; dependent stops follow and say why ("Food at a restaurant means the restaurant is the venue"). |
| **Add / remove stops** | Add a photographer, mehendi artist, return gifts, DJ, lights, makeup, anchor, poojari, transport — or anything typed, estimated at 3% of the budget and labelled as an estimate. Remove any stop that isn't booked. |

**Ask to adjust** turns words into **operations** (`remove_stop`, `skip_stop`, `have`, `own`, `set_mode`,
`add_stop`, moments on the day, budget/guests/date/indoors/outdoors). The model proposes them; code
validates each against the plan (real stop, real mode, safe values) and applies it deterministically; a
rules parser reads the common phrasings when the model is offline or unsure. "**I don't want cake**"
removes the cake stop, its moment on the day and its mention in the invite, frees ₹2,400 and shows the
diff. A request neither can read gets an honest "here's what I can do" plus the closest operation as a
one-tap offer — never silently nothing. Stops you've already booked are never swapped.

## Architecture

```
 Browser (vanilla HTML/CSS/JS modules, no build, no CDN)
   │  #welcome → #chat (details) → #journey → #day → #plan → #invite
   │  store.js owns the plan state; model.js derives every number   state: localStorage only
   │  invite.js draws the card on a canvas; photos never leave the browser
   ▼
 FastAPI  app/main.py   size limit · per-IP rate limits · CSP + security headers · gzip
   ├─ POST /api/chat      intake.py ──┐
   ├─ POST /api/plan      planner.py ─┼──► rules.py      pure, table-driven decisions + "why", modes, catalogue
   ├─ POST /api/adjust    choices.py ─┤    validators.py budget sums, dates, caps, prose checks
   ├─ POST /api/apply     choices.py ─┤    templates.py  deterministic plan, modes, added stops
   ├─ POST /api/invite    invites.py ─┤
   ├─ GET  /api/places    places.py ──┤
   ├─ GET  /api/photo     places.py   └──► llm.py        the ONLY module that calls a model
   ├─ POST /api/whereami  places.py               │
   ├─ POST /api/ics       planner.py              ▼
   └─ GET  /api/health          OpenRouter (free models only)
                                OSM Overpass → Nominatim fallback · Wikimedia Commons / Wikidata · Openverse · Foursquare (optional)
```

**The split between rules and the model.** The rules engine decides *what must be true* (budget band,
urgency, venue size, must-haves, per-stop budget targets, lead times, what each way of doing a stop
costs). The model decides *what to propose and how to say it* inside those limits, choosing only from
places passed to it by id. Code has the final say on every number, date, id and length:

| The model writes | Code enforces |
|---|---|
| Next intake question, chips, profile guesses, *What I understood* | Numbers and dates parsed deterministically and preferred; required fields; turn cap; blocks |
| Picks, alternatives, reasons, run-sheet, invite | Place ids must exist in the fetched OSM set (otherwise a generic, unnamed suggestion); no unverified business names; prices clamped; totals scaled to fit the budget; deadlines clamped to today…event day; **no amounts in prose** (they go stale); a venue's kind and distance come from OSM, not the model |
| Operations for "Ask to adjust" | Each operation validated against the plan, then applied and diffed deterministically |
| Three invitations | Right script for Hindi/Telugu, the real date present, no template slots, length cap; templates otherwise |

**One source of truth.** The browser keeps one plan state (`store.js`); every screen — Details, Journey,
Event day, My plan, Invite — derives its numbers from it through `model.js`, using the same pricing rule
as the server (`choices.stop_price`). The e2e run checks that the budget line and every stop price are
identical on Details, Journey and My plan after picking, skipping, adding, booking and adjusting.

## Where GenAI is used

- **Intake** (`/api/chat`): understands free text, infers fields, chooses the next question, writes
  *What I understood*.
- **Plan** (`/api/plan`): one call writes every stop's recommendation, alternatives and reasons,
  personalised to who the event is for, ranks real venues, writes the run-sheet and invite.
- **Adjust** (`/api/adjust`): turns a free-text change into validated operations.
- **Invite** (`/api/invite`): three invitations in the chosen language and tone.

AI content is badged ("Proposed by AI · checked by rules", "Asked by AI", "Written by AI"); rule output is
badged "Rules engine, not AI"; when the model is unavailable the badges say so ("Rules parser · AI
offline", "From templates") and an **AI offline** banner explains the plan came from the rules.

`app/llm.py`: OpenRouter chat completions, JSON mode, pydantic validation, hard deadline per call, LRU
cache keyed by a hash of the request, at most 4 concurrent calls. A failed attempt (error, empty reply,
bad JSON) is **retried from the next model in `WP_LLM_MODEL`**, then the deterministic fallback is used.
**$0 is enforced in code**: only `openrouter/free` or ids ending in `:free` are accepted, and a reply that
reports a cost is discarded.

## Where the places come from

Every venue, bakery, park, parking spot and stay is a real OpenStreetMap object with its OSM id. The model
only ranks and explains places passed to it by id. Sources are tried in order:

1. **Overpass** (one query for all categories around the point). Public instances, in order:
   overpass-api.de, lz4 and z.overpass-api.de, maps.mail.ru and overpass.private.coffee. It **fails
   fast**: 3 s to connect, 8 s for all instances together. A **circuit breaker** skips an instance for
   10 minutes after it fails, so a dead host costs nothing on the next lookup.
2. **Nominatim bounded POI search** when every Overpass instance is down, which is the case from some
   networks. Each category is one search inside a box around the event point (`viewbox` + `bounded=1`):
   cafe, restaurant, banquet hall, function hall, convention centre, community hall, park and bakery for
   venues; restaurant, cafe, parking and hotel for *Around the venue*. Requests are ≥ 1 s apart
   (Nominatim policy) and cached for 24 h. Results are merged and de-duplicated by OSM id and name (chains
   keep the closest branch), cut to the search radius, and the distance is computed locally. Their
   `extratags` provide capacity, cuisine, website and `wikimedia_commons` photos when present. A hall
   tagged only as a building is kept when its name says so ("Sri Sai Function Hall").
3. **Nothing reachable**: the plan is honest and says *"Live place listings were unavailable, so venues are
   described, not named."* It says this only when every source failed. When a source answered but nothing
   is mapped nearby, the note says that instead.

Photon (photon.komoot.io) was tried and is not used: it ignores the location for POI searches. Places code
calls only the hosts on an allow list (`places.ALLOWED_HOSTS`); anything else is refused before a socket
opens. To check the chain against the live services:

```bash
.venv/bin/python -m scripts.live_places "Gachibowli, Hyderabad" "Meerpet Road, Jillelaguda"
```

## Photos and attribution

For each venue, in this order:

1. **Wikimedia Commons / Wikidata** photos of *this* place, from its OpenStreetMap tags — labelled *Photo
   of this place*, credited with author and licence.
2. **Foursquare** photos of the venue, only if `FOURSQUARE_API_KEY` is set in `.env` (optional; nobody
   needs to sign up for Waypoint to work). A Foursquare place is used only when its name matches and it
   is within 150 m of the OSM point; otherwise it is skipped silently. Not exercised against the live
   service here (no key) — covered by mocked tests.
3. **Openverse** (api.openverse.org, free, no key, Creative Commons) photos of *similar* places, always
   labelled **"Similar vibe · not this venue"**, with creator, licence and a link to the Openverse page.
   Never presented as the venue itself.

Ideas (theme, food, cake) use Commons search topped up from Openverse, labelled *Inspiration photo, not
the actual item*. A venue card shows its name, distance, an *Open in OpenStreetMap* link and a photo
strip with *This place / Similar vibe* tags. With nothing real to show, an illustrated "no photo" card
appears instead of a stand-in. Map data © OpenStreetMap contributors (ODbL).

## Location and privacy

- By default Waypoint geocodes the **area you type** (Nominatim, India only), shows what the map made of
  it ("Found on the map as: Gachibowli, Hyderabad") so you can confirm or correct it, and searches
  **around the event area or venue — not around you**. The map toggle is *Around the venue*, every
  distance reads "1.2 km from Gachibowli" (or from the venue), and a line above the map says
  "Searching around: Gachibowli, Hyderabad · change".
- **Namesakes are never guessed.** OSM often has several areas with one name: "Meerpet, Hyderabad" is
  both a ward in Uppal and a town near Balapur / Jillelaguda, ~15 km apart. Waypoint asks Nominatim for
  5 hits with address details, drops shops, clinics, buildings and other amenities, and treats hits
  under 2 km apart as one area. When the full query returns a single area alongside points of interest
  that merely mention the name, it also searches the name alone and keeps areas within 60 km. A district
  that shares its town's name ("Jaipur") is not offered as a separate choice. If more than one area
  remains, the chat and the Details *Where* field show a calm **"Which Meerpet?"** picker with each
  option's mandal, district and pincode ("Meerpet Road, Jillelaguda · Balapur mandal, Ranga Reddy ·
  500097"). No venue search runs and the plan doesn't start until you pick one. The pick becomes the
  event's search point, and "Searching around: …" shows its name.
- **Pin on map** is the fallback in the picker (and behind "pin it on the map" for any area): a small
  OpenStreetMap map where you tap the exact spot, or move the pin with the arrow keys. The pin is rounded
  to about 10 m and named by a reverse lookup that only sees it rounded to about 100 m. The map uses
  OSM's standard tiles from `tile.openstreetmap.org`, the only host added to the CSP for it. In line
  with the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/), tiles load only
  after you open the map, about a dozen per view with normal browser caching. The tile images send a
  Referer for this site (`strict-origin-when-cross-origin` on those images only, while the page stays
  `no-referrer`), there is no prefetching or bulk download, and the map carries the "© OpenStreetMap
  contributors" attribution.
- **Use my location** is opt-in: the browser asks only when you tap it. The position is rounded to about
  100 m on the device, POSTed once to `/api/whereami` (so it never appears in a URL or access log) to get
  an area name, and then used only for the place search. The server doesn't cache, log or store it, and
  it is never sent to the model. Only then do distances read "from you". Typing another area drops it.
- The invitation photo is drawn on a canvas in the browser and is never uploaded.

## Security

- Secrets only in `.env` (gitignored); `.env.example` lists names only. Keys are never logged.
- Prompt injection: user text only travels inside a delimited `<user_data>` JSON block (with `<` escaped);
  the system prompt says to treat it as data; replies must match a schema and every value is
  re-validated; the model has no tools, can't name places that weren't fetched, and its "Ask to adjust"
  output is a list of operations that are checked one by one against the plan.
- Strict CSP (`default-src 'self'`, no inline script or style, no `eval` — the e2e run proves it, images
  only from self, `blob:`/`data:`, `upload.wikimedia.org`, `api.openverse.org`, `tile.openstreetmap.org` (Pin on map) and — only when a
  Foursquare key is set — `fastly.4sqi.net`), `frame-ancestors 'none'`, nosniff, DENY framing,
  no-referrer, `Permissions-Policy` (camera, microphone, payment off; geolocation for this site only),
  COOP/CORP.
- All browser input validated with pydantic (length caps, ranges, enums) → friendly 422 that never echoes
  input; 64 KB request limit (413); per-IP sliding-window rate limits on AI and lookup routes (429 with
  `Retry-After`); generic 500s without stack traces; no API docs exposed.
- Front end builds the DOM with `textContent` only (`js/dom.js`); model, place and user text is never
  parsed as HTML; links only to Commons, Openverse, Foursquare and OpenStreetMap; `null` is never rendered.
- Outbound calls: proper User-Agent, an allow list of hosts, per-host rate limiting (Nominatim 1 req/s,
  Openverse within its anonymous quota), timeouts, a circuit breaker for Overpass, caching.

## Accessibility

- Semantic landmarks, one `h1` per screen, skip link, labels on every control, visible focus rings, 44 px
  targets, full keyboard use; the Details fields are a description list.
- Focus moves to the active stop's heading after every decision; the options sheet is a modal dialog with
  a focus trap, Escape and focus return, and the page makes room so the stop it's about stays visible.
- The AI overlay is `role=status` with `aria-live`; focus moves into it and back, the page behind is
  `inert`; under `prefers-reduced-motion` the motif rests fully drawn.
- `aria-live` announces AI questions, proposals and changes; the **List view is the screen-reader
  equivalent** of the path and the map; budget bar segments are labelled buttons with a text list; the
  invitation canvas has a text alternative with the full card text.
- Light and dark themes (follows `prefers-color-scheme`, header toggle persisted, set before first paint),
  both WCAG AA — `tests/test_contrast.py` checks every text/background pair of both themes at ≥ 4.5:1.
- Guidance is accessible too: the tour is a labelled dialog with a focus trap, Esc and focus return; the
  More menus are `aria-haspopup="menu"` buttons with arrow-key menus; the hesitation hint is a polite live
  region that is always present; *You are here* and *Up next* are in the node labels and List view.
- **axe-core: 0 violations** on welcome, details, journey, options sheet, event day, plan and invite, and
  with the tour and both More menus open, in both themes (run by the e2e script). Phones (390 px: no
  sideways scrolling on any screen) to laptops.

## Efficiency

- One model call per plan; adjustments and choices re-plan deterministically (a small model call only to
  read a typed request). Choices that need no re-planning (skip, have, own, moments) happen instantly in
  the browser.
- LRU cache for model replies; venues, geocodes and photos cached in memory (Openverse for a day); the
  browser **prefetches venues as soon as the area is known**; concurrent lookups for the same area share
  one request; the card is drawn locally.
- No framework, no build step, 5 small pinned runtime dependencies; self-hosted variable fonts (Latin
  subsets, ~140 KB), gzip, immutable caching for fonts.

## Testing

`pytest` — **418 tests**, fully offline (mock model, `respx` for HTTP):

| File | What it covers |
|---|---|
| `test_rules.py` (62) | Table-driven cases for every rule family and their boundaries |
| `test_validators.py` (77) | Budget always ≤ total after fixing, rounding, dates, caps, no amounts or wrong place kinds in prose |
| `test_llm.py` (40) | Free-model enforcement, cost check, empty reply → next model in the chain → fallback, cache, deadline, injection stays data |
| `test_choices.py` (39) | **I don't want cake, already have a poojari, cook at home, add a photographer, skip invites**, cascades, validation of model operations, booked stops, moments, parser phrasings, temples, invite clean-up |
| `test_contrast.py` (38) | WCAG AA contrast for text colours in both themes |
| `test_intake.py` (35) | Parsing, one question at a time, blocks, *What I understood*, first insights |
| `test_api.py` (33) | chat → plan → adjust, 409 blocks, 413, 422, 429, security headers, `.ics`, rules-parser fallback |
| `test_places.py` (44) | Invented place ids rejected, empty Overpass, **Overpass down → Nominatim places used** (venues and *Around the venue*), **both down → honest empty state**, **circuit breaker** skips a dead instance and retries it after the cool-down, the 8 s budget, Nominatim requests spaced out, the same place from two sources kept once, host allow list, Openverse parsing and caching, place-first photo order, Foursquare skip and matching |
| `test_guidance.py` (24) | The choice line: the model's question kept only when safe (one question, an "or", no amounts or numbers, only real alternatives), the rules' fallback for every stop and alternative |
| `test_location.py` (9) | India-only geocoding, reverse lookup rounded and uncached, POST-only, position never sent to the model |
| `test_area_picker.py` (9) | *Which Meerpet?*: mocked Nominatim with the real Meerpet hits gives two options (mandal, district, pincode) and no venue search; a single clear area gives no picker and no second lookup; clinics and buildings filtered out; a district isn't a second choice beside its city; far namesakes ignored; the pick is the search centre; CSP allows OSM tiles only |
| `test_invites.py` (8) | Three tones × three languages, localised dates, bad model text replaced, wrong script rejected |

`tests/e2e/run_e2e.py` — a headless Playwright run (not part of pytest, so Playwright is not a dependency):
types a brief, answers the chat, edits details, plans, checks the overlay, the venue card and photo
labels, confirms, picks an option, skips a stop, switches food to *cook at home*, marks one stop as
already had, writes one in its own words, adds a photographer, Map + Around the venue, List, *Plan the
rest*, changes moments on the event day, books a task, adjusts, designs the invite (long name + emoji +
Hindi must fit; the download must be a real PNG; the photo upload must make no network request), checks
**the totals match across screens** at every step, then every screen at phone width; the whole flow again
in dark mode; edge states, *I don't want cake* end to end, *Use my location* with a granted browser
position, reduced motion; the **"Which Meerpet?" picker** in the chat and on Details, then *Pin on map* (tap and arrow keys, tiles stubbed so the run makes no tile requests), with the journey's "Searching around" naming the pin; then the **guidance** in both themes: exactly one primary action named by the
guide line on every screen, the tour on the first visit and not the second (its arrow on *Looks good*, the
ring tight round the buttons, the scrim, *Tour 2/3*), *?* replaying it, the choice
line, *You are here*, the hint after waiting, *Plan the rest*, every **More** action (skip, already have,
write my own, remove) with keyboard checks, and empty states; asserts no page or console errors;
optionally runs axe-core. Mock model by default or the real one (`--live`). `WP_E2E_ONLY=guided` (or `picker`)
runs just that part.

## Assumptions and limits

- India-first: rupees, Indian events (mehendi, sangeet, griha pravesh), city tiers for cost benchmarks,
  Indian seasons, geocoding limited to India (`WP_COUNTRIES`). Benchmarks and mode shares in `rules.py`
  are reasonable estimates, not quotes.
- Places come from OpenStreetMap, so coverage and capacities vary; prices are estimates to check with the
  venue. Small venues rarely have their own photo: then you see labelled *similar vibe* photos or the
  "no photo" card. Openverse's anonymous quota is small (20/min, 200/day), so results are cached.
- Free models can be slow or busy; replies are validated, retried down the model chain and fall back to
  the rules. A first plan for a new area can take ~30–60 s (places + model). When Overpass is down,
  the Nominatim fallback takes ~15–30 s for a new area (one request a second), then is cached for a day.
- No free, key-less image generator was reliable enough for an *AI background* on the invite or an
  "Imagine it" preview, so the card uses hand-drawn templates. Hindi and Telugu render with the
  device's system fonts (the bundled fonts are Latin subsets).

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # add OPENROUTER_API_KEY, or leave it empty for mock mode
.venv/bin/uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000
```

- `WP_LLM_MODEL` — a free model id or a comma-separated chain (see `.env.example`).
- `FOURSQUARE_API_KEY` — optional real venue photos from Foursquare Places; empty = skipped.
- `WP_LLM_MOCK=1` — deterministic offline mode. `WP_TODAY=2026-10-20` pins "today" for demos.
- Edge states: `/?demo=budget`, `/?demo=date`, `/?demo=offline`, `/?demo=nophoto`; `/?reset=1` clears saved state.

Tests:

```bash
.venv/bin/python -m pytest
python tests/e2e/run_e2e.py [--live] [--axe path/to/axe.min.js]   # any Python with Playwright
```

Fonts: Fraunces and Hanken Grotesk, SIL Open Font License (`app/static/fonts/OFL.txt`). Map and place
data © OpenStreetMap contributors (ODbL). Photos: Wikimedia Commons and Openverse (Creative Commons),
credited individually; Foursquare photos only with your own key.
