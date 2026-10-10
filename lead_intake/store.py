"""SQLite store. The `leads` table stands in for the CRM; in a client project the same
functions would call the HubSpot, Pipedrive or Salesforce API instead."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .schema import Analysis, LeadFields

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, message_id TEXT UNIQUE, from_email TEXT, from_name TEXT, subject TEXT, body TEXT,
    category TEXT, route TEXT, route_reason TEXT, analysis TEXT, lead_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY, email TEXT UNIQUE, contact_name TEXT, company TEXT, phone TEXT, job_title TEXT,
    country TEXT, product_interest TEXT, budget_amount REAL, budget_currency TEXT, timeline TEXT,
    score INTEGER, tier TEXT, score_reasons TEXT, source TEXT, status TEXT DEFAULT 'new', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY, lead_id INTEGER, message_pk INTEGER, kind TEXT, note TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY, message_pk INTEGER, reason TEXT, status TEXT DEFAULT 'pending',
    reviewer_note TEXT, lead_id INTEGER, created_at TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY, lead_id INTEGER, channel TEXT, text TEXT, created_at TEXT);
"""
LEAD_COLUMNS = ["contact_name", "company", "phone", "job_title", "country", "budget_amount", "budget_currency", "timeline"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        # One connection shared by the API's worker threads, guarded by a lock: simple and
        # enough for an inbox. A busy system would use Postgres and a connection pool.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    # -- messages -------------------------------------------------------------------------

    def find_message(self, message_id: str | None) -> dict | None:
        if not message_id:
            return None
        rows = self._all("SELECT * FROM messages WHERE message_id = ?", (message_id,))
        return rows[0] if rows else None

    def save_message(self, a: Analysis, lead_id: int | None = None) -> int:
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO messages (message_id, from_email, from_name, subject, body, category, route, route_reason,"
                " analysis, lead_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (a.email.message_id, a.email.from_email, a.email.from_name, a.email.subject, a.email.body, a.category,
                 a.route, a.route_reason, a.model_dump_json(), lead_id, now()))
            return int(cur.lastrowid)

    # -- CRM ------------------------------------------------------------------------------

    def upsert_lead(self, a: Analysis, message_pk: int, source: str) -> tuple[int, bool]:
        """Create the lead, or add this email to the lead that already has this address.

        Returns (lead id, created). For a known lead, empty fields are filled in and the score
        only goes up: a short follow-up email must not erase what an earlier one told us.
        """
        lead, email = a.lead, (a.lead.email or a.email.from_email).lower()
        with self.lock, self.db:
            old = self.db.execute("SELECT * FROM leads WHERE email = ?", (email,)).fetchone()
            if old is None:
                cur = self.db.execute(
                    "INSERT INTO leads (email, contact_name, company, phone, job_title, country, product_interest,"
                    " budget_amount, budget_currency, timeline, score, tier, score_reasons, source, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (email, lead.contact_name, lead.company, lead.phone, lead.job_title, lead.country,
                     json.dumps(lead.product_interest), lead.budget_amount, lead.budget_currency, lead.timeline,
                     a.score, a.tier, json.dumps(a.score_reasons), source, now(), now()))
                lead_id, created = int(cur.lastrowid), True
            else:
                lead_id, created = int(old["id"]), False
                merged = {c: old[c] if old[c] not in (None, "", "unknown") else getattr(lead, c) for c in LEAD_COLUMNS}
                products = sorted(set(json.loads(old["product_interest"] or "[]")) | set(lead.product_interest))
                better = a.score > (old["score"] or 0)
                self.db.execute(
                    f"UPDATE leads SET {', '.join(c + ' = ?' for c in LEAD_COLUMNS)}, product_interest = ?, score = ?,"
                    " tier = ?, score_reasons = ?, updated_at = ? WHERE id = ?",
                    (*merged.values(), json.dumps(products), a.score if better else old["score"],
                     a.tier if better else old["tier"], json.dumps(a.score_reasons) if better else old["score_reasons"],
                     now(), lead_id))
            self.db.execute("INSERT INTO activities (lead_id, message_pk, kind, note, created_at) VALUES (?,?,?,?,?)",
                            (lead_id, message_pk, "email_in" if created else "follow_up", a.summary or a.email.subject, now()))
            self.db.execute("UPDATE messages SET lead_id = ? WHERE id = ?", (lead_id, message_pk))
        return lead_id, created

    def leads(self) -> list[dict]:
        rows = self._all("SELECT l.*, (SELECT COUNT(*) FROM activities a WHERE a.lead_id = l.id) AS emails"
                         " FROM leads l ORDER BY l.score DESC, l.id DESC")
        for row in rows:
            row["product_interest"] = json.loads(row["product_interest"] or "[]")
            row["score_reasons"] = json.loads(row["score_reasons"] or "[]")
        return rows

    # -- review queue ---------------------------------------------------------------------

    def enqueue_review(self, message_pk: int, reason: str) -> int:
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO review_queue (message_pk, reason, created_at) VALUES (?,?,?)",
                                  (message_pk, reason, now()))
            return int(cur.lastrowid)

    def reviews(self, status: str | None = "pending") -> list[dict]:
        rows = self._all(
            "SELECT r.*, m.analysis FROM review_queue r JOIN messages m ON m.id = r.message_pk"
            + (" WHERE r.status = ?" if status else "") + " ORDER BY r.id", (status,) if status else ())
        for row in rows:
            row["analysis"] = json.loads(row["analysis"])
        return rows

    def review(self, review_id: int) -> dict | None:
        rows = [r for r in self.reviews(None) if r["id"] == review_id]
        return rows[0] if rows else None

    def resolve_review(self, review_id: int, status: str, note: str = "", lead_id: int | None = None) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE review_queue SET status = ?, reviewer_note = ?, lead_id = ?, resolved_at = ? WHERE id = ?",
                            (status, note, lead_id, now(), review_id))

    # -- the rest -------------------------------------------------------------------------

    def notify(self, lead_id: int | None, channel: str, text: str) -> int:
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO notifications (lead_id, channel, text, created_at) VALUES (?,?,?,?)",
                                  (lead_id, channel, text, now()))
            return int(cur.lastrowid)

    def notifications(self) -> list[dict]:
        return self._all("SELECT * FROM notifications ORDER BY id DESC")

    def messages(self, limit: int = 200) -> list[dict]:
        return self._all("SELECT id, message_id, from_email, from_name, subject, category, route, route_reason, lead_id,"
                         " created_at FROM messages ORDER BY id DESC LIMIT ?", (limit,))

    def stats(self) -> dict:
        one = lambda sql: self._all(sql)[0]["n"]  # noqa: E731
        return {"messages": one("SELECT COUNT(*) n FROM messages"), "leads": one("SELECT COUNT(*) n FROM leads"),
                "hot_leads": one("SELECT COUNT(*) n FROM leads WHERE tier = 'hot'"),
                "review_pending": one("SELECT COUNT(*) n FROM review_queue WHERE status = 'pending'"),
                "archived": one("SELECT COUNT(*) n FROM messages WHERE route = 'archive'"),
                "by_category": {r["category"]: r["n"] for r in
                                self._all("SELECT category, COUNT(*) n FROM messages GROUP BY category")}}
