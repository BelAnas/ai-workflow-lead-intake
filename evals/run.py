"""Measures the pipeline on the labelled emails and writes a JSON report.

    python -m evals.run                       # offline provider, all 160 emails
    python -m evals.run --per-kind 3          # a sample: 3 emails of every kind
    LLM_PROVIDER=openai python -m evals.run   # a real model (see .env.example)

What is measured:
- category: is the email filed under the right category;
- fields: for true leads, each extracted field against the label (a field that should be
  empty and is empty counts as correct);
- routing: where true leads end up, and what reaches the CRM that should not.

With --gate the script exits with an error if a prompt-injection email reaches the CRM, so
CI can refuse a change that opens that door.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from lead_intake.analyze import analyze
from lead_intake.config import DATA, Settings
from lead_intake.llm import make_provider
from lead_intake.schema import Analysis, EmailIn
from lead_intake.textutil import digits, norm

FIELDS = ["contact_name", "company", "email", "phone", "job_title", "country", "product_interest", "budget_amount",
          "budget_currency", "timeline"]


def same(field: str, got, want) -> bool:
    if field == "phone":
        return digits(got) == digits(want)
    if field == "product_interest":
        return sorted(got or []) == sorted(want or [])
    if field == "budget_amount":
        return (got is None) == (want is None) and (got is None or abs(got - want) < 0.01)
    return norm(str(got)) == norm(str(want)) if got is not None and want is not None else got is None and want is None


def pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def summarise(rows: list[dict]) -> dict:
    """Metrics for one group of evaluated emails."""
    leads = [r for r in rows if r["true"] == "lead"]
    others = [r for r in rows if r["true"] != "lead"]
    field_ok = Counter()
    for r in leads:
        for f in FIELDS:
            field_ok[f] += r["field_ok"][f]
    in_crm = [r for r in leads if r["route"] == "crm"]
    return {
        "emails": len(rows),
        "category_accuracy_pct": pct(sum(r["true"] == r["pred"] for r in rows), len(rows)),
        "true_leads": len(leads),
        "leads_recognised_pct": pct(sum(r["pred"] == "lead" for r in leads), len(leads)),
        "field_accuracy_pct": pct(sum(field_ok.values()), len(leads) * len(FIELDS)),
        "field_accuracy_by_field_pct": {f: pct(field_ok[f], len(leads)) for f in FIELDS},
        "leads_to_crm": len(in_crm),
        "leads_to_review": sum(r["route"] == "review" for r in leads),
        "leads_lost_in_archive": sum(r["route"] == "archive" for r in leads),
        "crm_records_all_fields_correct": sum(all(r["field_ok"].values()) for r in in_crm),
        "crm_records_wrong_contact": sum(not (r["field_ok"]["email"] and r["field_ok"]["contact_name"]) for r in in_crm),
        "non_leads": len(others),
        "non_leads_filed_in_crm": sum(r["route"] == "crm" for r in others),
        "non_leads_sent_to_review": sum(r["route"] == "review" for r in others),
        "review_share_pct": pct(sum(r["route"] == "review" for r in rows), len(rows)),
    }


def evaluate(per_kind: int | None = None, settings: Settings | None = None, verbose: bool = True) -> dict:
    settings = settings or Settings.from_env()
    provider = make_provider(settings)
    data = [json.loads(line) for line in (DATA / "emails.jsonl").read_text().splitlines()]
    if per_kind:
        taken: dict[tuple, int] = defaultdict(int)
        sample = []
        for row in data:
            key = (row["style"], row["kind"])
            if taken[key] < per_kind:
                taken[key] += 1
                sample.append(row)
        data = sample
    rows, usage = [], Counter()
    for n, row in enumerate(data, 1):
        a: Analysis = analyze(EmailIn(**row["email"]), provider, settings)
        want = row["label"].get("lead", {})
        rows.append({"id": row["id"], "style": row["style"], "kind": row["kind"], "true": row["label"]["category"],
                     "pred": a.category, "route": a.route, "route_reason": a.route_reason, "score": a.score, "tier": a.tier,
                     "flags": a.flags, "notes": a.notes,
                     "field_ok": {f: same(f, getattr(a.lead, f), want.get(f)) for f in FIELDS} if want else {},
                     "extracted": a.lead.model_dump() if want else None})
        usage.update(tokens_in=a.usage.tokens_in, tokens_out=a.usage.tokens_out)
        usage["cost_usd"] += a.usage.cost_usd
        usage["seconds"] += a.usage.seconds
        if verbose and provider.name != "mock":
            print(f"  {n}/{len(data)} {row['id']} {row['kind']}: {a.category} -> {a.route} ({a.usage.seconds}s)", file=sys.stderr, flush=True)
    injections = [r for r in rows if r["kind"] == "prompt_injection"]
    by_kind = defaultdict(list)
    for r in rows:
        by_kind[f"{r['style']}/{r['kind']}"].append(r)
    return {
        "provider": provider.name,
        "emails": len(rows),
        "all": summarise(rows),
        "plain": summarise([r for r in rows if r["style"] == "plain"]),
        "hard": summarise([r for r in rows if r["style"] == "hard"]),
        "by_kind": {k: {"emails": len(v), "category_correct": sum(r["true"] == r["pred"] for r in v),
                        "routes": dict(Counter(r["route"] for r in v))} for k, v in by_kind.items()},
        "prompt_injection": {"emails": len(injections), "reached_crm": sum(r["route"] == "crm" for r in injections),
                             "classified_as_lead_by_model": sum(r["pred"] == "lead" for r in injections)},
        "usage": {"tokens_in": usage["tokens_in"], "tokens_out": usage["tokens_out"], "cost_usd": round(usage["cost_usd"], 4),
                  "seconds_per_email": round(usage["seconds"] / max(len(rows), 1), 2)},
        "rows": rows,
    }


def show(report: dict) -> None:
    print(f"provider: {report['provider']}   emails: {report['emails']}")
    names = [("emails", "emails"), ("category_accuracy_pct", "category correct %"), ("true_leads", "true leads"),
             ("leads_recognised_pct", "leads recognised %"), ("field_accuracy_pct", "lead fields correct %"),
             ("leads_to_crm", "leads filed in CRM"), ("crm_records_all_fields_correct", "  of which every field correct"),
             ("crm_records_wrong_contact", "  of which wrong name or email"), ("leads_to_review", "leads sent to review"),
             ("leads_lost_in_archive", "leads lost in archive"), ("non_leads_filed_in_crm", "non-leads filed in CRM"),
             ("non_leads_sent_to_review", "non-leads sent to review"), ("review_share_pct", "share sent to review %")]
    print(f"{'':34}{'all':>8}{'plain':>8}{'hard':>8}")
    for key, label in names:
        print(f"{label:34}" + "".join(f"{str(report[g][key]):>8}" for g in ("all", "plain", "hard")))
    inj = report["prompt_injection"]
    print(f"prompt-injection emails: {inj['emails']}, taken for a lead by the provider: {inj['classified_as_lead_by_model']}, "
          f"reached the CRM: {inj['reached_crm']}")
    u = report["usage"]
    print(f"tokens in/out: {u['tokens_in']}/{u['tokens_out']}   cost: ${u['cost_usd']}   seconds per email: {u['seconds_per_email']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-kind", type=int, help="evaluate only this many emails of every kind")
    parser.add_argument("--out", help="where to write the JSON report")
    parser.add_argument("--gate", action="store_true", help="exit with an error if an injection email reaches the CRM")
    args = parser.parse_args()
    report = evaluate(args.per_kind)
    show(report)
    out = Path(args.out or f"evals/report-{report['provider'].replace(':', '-').replace('/', '-')}.json")
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(f"report written to {out}")
    if args.gate and report["prompt_injection"]["reached_crm"]:
        sys.exit("GATE FAILED: a prompt-injection email reached the CRM")


if __name__ == "__main__":
    main()
