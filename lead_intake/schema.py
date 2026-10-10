"""The shapes that move through the pipeline. Everything after the model call is typed."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["lead", "support", "vendor_pitch", "job_application", "other"]
Product = Literal["pallet_racking", "shelving", "conveyors", "mezzanine", "packing_stations"]
Timeline = Literal["within_1_month", "within_3_months", "later", "unknown"]
Route = Literal["crm", "review", "archive"]
Tier = Literal["hot", "warm", "cold"]


class EmailIn(BaseModel):
    """One inbound email, as the n8n trigger hands it over."""
    message_id: str | None = None   # the mail server's id; used to ignore a message delivered twice
    from_name: str = ""
    from_email: str
    subject: str = ""
    body: str
    received_at: str | None = None

    def text(self) -> str:
        return f"From: {self.from_name} <{self.from_email}>\nSubject: {self.subject}\n\n{self.body}"


class LeadFields(BaseModel):
    # No defaults and no extra keys: that is what OpenAI's strict structured output requires,
    # and it means a reply with a missing field fails loudly instead of passing as "empty".
    model_config = ConfigDict(extra="forbid")

    contact_name: str | None
    company: str | None
    email: str | None
    phone: str | None
    job_title: str | None
    country: str | None
    product_interest: list[Product]
    budget_amount: float | None
    budget_currency: str | None
    timeline: Timeline


class Extraction(BaseModel):
    """Exactly what the model is asked to return. Nothing in here is trusted yet."""
    model_config = ConfigDict(extra="forbid")

    category: Category
    lead: LeadFields
    summary: str


def empty_lead() -> LeadFields:
    return LeadFields(contact_name=None, company=None, email=None, phone=None, job_title=None, country=None,
                      product_interest=[], budget_amount=None, budget_currency=None, timeline="unknown")


class Usage(BaseModel):
    provider: str = "mock"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0


class Analysis(BaseModel):
    """The result for one email: what it is, what was extracted, the score, and where it goes."""
    email: EmailIn
    category: Category
    lead: LeadFields
    summary: str = ""
    flags: list[str] = Field(default_factory=list)   # reasons a person should look at it
    notes: list[str] = Field(default_factory=list)   # what the checks changed, for the audit trail
    score: int = 0
    tier: Tier = "cold"
    score_reasons: list[str] = Field(default_factory=list)
    route: Route = "archive"
    route_reason: str = ""
    usage: Usage = Field(default_factory=Usage)
