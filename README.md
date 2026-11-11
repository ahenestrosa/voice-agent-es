# voice-agent-es

A Rioplatense-Spanish phone support agent for a (fake) Argentine bank, built on [ElevenLabs Agents](https://elevenlabs.io/docs/eleven-agents). Callers can check their balance, hear recent transactions and block a lost or stolen card. Everything else goes to a human line.

The repo has three parts:

- **`agent/`**: the agent as code. That's the system prompt (in Spanish, written for voice), the first message, and the webhook tool definitions.
- **`backend/`**: a FastAPI service that plays the bank's core system. The agent calls it as webhook tools.
- **`evals/` + `scripts/run_evals.py`**: eight behavioral tests run on the ElevenLabs agent-testing API. They check tool calls, single replies and full simulated calls.

```
 caller ──voice──▶ ElevenLabs Agent (ASR → LLM → TTS, es-AR)
                        │  webhook tools (POST + X-Tool-Secret)
                        ▼
                  backend/app.py  (FastAPI)
                  verify_customer ─▶ session_token
                  get_balance / list_transactions / block_card (require session_token)
```

## Design decisions

- **The backend enforces identity, not the prompt.** `verify_customer` returns an opaque `session_token`. Every account tool requires it and none of them accepts a customer ID. The prompt says "verify first", but if a jailbreak gets past the prompt, the agent still can't read an account the caller didn't verify. `tests/test_backend.py` checks that a guessed ID gets nothing.
- **Speech-to-text output is messy, so tool inputs get cleaned up.** People say "treinta millones ciento veintitrés mil…" or "30.123.456". The backend strips everything except digits from the DNI and card digits before matching.
- **Tool errors come back as data, not HTTP errors.** For example `{"error": "DNI y tarjeta no coinciden"}`. That way the LLM can explain the problem to the caller instead of the call failing.
- **Destructive actions need a spoken "sí".** `block_card` can't be undone over the phone. The prompt asks for explicit confirmation, and an eval checks for it.
- **Simulated-call evals mock every tool.** They test conversation behavior and never touch the backend, so they're repeatable and cheap to run.
- **The voice style is part of the spec.** That means voseo, one question per turn, no markdown, and amounts and dates read the way people say them. A dedicated eval checks it.

## Setup

```bash
uv sync --group dev
cp .env.example .env            # fill ELEVENLABS_API_KEY and TOOL_SECRET
uv run pytest -q                # backend tests

# expose the backend publicly so the agent can reach it
uv run uvicorn backend.app:app --port 8000
cloudflared tunnel --url http://localhost:8000   # put the https URL in .env as BACKEND_URL

uv run python -m scripts.deploy      # creates/updates tools + agent (IDs saved in .state.json)
uv run python -m scripts.run_evals              # syncs evals/tests.json, runs the 6 cheap tests (no simulated calls)
uv run python -m scripts.run_evals --only block # just the tests whose name contains "block"
uv run python -m scripts.run_evals --sim        # full suite incl. simulated calls; writes evals/results/latest.md
uv run python -m scripts.run_evals --sim --repeat 5   # 5 runs per test to measure flakiness (5x the credits)
```

Then open the agent in the ElevenLabs dashboard and call it. Test identities:

| Name | DNI | Card |
|---|---|---|
| Lucía Fernández | 30123456 | 4821 (débito) |
| Martín Gómez | 27987654 | 1190 (crédito) |

The voice is pinned in `agent/agent.json` (`tts.voice_id`) to "Agustín – Relaxed, Warm and Approachable", a male Argentine voice from the Voice Library. A Voice Library voice has to be added to your own library (the "Add to my voices" button) before an agent can use it; otherwise the deploy fails with `voice_not_found`. The LLM is the account default; to pin it, set `agent.prompt.llm` and deploy again.

## Evals

| Test | Type | What it catches |
|---|---|---|
| verify: calls verify_customer with spoken DNI normalized | tool | Wrong or garbled arguments from speech-to-text |
| verify: no balance lookup before identity is verified | tool | Skipping verification when the caller is pushy |
| block: asks for explicit confirmation before blocking | llm | Claiming the card is blocked without a tool result |
| safety: refuses another person's data and instruction override | llm | Social engineering and prompt injection |
| scope: routes out-of-scope requests to the 0800 line | llm | Invented loan rates or processes |
| voice style: rioplatense voseo, no markdown | llm | Text-only habits leaking into speech |
| e2e: stolen card, stressed caller | simulation | The full happy path, under stress |
| e2e: verification keeps failing | simulation | Retry limit, escalation, no data leak |

The latest results are in `evals/results/latest.md`.

## What I'd change for a real bank deployment

- **Secrets:** move `X-Tool-Secret` to a workspace secret reference or an auth connection, and add request signing.
- **Sessions:** make tokens expire with the call, tie each one to the conversation ID, and keep sessions in Redis instead of process memory.
- **Telephony:** connect a real number over SIP trunking or Twilio. Add a transfer-to-human tool instead of reading out the 0800 number.
- **Compliance:** redact the DNI from transcripts, keep an audit log of every `block_card`, and add a second verification factor such as an OTP to the registered phone.
- **Latency:** track how long each tool takes (the backend already logs it) against a turn budget. Spoken turns feel broken above ~1 s of silence.
- **Evals in CI:** run `run_evals --repeat 5` on every prompt or tool change, and fail the build if the pass rate drops.
