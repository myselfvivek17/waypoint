"""Reading a revision said in one sentence: guests, budget and date, without a model.

"50 more guests, budget down to ₹35k and the party is two weeks earlier" becomes three changes with
absolute values (80 guests, ₹35,000, 1 Nov), each titled in the user's terms. The sentence is split
into clauses; every clause is read for a date, a guest change and a budget change, in that order,
each reader removing the words it understood. Clauses about anything else ("no cake", "add a
photographer") are handed back untouched for the other readers and, failing those, the model.

Vague amounts are never guessed silently: "the budget decreased a bit" is read as 10% less, and the
assumption is said out loud so the user can correct it.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .intake import parse_date, parse_money
from .rules import inr
from .schemas import Profile

WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
         "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "couple": 2,
         "few": 3, "dozen": 12}
TENS = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?"
NUM = (rf"(?:\d+(?:\.\d+)?|(?:a\s+)?(?:couple(?:\s+of)?|few|dozen)|{TENS}|one|two|three|four|five|six|seven|eight|nine|"
       r"ten|eleven|twelve|fifteen|hundred|an?)")
GUEST = r"(?:guests?|people|persons?|pax|heads|attendees|invitees|friends|relatives|members|adults|folks|kids|children)"
UNIT = r"(?:days?|weeks?|wks?|months?)"

# "two weeks earlier", "a week later"
SHIFT = re.compile(rf"\b(?P<n>{NUM})\s*(?P<u>{UNIT})\s+(?P<w>earlier|sooner|before|ahead|early|later|after|late|out|back)\b")
# "postpone by 3 days", "bring it forward by a week", "push the party back two weeks"
SHIFT_VERB = re.compile(rf"\b(?P<v>prepone[ds]?|advance[ds]?|bring|brought|pull(?:ed)?|move[ds]?|shift(?:ed)?|"
                        rf"postpone[ds]?|delay(?:ed)?|push(?:ed)?|put)(?:\s+(?:it|everything|the\s+\w+))?"
                        rf"(?:\s+(?P<p>forward|up|back|out|off))?\s+(?:by\s+)?(?P<n>{NUM})\s*(?P<u>{UNIT})\b")
EARLY_WORDS = {"earlier", "sooner", "before", "ahead", "early"}
EARLY_VERBS = re.compile(r"^(?:prepone|advance|bring|brought|pull)")
LATE_VERBS = re.compile(r"^(?:postpone|delay|push|put)")
MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE_TEXT = re.compile(rf"\d{{4}}-\d{{1,2}}-\d{{1,2}}|\b\d{{1,2}}(?:st|nd|rd|th)?\s*(?:of\s+)?{MONTH}(?:,?\s*\d{{4}})?|"
                       rf"\b{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s*\d{{4}})?|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?")
DATE_CUE = re.compile(r"\b(?:date|move|moved|shift|postpone|prepone|reschedule|on|to|now|instead|tomorrow)\b|"
                      r"\d{4}-\d{2}-\d{2}|\b\d{1,2}/\d{1,2}\b")

# "50 more guests", "+50 people", "10 fewer friends"
GUESTS_N = re.compile(rf"(?P<sign>[+-]\s*|\bplus\s+|\bminus\s+)?\b(?P<n>{NUM})\s+"
                      rf"(?P<q>more\s+|extra\s+|additional\s+|new\s+|fewer\s+|less\s+)?{GUEST}\b")
# "guests down to 40", "guests up by 10", "people count is 75"
GUESTS_TO = re.compile(rf"\b{GUEST}\s+(?:count\s+)?(?P<how>(?:is|are|to|now|up|down|went up|went down|increased?|decreased?|"
                       rf"reduced?|by)(?:\s+(?:to|by))?)\s+(?P<n>{NUM})\b")
UP = re.compile(r"\b(?:more|extra|additional|another|add(?:ed|ing)?|plus|invite[ds]?|up|increase[ds]?|raise[ds]?|"
                r"higher|grow[ns]?|new)\b")
DOWN = re.compile(r"\b(?:fewer|less|lower|minus|remove[ds]?|drop(?:ped)?|cut|reduce[ds]?|decrease[ds]?|down|tighter|"
                  r"shr[iu]nk|trim(?:med)?|can'?t come|cancell?ed|not coming|smaller)\b")
ABSOLUTE_LEAD = re.compile(r"\b(?:to|is|are|now|for|of|be|just|only|total|expecting|expect)\s*$")

BUDGET_WORD = re.compile(r"\b(?:budget|money|funds|spend(?:ing)?)\b")
SPOKEN_AMOUNT = re.compile(rf"\b(?P<n>(?:{TENS}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|"
                           rf"hundred)(?:\s+and\s+a\s+half)?)\s+(?P<u>thousand|lakhs?|lacs?|crores?)\b")
MONEY = re.compile(r"budget|money|funds|spend|₹|\binr\b|\d\s*(?:k|lakhs?|lacs?|l|crores?|cr|thousand)\b|%|percent|per cent")
MONEY_TEXT = re.compile(r"(?:₹\s*)?\d+(?:\.\d+)?\s*(?:k|lakhs?|lacs?|l|crores?|cr|thousand)?\b")
FILLER = re.compile(r"(?i)^(?:actually|ok(?:ay)?|so|oh|also|and|but|then|update|change|hey|hi|well)\b[\s,:-]*")
VAGUE_SHARE = 10  # "a bit" less, in percent: said out loud whenever it is used
VAGUE_NOTE = "You didn't say how much the budget"


def number(word: str) -> float | None:
    """'50', '2.5', 'fifty', 'twenty-five', 'a couple of', 'a dozen' -> the number."""
    w = re.sub(r"\s+of$", "", word.strip().lower())
    if w not in ("a", "an"):
        w = re.sub(r"^a\s+", "", w)
    try:
        return float(w)
    except ValueError:
        parts = re.split(r"[\s-]+", w)
        return float(sum(WORDS[p] for p in parts)) if all(p in WORDS for p in parts) else None


@dataclass
class Revision:
    """What the sentence changes. Values are absolute; ``said`` keeps the user's terms for titles."""

    guests: int | None = None
    budget: int | None = None
    date: dt.date | None = None
    said: dict[str, str] = field(default_factory=dict)  # guests/budget/date -> title in the user's terms
    rest: list[str] = field(default_factory=list)       # clauses about something else
    assumed: list[str] = field(default_factory=list)    # assumptions, said out loud
    problems: list[str] = field(default_factory=list)   # changes that could not be made, and why

    @property
    def found(self) -> bool:
        return self.guests is not None or self.budget is not None or self.date is not None

    def ops(self) -> list[dict]:
        """The changes as operations for choices.run_ops: absolute values, titles in ``text``."""
        out = []
        if self.guests is not None:
            out.append({"op": "set_guests", "amount": self.guests, "text": self.said["guests"]})
        if self.budget is not None:
            out.append({"op": "set_budget", "amount": self.budget, "text": self.said["budget"]})
        if self.date is not None:
            out.append({"op": "move_date", "date": self.date.isoformat(), "text": self.said["date"]})
        return out


def clauses(text: str) -> list[str]:
    """Split on commas, semicolons and joining words; digit-group commas ("35,000") stay, and so does
    the user's own casing (a clause about something else is passed on verbatim)."""
    t = re.sub(r"(?<=\d),(?=\d)", "", text.strip())
    t = re.sub(r"(?i)\b(?:rs\.?|inr)\s*(?=\d)", "₹", t)
    out = []
    for part in re.split(r"(?i)[,;]|\s(?:and(?!\s+a\s+half)|but|also|then|plus(?!\s*\d)|while)\s|\.(?:\s|$)", t):
        part = part.strip(" .!?")
        while FILLER.match(part) and FILLER.sub("", part, count=1) != part:
            part = FILLER.sub("", part, count=1).strip()
        if part:
            out.append(part)
    return out


def _months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    for day in (d.day, 30, 29, 28):
        try:
            return dt.date(d.year + y, m + 1, day)
        except ValueError:
            continue
    return d


def _unit_words(n: int, unit: str) -> str:
    unit = "week" if unit.startswith("w") else "month" if unit.startswith("m") else "day"
    return f"a {unit}" if n == 1 else f"{n} {unit}s"


def read_date(c: str, p: Profile, today: dt.date, rev: Revision) -> str:
    """'two weeks earlier', 'postpone by 3 days', 'a week later', 'on 1 Nov', 'move it to 6 Dec'."""
    m = SHIFT.search(c)
    sign = None
    if m:
        sign = -1 if m["w"] in EARLY_WORDS else 1
    else:
        m = SHIFT_VERB.search(c)
        if m:
            part, verb = m["p"] or "", m["v"]
            sign = -1 if part in ("forward", "up") or (not part and EARLY_VERBS.match(verb)) else \
                1 if part in ("back", "out", "off") or LATE_VERBS.match(verb) else None
    if m and sign is not None:
        n = number(m["n"])
        if not n or p.date is None:
            if p.date is None:
                rev.problems.append("The plan has no date yet, so there was nothing to move.")
            return c.replace(m.group(0), " ")
        n = int(n)
        new = _months(p.date, sign * n) if m["u"].startswith("m") else \
            p.date + dt.timedelta(days=sign * n * (7 if m["u"].startswith("w") else 1))
        _set_date(new, f"{_unit_words(n, m['u'])} {'earlier' if sign < 0 else 'later'}", today, rev)
        return c.replace(m.group(0), " ")
    if DATE_CUE.search(c) or re.search(rf"\b{MONTH}", c):
        new = parse_date(c, today)
        if new is not None:
            _set_date(new, "", today, rev)
            return DATE_TEXT.sub(" ", c, count=1)
    return c


def _set_date(new: dt.date, how: str, today: dt.date, rev: Revision) -> None:
    label = f"{new.day} {new:%b}"
    if new < today:
        rev.problems.append(f"{label} has already passed, so I kept the date as it was.")
        return
    rev.date = new
    rev.said["date"] = f"{how} ({label})" if how else f"move to {label}"


def read_guests(c: str, p: Profile, rev: Revision) -> str:
    """'50 more guests', '+50 people', '10 fewer', 'for 80 people', 'guests down to 40', 'double the guests'."""
    old = p.guests or 0
    if old and re.search(rf"\b(?:double|twice)\b.*\b{GUEST}\b|\b{GUEST}\b.*\bdoubled?\b", c):
        new = min(5000, old * 2)
        rev.guests, rev.said["guests"] = new, f"twice the guests ({old} → {new})"
        return re.sub(rf"\b(?:doubled?|twice)\b|\b{GUEST}\b", " ", c)
    m = GUESTS_N.search(c)
    if m:
        lead, q, sign = c[:m.start()], (m["q"] or "").strip(), (m["sign"] or "").strip()
        absolute = bool(ABSOLUTE_LEAD.search(lead)) and not q and not sign
        up = sign in ("+", "plus") or q in ("more", "extra", "additional", "new") or (not q and UP.search(c))
        down = sign in ("-", "minus") or q in ("fewer", "less") or (not q and DOWN.search(c))
    else:
        m = GUESTS_TO.search(c)
        if not m:
            return c
        how = m["how"]
        absolute = not how.endswith("by")
        up, down = bool(UP.search(c) or "increase" in how), bool(DOWN.search(c) or "decrease" in how)
    n = number(m["n"])
    if not n or n < 1:
        return c
    n = int(n)
    if not absolute and up and not down:
        new, word = old + n, f"{n} more guests"
    elif not absolute and down and not up:
        new, word = old - n, f"{n} fewer guests"
    else:
        new, word = n, f"{n} guests"
    new = max(1, min(5000, new))
    rev.guests, rev.said["guests"] = new, f"{word} ({old} → {new})" if old else word
    return c.replace(m.group(0), " ")


def spoken_amounts(c: str) -> str:
    """'thirty-two thousand' -> '₹32000', 'two and a half lakh' -> '₹250000': said in words, still money."""
    def num(m: re.Match) -> str:
        half = "and a half" in m["n"]
        n = number(re.sub(r"\s+and\s+a\s+half", "", m["n"]))
        if n is None:
            return m.group(0)
        unit = {"thousand": 1e3, "lakh": 1e5, "lac": 1e5, "crore": 1e7}[m["u"].rstrip("s")]
        return f"₹{int((n + (.5 if half else 0)) * unit)}"
    return SPOKEN_AMOUNT.sub(num, c)


def read_budget(c: str, p: Profile, rev: Revision) -> str:
    """'budget down to ₹35k', '₹1.5 lakh', 'cut 20%', 'budget up by 10k', 'budget decreased a bit', 'half',
    'thirty-two thousand', 'the money is tighter'."""
    old = p.budget or 0
    c = spoken_amounts(c)
    said_budget = bool(BUDGET_WORD.search(c))
    if not MONEY.search(c):
        return c
    up, down = bool(UP.search(c)), bool(DOWN.search(c))
    pct = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)", c)
    if pct and old and (up != down or said_budget):
        n = min(90.0, float(pct[1]))
        rise = up and not down
        new = int(round(old * (1 + n / 100 if rise else 1 - n / 100) / 100) * 100)
        rev.budget, rev.said["budget"] = new, f"budget {'up' if rise else 'down'} {n:g}% ({inr(old)} → {inr(new)})"
        return c.replace(pct[0], " ")
    if old and said_budget and re.search(r"\bhal(?:f|ve[sd]?)\b", c):
        new = int(round(old / 2 / 100) * 100)
        rev.budget, rev.said["budget"] = new, f"half the budget ({inr(old)} → {inr(new)})"
        return " "
    amount = parse_money(c, loose=said_budget)
    if amount is not None and amount >= 500:
        by = re.search(r"\bby\b", c) and not re.search(r"\b(?:to|is|now|of)\s*₹?\s*\d", c)
        if by and down and not up:
            new, word = old - amount, f"budget down by {inr(amount)}"
        elif by and up and not down:
            new, word = old + amount, f"budget up by {inr(amount)}"
        else:
            new, word = amount, f"budget {'down to' if amount < old else 'up to' if amount > old else 'stays at'} {inr(amount)}"
        new = max(1000, min(500_000_000, new))
        rev.budget, rev.said["budget"] = new, word
        rev.assumed = [a for a in rev.assumed if not a.startswith(VAGUE_NOTE)]  # an exact amount replaces a guess
        return MONEY_TEXT.sub(" ", c, count=1)
    if old and said_budget and (up != down or re.search(r"\btight(?:er)?\b", c)):
        up = up and not down
        new = int(round(old * (1 + VAGUE_SHARE / 100 if up else 1 - VAGUE_SHARE / 100) / 100) * 100)
        rev.budget = new
        rev.said["budget"] = f"budget {'up' if up else 'down'} about {VAGUE_SHARE}% ({inr(old)} → {inr(new)})"
        rev.assumed.append(f"{VAGUE_NOTE} {'went up' if up else 'dropped'}, so I took "
                           f"{VAGUE_SHARE}%: {inr(new)}. Tell me the amount if it's different.")
        return " "
    return c


def read(text: str, p: Profile, today: dt.date) -> Revision:
    """Every guest, budget and date change in ``text``; clauses about anything else go to ``rest``."""
    rev = Revision()
    for c in clauses(text):
        before = (rev.guests, rev.budget, rev.date, len(rev.problems))
        left = read_date(c.lower(), p, today, rev)
        left = read_guests(left, p, rev)
        read_budget(left, p, rev)
        if (rev.guests, rev.budget, rev.date, len(rev.problems)) == before:
            rev.rest.append(c)
    return rev
