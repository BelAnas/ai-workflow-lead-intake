"""Small text helpers shared by the offline provider, the grounding check and the evaluation."""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<![\w.])\+?\(?\d[\d\s().-]{7,}\d(?![\w])")
# A reply quotes the earlier message below one of these lines. What is quoted was written
# by someone else (often by us), so contact details must not be taken from it.
QUOTE_START = re.compile(r"^(>|On .{5,80} wrote:|-{2,}\s*Original Message|Le .{5,80} a écrit|From: .+\nSent: )", re.M | re.I)

AMOUNT_RE = re.compile(
    r"(?P<pre>[€£$]|\b(?:EUR|USD|GBP)\b)?\s?(?P<num>\d{1,3}(?:[ ,.  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s?(?P<k>k\b)?\s?(?P<post>[€£$]|\b(?:EUR|USD|GBP|euros?|dollars?|pounds?)\b)?",
    re.I)
CURRENCY = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "$": "USD", "usd": "USD", "dollar": "USD",
            "dollars": "USD", "£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP"}


def norm(value: str | None) -> str:
    """Lower case, single spaces, no surrounding punctuation. Used to compare text values."""
    return re.sub(r"\s+", " ", (value or "").lower()).strip(" .,;:")


def digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def unquoted(body: str) -> str:
    """The part of a body the sender wrote, without the quoted earlier message."""
    match = QUOTE_START.search(body)
    return body[:match.start()] if match else body


def parse_number(raw: str, thousand: bool = False) -> float:
    """'25,000' '25.000' '25 000' '1,250.50' '1.250,50' -> a float. A trailing k multiplies by 1000."""
    s = re.sub(r"[   ]", "", raw)
    decimal = re.search(r"[.,]\d{1,2}$", s)
    whole, frac = (s[:decimal.start()], s[decimal.start() + 1:]) if decimal else (s, "")
    value = float(re.sub(r"[.,]", "", whole) + ("." + frac if frac else ""))
    return value * 1000 if thousand else value


def amounts(text: str) -> list[tuple[float, str | None, int]]:
    """Every number in the text as (value, currency or None, position)."""
    found = []
    for m in AMOUNT_RE.finditer(text):
        symbol = (m.group("pre") or m.group("post") or "").lower()
        found.append((parse_number(m.group("num"), bool(m.group("k"))), CURRENCY.get(symbol), m.start()))
    return found
