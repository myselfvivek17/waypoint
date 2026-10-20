"""Invitation text: three tones, three languages, model output checked, templates as fallback."""

import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import invites, llm, main

client = TestClient(main.app)
BASE = {"title": "Aditya's 22nd birthday", "type": "birthday", "date": "2026-11-15", "time": "6:00 PM",
        "place": "Olive Kitchen, Gachibowli", "host": "Priya", "rsvp": ""}


@pytest.mark.parametrize("lang,script", [("en", None), ("hi", invites.SCRIPT["hi"]), ("te", invites.SCRIPT["te"])])
def test_three_tones_in_each_language(lang, script):
    r = client.post("/api/invite", json={**BASE, "language": lang})
    assert r.status_code == 200
    out = r.json()
    assert [o["tone"] for o in out["options"]] == ["warm", "fun", "formal"] and out["source"] == "mock"
    for o in out["options"]:
        assert "15" in o["text"] and "Olive Kitchen" in o["text"] and "Priya" in o["text"]
        assert "{" not in o["text"] and len(o["text"]) <= invites.MAX_CHARS
        if script:
            assert len(script.findall(o["text"])) > 10


def test_hindi_and_telugu_dates_are_localised():
    d = dt.date(2026, 11, 15)
    assert invites.when(d, "hi") == "रविवार, 15 नवंबर" and invites.when(d, "te") == "ఆదివారం, 15 నవంబర్"
    assert invites.when(d, "en") == "Sunday, 15 November"


def test_at_home_and_own_rsvp_line():
    out = client.post("/api/invite", json={**BASE, "place": "", "at_home": True, "rsvp": "RSVP to Priya by 10 Nov"}).json()
    assert all("at our home" in o["text"] and "RSVP to Priya by 10 Nov" in o["text"] for o in out["options"])


def test_bad_model_invites_are_replaced_by_templates(monkeypatch):
    async def model(*a, **k):
        return llm.Result(invites.InviteDraft(invites=[
            {"tone": "warm", "text": "Come to {title} on [date]!"},                       # template slots
            {"tone": "fun", "text": "A party on 15 November at Olive Kitchen! 🎉 See you there."},
            {"tone": "formal", "text": "You are invited on a date to be decided."}]), "ai")   # no date
    monkeypatch.setattr(invites.llm, "ask", model)
    out = asyncio.run(invites.write(invites.InviteIn(**{**BASE, "date": dt.date(2026, 11, 15)})))
    texts = {o.tone: o.text for o in out.options}
    assert texts["fun"].startswith("A party on 15 November") and "{" not in texts["warm"] and "15" in texts["formal"]
    assert out.source == "fallback"


def test_wrong_script_is_rejected():
    inp = invites.InviteIn(**{**BASE, "date": dt.date(2026, 11, 15), "language": "hi"})
    assert invites.check("Join us on 15 November at Olive Kitchen!", inp) is None
    assert invites.check("15 नवंबर को ओलिव किचन में हमारे साथ जश्न मनाइए!", inp)


def test_invite_input_is_validated():
    assert client.post("/api/invite", json={**BASE, "language": "fr"}).status_code == 422
    assert client.post("/api/invite", json={**BASE, "title": "x" * 200}).status_code == 422
