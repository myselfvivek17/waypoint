"""Deterministic, explainable decision engine.

Pure functions over a :class:`Profile`. Every decision is driven by a table at the top
of this module and returns a plain-language "why" so the UI can show the rule behind
each proposal. Nothing here calls the network or the model.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .schemas import Block, ChecklistItem, EventType, Insight, Profile, Way

# ---------------------------------------------------------------- tables

TIER1 = {
    "mumbai", "delhi", "new delhi", "bengaluru", "bangalore", "hyderabad", "secunderabad",
    "chennai", "kolkata", "pune", "gurugram", "gurgaon", "noida", "navi mumbai", "thane",
}
TIER2 = {
    "jaipur", "lucknow", "ahmedabad", "kochi", "chandigarh", "indore", "udaipur", "surat",
    "nagpur", "coimbatore", "bhopal", "visakhapatnam", "vizag", "mysuru", "mysore", "goa",
    "vadodara", "jodhpur", "amritsar", "dehradun", "bhubaneswar", "guwahati", "madurai",
    "thiruvananthapuram", "nashik", "ludhiana", "kanpur", "patna", "raipur", "ranchi",
    "vijayawada", "mangaluru", "mangalore", "agra", "varanasi",
}
DEFAULT_TIER = 2

# Per-guest spend for the whole event (food + space + decor), in rupees:
# (floor, typical, generous). Below the floor the event cannot be done as described.
PER_GUEST: dict[EventType, dict[int, tuple[int, int, int]]] = {
    "birthday": {1: (600, 1500, 3000), 2: (500, 1200, 2500), 3: (400, 900, 2000)},
    "wedding": {1: (1500, 5000, 12000), 2: (1200, 4000, 10000), 3: (1000, 3000, 8000)},
    "house-warming": {1: (500, 1200, 2500), 2: (400, 1000, 2000), 3: (300, 800, 1600)},
    "community": {1: (250, 600, 1500), 2: (200, 500, 1200), 3: (150, 400, 1000)},
    "other": {1: (600, 1500, 3000), 2: (500, 1200, 2500), 3: (400, 900, 2000)},
}

BAND_ADVICE = {
    "tight": [
        "Pick a café or restaurant package so food and space come together.",
        "A home, terrace or clubhouse saves the hall rental.",
        "Digital invites instead of printed cards.",
    ],
    "comfortable": [
        "Room for one splurge: a live counter or a photographer.",
        "Keep about 5% aside for last-minute extras.",
    ],
    "generous": [
        "A coordinator on the day frees the family to enjoy it.",
        "Keep 10% aside; generous budgets drift upward.",
    ],
}

# The stops on the journey, in order, per event type: (key, label).
STOPS: dict[EventType, list[tuple[str, str]]] = {
    "birthday": [("venue", "Venue"), ("theme", "Theme"), ("food", "Food"), ("cake", "Cake"),
                 ("invites", "Invites"), ("day", "Day-of schedule")],
    "wedding": [("venue", "Venue"), ("theme", "Theme"), ("food", "Food"), ("music", "Music"),
                ("invites", "Invites"), ("day", "Day-of schedule")],
    "house-warming": [("venue", "Space"), ("puja", "Puja"), ("food", "Food"), ("theme", "Decor"),
                      ("invites", "Invites"), ("day", "Day-of schedule")],
    "community": [("venue", "Venue"), ("theme", "Decor"), ("food", "Food"), ("sound", "Sound & stage"),
                  ("invites", "Invites"), ("day", "Day-of schedule")],
    "other": [("venue", "Venue"), ("theme", "Theme"), ("food", "Food"), ("cake", "Cake"),
              ("invites", "Invites"), ("day", "Day-of schedule")],
}

# Share of the budget per stop. Each row sums to 0.95: 5% is always kept as a buffer.
SPLIT: dict[EventType, dict[str, float]] = {
    "birthday": {"venue": .30, "theme": .14, "food": .36, "cake": .06, "invites": .04, "day": .05},
    "wedding": {"venue": .27, "theme": .17, "food": .25, "music": .12, "invites": .05, "day": .09},
    "house-warming": {"venue": .10, "puja": .15, "food": .45, "theme": .12, "invites": .03, "day": .10},
    "community": {"venue": .25, "theme": .15, "food": .35, "sound": .12, "invites": .03, "day": .05},
    "other": {"venue": .30, "theme": .14, "food": .36, "cake": .06, "invites": .04, "day": .05},
}
BUFFER = 0.05

# Days before the event by which each stop should be booked.
LEAD: dict[EventType, dict[str, int]] = {
    "birthday": {"venue": 21, "invites": 14, "theme": 10, "food": 10, "cake": 5, "day": 3},
    "wedding": {"venue": 120, "food": 90, "theme": 60, "music": 60, "invites": 45, "day": 14},
    "house-warming": {"puja": 21, "invites": 14, "food": 10, "theme": 7, "venue": 5, "day": 3},
    "community": {"venue": 30, "invites": 21, "sound": 14, "food": 14, "theme": 10, "day": 5},
    "other": {"venue": 21, "invites": 14, "theme": 10, "food": 10, "cake": 5, "day": 3},
}

TASK_VERBS = {
    "venue": "Book the venue", "theme": "Order the decor", "food": "Confirm the food",
    "cake": "Order the cake", "invites": "Send the invites", "day": "Share the run-sheet",
    "music": "Book the music", "puja": "Book the priest and samagri", "sound": "Book sound and stage",
}

# ---- user choice: ways of doing a stop. (id, label, budget multiplier, days ahead, task verb, why)
MODES: dict[str, list[tuple[str, str, float, int, str, str]]] = {
    "food": [
        ("caterer", "Caterer", 1.0, 10, "Confirm the caterer",
         "A caterer brings the food, the cooks and the serving staff: the usual choice."),
        ("home", "Cook at home", .45, 3, "Plan the menu and buy groceries",
         "Cooking at home costs about half (groceries and a helper) but needs a kitchen and two days of prep."),
        ("home_caterer", "Home caterer", .8, 7, "Confirm the home caterer",
         "A home caterer cooks in their own kitchen and delivers: cheaper than a full caterer, no serving staff."),
        ("restaurant", "Restaurant", 1.1, 10, "Reserve the restaurant",
         "At a restaurant the food and the space come together, so the venue costs little more than the booking."),
        ("hall", "Convention-hall catering", 1.2, 30, "Confirm the hall's caterer",
         "The hall's own caterer makes big groups easy, but halls book out early, so it needs a month's notice."),
    ],
    "venue": [
        ("hall", "Rented hall", 1.0, 21, "Book the hall",
         "A hall gives space, seating and power in one booking."),
        ("home", "At home", .3, 5, "Arrange seating and a canopy",
         "At home the budget only covers rented chairs, a canopy and cleaning help."),
        ("restaurant", "Restaurant", .5, 10, "Reserve the restaurant",
         "A restaurant's private area: the booking is small because the food bill covers the space."),
        ("outdoor", "Outdoor", .8, 21, "Book the garden or lawn",
         "A park, garden or lawn: open sky and room, but it needs a weather backup."),
    ],
    "puja": [
        ("need", "Need a poojari near me", 1.0, 21, "Ask the temple about a poojari",
         "I look up temples near you on OpenStreetMap; ask there for a poojari. I never suggest a person by name."),
        ("have", "Already have one", 0.0, 7, "Confirm the time with your poojari",
         "Your own poojari leads the puja; only the samagri and flowers are left to buy."),
    ],
}
MODE_STOPS = {"poojari": "puja"}  # added stops that share a stop's modes
# When a stop's mode changes, another stop may have to follow: (stop, mode) -> [(other stop, mode, why)].
MODE_EFFECTS: dict[tuple[str, str], list[tuple[str, tuple[str, ...], str, str]]] = {
    # (other stop, modes it may keep, mode to switch to, why)
    ("food", "restaurant"): [("venue", ("restaurant",), "restaurant", "Food at a restaurant means the restaurant is the venue.")],
    ("food", "hall"): [("venue", ("hall",), "hall", "The hall's caterer serves at the hall, so the venue is a hall.")],
    ("venue", "home"): [("food", ("caterer", "home", "home_caterer"), "home_caterer",
                         "A party at home needs food cooked there or brought in, not a restaurant or a hall's caterer.")],
    ("venue", "restaurant"): [("food", ("restaurant",), "restaurant", "At a restaurant the restaurant serves the food.")],
    ("venue", "outdoor"): [("food", ("caterer", "home_caterer"), "caterer", "An outdoor venue needs a caterer who brings everything.")],
}

# Stops the user can add: key -> (label, pattern, share of budget or per-guest price, per guest?, days ahead,
# task verb, [(kind, name, price factor, reason)], photo search)
EXTRA_STOPS: dict[str, tuple] = {
    "photographer": ("Photographer", r"photo|camera|video", .08, False, 21, "Book the photographer",
                     [("Recommended", "Photographer for three hours, edited album", 1.0, "Candid and posed shots of everyone."),
                      ("Cheaper", "A friend with a good camera, you edit", .25, "Free, but someone misses the party."),
                      ("Different vibe", "Candid photographer plus a one-minute film", 1.7, "A short film to share the next day.")],
                     "event photographer"),
    "mehendi": ("Mehendi artist", r"mehendi|mehndi|henna", .05, False, 21, "Book the mehendi artist",
                [("Recommended", "Mehendi artist for the hosts and close family", 1.0, "Two artists for about four hours."),
                 ("Cheaper", "One artist, simple designs", .5, "Quick designs for everyone who wants one."),
                 ("Different vibe", "Bridal-style mehendi with an assistant", 1.8, "Detailed designs and a second artist.")],
                "mehendi henna"),
    "gifts": ("Return gifts", r"return gift|gift|favou?r", 120, True, 10, "Order the return gifts",
              [("Recommended", "Small useful return gift for each family", 1.0, "A plant, a sweet box or a diya set."),
               ("Cheaper", "Sweet box for each guest", .6, "Simple, and everyone likes it."),
               ("Different vibe", "Personalised keepsake", 2.0, "Printed with the date and a name.")],
              "gift boxes"),
    "dj": ("DJ & music", r"\bdj\b|playlist|band|music", .08, False, 14, "Book the DJ",
           [("Recommended", "DJ with lights for three hours", 1.0, "A dance floor that fills itself."),
            ("Cheaper", "Your playlist on a rented speaker", .25, "A friend runs the music."),
            ("Different vibe", "Live acoustic duo", 1.5, "Bollywood unplugged, then the playlist.")],
           "dj party"),
    "decor_lights": ("Lights", r"light|fairy|lamp|diya", .05, False, 7, "Order the lights",
                     [("Recommended", "Fairy lights and lanterns", 1.0, "Warm light along the walls and tables."),
                      ("Cheaper", "Borrowed string lights and diyas", .4, "Ask family; buy only the diyas."),
                      ("Different vibe", "Marigold-and-light canopy", 1.6, "A canopy of light over the main area.")],
                     "fairy lights"),
    "makeup": ("Makeup artist", r"make-?up|salon|hair", .06, False, 14, "Book the makeup artist",
               [("Recommended", "Makeup artist for the hosts", 1.0, "At home, two hours before guests arrive."),
                ("Cheaper", "Salon visit the same morning", .5, "Go to a salon nearby."),
                ("Different vibe", "Makeup and hair for the whole family", 2.0, "Everyone camera-ready.")],
               "makeup"),
    "anchor": ("Anchor", r"anchor|emcee|\bmc\b|host", .06, False, 14, "Book the anchor",
               [("Recommended", "Anchor for games and speeches", 1.0, "Keeps the evening moving."),
                ("Cheaper", "A cousin with a mic and a script", .1, "Free, and personal."),
                ("Different vibe", "Anchor plus a magician for the kids", 1.8, "Something for the children.")],
               "stage anchor"),
    "poojari": ("Poojari", r"poojari|pujari|pandit|priest|purohit|panditji", .08, False, 21, "Ask the temple about a poojari",
                [("Recommended", "A poojari from a temple near you", 1.0, "Ask at the temple; dakshina and samagri included."),
                 ("Cheaper", "Short ceremony, samagri bought yourself", .6, "The poojari only; you shop for samagri.")],
                "puja"),
    "cake": ("Cake", r"cake", .06, False, 5, "Order the cake",
             [("Recommended", "Classic cake for everyone", 1.0, "About 1 kg for every 12 guests."),
              ("Cheaper", "Pineapple cake", .7, "Plain and classic."),
              ("Different vibe", "Smaller cake plus cupcakes", 1.3, "Easy to hand round.")],
             "birthday cake"),
    "transport": ("Transport", r"transport|cab|bus|car|pick-?up", .06, False, 7, "Book the transport",
                  [("Recommended", "Two cabs for elders, on call", 1.0, "Pick-up and drop for those who need it."),
                   ("Cheaper", "Share a list of who can give lifts", .1, "Friends drive each other."),
                   ("Different vibe", "A mini-bus from one meeting point", 1.6, "Everyone arrives together.")],
                  "minibus"),
}

# Words people use for a stop -> the stop keys that can mean (first one present wins).
SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    (r"cake|cupcake", ("cake",)),
    (r"poojari|pujari|pandit|priest|purohit|puja|pooja|havan", ("puja", "poojari")),
    (r"cater|food|meal|lunch|dinner|menu|cook|thali|buffet", ("food",)),
    (r"venue|hall|place|space|location|banquet", ("venue",)),
    (r"decor|decoration|theme|flowers?", ("theme",)),
    (r"invit|card", ("invites",)),
    (r"\bdj\b|music|band|playlist", ("music", "dj", "sound")),
    (r"sound|speaker|stage|mic", ("sound", "music", "dj")),
    (r"schedule|run-?sheet|timeline|day-of|coordinator", ("day",)),
    (r"photo|camera", ("photographer",)),
    (r"mehendi|mehndi|henna", ("mehendi",)),
    (r"gift", ("gifts",)),
    (r"make-?up", ("makeup",)),
    (r"anchor|emcee", ("anchor",)),
]

# Other ways to do a moment on the event day, by its kind.
DAY_ALTS = {
    "arrive": ["Welcome drinks at the door", "Family greets each guest", "Photo corner as guests arrive"],
    "games": ["Antakshari", "Quiz in two teams", "Photo booth with props", "Dumb charades"],
    "cake": ["Dessert table instead of a cake", "Mithai platter", "Ice-cream counter"],
    "dinner": ["Buffet", "Served thali at the table", "Live chaat and dosa counters"],
    "music": ["Playlist and dancing", "Open mic for friends", "Live singer for an hour"],
    "ritual": ["Short puja with family", "Aarti together", "Blessings from the elders"],
}
DAY_STOP = {"cake": "cake", "ritual": "puja", "music": "music", "dinner": "food"}  # moment kind -> stop it needs


def find_stop(text: str, keys: list[str]) -> str | None:
    """Which of the plan's stops the words refer to (by key, then by synonym)."""
    low = text.lower()
    for k in keys:
        if re.search(rf"\b{re.escape(k)}\b", low):
            return k
    for pat, cands in SYNONYMS:
        if re.search(pat, low):
            hit = next((c for c in cands if c in keys), None)
            if hit:
                return hit
    return None


def extra_stop(text: str) -> str | None:
    """The catalogue entry that matches what the user wants to add, if any."""
    low = text.lower()
    return next((k for k, row in EXTRA_STOPS.items() if re.search(row[1], low)), None)


def modes_for(key: str) -> list[tuple[str, str, float, int, str, str]]:
    return MODES.get(MODE_STOPS.get(key, key), [])


def mode_row(key: str, mode: str | None) -> tuple[str, str, float, int, str, str] | None:
    return next((m for m in modes_for(key) if m[0] == mode), None)


# (min_days_left_inclusive, urgency, advice). First match from the top wins.
URGENCY = [
    (60, "relaxed", "Plenty of time. Lock the venue first, then the rest can follow at an easy pace."),
    (21, "on-track", "A comfortable runway. Book the long-lead items this week."),
    (7, "tight", "Under three weeks. Book everything with a long lead time in the next few days."),
    (0, "rush", "Less than a week. Favour places that bundle food and space, and send invites today."),
]

# month -> list of (flag, title, why, suggestion)
SEASON: dict[int, list[tuple[str, str, str, str]]] = {}
for _m in (6, 7, 8, 9):
    SEASON.setdefault(_m, []).append((
        "monsoon", "Monsoon month",
        "June to September is monsoon across most of India, so an open-air plan needs a covered backup.",
        "Prefer an indoor or covered venue, or budget a rain tent."))
for _m in (11, 12, 1, 2):
    SEASON.setdefault(_m, []).append((
        "peak", "Peak wedding season",
        "November to February is peak wedding season: good venues and caterers book out early and charge more.",
        "Book the venue and caterer as early as you can."))
for _m in (4, 5):
    SEASON.setdefault(_m, []).append((
        "heat", "Summer heat",
        "April and May are very hot in most of India, so afternoon events outdoors are hard on guests.",
        "Start in the evening and plan shade, fans and water."))

# (max_guests_inclusive, venue size, catering mode)
SIZE = [
    (15, "home or a private dining room", "home-cooked food or an order-in"),
    (40, "a café or restaurant private area", "a set menu or thali"),
    (120, "a small banquet hall or terrace", "a buffet"),
    (400, "a banquet hall or lawn", "a buffet with live counters"),
    (10**9, "a large lawn or convention hall", "a multi-counter buffet with a catering crew"),
]

# (regex, checklist text, why, stop)
FOOD_NEEDS = [
    (r"\bjain\b", "Separate Jain counter and utensils",
     "Jain guests avoid root vegetables, onion and garlic, so their food is cooked and served apart.", "food"),
    (r"no onion|without onion|no garlic|satvik|sattvic", "Label satvik dishes (no onion or garlic)",
     "Guests who skip onion and garlic need dishes they can trust at a glance.", "food"),
    (r"\bvegan\b", "At least two vegan mains and a vegan dessert",
     "Vegan guests skip dairy too, which rules out most Indian sweets and paneer dishes.", "food"),
    (r"gluten|coeliac|celiac", "Gluten-free dishes kept apart",
     "Gluten-free guests need dishes prepared away from rotis and naan.", "food"),
    (r"\bnuts?\b|peanut", "Allergen labels on every dish",
     "A nut allergy can be serious; every dish needs a clear label.", "food"),
    (r"halal", "Halal certification confirmed with the caterer",
     "Some guests eat only halal meat; ask the caterer for proof.", "food"),
    (r"non[- ]?veg", "Separate veg and non-veg counters",
     "Mixed menus need separate counters and serving spoons.", "food"),
    (r"(?<!non[- ])(?<!non)\bveg(etarian)?\b|pure veg", "Pure-veg kitchen confirmed with the caterer",
     "An all-vegetarian event needs a caterer who cooks no meat in the same kitchen.", "food"),
    (r"diabet|sugar[- ]?free", "A sugar-free dessert option",
     "Diabetic guests should have a dessert they can enjoy too.", "food"),
]
ACCESS_NEEDS = [
    (r"wheelchair|step[- ]?free|\blift\b|elevator|ground floor|ramp", "Step-free entrance and a lift confirmed",
     "Someone needs step-free access, so check the entrance, the lift and the washroom.", "venue"),
    (r"elder|senior|grand(ma|pa|parent|mother|father)|\bold\b", "Reserved seating for elders away from speakers",
     "Elders need chairs with backs, near the action but away from loud speakers.", "venue"),
    (r"hearing|deaf", "Seat guests with hearing loss near the front; caption any videos",
     "Guests with hearing loss follow speeches best from the front, with captions on videos.", "day"),
    (r"vision|blind|low sight", "A clear, well-lit path and a helper at the entrance",
     "Guests with low vision need a step-free, well-lit route and someone to guide them.", "venue"),
    (r"\bkids?\b|child|toddler|baby", "A kids' corner and early food for children",
     "Children eat earlier and need a safe corner to play.", "food"),
]


# ---------------------------------------------------------------- helpers

def inr(n: int | float) -> str:
    """Format rupees the Indian way: 1234567 -> '₹12,34,567'."""
    n = int(round(n))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        s = head + "," + tail
    return f"{sign}₹{s}"


def city_tier(area: str) -> int:
    """Return 1 (metro), 2 (large city) or 3 from the free-text area."""
    text = area.lower()
    if any(re.search(rf"\b{re.escape(c)}\b", text) for c in TIER1):
        return 1
    if any(re.search(rf"\b{re.escape(c)}\b", text) for c in TIER2):
        return 2
    return 3 if text.strip() else DEFAULT_TIER


def stops_for(event_type: EventType) -> list[tuple[str, str]]:
    return STOPS[event_type]


def days_until(event_date: dt.date | None, today: dt.date) -> int | None:
    return None if event_date is None else (event_date - today).days


# ---------------------------------------------------------------- rule families

def budget_band(p: Profile) -> tuple[str | None, Insight | None]:
    """Classify budget per guest against the event-type/city-tier table."""
    if not p.guests or p.budget is None:
        return None, None
    tier = city_tier(p.area)
    floor, typical, generous = PER_GUEST[p.type][tier]
    per = p.budget // p.guests
    where = f"a tier-{tier} city" if p.area else "a typical Indian city"
    if per < floor:
        band, level = "impossible", "block"
        why = (f"{inr(per)} a guest is below the {inr(floor)} floor for food, a space and basic decor "
               f"for a {p.type} in {where}.")
    elif per < typical:
        band, level = "tight", "warn"
        why = f"{inr(per)} a guest is tight: a catered {p.type} in {where} typically costs about {inr(typical)} a guest."
    elif per < generous:
        band, level = "comfortable", "info"
        why = f"{inr(per)} a guest is comfortable for a {p.type} in {where} (typical is about {inr(typical)})."
    else:
        band, level = "generous", "info"
        why = f"{inr(per)} a guest is generous for a {p.type} in {where}; {inr(generous)}+ allows premium choices."
    titles = {"impossible": "Budget can't cover this", "tight": "Tight budget",
              "comfortable": "Comfortable budget", "generous": "Generous budget"}
    return band, Insight(id="budget", family="budget", level=level, title=titles[band], why=why,
                         suggestions=BAND_ADVICE.get(band, []))


def budget_block(p: Profile) -> Block | None:
    """Three concrete ways out when the budget is below the floor."""
    band, _ = budget_band(p)
    if band != "impossible":
        return None
    assert p.guests and p.budget is not None
    floor = PER_GUEST[p.type][city_tier(p.area)][0]
    per = p.budget // p.guests
    fit = p.budget // floor
    raise_to = -(-p.guests * floor // 100) * 100
    # "invite fewer" only when the budget still feeds a small gathering at the floor; "Keep ₹1,
    # invite 4: about ₹0 a head" is no way out
    fewer = [Way(title=f"Keep {inr(p.budget)}, invite {fit}",
                 detail=f"Close friends and family only: about {inr(p.budget // fit)} a head.",
                 apply={"guests": fit})] if fit >= 4 else []
    return Block(
        type="budget",
        title=f"{inr(p.budget)} for {p.guests} guests is {inr(per)} a head." if per >= 10
        else f"{inr(p.budget)} won't cover {p.guests} guest{'s' if p.guests != 1 else ''}.",
        body=f"That won't stretch to food, a space and decor. Here are {'three' if fewer else 'two'} ways to make it work:",
        ways=fewer + [
            Way(title=f"Keep {p.guests} guests, raise to {inr(raise_to)}",
                detail=f"{inr(floor)} a head is the floor for food, a space and basic decor here.",
                apply={"budget": raise_to}),
            Way(title="Keep both, go potluck",
                detail="A clubhouse or community room, everyone brings a dish, you host tea and sweets.",
                apply={"food": "Potluck + hosted tea and sweets", "budget": max(p.budget, p.guests * 35)}),
        ],
    )


def date_block(p: Profile, today: dt.date) -> Block | None:
    """A date in the past gets two ways out: same day next year, or pick again."""
    if p.date is None or p.date >= today:
        return None
    try:
        nxt = p.date.replace(year=today.year if p.date.replace(year=today.year) >= today else today.year + 1)
    except ValueError:  # 29 Feb
        nxt = p.date.replace(year=today.year + 1, day=28)
    return Block(
        type="date",
        title=f"{p.date.day} {p.date:%b %Y} has already passed.",
        body=f"Today is {today.day} {today:%b %Y}. Did you mean a later date?",
        ways=[Way(title=f"Use {nxt.day} {nxt:%b %Y}", detail="Same day, next time it comes round.",
                  apply={"date": nxt.isoformat()}),
              Way(title="Pick another date", detail="I'll ask again.", apply=None)],
    )


def urgency(p: Profile, today: dt.date) -> tuple[str | None, list[str], list[str], Insight | None]:
    """Days left -> urgency level, the order to book things in, and what is already late."""
    d = days_until(p.date, today)
    if d is None:
        return None, [], [], None
    lead = LEAD[p.type]
    order = sorted(lead, key=lambda k: -lead[k])
    late = [k for k in order if lead[k] > d]
    if d < 0:
        return "past", order, order, Insight(
            id="timing", family="timing", level="block", title="Date has passed",
            why="The date is in the past, so nothing can be booked for it.")
    level, advice = next((name, adv) for lim, name, adv in URGENCY if d >= lim)
    labels = dict(STOPS[p.type])
    first = next((k for k in order if k not in late), order[-1])
    parts = [f"{d} days to go: {advice}"]
    if late:
        names = ", ".join(labels[k].lower() for k in late)
        parts.append(f"Usually booked earlier than this: {names}. Treat these as urgent.")
    parts.append(f"Book the {labels[first].lower()} first; it needs about {lead[first]} days' notice.")
    return level, order, late, Insight(
        id="timing", family="timing", level="warn" if late or level in ("tight", "rush") else "info",
        title={"relaxed": "Plenty of time", "on-track": "On track", "tight": "Short runway",
               "rush": "Very little time"}[level],
        why=" ".join(parts))


def season(p: Profile) -> tuple[list[str], list[Insight]]:
    """Month of the event -> monsoon / peak wedding season / summer heat flags."""
    if p.date is None:
        return [], []
    flags, out = [], []
    for flag, title, why, tip in SEASON.get(p.date.month, []):
        flags.append(flag)
        level = "warn" if flag == "monsoon" or (flag == "peak" and p.type == "wedding") else "info"
        out.append(Insight(id=f"season-{flag}", family="season", level=level, title=title, why=why,
                           suggestions=[tip]))
    return flags, out


def size(p: Profile) -> tuple[str | None, str | None, Insight | None]:
    """Guest count -> venue size and catering mode."""
    if not p.guests:
        return None, None, None
    venue, catering = next((v, c) for lim, v, c in SIZE if p.guests <= lim)
    servers = max(1, -(-p.guests // 20))
    why = (f"{p.guests} guests fit {venue}, served as {catering}. "
           f"Plan about {servers} server{'s' if servers > 1 else ''} (one per 20 guests).")
    return venue, catering, Insight(id="size", family="size", level="info", title="Size and catering", why=why)


def needs(p: Profile) -> tuple[list[ChecklistItem], list[Insight]]:
    """Dietary and accessibility answers -> mandatory checklist items."""
    items: list[ChecklistItem] = []
    for text, table, prefix in ((p.food, FOOD_NEEDS, "food"), (p.access, ACCESS_NEEDS, "access")):
        low = text.lower()
        if not low or re.search(r"\bno (special )?needs?\b|\bnone\b", low):
            continue
        for i, (pat, item, why, stop) in enumerate(table):
            if re.search(pat, low):
                items.append(ChecklistItem(id=f"{prefix}-{i}", text=item, why=why, stop=stop))
    # "Veg" also matches inside "veg and non-veg": keep only the more specific item.
    ids = {c.id for c in items}
    nonveg = f"food-{[r[1] for r in FOOD_NEEDS].index('Separate veg and non-veg counters')}"
    pureveg = f"food-{[r[1] for r in FOOD_NEEDS].index('Pure-veg kitchen confirmed with the caterer')}"
    if nonveg in ids:
        items = [c for c in items if c.id != pureveg]
    insights = [Insight(id=f"need-{c.id}", family="needs", level="info", title=c.text, why=c.why) for c in items]
    return items, insights


# ---------------------------------------------------------------- aggregate

@dataclass
class RulesResult:
    """Everything the planner and UI need from the rules, plus the explanations."""

    tier: int
    per_guest: int | None
    band: str | None
    days_left: int | None
    urgency: str | None
    book_order: list[str]
    late: list[str]
    season: list[str]
    venue_size: str | None
    catering: str | None
    mandatory: list[ChecklistItem]
    split: dict[str, float]
    lead: dict[str, int]
    insights: list[Insight] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)

    def stop_rule(self, key: str, p: Profile) -> str:
        """The deterministic rule behind a stop, shown under "Why?"."""
        labels = dict(STOPS[p.type])
        share = self.split.get(key, 0)
        parts = []
        if p.budget and share < SPLIT[p.type].get(key, 0) - 1e-9:  # sized below its share: the budget is lavish
            parts.append(f"Rule: {labels.get(key, key)} is sized at about {inr(p.budget * share)}, what a lavish "
                         f"{p.type} needs; the rest of your budget stays spare.")
        elif p.budget:
            parts.append(f"Rule: {labels.get(key, key)} gets about {round(share * 100)}% of the budget "
                         f"({inr(p.budget * share)}), with {round(BUFFER * 100)}% kept spare.")
        if key == "venue":
            if self.band and self.per_guest is not None:
                parts.append(next(i.why for i in self.insights if i.id == "budget"))
            if self.venue_size:
                parts.append(f"{p.guests} guests fit {self.venue_size}.")
            if "monsoon" in self.season:
                parts.append("Monsoon month, so the venue must be covered.")
        if key == "food" and self.catering:
            parts.append(f"{p.guests} guests are best served as {self.catering}.")
        for c in self.mandatory:
            if c.stop == key:
                parts.append(f"Must have: {c.text.lower()}.")
        if key in self.lead and self.days_left is not None:
            late = " That is already past, so it is urgent." if key in self.late else ""
            parts.append(f"Book about {self.lead[key]} days ahead.{late}")
        return " ".join(parts)


LAVISH = 2  # estimates stop growing at twice the "generous" per-guest figure; the rest stays spare


def planned_split(p: Profile) -> tuple[dict[str, float], int | None]:
    """The budget shares the estimates are sized from, and the most worth planning for when the budget
    is far beyond what the event needs (₹10 crore for 30 guests must not make a WhatsApp invite cost
    ₹40 lakh, nor one guest's dinner ₹14,400). None when the whole budget is used."""
    split = dict(SPLIT[p.type])
    if not p.guests or not p.budget:
        return split, None
    ceiling = p.guests * PER_GUEST[p.type][city_tier(p.area)][2] * LAVISH
    if p.budget <= ceiling:
        return split, None
    ratio = ceiling / p.budget
    return {k: v * ratio for k, v in split.items()}, ceiling


def evaluate(p: Profile, today: dt.date) -> RulesResult:
    """Run every rule family over the profile."""
    band, b_ins = budget_band(p)
    split, ceiling = planned_split(p)
    if ceiling and b_ins is not None:
        b_ins = b_ins.model_copy(update={"why": f"{b_ins.why} I sized the estimates for about {inr(ceiling)} "
                                                f"({inr(ceiling // p.guests)} a guest); the rest of your budget stays spare."})
    level, order, late, t_ins = urgency(p, today)
    flags, s_ins = season(p)
    venue, catering, z_ins = size(p)
    mandatory, n_ins = needs(p)
    insights = [i for i in (b_ins, t_ins) if i] + s_ins + ([z_ins] if z_ins else []) + n_ins
    blocks = [b for b in (budget_block(p), date_block(p, today)) if b]
    return RulesResult(
        tier=city_tier(p.area), per_guest=(p.budget // p.guests) if p.guests and p.budget is not None else None,
        band=band, days_left=days_until(p.date, today), urgency=level, book_order=order, late=late,
        season=flags, venue_size=venue, catering=catering, mandatory=mandatory,
        split=split, lead=dict(LEAD[p.type]), insights=insights, blocks=blocks,
    )
