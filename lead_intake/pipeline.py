"""Applies a decision to the store. Shared by the one-call endpoint, the n8n endpoints and the CLI."""
from __future__ import annotations

from .analyze import analyze
from .config import Settings
from .llm import Provider
from .schema import Analysis, EmailIn, LeadFields
from .scoring import score_lead
from .store import Store


def _already(store: Store, message_id: str | None) -> dict | None:
    """Mail servers and workflow retries deliver the same message twice. The second time changes nothing."""
    seen = store.find_message(message_id)
    if seen is None:
        return None
    return {"duplicate": True, "message_pk": seen["id"], "route": seen["route"], "route_reason": seen["route_reason"],
            "category": seen["category"], "lead_id": seen["lead_id"]}


def apply(a: Analysis, store: Store) -> dict:
    """Write one analysed email where its route says, and report what was written."""
    if (dup := _already(store, a.email.message_id)) is not None:
        return dup
    message_pk = store.save_message(a)
    out = {"duplicate": False, "message_pk": message_pk, "route": a.route, "route_reason": a.route_reason,
           "category": a.category, "lead_id": None}
    if a.route == "crm":
        lead_id, created = store.upsert_lead(a, message_pk, source="auto")
        out.update(lead_id=lead_id, lead_created=created, score=a.score, tier=a.tier)
    elif a.route == "review":
        out["review_id"] = store.enqueue_review(message_pk, a.route_reason)
    return out


def intake(email: EmailIn, provider: Provider, store: Store, settings: Settings) -> dict:
    """Analyse and store in one step. This is the whole flow without n8n."""
    if (dup := _already(store, email.message_id)) is not None:
        return dup  # checked first so a repeated delivery costs no model call
    a = analyze(email, provider, settings)
    result = apply(a, store)
    if a.route == "crm" and a.tier == "hot":
        result["notification_id"] = store.notify(result["lead_id"], "sales", hot_lead_text(a))
    return {**result, "analysis": a.model_dump()}


def hot_lead_text(a: Analysis) -> str:
    who = " at ".join(x for x in (a.lead.contact_name, a.lead.company) if x) or a.lead.email
    return f"Hot lead ({a.score}/100): {who}. {a.summary}"


def approve(review_id: int, corrected: LeadFields | None, note: str, store: Store, settings: Settings) -> dict:
    """A person accepts a queued email as a lead, with their corrections. It is re-scored on the corrected fields."""
    item = store.review(review_id)
    if item is None or item["status"] != "pending":
        raise LookupError("no pending review with this id")
    a = Analysis.model_validate(item["analysis"])
    a.lead = corrected or a.lead
    if not a.lead.email:
        raise ValueError("a lead needs an email address")
    a.category, a.route = "lead", "crm"
    a.score, a.tier, a.score_reasons = score_lead(a.lead, settings)
    lead_id, created = store.upsert_lead(a, item["message_pk"], source="review")
    store.resolve_review(review_id, "approved", note, lead_id)
    return {"review_id": review_id, "status": "approved", "lead_id": lead_id, "lead_created": created, "score": a.score, "tier": a.tier}


def reject(review_id: int, note: str, store: Store) -> dict:
    item = store.review(review_id)
    if item is None or item["status"] != "pending":
        raise LookupError("no pending review with this id")
    store.resolve_review(review_id, "rejected", note)
    return {"review_id": review_id, "status": "rejected"}
