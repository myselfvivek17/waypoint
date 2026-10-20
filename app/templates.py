"""Deterministic plan content, used in mock mode and whenever the model is unavailable.

Built only from the profile, the rules result and real places, so the offline plan is
still personal and explainable. It is also the gap-filler when a model reply is partial.
"""

from __future__ import annotations

import math
import re

from . import rules
from .places import Place
from .rules import RulesResult, inr
from .schemas import DraftDay, DraftDayItem, DraftOption, DraftPlan, DraftRipple, DraftStop, Profile

# (pattern, short tag, theme name, details, photo search)
INTERESTS = [
    (r"cricket", "cricket", "Cricket night", "stumps as table numbers and a scoreboard backdrop", "cricket stadium"),
    (r"bollywood|film|movie|cinema", "film", "Retro Bollywood", "a film-poster backdrop and marquee bulbs",
     "Bollywood film poster"),
    (r"football|soccer", "football", "Matchday", "team-colour bunting and a mini goal for photos", "football stadium"),
    (r"music|sing|guitar|antakshari", "music", "Music night", "vinyl-record table mats and a small stage corner",
     "music concert stage lights"),
    (r"flower|garden|plant", "flowers", "Garden florals", "fresh flower garlands and potted plants",
     "flower garland decoration"),
    (r"travel|trip", "travel", "Around the world", "luggage-tag place cards and a map backdrop", "vintage map"),
    (r"game|gaming", "games", "Game night", "board-game tables and pixel-art signs", "board games"),
    (r"book|read|poetry", "books", "Storybook", "book-stack centrepieces and quote cards", "library books"),
    (r"art|paint|draw", "art", "Art studio", "easels, paint palettes and a live canvas", "painting easel"),
    (r"folk|rajasthan", "folk", "Rajasthani folk", "block-print drapes, puppets and brass lamps",
     "Rajasthani puppets"),
    (r"danc", "dance", "Dance floor", "a lit dance floor and disco balls", "disco ball"),
]
DEFAULT_THEME = {
    "birthday": ("Marigold and fairy lights", "marigold strings, fairy lights and a photo corner", "fairy lights"),
    "wedding": ("Marigold and mirror-work mandap", "marigold, mirror-work cushions and brass lamps",
                "marigold decoration wedding"),
    "house-warming": ("Fresh flowers and diyas", "a flower rangoli, mango-leaf toran and diyas", "rangoli"),
    "community": ("Bunting and paper lanterns", "paper lanterns, bunting and a welcome arch", "paper lanterns"),
    "other": ("Marigold and fairy lights", "marigold strings, fairy lights and a photo corner", "fairy lights"),
}
KIND_PRICE = {"cafe": .8, "restaurant": 1.0, "community_centre": .6, "events_venue": 1.1, "banquet_hall": 1.1,
              "conference_centre": 1.2, "hotel": 1.3, "resort": 1.4, "park": .5, "garden": .7}
KIND_RANK = ["park", "community_centre", "cafe", "garden", "restaurant", "events_venue", "banquet_hall",
             "conference_centre", "hotel", "resort"]


def interests(p: Profile) -> list[tuple[str, str, str, str, str]]:
    text = f"{p.who} {p.vibe} {p.title}".lower()
    return [row for row in INTERESTS if re.search(row[0], text)]


def first_name(p: Profile) -> str:
    name = re.split(r"[,—–\-:(]", p.who or "")[0].strip()
    return "" if not name or re.match(r"(?i)^(my|our|the)\b", name) or len(name) > 30 else name


def area_short(p: Profile) -> str:
    return (p.area or "your area").split(",")[0].strip()


def is_veg(p: Profile) -> bool:
    low = p.food.lower()
    return bool(re.search(r"\bveg|jain|satvik|sattvic|no onion", low)) and not re.search(r"non[- ]?veg", low)


def _r100(n: float) -> int:
    return int(round(n / 100.0)) * 100


def _r5(n: float) -> int:
    return int(math.floor(n / 5.0)) * 5


def rank_venues(cands: list[Place], p: Profile, r: RulesResult) -> list[Place]:
    """Order real venues by fit: step-free when needed, veg-friendly when veg, then distance."""
    needs_step_free = any(c.id.startswith("access-0") for c in r.mandatory)
    veg = is_veg(p)

    def score(pl: Place) -> float:
        s = pl.dist_km
        if needs_step_free and pl.wheelchair == "yes":
            s -= 1.5
        if needs_step_free and pl.wheelchair == "no":
            s += 5
        if veg and pl.veg:
            s -= 0.8
        if "monsoon" in r.season and pl.outdoor:
            s += 3
        return s

    return sorted(cands, key=score)


def _place_option(pl: Place, kind: str, line: float, reason: str) -> DraftOption:
    return DraftOption(kind=kind, name=pl.name, price=_r100(line * KIND_PRICE.get(pl.kind, 1.0)),
                       place_id=pl.id, reason=reason, outdoor=pl.outdoor)


def venue_stop(p: Profile, r: RulesResult, cands: list[Place]) -> DraftStop:
    line = (p.budget or 0) * r.split["venue"]
    guests = p.guests or 0
    theme_line = (p.budget or 0) * r.split.get("theme", 0)
    where = area_short(p)
    monsoon = "monsoon" in r.season
    rain = DraftRipple(stop="day", note="Open-air venue in monsoon: keep a rain tent on standby.",
                       extra=_r100((p.budget or 0) * .03)) if monsoon else \
        DraftRipple(stop="day", note="Open-air evening: keep the speaker low after 10 pm.", extra=0)
    character = DraftRipple(stop="theme", note="The venue has its own character, so decor needs less.",
                            extra=-_r100(theme_line * .3))
    if p.type == "house-warming":
        return DraftStop(key="venue", pick=DraftOption(
            name="Your new home, living room and terrace", price=_r100(line),
            reason=f"Set up for {guests} guests with rented chairs, durries and a shamiana if needed.",
            why="A house-warming happens at home; the budget goes on seating, a canopy and cleaning."),
            options=[DraftOption(kind="Cheaper", name="Home, with borrowed seating", price=_r100(line * .5),
                                 reason="Neighbours' chairs and durries; only cleaning help is hired."),
                     DraftOption(kind="Different vibe", name="Home plus a building clubhouse", price=_r100(line * 1.6),
                                 reason="Puja at home, food and guests in the clubhouse.")])
    if not cands:
        size = r.venue_size or "a suitable venue"
        return DraftStop(key="venue", pick=DraftOption(
            name=f"{size[0].upper()}{size[1:]} near {where}", price=_r100(line),
            reason=f"Fits {guests} guests within the venue share of your budget.",
            why="I couldn't reach live place listings, so this is the kind of place to look for."),
            options=[DraftOption(kind="Cheaper", name=f"A home, terrace or clubhouse near {where}",
                                 price=_r100(line * .45), reason="Saves the hall rental; food is brought in."),
                     DraftOption(kind="Different vibe", name=f"An open-air garden near {where}", price=_r100(line * .9),
                                 reason="Open sky and room to spread out.", outdoor=True,
                                 ripple=rain)])
    ranked = rank_venues(cands, p, r)
    rec = ranked[0]
    used = {rec.id}
    opts: list[DraftOption] = []
    cheaper = next((c for c in ranked[1:] if KIND_RANK.index(c.kind) < KIND_RANK.index(rec.kind)), None) \
        or next((c for c in ranked[1:] if c.kind == rec.kind), None)
    if cheaper:
        o = _place_option(cheaper, "Cheaper", line * .8, f"{cheaper.label}, {cheaper.dist_km} km away. Simpler, "
                          "so it costs less.")
        o.price = min(o.price, _r100(line * .8))
        opts.append(o)
        used.add(cheaper.id)
    closer = next((c for c in sorted(cands, key=lambda c: c.dist_km) if c.id not in used and c.dist_km < rec.dist_km),
                  None)
    if closer:
        o = _place_option(closer, "Closer", line, f"{closer.label}, only {closer.dist_km} km away.")
        if closer.kind == "community_centre":
            o.ripple = DraftRipple(stop="food", note="Community halls rarely have a kitchen: the caterer needs a "
                                   "set-up crew.", extra=_r100((p.budget or 0) * .02))
        opts.append(o)
        used.add(closer.id)
    vibe = next((c for c in ranked if c.id not in used and c.outdoor and not monsoon), None) or \
        next((c for c in ranked if c.id not in used and c.kind != rec.kind), None)
    if vibe:
        o = _place_option(vibe, "Different vibe", line * 1.1, f"{vibe.label}, {vibe.dist_km} km away: a different "
                          "feel from the first pick.")
        o.ripple = rain if vibe.outdoor else character
        opts.append(o)
    reason = f"{rec.label}, {rec.dist_km} km from {where}. Fits {guests} guests."
    if rec.veg:
        reason += " Vegetarian-friendly."
    return DraftStop(key="venue", pick=DraftOption(
        name=rec.name, place_id=rec.id, price=_r100(line * KIND_PRICE.get(rec.kind, 1.0)),
        reason=reason, why=f"Closest good match for {r.venue_size or 'your group'}, ranked by distance and your needs.",
        outdoor=rec.outdoor), options=opts)


def theme_stop(p: Profile, r: RulesResult) -> DraftStop:
    line = (p.budget or 0) * r.split["theme"]
    likes = interests(p)
    base_name, base_detail, base_q = DEFAULT_THEME[p.type]
    if len(likes) >= 2:
        name = f"{likes[0][2]} meets {likes[1][2]}"
        detail, query = f"{likes[0][3]}, {likes[1][3]}", likes[0][4]
    elif likes:
        name, detail, query = likes[0][2], likes[0][3], likes[0][4]
    else:
        name, detail, query = base_name, base_detail, base_q
    who = first_name(p)
    why = (f"{who} loves {', '.join(x[1] for x in likes)}, so the decor leans into that."
           if who and likes else "A warm, flexible look that works in any venue.")
    alt = likes[1] if len(likes) >= 2 else INTERESTS[4] if not likes or likes[0][1] != "flowers" else INTERESTS[3]
    return DraftStop(key="theme", pick=DraftOption(name=name, price=_r100(line), reason=f"{detail[0].upper()}"
                                                   f"{detail[1:]}. Props are rented.", why=why, photo_query=query),
                     options=[DraftOption(kind="Cheaper", name=f"{base_name}, minimal", price=_r100(line * .6),
                                          reason="Calm and warm, with one photo corner.", photo_query=base_q),
                              DraftOption(kind="Different vibe", name=alt[2], price=_r100(line * 1.25),
                                          reason=f"{alt[3][0].upper()}{alt[3][1:]}.", photo_query=alt[4])])


def food_stop(p: Profile, r: RulesResult) -> DraftStop:
    guests = max(1, p.guests or 1)
    rate = _r5((p.budget or 0) * r.split["food"] / guests)
    veg = is_veg(p)
    jain = "jain" in p.food.lower()
    style = "thali counter" if guests <= 40 else "buffet" if guests <= 120 else "buffet with live counters"
    name = f"{'Veg ' if veg else ''}{style}{' with a Jain station' if jain else ''}"
    reason = f"{guests} guests{', all vegetarian' if veg else ''}{', with a separate Jain counter' if jain else ''}."
    q = "thali" if guests <= 40 else "Indian buffet"
    return DraftStop(key="food", pick=DraftOption(name=name[0].upper() + name[1:], price=rate, reason=reason,
                                                  why=f"{(r.catering or 'This format')[0].upper()}{(r.catering or 'This format')[1:]} keeps the per-guest cost inside the food share.",
                                                  photo_query=q),
                     options=[DraftOption(kind="Cheaper", name="Chaat, snacks and a rice dish", price=_r5(rate * .75),
                                          reason="Lighter, and easy to make Jain-friendly.", photo_query="chaat"),
                              DraftOption(kind="Closer", name=f"Home-style caterer in {area_short(p)}",
                                          price=_r5(rate * .9), reason="Local, so food arrives hot.",
                                          photo_query="Indian home cooked food"),
                              DraftOption(kind="Different vibe", name="Street-food live stations", price=_r5(rate * 1.2),
                                          reason="Pav bhaji, chaat and dosa counters. A livelier room.",
                                          photo_query="dosa")])


def cake_stop(p: Profile, r: RulesResult, bakeries: list[Place]) -> DraftStop:
    line = (p.budget or 0) * r.split["cake"]
    kg = max(1.0, math.ceil((p.guests or 10) / 12 * 2) / 2)
    egg = "eggless " if is_veg(p) else ""
    likes = interests(p)
    motif = {"cricket": "Cricket-pitch", "film": "Film-reel", "music": "Vinyl-record", "flowers": "Floral",
             "football": "Football-pitch"}.get(likes[0][1] if likes else "", "Classic")
    shop = bakeries[0] if bakeries else None
    return DraftStop(key="cake", pick=DraftOption(
        name=f"{motif} cake, {kg:g} kg {egg}".strip(), price=_r100(line), place_id=shop.id if shop else "",
        reason=f"{kg:g} kg gives each of {p.guests or 'your'} guests a generous slice."
               + (" Eggless, so everyone can have some." if egg else ""),
        why="About 1 kg for every 12 guests.", photo_query="birthday cake"),
        options=[DraftOption(kind="Cheaper", name=f"Pineapple {egg}cake, {max(1.0, kg - .5):g} kg",
                             price=_r100(line * .7), reason="Plain and classic.", photo_query="pineapple cake",
                             place_id=bakeries[1].id if len(bakeries) > 1 else ""),
                 DraftOption(kind="Different vibe", name="Smaller cake plus themed cupcakes", price=_r100(line * 1.3),
                             reason="Easy to hand round, and fun to decorate.", photo_query="cupcakes")])


def simple_stop(key: str, p: Profile, r: RulesResult) -> DraftStop:
    """Music, sound, puja, invites and the day-of schedule."""
    line = (p.budget or 0) * r.split[key]
    elders = any(c.id.startswith("access-1") for c in r.mandatory)
    likes = interests(p)
    if key == "music":
        rec = ("DJ with a live dhol team", "DJ for the dance floor, dhol for the baraat.", "dhol")
        opts = [("Cheaper", "DJ only", .65, "The baraat uses a recorded dhol track.", "DJ console"),
                ("Different vibe", "Live folk troupe", 1.35, "Folk singers and dancers. Unforgettable.", "folk dance")]
    elif key == "sound":
        rec = ("Speaker set, two mics and a small stage", "Enough for announcements and a few performances.",
               "stage lights")
        opts = [("Cheaper", "One speaker and a mic", .5, "Fine for announcements only.", "loudspeaker"),
                ("Different vibe", "Stage, lights and a local band", 1.6, "Turns it into a proper show.", "band stage")]
    elif key == "puja":
        rec = ("Griha pravesh puja with a local priest", "Priest, samagri and flowers for a morning puja.",
               "puja flowers")
        opts = [("Cheaper", "Short puja, samagri bought yourself", .6, "Priest only; you shop for samagri.", "diya"),
                ("Different vibe", "Puja plus a havan and bhajan group", 1.5, "A fuller morning with music.",
                 "havan")]
    elif key == "invites":
        rec = ("WhatsApp invite with an RSVP line" + (" and printed cards for elders" if elders else ""),
               "Easy to forward; replies come back to you.", "")
        opts = [("Cheaper", "Free template with your text", 0, "Edit a ready design yourself. No cost.", ""),
                ("Different vibe", "Printed cards, hand-delivered", 1.8, "A keepsake, but needs a week to print.", "")]
    else:
        games = "Cricket quiz, games" if likes and likes[0][1] == "cricket" else "Ice-breaker games"
        rec = ({"wedding": "Run-sheet for every function, with a coordinator",
                "house-warming": "Morning puja, then lunch and a house tour",
                "community": "Welcome, performances, food and a thank-you"}.get(p.type, f"{games}, cake, then dinner"),
               "Covers props, a speaker and a printed run-sheet.", "")
        opts = [("Cheaper", "A friend runs the day from a printed sheet", .4, "Keep one game; skip hired extras.", ""),
                ("Different vibe", "Hired emcee or coordinator", 2.0, "Someone else runs the room, so you can enjoy it.",
                 "")]
    return DraftStop(key=key, pick=DraftOption(name=rec[0], price=_r100(line), reason=rec[1],
                                               why=f"Sized to about {round(r.split[key] * 100)}% of your budget.", photo_query=rec[2]),
                     options=[DraftOption(kind=k, name=n, price=_r100(line * f), reason=why, photo_query=q)
                              for k, n, f, why, q in opts])


def days(p: Profile, r: RulesResult) -> list[DraftDay]:
    likes = interests(p)
    jain = "jain" in p.food.lower()
    inside = "monsoon" in r.season
    I = DraftDayItem  # noqa: E741 - short alias keeps the tables readable
    if p.type == "wedding":
        return [
            DraftDay(label="Mehendi", title="Mehendi: a slow afternoon", items=[
                I(time="2:00 PM", title="Artists arrive", note="Mehendi artists set up in the shade.", art="arrive"),
                I(time="4:00 PM", title="Chai and snacks", note="Light counters: kachori, chai, fruit.", art="dinner"),
                I(time="6:00 PM", title="Folk songs", note="Songs together while the mehendi sets.", art="music")]),
            DraftDay(label="Sangeet", title="Sangeet: music and dancing", items=[
                I(time="7:00 PM", title="Families welcome", note="Each side introduced with a short skit.", art="arrive"),
                I(time="8:00 PM", title="Performances", note="Cousins and friends take the stage.", art="music"),
                I(time="9:30 PM", title="Dinner and dancing", note="Dinner, then the DJ takes over.", art="dinner")]),
            DraftDay(label="Wedding", title="The wedding day", items=[
                I(time="4:30 PM", title="Baraat", note="Dhol and procession to the venue.", art="music"),
                I(time="6:00 PM", title="Varmala", note="The garland exchange, family around the stage.", art="ritual"),
                I(time="7:30 PM", title="Pheras", note="Under the mandap, with the pandit.", art="ritual"),
                I(time="9:00 PM", title="Dinner", note="Counters open; elders are served first.", art="dinner"),
                I(time="11:00 PM", title="Vidaai", note="A quiet goodbye at the gate.", art="arrive")]),
        ]
    if p.type == "house-warming":
        return [DraftDay(label="House-warming", title="The day, hour by hour", items=[
            I(time="8:00 AM", title="Puja begins", note="Priest arrives; family seated by the entrance.", art="ritual"),
            I(time="10:00 AM", title="Guests arrive", note="Welcome with sweets and a house tour.", art="arrive"),
            I(time="12:30 PM", title="Lunch", note="Served on banana leaves or plates." + (" Jain food first." if jain
                                                                                          else ""), art="dinner"),
            I(time="3:00 PM", title="Tea and goodbyes", note="Return gifts at the door.", art="arrive")])]
    if p.type == "community":
        return [DraftDay(label="Event day", title="The event, hour by hour", items=[
            I(time="5:00 PM", title="Gates open", note="Volunteers at the entrance with a sign-in sheet.", art="arrive"),
            I(time="6:00 PM", title="Performances", note="Kids first, then the adults' programme.", art="music"),
            I(time="7:30 PM", title="Food", note="Counters open; elders and children first.", art="dinner"),
            I(time="9:00 PM", title="Thank-you", note="Volunteers and sponsors thanked by name.", art="ritual")])]
    game = "Cricket quiz in two teams" if likes and likes[0][1] == "cricket" else "Ice-breaker games"
    return [DraftDay(label="The evening", title="The evening, hour by hour", items=[
        I(time="6:00 PM", title="Guests arrive", note="Welcome drink at the entrance." +
          (" Jain counter clearly marked." if jain else ""), art="arrive"),
        I(time="6:30 PM", title="Games", note=f"{game}{', indoors' if inside else ''}.", art="games"),
        I(time="7:15 PM", title="Cake", note="Lights down, cake, and a few short speeches.", art="cake"),
        I(time="8:00 PM", title="Dinner", note="Food counters open." + (" Jain station served first." if jain
                                                                        else ""), art="dinner"),
        I(time="9:00 PM", title="Dance floor", note="Guests request the last three songs.", art="music")])]


def invite(p: Profile, start: str) -> str:
    when = f"{p.date:%A}, {p.date.day} {p.date:%B}" if p.date else "Date to be confirmed"
    lines = [f"You're invited to {p.title or 'our celebration'}!", f"{when} · from {start}"]
    if p.area:
        lines.append(p.area)
    if p.food:
        lines.append(f"Food: {p.food}.")
    lines.append("Please RSVP by replying to this message.")
    return "\n".join(lines)


def draft_plan(p: Profile, r: RulesResult, venues: list[Place], bakeries: list[Place]) -> DraftPlan:
    """A complete, deterministic draft in the same shape the model is asked for."""
    stops: list[DraftStop] = []
    for key, _ in rules.stops_for(p.type):
        if key == "venue":
            stops.append(venue_stop(p, r, venues))
        elif key == "theme":
            stops.append(theme_stop(p, r))
        elif key == "food":
            stops.append(food_stop(p, r))
        elif key == "cake":
            stops.append(cake_stop(p, r, bakeries))
        else:
            stops.append(simple_stop(key, p, r))
    d = days(p, r)
    return DraftPlan(stops=stops, days=d, invite=invite(p, d[0].items[0].time))
