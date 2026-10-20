"""Both themes keep text at WCAG AA contrast (4.5:1), checked from the theme variables in app.css."""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text()


def theme(selector: str) -> dict[str, str]:
    block = re.search(re.escape(selector) + r"\{(.*?)\}", CSS, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):(#[0-9A-Fa-f]{6})", block))


LIGHT = theme(":root")
DARK = {**LIGHT, **theme(':root[data-theme="dark"]')}


def lum(hex_: str) -> float:
    def ch(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# (text, background): legend, task meta and badges (muted), placeholders, chips (ink on paper/sand),
# links and step labels (saff), errors (danger), savings (good), night panels.
PAIRS = [
    ("muted", "cream"), ("muted", "paper"), ("muted", "sand"), ("placeholder", "paper"), ("placeholder", "field"),
    ("ink", "paper"), ("ink", "sand"), ("ink", "cream"), ("saff", "cream"), ("saff", "paper"), ("saff", "sand"),
    ("danger", "cream"), ("danger", "paper"), ("good", "paper"), ("on-mari", "mari"),
    ("on-night", "night"), ("night-dim", "night"), ("night-step", "night"), ("cream", "ink"),
]


@pytest.mark.parametrize("name,vars_", [("light", LIGHT), ("dark", DARK)])
@pytest.mark.parametrize("fg,bg", PAIRS)
def test_text_contrast_is_aa(name, vars_, fg, bg):
    assert ratio(vars_[fg], vars_[bg]) >= 4.5, f"{name}: --{fg} on --{bg} is {ratio(vars_[fg], vars_[bg]):.2f}:1"
