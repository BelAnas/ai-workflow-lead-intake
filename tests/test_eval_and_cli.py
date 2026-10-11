from __future__ import annotations

import json
import subprocess
import sys

from evals.run import evaluate, same
from lead_intake.cli import samples
from lead_intake.config import ROOT, Settings


def test_field_comparison():
    assert same("phone", "+44 (20) 7946-0123", "+44 20 7946 0123")
    assert same("company", "acme  ltd.", "Acme Ltd")
    assert same("product_interest", ["shelving", "conveyors"], ["conveyors", "shelving"])
    assert same("budget_amount", None, None) and not same("budget_amount", 100.0, None)
    assert same("country", None, None) and not same("country", "France", None) and not same("country", None, "France")


def test_evaluation_report_on_a_sample():
    report = evaluate(per_kind=1, settings=Settings(), verbose=False)
    assert report["provider"] == "mock" and report["emails"] == len(report["rows"]) == 20
    assert report["plain"]["category_accuracy_pct"] == 100.0
    assert report["prompt_injection"] == {"emails": 1, "reached_crm": 0, "classified_as_lead_by_model": 0}
    total = report["all"]
    assert total["leads_to_crm"] + total["leads_to_review"] + total["leads_lost_in_archive"] == total["true_leads"]


def test_samples_cover_every_kind(dataset):
    kinds = {row["kind"] for row in dataset}
    assert {row["kind"] for row in samples(len(kinds))} == kinds


def test_cli_process_and_demo(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "LEADS_DB": str(tmp_path / "cli.db"), "LLM_PROVIDER": "mock"}
    email = json.dumps({"from_name": "Ann Lee", "from_email": "ann@boltdepot.example", "subject": "Quote",
                        "body": "Hello, we need 40 bays of shelving asap.\n\nThanks,\nAnn Lee\nBolt Depot"})
    done = subprocess.run([sys.executable, "-m", "lead_intake.cli", "process", "-"], input=email, capture_output=True, text=True, cwd=ROOT, env=env)
    out = json.loads(done.stdout)
    assert (out["category"], out["route"], out["lead"]["company"]) == ("lead", "crm", "Bolt Depot")
    demo = subprocess.run([sys.executable, "-m", "lead_intake.cli", "demo", "--count", "8", "--fresh"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert demo.returncode == 0 and '"messages": 8' in demo.stdout
