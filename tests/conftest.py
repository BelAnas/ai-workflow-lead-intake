"""Shared fixtures. Every test is offline: the rule-based provider or a scripted fake, and a temporary database."""
from __future__ import annotations

import json

import pytest

from lead_intake.config import DATA, Settings
from lead_intake.llm import MockProvider
from lead_intake.schema import EmailIn, Extraction, LeadFields, Usage, empty_lead
from lead_intake.store import Store


class FakeProvider:
    """Returns whatever a test tells it to, the way a model that misbehaves would."""

    name = "fake"

    def __init__(self, category: str = "lead", summary: str = "summary", **fields) -> None:
        self.extraction = Extraction(category=category, summary=summary,
                                     lead=LeadFields(**{**empty_lead().model_dump(), **fields}))
        self.calls = 0

    def extract(self, email: EmailIn) -> tuple[Extraction, Usage]:
        self.calls += 1
        return self.extraction.model_copy(deep=True), Usage()


@pytest.fixture(scope="session")
def dataset() -> list[dict]:
    return [json.loads(line) for line in (DATA / "emails.jsonl").read_text().splitlines()]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=str(tmp_path / "leads.db"))


@pytest.fixture
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture
def mock() -> MockProvider:
    return MockProvider()


@pytest.fixture
def lead_email() -> EmailIn:
    return EmailIn(
        message_id="<m1@mail.example>", from_name="Dana Whitfield", from_email="dana.whitfield@northpoint-foods.example",
        subject="Quote for shelving",
        body="Hello,\n\nWe are Northpoint Foods and we need 60 bays of longspan shelving. Our budget is around €25,000 and we "
             "would like it installed as soon as possible. Could you send a quote?\n\nBest regards,\nDana Whitfield\n"
             "Operations Director\nNorthpoint Foods\n+44 20 7946 0555\n")
