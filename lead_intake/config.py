"""Settings, read once from the environment (and from a local .env file if there is one)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# The business this demo is configured for. Changing the catalogue means editing this
# block and the scoring rules, not the pipeline.
COMPANY = "Meridian Warehouse Equipment"
OWN_DOMAIN = "meridian-we.example"
CATALOGUE = {
    "pallet_racking": "pallet racking for palletised stock",
    "shelving": "shelving for hand-picked items",
    "conveyors": "conveyor lines",
    "mezzanine": "mezzanine floors",
    "packing_stations": "packing benches and packing stations",
}

# USD per million tokens (input, output), used only for the cost shown in results. A model
# that is not listed is reported at 0, which is right for local models only.
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40)}


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader. Real environment variables win over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Settings:
    provider: str = "mock"            # "mock" (offline rules) or "openai" (any OpenAI-compatible API)
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    base_url: str | None = None       # e.g. http://localhost:11434/v1 for a local Ollama model
    max_output_tokens: int = 400      # cost ceiling per email
    api_token: str | None = None      # shared secret for the API; None means no check
    db_path: str = str(ROOT / "leads.db")
    hot_score: int = 70               # at or above: hot, sales is notified at once
    warm_score: int = 40              # at or above: warm; below: cold

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        env = os.environ.get
        return cls(
            provider=(env("LLM_PROVIDER") or "mock").lower(),
            model=env("OPENAI_MODEL") or "gpt-4o-mini",
            api_key=env("OPENAI_API_KEY") or None,
            base_url=env("OPENAI_BASE_URL") or None,
            max_output_tokens=int(env("MAX_OUTPUT_TOKENS") or "400"),
            api_token=env("API_TOKEN") or None,
            db_path=env("LEADS_DB") or str(ROOT / "leads.db"),
        )
