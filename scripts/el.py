"""Thin ElevenLabs REST helper plus the local state file that maps names to remote IDs."""

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".state.json"
load_dotenv(ROOT / ".env")

client = httpx.Client(
    base_url="https://api.elevenlabs.io",
    headers={"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")},
    timeout=60,
)


def api(method: str, path: str, body: dict | None = None) -> dict:
    r = client.request(method, path, json=body)
    if r.is_error:
        raise SystemExit(f"{method} {path} -> {r.status_code}\n{r.text}")
    return r.json() if r.content else {}


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"tools": {}, "tests": {}}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))
