"""Writes n8n/lead-intake.workflow.json.

    python -m scripts.make_workflow

The workflow is generated so that its nodes stay consistent (same retry settings, same
header, same service address) and so a change shows up as a readable diff of this file.
The JSON it writes is what n8n imports; you can also edit the workflow in the n8n editor.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "n8n" / "lead-intake.workflow.json"
WORKFLOW_ID = "leadIntakeToCrm01"
ANALYSIS = "$('Analyse email').first().json"


def http(name: str, path: str, body: str, position: list[int], notes: str = "", on_error: str = "continueErrorOutput") -> dict:
    return {
        # After the retries, a failure leaves through the node's second output, which is wired to
        # "Respond: failed". Without this n8n ends the run and the caller gets an empty reply.
        "onError": on_error,
        "parameters": {
            "method": "POST", "url": "={{ $('Config').first().json.service_url }}" + path,
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "X-API-Key", "value": "={{ $('Config').first().json.api_token }}"}]},
            "sendBody": True, "specifyBody": "json", "jsonBody": body, "options": {"timeout": 120000}},
        "id": name.lower().replace(" ", "-"), "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
        "position": position, "notes": notes,
        # The service may be restarting, and a model call can time out: try three times before failing.
        "retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000}


def condition(left: str, right: str | None, kind: str = "string", operation: str = "equals") -> dict:
    test: dict = {"id": f"c-{right or operation}", "leftValue": left, "operator": {"type": kind, "operation": operation}}
    if right is None:
        test["operator"]["singleValue"] = True
    else:
        test["rightValue"] = right
    return {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [test], "combinator": "and"}


def link(*targets: str) -> dict:
    """One connection per output of a node, in order."""
    return {"main": [[{"node": target, "type": "main", "index": 0}] for target in targets]}


FAILED = "Respond: failed"


def build() -> dict:
    nodes = [
        {"parameters": {"httpMethod": "POST", "path": "lead-intake", "responseMode": "responseNode", "options": {}},
         "id": "webhook", "name": "Inbound email", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 300],
         "webhookId": "lead-intake",
         "notes": "Receives one email as JSON: from_name, from_email, subject, body, message_id. In production, replace "
                  "this node with the Gmail, Outlook or IMAP trigger and keep the rest."},
        {"parameters": {"assignments": {"assignments": [
            {"id": "a1", "name": "service_url", "value": "http://lead-service:8024", "type": "string"},
            {"id": "a2", "name": "api_token", "value": "", "type": "string"}]}, "includeOtherFields": True, "options": {}},
         "id": "config", "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [220, 300],
         "notes": "Where the Python service lives. In production, keep the token in an n8n credential instead."},
        http("Analyse email", "/analyze", "={{ JSON.stringify($json.body) }}", [440, 300],
             "One model call returns the category and the lead fields. The service then checks the values against the "
             "email, scores the lead and decides the route."),
        {"parameters": {"rules": {"values": [
            {"conditions": condition("={{ $json.route }}", route), "renameOutput": True, "outputKey": route}
            for route in ("crm", "review", "archive")]}, "options": {}},
         "id": "route", "name": "Route", "type": "n8n-nodes-base.switch", "typeVersion": 3.2, "position": [660, 300]},
        http("Write lead to CRM", "/crm/leads", "={{ JSON.stringify($json) }}", [900, 120],
             "The CRM here is a table in the service. For HubSpot, Pipedrive or Salesforce, replace this node with the "
             "CRM node and map the fields of $json.lead."),
        {"parameters": {"conditions": condition("={{ $json.hot }}", None, "boolean", "true"), "options": {}},
         "id": "hot", "name": "Hot lead?", "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": [1120, 120]},
        http("Notify sales", "/notifications",
             "={{ JSON.stringify({ lead_id: $json.lead_id, channel: 'sales', text: $json.notification_text }) }}", [1340, 40],
             "Stands in for a Slack or Teams message. Replace with the Slack node.",
             on_error="continueRegularOutput"),  # the lead is already stored: a lost notification must not fail the email
        http("Add to review queue", "/review", "={{ JSON.stringify($json) }}", [900, 300]),
        http("Archive", "/archive", "={{ JSON.stringify($json) }}", [900, 480]),
        {"parameters": {"respondWith": "json", "options": {},
                        "responseBody": "={{ JSON.stringify({ " + ", ".join(
                            f"{key}: {ANALYSIS}.{key}" for key in ("category", "route", "route_reason", "score", "tier", "flags"))
                        + ", stored: $json }) }}"},
         "id": "respond", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [1580, 300]},
        {"parameters": {"respondWith": "json", "options": {"responseCode": 503},
                        "responseBody": "={{ JSON.stringify({ error: 'lead service unavailable, the email was not processed', "
                                        "detail: $json.error.message }) }}"},
         "id": "respond-failed", "name": FAILED, "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1,
         "position": [1580, 560],
         "notes": "A 503 tells the sender to try again. Sending the same email twice is safe: the service ignores a "
                  "message id it has already stored."},
    ]
    connections = {
        "Inbound email": link("Config"), "Config": link("Analyse email"), "Analyse email": link("Route", FAILED),
        "Route": link("Write lead to CRM", "Add to review queue", "Archive"),
        "Write lead to CRM": link("Hot lead?", FAILED), "Hot lead?": link("Notify sales", "Respond"),
        "Notify sales": link("Respond"), "Add to review queue": link("Respond", FAILED), "Archive": link("Respond", FAILED),
    }
    return {"id": WORKFLOW_ID, "name": "Lead intake: email to CRM", "active": False, "nodes": nodes,
            "connections": connections, "settings": {"executionOrder": "v1"}, "pinData": {}, "tags": []}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT.relative_to(OUT.parent.parent)}")
