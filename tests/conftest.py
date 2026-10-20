"""Shared fixtures. Tests never touch the network: the LLM runs in mock mode."""

import datetime as dt
import os

os.environ["WP_LLM_MOCK"] = "1"
os.environ.pop("OPENROUTER_API_KEY", None)

import pytest  # noqa: E402

from app.schemas import Profile  # noqa: E402

TODAY = dt.date(2026, 10, 20)


@pytest.fixture
def today() -> dt.date:
    return TODAY


@pytest.fixture
def aditya() -> Profile:
    return Profile(type="birthday", title="Aditya's 22nd birthday", guests=30, budget=40000,
                   area="Gachibowli, Hyderabad", date=dt.date(2026, 11, 15),
                   who="Aditya, 22 — loves cricket and retro Bollywood", food="Veg + 2 Jain guests",
                   access="Lift or ground floor")


@pytest.fixture
def wedding() -> Profile:
    return Profile(type="wedding", title="A wedding in Jaipur", guests=250, budget=1200000,
                   area="Jaipur, Rajasthan", date=dt.date(2026, 12, 12),
                   who="The couple and both families — folk music, Rajasthani food",
                   food="Vegetarian", access="Seating for elders")
