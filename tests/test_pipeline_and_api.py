from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lead_intake import pipeline
from lead_intake.api import create_app
from lead_intake.config import Settings
from lead_intake.schema import EmailIn, empty_lead
from scripts.make_workflow import OUT, build

from .conftest import FakeProvider


def test_hot_lead_is_stored_and_sales_is_notified(lead_email, mock, store, settings):
    out = pipeline.intake(lead_email, mock, store, settings)
    assert (out["route"], out["tier"], out["lead_created"]) == ("crm", "hot", True)
    (lead,) = store.leads()
    assert (lead["email"], lead["score"], lead["source"]) == ("dana.whitfield@northpoint-foods.example", 100, "auto")
    assert len(store.notifications()) == 1 and "Dana Whitfield at Northpoint Foods" in store.notifications()[0]["text"]


def test_same_message_twice_is_stored_once_and_costs_one_model_call(lead_email, store, settings):
    provider = FakeProvider(contact_name="Dana Whitfield", company="Northpoint Foods")
    first = pipeline.intake(lead_email, provider, store, settings)
    second = pipeline.intake(lead_email, provider, store, settings)
    assert second["duplicate"] and second["lead_id"] == first["lead_id"]
    assert provider.calls == 1 and store.stats()["messages"] == 1 and len(store.leads()) == 1


def test_repeat_sender_is_merged_into_the_existing_lead(lead_email, mock, store, settings):
    pipeline.intake(lead_email, mock, store, settings)
    follow_up = EmailIn(message_id="<m2@mail.example>", from_name="Dana Whitfield", from_email=lead_email.from_email.upper(),
                        subject="Also conveyors",
                        body="Hello,\n\nWe also need a roller conveyor line of roughly 25 metres. Please add it to the quote.\n\n"
                             "Regards,\nDana Whitfield\nNorthpoint Foods\nLeeds, United Kingdom")
    out = pipeline.intake(follow_up, mock, store, settings)
    assert out["lead_created"] is False
    (lead,) = store.leads()
    assert lead["product_interest"] == ["conveyors", "shelving"]
    assert lead["country"] == "United Kingdom"            # an empty field is filled in
    assert (lead["score"], lead["budget_amount"]) == (100, 25000)  # the weaker follow-up does not lower or erase anything
    assert lead["emails"] == 2


def test_review_approve_with_corrections_and_reject(mock, store, settings):
    vague = EmailIn(message_id="<v1@mail.example>", from_email="omar.demo@gmail.com", subject="price", body="How much is a mezzanine roughly")
    out = pipeline.intake(vague, mock, store, settings)
    assert out["route"] == "review" and store.leads() == []
    corrected = empty_lead().model_copy(update={"email": "omar.demo@gmail.com", "contact_name": "Omar Farouk", "product_interest": ["mezzanine"]})
    done = pipeline.approve(out["review_id"], corrected, "called him", store, settings)
    assert done["lead_created"] and store.leads()[0]["source"] == "review" and store.leads()[0]["contact_name"] == "Omar Farouk"
    with pytest.raises(LookupError):
        pipeline.approve(out["review_id"], None, "", store, settings)  # already decided

    other = pipeline.intake(vague.model_copy(update={"message_id": "<v2@mail.example>", "from_email": "z.demo@gmail.com"}), mock, store, settings)
    assert pipeline.reject(other["review_id"], "spam", store)["status"] == "rejected"
    assert store.reviews() == [] and len(store.reviews(None)) == 2 and len(store.leads()) == 1


@pytest.fixture
def client(settings, mock, store) -> TestClient:
    return TestClient(create_app(settings, mock, store))


def test_one_call_endpoint(client, lead_email):
    out = client.post("/intake", json=lead_email.model_dump()).json()
    assert out["route"] == "crm" and out["analysis"]["score"] == 100
    assert client.get("/stats").json()["hot_leads"] == 1
    assert client.get("/leads").json()[0]["company"] == "Northpoint Foods"
    assert client.get("/messages").json()[0]["route"] == "crm"
    assert client.get("/").status_code == 200 and client.get("/health").json()["provider"] == "mock"


def test_n8n_endpoints_follow_the_route(client, lead_email):
    analysis = client.post("/analyze", json=lead_email.model_dump()).json()
    assert analysis["route"] == "crm" and client.get("/stats").json()["messages"] == 0  # analysing stores nothing
    assert client.post("/review", json=analysis).status_code == 409                     # a miswired workflow is refused
    assert client.post("/archive", json=analysis).status_code == 409
    written = client.post("/crm/leads", json=analysis).json()
    assert written["hot"] is True and written["lead_id"] == 1
    again = client.post("/crm/leads", json=analysis).json()
    assert again["duplicate"] and again["hot"] is False                                 # no second notification
    note = client.post("/notifications", json={"lead_id": 1, "text": written["notification_text"]}).json()
    assert note["notification_id"] == 1 and len(client.get("/notifications").json()) == 1


def test_review_over_http(client):
    email = {"from_email": "omar.demo@gmail.com", "subject": "price", "body": "How much is a mezzanine roughly"}
    analysis = client.post("/analyze", json=email).json()
    review_id = client.post("/review", json=analysis).json()["review_id"]
    assert client.get("/review").json()[0]["reason"] == "no contact name and no company"
    assert client.post(f"/review/{review_id}/approve", json={}).json()["status"] == "approved"
    assert client.post(f"/review/{review_id}/reject", json={}).status_code == 404
    assert client.post("/analyze", json={"subject": "no sender"}).status_code == 422


def test_token_protects_the_api(mock, store, lead_email):
    client = TestClient(create_app(Settings(api_token="s3cret"), mock, store))
    assert client.post("/intake", json=lead_email.model_dump()).status_code == 401
    assert client.get("/leads", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/intake", json=lead_email.model_dump(), headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_workflow_file_is_current_and_wired_to_real_endpoints(client):
    workflow = json.loads(Path(OUT).read_text())
    assert workflow == build(), "run: python -m scripts.make_workflow"
    names = {node["name"] for node in workflow["nodes"]}
    for source, outputs in workflow["connections"].items():
        assert source in names
        assert all(link["node"] in names for output in outputs["main"] for link in output)
    paths = {route.path for route in client.app.routes}
    for node in workflow["nodes"]:
        if node["type"].endswith("httpRequest"):
            assert node["parameters"]["url"].split("}}")[-1] in paths
            assert node["retryOnFail"] and node["maxTries"] == 3
