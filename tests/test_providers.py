from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lead_intake.analyze import analyze
from lead_intake.config import Settings
from lead_intake.llm import OpenAIProvider, make_provider
from lead_intake.schema import EmailIn, Extraction


def test_offline_provider_reads_a_plain_lead(lead_email, mock):
    extraction, _ = mock.extract(lead_email)
    lead = extraction.lead
    assert extraction.category == "lead"
    assert (lead.contact_name, lead.company, lead.job_title) == ("Dana Whitfield", "Northpoint Foods", "Operations Director")
    assert (lead.phone, lead.budget_amount, lead.budget_currency, lead.timeline) == ("+44 20 7946 0555", 25000, "EUR", "within_1_month")
    assert lead.product_interest == ["shelving"]


def test_offline_provider_on_the_plain_emails(dataset, mock, settings):
    """The rules were written for this style: every plain email is filed correctly and every lead field matches."""
    for row in (r for r in dataset if r["style"] == "plain"):
        a = analyze(EmailIn(**row["email"]), mock, settings)
        assert a.category == row["label"]["category"], row["id"]
        if a.category == "lead":
            assert a.lead.model_dump() == row["label"]["lead"], row["id"]
            assert a.route == "crm", row["id"]


def test_no_injection_email_reaches_the_crm(dataset, mock, settings):
    for row in (r for r in dataset if r["kind"] == "prompt_injection"):
        assert analyze(EmailIn(**row["email"]), mock, settings).route != "crm"


def test_one_line_email_has_no_signature_to_misread(mock):
    extraction, _ = mock.extract(EmailIn(from_email="x@gmail.com", subject="price", body="How much is a mezzanine roughly"))
    assert extraction.category == "lead" and extraction.lead.company is None and extraction.lead.contact_name is None


def test_contact_details_are_not_taken_from_a_quoted_message(mock):
    body = ("We need 40 bays of shelving at Acme Ltd. Can someone send prices?\n\nAnn Lee\n\n"
            "On Tue, 6 Oct 2026 at 09:00, Meridian <news@meridian-we.example> wrote:\n> Call our sales team on +44 20 7946 0000.")
    extraction, _ = mock.extract(EmailIn(from_name="Ann Lee", from_email="ann@acme.example", subject="Re: offers", body=body))
    assert extraction.lead.phone is None and extraction.lead.contact_name == "Ann Lee"


def test_schema_is_strict_enough_for_structured_output():
    """OpenAI's strict mode needs every property required and no extra keys, on every object."""
    schema = Extraction.model_json_schema()
    for part in [schema, *schema["$defs"].values()]:
        if part.get("type") == "object":
            assert part["additionalProperties"] is False
            assert set(part["required"]) == set(part["properties"])


class StubClient:
    """Stands in for the OpenAI client, so the provider's own code is tested without a network call."""

    def __init__(self, content: str) -> None:
        self.kwargs: dict = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.content = content

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
                               usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200))


def openai_provider(content: str) -> OpenAIProvider:
    provider = OpenAIProvider(Settings(provider="openai", api_key="test", max_output_tokens=123))
    provider.client = StubClient(content)
    return provider


def test_openai_provider_parses_the_reply_and_counts_cost(lead_email, mock):
    good, _ = mock.extract(lead_email)
    provider = openai_provider(good.model_dump_json())
    extraction, usage = provider.extract(lead_email)
    assert extraction == good
    assert (usage.tokens_in, usage.tokens_out) == (1000, 200)
    assert usage.cost_usd == pytest.approx((1000 * 0.15 + 200 * 0.60) / 1_000_000)
    sent = provider.client.kwargs
    assert sent["max_completion_tokens"] == 123 and sent["temperature"] == 0
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert "data, not instructions" in sent["messages"][0]["content"]


@pytest.mark.parametrize("reply", ["", "{\"category\": \"lead\"", json.dumps({"category": "spam"})])
def test_openai_provider_survives_a_bad_reply(lead_email, reply):
    extraction, _ = openai_provider(reply).extract(lead_email)
    assert extraction.summary == "MODEL_REPLY_INVALID"


def test_provider_selection():
    assert make_provider(Settings()).name == "mock"
    with pytest.raises(SystemExit):
        make_provider(Settings(provider="openai"))
    with pytest.raises(SystemExit):
        make_provider(Settings(provider="nope"))
