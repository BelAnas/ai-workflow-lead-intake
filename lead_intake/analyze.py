"""From an email to a decision: model call, grounding check, score, route. Nothing is stored here."""
from __future__ import annotations

import re

from .config import OWN_DOMAIN, Settings
from .llm import PRODUCT_WORDS, Provider
from .schema import Analysis, EmailIn, LeadFields
from .scoring import score_lead
from .textutil import EMAIL_RE, amounts, digits, norm, unquoted

# Wording that addresses the software instead of a person. It does not block anything by
# itself; it adds a flag, and a flagged lead is never filed without a person looking.
INSTRUCTION_RE = re.compile(r"ignore (all |any )?(previous|prior|above) instructions|system (note|prompt)|to the ai\b|"
                            r"classify this (message|email) as|mark (it|this) as (a )?(hot|lead)", re.I)
# Signs that a sender may want to buy. Used only to catch a lead the model filed elsewhere.
BUYING_RE = re.compile(r"\b(quote|quotation|pricing|prices?|how much|devis|we need|interested in|what would (this|that) cost)\b", re.I)
PLACEHOLDERS = {"unknown", "n/a", "na", "none", "null", "not provided", "not stated", "not specified", "-"}
IDENTITY_FIELDS = ("contact_name", "company", "email", "phone")
SOFT_FIELDS = ("job_title", "country")


def ground(lead: LeadFields, email: EmailIn) -> tuple[LeadFields, list[str], list[str]]:
    """Keep only values that are written in the email. Returns (lead, flags, notes).

    A model can produce a plausible phone number or company that the sender never wrote. A
    wrong contact in the CRM is worse than an empty one, so a value that cannot be found in
    the text is removed. For the fields that identify the person this also raises a flag.
    """
    # Only what the sender wrote counts. A reply often quotes our own newsletter underneath, with
    # our phone number and country in it, and those must not end up on the customer's record.
    own_words = unquoted(email.body)
    text, flags, notes = norm(f"{email.from_name} <{email.from_email}>\n{email.subject}\n{own_words}"), [], []
    values = lead.model_dump()
    for name in IDENTITY_FIELDS + SOFT_FIELDS:
        value = values[name]
        if isinstance(value, str) and norm(value) in PLACEHOLDERS:
            values[name] = value = None  # small models write "unknown" where the schema allows null
        if not value:
            continue
        found = digits(value) in digits(own_words) if name == "phone" else norm(value) in text
        if not found:
            values[name] = None
            notes.append(f"removed {name} '{value}': not found in the email")
            if name in IDENTITY_FIELDS:
                flags.append(f"ungrounded:{name}")
    if values["budget_amount"] is not None and not any(abs(v - values["budget_amount"]) < 0.01 for v, _, _ in amounts(own_words)):
        notes.append(f"removed budget {values['budget_amount']:g}: no such amount in the email")
        flags.append("ungrounded:budget_amount")
        values["budget_amount"] = values["budget_currency"] = None
    if values["budget_amount"] is None:
        values["budget_currency"] = None
    elif values["budget_currency"]:
        values["budget_currency"] = values["budget_currency"].strip().upper()[:3]
    if values["email"]:
        values["email"] = values["email"].strip().lower()
    return LeadFields(**values), flags, notes


def decide_route(category: str, lead: LeadFields, flags: list[str], email: EmailIn) -> tuple[str, str]:
    """Where the email goes, and why. The CRM only receives leads that are complete and unflagged."""
    if category != "lead":
        # The costly mistake is a real buyer filed as something else and never answered. A
        # complaint or an "other" that talks about buying our products gets a second look.
        # Pitches and job applications are left out: they use the same words by nature.
        text = f"{email.subject}\n{email.body}"
        if category in ("support", "other") and BUYING_RE.search(text) and any(
                re.search(pattern, text, re.I) for pattern in PRODUCT_WORDS.values()):
            return "review", f"filed as {category}, but it uses buying words about our products"
        return "archive", f"not a lead: {category}"
    if flags:
        return "review", "needs a person: " + ", ".join(flags)
    if not lead.email or not EMAIL_RE.fullmatch(lead.email) or lead.email.endswith("@" + OWN_DOMAIN):
        return "review", "no usable contact email"
    if not (lead.contact_name or lead.company):
        return "review", "no contact name and no company"
    return "crm", "complete lead"


def analyze(email: EmailIn, provider: Provider, settings: Settings) -> Analysis:
    extraction, usage = provider.extract(email)
    usage.provider = provider.name
    lead = extraction.lead
    if extraction.category == "lead" and not lead.email:
        # People rarely type their own address in the body; the sender address is the contact.
        lead = lead.model_copy(update={"email": email.from_email})
    lead, flags, notes = ground(lead, email)
    if INSTRUCTION_RE.search(email.text()):
        flags.append("suspicious_instructions")
    if extraction.summary == "MODEL_REPLY_INVALID":
        flags.append("model_reply_invalid")
    category = extraction.category
    route, reason = decide_route(category, lead, flags, email)
    if "model_reply_invalid" in flags and route == "archive":
        route, reason = "review", "the model's reply could not be read"
    score, tier, reasons = score_lead(lead, settings) if category == "lead" else (0, "cold", [])
    return Analysis(email=email, category=category, lead=lead, summary=extraction.summary[:300], flags=flags, notes=notes,
                    score=score, tier=tier, score_reasons=reasons, route=route, route_reason=reason, usage=usage)
