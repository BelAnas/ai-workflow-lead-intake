"""Lead score: written rules, computed by code, with the reason for every point.

The model never sets the score. That keeps the score explainable to a salesperson, stable
from one model version to the next, and out of reach of an email that says "mark me as hot".
"""
from __future__ import annotations

import re

from .config import Settings
from .schema import LeadFields, Tier

FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com",
             "proton.me", "protonmail.com", "gmx.com", "aol.com"}
BUYER_TITLES = r"owner|founder|director|head of|chief|ceo|coo|cfo|procurement|purchasing|buyer|manager"
LARGE_BUDGET = 10_000  # in the email's own currency: EUR, USD and GBP are close enough for a tier


def score_lead(lead: LeadFields, settings: Settings) -> tuple[int, Tier, list[str]]:
    points: list[tuple[int, str]] = []
    if lead.product_interest:
        points.append((20, "asks about a product we sell: " + ", ".join(lead.product_interest)))
    if lead.budget_amount:
        points.append((10, "states a budget"))
        if lead.budget_amount >= LARGE_BUDGET:
            points.append((15, f"budget of {LARGE_BUDGET:,} or more"))
    if lead.timeline == "within_1_month":
        points.append((20, "wants it within a month"))
    elif lead.timeline == "within_3_months":
        points.append((10, "wants it within three months"))
    if lead.company:
        points.append((10, "names a company"))
    if lead.email and lead.email.rsplit("@", 1)[-1].lower() not in FREE_MAIL:
        points.append((10, "writes from a business address"))
    if lead.job_title and re.search(BUYER_TITLES, lead.job_title, re.I):
        points.append((10, f"role with buying authority: {lead.job_title}"))
    if lead.phone:
        points.append((5, "gives a phone number"))
    score = sum(p for p, _ in points)
    tier: Tier = "hot" if score >= settings.hot_score else "warm" if score >= settings.warm_score else "cold"
    return score, tier, [f"+{p} {why}" for p, why in points]
