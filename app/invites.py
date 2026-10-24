"""Invitation text: three options (warm, fun, formal) in English, Hindi or Telugu.

The model writes them from the plan's facts only; code checks each one (right script, the real
date, no template slots, a length cap) and fills any gap from deterministic templates, which are
also the whole answer when no model is available. The card itself is drawn in the browser; user
photos never reach the server.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .schemas import EventType, Source, Strict
from .validators import clip

Tone = Literal["warm", "fun", "formal"]
Lang = Literal["en", "hi", "te"]
TONES: tuple[Tone, ...] = ("warm", "fun", "formal")
MAX_CHARS = 420

SCRIPT = {"hi": re.compile(r"[ऀ-ॿ]"), "te": re.compile(r"[ఀ-౿]")}
LANG_NAME = {"en": "English", "hi": "Hindi (Devanagari script)", "te": "Telugu (Telugu script)"}

MONTHS = {
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "te": ["జనవరి", "ఫిబ్రవరి", "మార్చి", "ఏప్రిల్", "మే", "జూన్", "జూలై", "ఆగస్టు", "సెప్టెంబర్", "అక్టోబర్", "నవంబర్",
           "డిసెంబర్"],
}
WEEKDAYS = {
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "te": ["సోమవారం", "మంగళవారం", "బుధవారం", "గురువారం", "శుక్రవారం", "శనివారం", "ఆదివారం"],
}
RSVP = {
    "en": "Please RSVP by replying to this message.",
    "hi": "कृपया इस संदेश का जवाब देकर बताएं कि आप आ रहे हैं।",
    "te": "దయచేసి ఈ సందేశానికి జవాబిచ్చి మీ రాకను తెలియజేయండి.",
}
# {title} {when} {time} {where} {rsvp} {host}
TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "warm": "{title}! We would love you to join us on {when} at {time}, {where}. {host}{rsvp}",
        "fun": "Clear your calendar! 🎉 {title} is happening on {when}, {time}, {where}. Come hungry, leave happy. {host}{rsvp}",
        "formal": "You are cordially invited to {title} on {when} at {time}, {where}. {host}{rsvp}",
    },
    "hi": {
        "warm": "{title} पर आपका हार्दिक स्वागत है! {when}, {time}, {where}। आपके आने से हमारी खुशी दोगुनी होगी। {host}{rsvp}",
        "fun": "तैयार हो जाइए! 🎉 {title}: {when}, {time}, {where}। मस्ती, खाना और ढेर सारी यादें! {host}{rsvp}",
        "formal": "आपको सादर आमंत्रित किया जाता है: {title}, दिनांक {when}, समय {time}, स्थान {where}। {host}{rsvp}",
    },
    "te": {
        "warm": "{title}కి మీకు హృదయపూర్వక ఆహ్వానం! {when}, {time}, {where}. మీరు వస్తే మాకు ఎంతో ఆనందం. {host}{rsvp}",
        "fun": "రెడీ అవ్వండి! 🎉 {title}: {when}, {time}, {where}. ఆటలు, పాటలు, విందు! {host}{rsvp}",
        "formal": "{title} సందర్భంగా మిమ్మల్ని సాదరంగా ఆహ్వానిస్తున్నాము. తేదీ: {when}, సమయం: {time}, స్థలం: {where}. {host}{rsvp}",
    },
}
HOST = {"en": "With love, {host}. ", "hi": "सप्रेम, {host}। ", "te": "ప్రేమతో, {host}. "}
AT_HOME = {"en": "at our home", "hi": "हमारे घर पर", "te": "మా ఇంట్లో"}

INSTRUCTIONS = """
You write invitation messages for a celebration in India. The data block has the facts (title,
occasion, date, time, place, host, rsvp) and language. Write three different invitations in that
language and script: "warm", "fun" (one emoji at most) and "formal". Each under 60 words, ready to
send on WhatsApp. Use only the facts given: the exact date and time, the place as given, the host
and the RSVP line if present. No placeholders, no brackets, no invented names, places or phone numbers.
Reply with JSON only: {"invites": [{"tone": "warm", "text": ""}, {"tone": "fun", "text": ""},
 {"tone": "formal", "text": ""}]}
"""


class InviteIn(Strict):
    title: str = Field(min_length=1, max_length=80)
    type: EventType = "birthday"
    date: dt.date | None = None
    time: str = Field("", max_length=12)
    place: str = Field("", max_length=120)    # the venue's name, or empty when it's at home
    at_home: bool = False
    host: str = Field("", max_length=60)
    rsvp: str = Field("", max_length=120)
    language: Lang = "en"


class InviteOption(BaseModel):
    tone: Tone
    text: str


class InviteOut(BaseModel):
    language: Lang
    options: list[InviteOption]
    source: Source


class InviteDraft(BaseModel):
    invites: list = []


def when(d: dt.date | None, lang: Lang) -> str:
    if d is None:
        return {"en": "a date to be confirmed", "hi": "जल्द ही बताई जाने वाली तारीख", "te": "త్వరలో తెలిపే తేదీ"}[lang]
    if lang == "en":
        return f"{d:%A}, {d.day} {d:%B}"
    return f"{WEEKDAYS[lang][d.weekday()]}, {d.day} {MONTHS[lang][d.month - 1]}"


def facts(inp: InviteIn) -> dict[str, str]:
    lang = inp.language
    place = AT_HOME[lang] if inp.at_home or not inp.place else inp.place
    return {"title": inp.title, "when": when(inp.date, lang), "time": inp.time or "6:00 PM", "where": place,
            "host": HOST[lang].format(host=inp.host) if inp.host else "", "rsvp": inp.rsvp or RSVP[lang]}


def template(inp: InviteIn, tone: Tone) -> str:
    text = TEMPLATES[inp.language][tone].format(**facts(inp))
    return clip(re.sub(r"\s{2,}", " ", text), MAX_CHARS)


def fallback(inp: InviteIn) -> InviteDraft:
    return InviteDraft(invites=[{"tone": t, "text": template(inp, t)} for t in TONES])


def check(text: str, inp: InviteIn) -> str | None:
    """A model-written invite is used only if it is clean, in the right script and has the date."""
    text = clip(text, MAX_CHARS)
    if not text or re.search(r"[{}\[\]<>]|\bnull\b|\bNone\b|XX|\.\.\.\s*$", text):
        return None
    if inp.language in SCRIPT and len(SCRIPT[inp.language].findall(text)) < 10:
        return None
    if inp.date is not None and str(inp.date.day) not in text:
        return None
    return text


async def write(inp: InviteIn) -> InviteOut:
    """Three invitation texts; each falls back to its template if the model's is unusable."""
    data = {**facts(inp), "occasion": inp.type, "language": LANG_NAME[inp.language],
            "date_iso": inp.date.isoformat() if inp.date else None}
    res = await llm.ask("invite", INSTRUCTIONS, data, InviteDraft, fallback=lambda: fallback(inp),
                        max_tokens=900, timeout=25, within=32)
    by_tone: dict[str, str] = {}
    for row in res.value.invites if isinstance(res.value.invites, list) else []:
        if isinstance(row, dict) and row.get("tone") in TONES and row["tone"] not in by_tone:
            ok = check(str(row.get("text", "")), inp)
            if ok:
                by_tone[row["tone"]] = ok
    used_model = res.source == "ai" and len(by_tone) == len(TONES)
    options = [InviteOption(tone=t, text=by_tone.get(t) or template(inp, t)) for t in TONES]
    source: Source = "ai" if used_model else ("mock" if res.source == "mock" else "fallback")
    return InviteOut(language=inp.language, options=options, source=source)
