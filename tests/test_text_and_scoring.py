from __future__ import annotations

import pytest

from lead_intake.config import Settings
from lead_intake.schema import LeadFields, empty_lead
from lead_intake.scoring import score_lead
from lead_intake.textutil import PHONE_RE, amounts, parse_number, unquoted


@pytest.mark.parametrize("text, value, currency", [
    ("Our budget is around €25,000.", 25000, "EUR"),
    ("about 40k USD set aside", 40000, "USD"),
    ("Budget: 8 000 EUR", 8000, "EUR"),
    ("The budget is £12,500.50", 12500.5, "GBP"),
    ("environ 30 000 € pour", 30000, "EUR"),
    ("25.000 EUR", 25000, "EUR"),
    ("We can spend up to $6,500.", 6500, "USD"),
])
def test_amounts_in_the_formats_people_write(text, value, currency):
    assert (value, currency) in [(v, c) for v, c, _ in amounts(text)]


def test_decimal_comma_and_decimal_point():
    assert parse_number("1.250,50") == 1250.5
    assert parse_number("1,250.50") == 1250.5
    assert parse_number("12.5") == 12.5


def test_quoted_older_message_is_cut_off():
    body = "We need shelving.\n\nAnn\n\nOn Tue, 6 Oct 2026 at 09:00, Meridian <news@x.example> wrote:\n> Call +44 20 7946 0000"
    assert "7946" not in unquoted(body)
    assert unquoted("no quote here") == "no quote here"


def test_phone_pattern_ignores_order_numbers_and_years():
    assert PHONE_RE.findall("call +44 20 7946 0123 today") == ["+44 20 7946 0123"]
    assert not PHONE_RE.findall("order MW-22331 from 2024, 600 pallets")


def lead(**fields) -> LeadFields:
    return LeadFields(**{**empty_lead().model_dump(), **fields})


def test_score_is_the_sum_of_listed_reasons():
    full = lead(contact_name="A B", company="Acme", email="a@acme.example", phone="+44 20 7946 0000", job_title="Operations Director",
                product_interest=["shelving"], budget_amount=25000, budget_currency="EUR", timeline="within_1_month")
    score, tier, reasons = score_lead(full, Settings())
    assert (score, tier) == (100, "hot")
    assert sum(int(r.split()[0]) for r in reasons) == score


def test_score_tiers_and_free_mail():
    score, tier, reasons = score_lead(lead(email="someone@gmail.com", product_interest=["conveyors"]), Settings())
    assert (score, tier) == (20, "cold")
    assert not any("business address" in r for r in reasons)
    score, tier, _ = score_lead(lead(email="a@acme.example", company="Acme", product_interest=["conveyors"]), Settings())
    assert (score, tier) == (40, "warm")


def test_small_budget_and_junior_title_score_less():
    small, _, _ = score_lead(lead(budget_amount=6500, job_title="Office Administrator"), Settings())
    large, _, _ = score_lead(lead(budget_amount=60000, job_title="Procurement Manager"), Settings())
    assert (small, large) == (10, 35)
