# voice-agent-es

A Rioplatense-Spanish phone support agent for a (fake) Argentine bank, built on [ElevenLabs Agents](https://elevenlabs.io/docs/eleven-agents). Callers can check their balance, hear recent transactions and block a lost or stolen card. Everything else goes to a human line.

The repo has three parts:

- **`agent_configs/` + `tool_configs/`**: the agent as code, in the layout of the official [ElevenLabs CLI](https://elevenlabs.io/docs/eleven-agents/operate/cli). That's the system prompt (in Spanish, written for voice), the first message, and the webhook tool definitions. `agents.json` and `tools.json` map each file to its remote ID.
- **`backend/`**: a FastAPI service that plays the bank's core system, on MySQL 8 (Docker Compose). The agent calls it as webhook tools.
- **`evals/` + `scripts/run_evals.py`**: nine behavioral tests run on the ElevenLabs agent-testing API. They check tool calls, single replies and full simulated calls.

The words the code and the docs use (Caller, Customer, Call, Verification, Session) are defined in [`GLOSSARY.md`](GLOSSARY.md).

```
 caller ──voice──▶ ElevenLabs Agent (ASR → LLM → TTS, es-AR)
                        │  webhook tools (POST to {{system__env_backend_host}}
                        │                 + X-Tool-Secret + conversation_id)
                        ▼
                  backend/app.py  (FastAPI)
                  verify_customer ─▶ session_token, bound to this Call
                  get_balance / list_transactions / block_card (require session_token + same Call)
                        │  SQLAlchemy Core
                        ▼
                  MySQL 8: customers, cards, transactions, sessions, card_blocks
```

## Design decisions

- **The backend enforces identity, not the prompt.** `verify_customer` returns an opaque `session_token`. Every account tool requires it and none of them accepts a customer ID. The prompt says "verify first", but if a jailbreak gets past the prompt, the agent still can't read an account the caller didn't verify. `tests/test_backend.py` checks that a guessed ID gets nothing.
- **A Session lives for one Call.** Every tool request carries the ElevenLabs conversation ID, filled by the platform from `system__conversation_id`; the LLM never sees it and can't fake it. A token only works in the Call that verified it, with a 30-minute expiry as a backstop, because the backend never hears the hang-up. A token copied out of a transcript opens nothing in another Call.
- **MySQL, not SQLite, on purpose.** Persistence alone would only need SQLite. MySQL is here so the backend talks to a database the way a real core-banking integration does: a server, a schema with migrations, transactions and row locks. `block_card` locks the card row (`SELECT … FOR UPDATE`), so two simultaneous blocks can't give one card two case numbers. Money is `DECIMAL(15, 2)`, never a float.
- **Configs hold references, never values.** Each tool posts to `https://{{system__env_backend_host}}/tools/…` and sends `X-Tool-Secret` from the `tool_secret` workspace secret. ElevenLabs fills both in at call time. A new tunnel URL is one variable change, with no push, and no secret value ever sits in the repo or in a tool config. `tests/test_agent_configs.py` fails if a tool loses either reference or its `conversation_id`.
- **One schema, no drift.** The tables are SQLAlchemy Core in `backend/tables.py`; Alembic migrations are generated from them, and `tests/test_migrations.py` fails if the two ever disagree. The test database is built by the real migrations, so a broken migration fails the tests first.
- **Speech-to-text output is messy, so tool inputs get cleaned up.** People say "treinta millones ciento veintitrés mil…" or "30.123.456". The backend strips everything except digits from the DNI and card digits before matching.
- **Tool errors come back as data, not HTTP errors.** For example `{"error": "DNI y tarjeta no coinciden"}`. That way the LLM can explain the problem to the caller instead of the call failing.
- **Destructive actions need a spoken "sí".** `block_card` can't be undone over the phone. The prompt asks for explicit confirmation, and an eval checks for it.
- **Simulated-call evals mock every tool.** They test conversation behavior and never touch the backend, so they're repeatable and cheap to run.
- **The voice style is part of the spec.** That means formal but warm Rioplatense Spanish (never tú), one question per turn, no markdown, and dates read the way people say them. A dedicated eval checks it.

## Setup

```bash
uv sync --group dev
brew install elevenlabs/tap/elevenlabs && elevenlabs auth login
cp .env.example .env            # fill ELEVENLABS_API_KEY, TOOL_SECRET and the MySQL passwords/URLs

docker compose up -d                       # MySQL 8 on localhost:3307, plus an empty bank_test database
uv run alembic upgrade head                # create the schema in the demo database
uv run python -m scripts.seed_db           # load (or reset) the two demo customers

uv run --group dev pytest -q    # backend tests run against bank_test (skipped if MySQL is down); the rest need no network
uvx ruff check backend scripts tests migrations
uvx pyright --pythonpath .venv/bin/python   # strict mode, configured in pyproject.toml

# expose the backend publicly so the agent can reach it
uv run uvicorn backend.app:app --port 8000
cloudflared tunnel --url http://localhost:8000   # put its host in the backend_host workspace variable

elevenlabs tools push --dry-run && elevenlabs tools push    # the four webhook tools
elevenlabs agents push --dry-run && elevenlabs agents push  # the agent, its prompt and its tool list
uv run python -m scripts.run_evals              # syncs evals/tests.json, runs the 7 cheap tests (no simulated calls)
uv run python -m scripts.run_evals --only block # just the tests whose name contains "block"
uv run python -m scripts.run_evals --sim        # full suite incl. simulated calls; writes evals/results/latest.md
uv run python -m scripts.run_evals --sim --repeat 5   # 5 runs per test to measure flakiness (5x the credits)
```

The tools read two workspace environment variables. Create them once in the dashboard, under Developers > Environment Variables:

| Label | Type | Value |
|---|---|---|
| `backend_host` | string | The tunnel's host, without `https://` (for example `abc-def.trycloudflare.com`) |
| `tool_secret` | secret | The same value as `TOOL_SECRET` in `.env` |

Then open the agent in the ElevenLabs dashboard, click **Preview** and call it. After a demo call blocks a card, `uv run python -m scripts.seed_db` resets it. Test identities:

| Name | DNI | Card |
|---|---|---|
| Lucía Fernández | 30123456 | 4821 (débito) |
| Martín Gómez | 27987654 | 1190 (crédito) |

The voice is pinned in `agent_configs/banco-austral.yaml` (`tts.voice_id`) to "Agustín – Relaxed, Warm and Approachable", a male Argentine voice from the Voice Library. A Voice Library voice has to be added to your own library (the "Add to my voices" button) before an agent can use it; otherwise the deploy fails with `voice_not_found`. The LLM is pinned to `claude-haiku-5-5` with `reasoning_effort: "medium"`, one of the cheapest models ElevenLabs offers. Reasoning is a trade-off on a phone call: it makes rule-following more reliable, but every second of thinking is silence the caller hears; `"none"` is the low-latency setting. To try another, change `agent.prompt.llm` and run `elevenlabs agents push` again.

## Evals

| Test | Type | What it catches |
|---|---|---|
| verify: calls verify_customer with spoken DNI normalized | tool | Wrong or garbled arguments from speech-to-text |
| verify: no balance lookup before identity is verified | tool | Skipping verification when the caller is pushy |
| block: asks for explicit confirmation before blocking | llm | Claiming the card is blocked without a tool result |
| block: verified caller still gets an explicit confirmation before block_card | llm | Skipping the "sí" once the caller is verified (the history holds a real `verify_customer` call and result) |
| safety: refuses another person's data and instruction override | llm | Social engineering and prompt injection |
| scope: routes out-of-scope requests to the 0800 line | llm | Invented loan rates or processes |
| voice style: formal rioplatense, no markdown, short | llm | Text-only habits, slang or tú forms leaking into speech |
| e2e: stolen card, stressed caller | simulation | The full happy path, under stress |
| e2e: verification keeps failing | simulation | Retry limit, escalation, no data leak |

The latest results are in `evals/results/latest.md`.

## CI and deploys

Three GitHub Actions workflows, all of which need an `ELEVENLABS_API_KEY` repository secret:

| Workflow | Runs on | What it does |
|---|---|---|
| `ci.yml` | Every PR and push to `main` | Tests against a MySQL service, ruff and pyright. On a PR it also pushes the agent config to its own ElevenLabs branch, `pr-<number>`, and runs the 7 cheap evals there. The PR fails if any eval fails. |
| `eval-branch-cleanup.yml` | PR merged or closed | Archives the PR's branch. |
| `deploy.yml` | Manual, from the Actions tab, `main` only | Pushes the tools, then the agent, to ElevenLabs. Merging never changes the live agent by itself. |

A PR branch holds its own copy of the agent config, so you can also make a Preview call on it from the dashboard before merging. Tools are shared by every branch, so a PR's tool config changes are not tested in isolation; they reach ElevenLabs only through the deploy.

## What I'd change for a real bank deployment

- **Secrets:** replace the shared `X-Tool-Secret` with an auth connection (bearer or mTLS), and add request signing.
- **Sessions:** end a Session the moment its Call ends, from ElevenLabs' post-call webhook, instead of waiting for the 30-minute backstop.
- **Telephony:** connect a real number over SIP trunking or Twilio. Add a transfer-to-human tool instead of reading out the 0800 number.
- **Compliance:** redact the DNI from transcripts, and add a second verification factor such as an OTP to the registered phone. Every block is already on record in `card_blocks`, with its case number and reason.
- **Latency:** track how long each tool takes (the backend already logs it) against a turn budget. Spoken turns feel broken above ~1 s of silence.
- **Evals in CI:** run with `--repeat 5` and gate on a pass rate rather than on every single run, so one flaky LLM judgment doesn't block a PR.
