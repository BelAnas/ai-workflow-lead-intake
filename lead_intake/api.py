"""HTTP API. Two ways in:

- POST /intake does everything for one email (analyse, store, notify). No n8n needed.
- POST /analyze only decides; the n8n workflow then calls /crm/leads, /review or /archive
  according to the route, and /notifications for hot leads. Those three write endpoints
  refuse an email whose route does not match, so a miswired workflow cannot file a doubtful
  email in the CRM.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import pipeline
from .analyze import analyze
from .config import COMPANY, Settings
from .llm import Provider, make_provider
from .schema import Analysis, EmailIn, LeadFields
from .store import Store

PAGE = Path(__file__).parent / "static" / "index.html"


class Decision(BaseModel):
    lead: LeadFields | None = None  # the reviewer's corrected fields; None keeps what was extracted
    note: str = ""


class Notification(BaseModel):
    lead_id: int | None = None
    channel: str = "sales"
    text: str


def create_app(settings: Settings | None = None, provider: Provider | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    provider = provider or make_provider(settings)
    store = store or Store(settings.db_path)

    def check_token(x_api_key: str | None = Header(default=None)) -> None:
        if settings.api_token and x_api_key != settings.api_token:
            raise HTTPException(401, "missing or wrong X-API-Key header")

    app = FastAPI(title="Lead intake", description=f"Inbound email triage for {COMPANY} (demo).")
    api = [Depends(check_token)]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "provider": provider.name, "token_required": bool(settings.api_token)}

    @app.get("/", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(PAGE)

    @app.post("/analyze", dependencies=api)
    def analyze_only(email: EmailIn) -> Analysis:
        return analyze(email, provider, settings)

    @app.post("/intake", dependencies=api)
    def intake(email: EmailIn) -> dict:
        return pipeline.intake(email, provider, store, settings)

    def write(a: Analysis, route: str) -> dict:
        if a.route != route:
            raise HTTPException(409, f"this email is routed to '{a.route}', not '{route}'")
        return pipeline.apply(a, store)

    @app.post("/crm/leads", dependencies=api)
    def crm_write(a: Analysis) -> dict:
        out = write(a, "crm")
        # "hot" tells the workflow whether to notify sales. A message delivered twice must not notify twice.
        return {**out, "hot": a.tier == "hot" and not out["duplicate"], "notification_text": pipeline.hot_lead_text(a)}

    @app.post("/review", dependencies=api)
    def review_write(a: Analysis) -> dict:
        return write(a, "review")

    @app.post("/archive", dependencies=api)
    def archive_write(a: Analysis) -> dict:
        return write(a, "archive")

    @app.post("/notifications", dependencies=api)
    def notify(n: Notification) -> dict:
        # Stands in for Slack or Teams. In n8n this call is replaced by the Slack node.
        return {"notification_id": store.notify(n.lead_id, n.channel, n.text), "lead_id": n.lead_id}

    @app.post("/review/{review_id}/approve", dependencies=api)
    def approve(review_id: int, decision: Decision) -> dict:
        try:
            return pipeline.approve(review_id, decision.lead, decision.note, store, settings)
        except LookupError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(422, str(error))

    @app.post("/review/{review_id}/reject", dependencies=api)
    def reject(review_id: int, decision: Decision) -> dict:
        try:
            return pipeline.reject(review_id, decision.note, store)
        except LookupError as error:
            raise HTTPException(404, str(error))

    @app.get("/review", dependencies=api)
    def review_list(status: str = "pending") -> list[dict]:
        return store.reviews(None if status == "all" else status)

    @app.get("/leads", dependencies=api)
    def leads() -> list[dict]:
        return store.leads()

    @app.get("/messages", dependencies=api)
    def messages() -> list[dict]:
        return store.messages()

    @app.get("/notifications", dependencies=api)
    def notifications() -> list[dict]:
        return store.notifications()

    @app.get("/stats", dependencies=api)
    def stats() -> dict:
        return {**store.stats(), "provider": provider.name}

    return app
