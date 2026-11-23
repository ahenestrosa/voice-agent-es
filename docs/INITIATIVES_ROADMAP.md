# Initiatives roadmap

An incremental plan to take the Banco Austral voice agent from a laptop demo to a production-shaped deployment. Each phase ships on its own and leaves the system working. Later phases build on earlier ones, but none of them needs the full plan to land.

The words used here (Caller, Customer, Call, Verification, Session) are defined in [`GLOSSARY.md`](../GLOSSARY.md).

## Where we are

- The agent, its prompt and four webhook tools are deployed by a custom script (`scripts/deploy.py`) through the ElevenLabs REST API.
- The FastAPI backend stores the bank in MySQL 8 (Docker Compose). Each Session is bound to its Call through `system__conversation_id`, with a 30-minute expiry.
- Nine behavioral evals run on the ElevenLabs agent-testing API from `scripts/run_evals.py`.
- Tests, ruff and pyright run only by hand.
- The backend reaches ElevenLabs through a cloudflared quick tunnel, which dies when the laptop sleeps.
- Webhooks authenticate with a plain `X-Tool-Secret` header stored in the tool config.
- The only way to talk to the agent is the dashboard Preview.

## Phase 0: Hardening the current client

Small fixes that the next phases depend on.

- Retry HTTP 504 with exponential backoff in `scripts/elevenlabs_api.py`. ElevenLabs starts enforcing server-side timeouts on 2026-11-26.
- Add a LICENSE.

**Done when:** a deploy or eval run survives one injected 504.

## Phase 1: Agents as code and CI

Move the agent definition to the official ElevenLabs CLI (`elevenlabs agents`). Gate every change behind CI.

- Restructure `agent/`, `agent/tools.json` and `evals/tests.json` into the CLI layout: the `agents.json`, `tools.json` and `tests.json` registries, plus `agent_configs/`, `tool_configs/` and `test_configs/`.
- Keep a small bootstrap script for what the CLI does not manage: workspace secrets, auth connections and environment variables.
- Retire `scripts/deploy.py` once `push` and `pull` cover the agent, tools and tests. `scripts/run_evals.py` shrinks to report rendering, or goes away if `elevenlabs agents test` covers it.
- Add a GitHub Actions workflow with two parts:
  - **On every PR:** pytest against a MySQL service container, then ruff, pyright and `elevenlabs agents push --dry-run`.
  - **On merge to main:** `push`, then `elevenlabs agents test` with a repeat count. The build fails if the pass rate drops below a threshold.

**Done when:** a prompt change reaches the agent only through a merged PR with green evals.

## Phase 2: Secrets and authentication

Take credentials out of the tool config.

- Store the tool secret as a workspace secret. The `X-Tool-Secret` header then references it by `secret_id` instead of holding the value.
- Evaluate auth connections (`bearer_auth`, or `mtls` for a stricter setup) as the long-term replacement for the shared header.
- Rotate the secret, and document the rotation procedure.

**Done when:** no secret value appears in any file in the repo or in a tool config returned by the API.

## Phase 3: Stable hosting

Replace the tunnel with a permanent deployment.

- Add a Dockerfile for the backend. Run migrations on startup, or as a separate release step.
- Deploy the backend to a managed container platform (Fly.io, Render or Cloud Run), and the database to a managed MySQL.
- Point the tools at a fixed `BACKEND_URL`.
- Add a `/health` endpoint that checks the database. Have the platform probe it.

**Done when:** the agent works after a laptop reboot with no redeploy.

## Phase 4: Call lifecycle and production analysis

Close the loop on real calls, not only test calls.

- Register a **post-call webhook** on the backend:
  - Verify its HMAC signature (the `ElevenLabs-Signature` header) with the SDK's `construct_event`.
  - Reject unsigned or stale events.
- On `post_call_transcription`, end the Session for that `conversation_id`. The 30-minute expiry stays as a backstop only.
- Store each Call's transcript summary, duration, cost and analysis results in a new `calls` table.
- Configure **success evaluation criteria** that grade every real Call:
  - Was the Caller verified before any account data was read?
  - Was an explicit "sí" given before `block_card`?
  - Were out-of-scope requests routed to a human?
- Configure **data collection** to extract structured fields: the Caller's intent, the block reason, and whether the Call was escalated.

Verify the exact `platform_settings.evaluation` and `data_collection` schemas against the API reference before implementing.

**Done when:** every finished Call has a row in `calls` with its evaluation results, and its Session is closed within seconds of hang-up.

## Phase 5: Conversation controls

Replace prompt-only rules with platform features where one exists.

- Add the `transfer_to_number` system tool for out-of-scope requests and failed Verification. It replaces reading the 0800 number aloud.
- Add the `end_call` system tool, so the agent closes the Call cleanly.
- Enable **guardrails**: prompt-injection protection, plus a focus guardrail for banking-only scope. Run them in streaming mode, because blocking mode adds about 200–500 ms per turn.
- Add tool-call tests that assert each guardrail fires on a known injection attempt.

**Done when:** an injection attempt in an eval ends in a guardrail trigger, and an out-of-scope request ends in a transfer.

## Phase 6: Latency and voice quality

Tune for the phone, where silence reads as failure.

- Set `turn_eagerness` and the turn timeouts for a stressed Caller who pauses mid-sentence.
- Set `pre_tool_speech` on every webhook tool, so the agent fills the wait ("dame un segundo") while a tool runs.
- Compare TTS models (Flash vs Turbo vs v4) on latency and Rioplatense quality.
- Set text normalization for amounts and dates, and add a pronunciation dictionary for "DNI" and card terms.
- Configure an **LLM cascade**: a backup model that takes over if the primary model exceeds a timeout.
- Track per-tool latency, which the backend already logs, against a budget of about 1 s per spoken turn.

**Done when:** the p95 time-to-first-audio after a tool call is under the budget on the analytics dashboard.

## Phase 7: Release management

Ship prompt and model changes without risking all traffic.

- Enable agent **versioning**. Versioning cannot be turned off once enabled, so decide this on purpose.
- Work on branches. Roll a change out with a **traffic split** (for example 90/10), then compare the success-evaluation rates from Phase 4 before merging.
- Use **environments** (`{{system__env_*}}` variables) so one agent serves dev and prod backends.
- Make the CI from Phase 1 push to a branch, not straight to production.

**Done when:** a prompt change runs at 10% of traffic, is compared on real-call evaluations, and is promoted or rolled back without a redeploy.

## Phase 8: Access channels

Let people reach the agent outside the dashboard.

- **Web:** a demo page with the embeddable widget. It uses signed URLs and a host allowlist, so the agent is not open to any site.
- **Phone:** import a Twilio number (or connect a SIP trunk) and pin it to the production environment.

**Done when:** a Caller can reach the agent from a web page and from a real phone number.

## Later, if the scope grows

- **Knowledge base (RAG)** for bank FAQs such as fees, branch hours and card limits. It adds about 250 ms per retrieval, so enable it only if the FAQ scope justifies it.
- **Agent workflows:** split the agent into a Verification subagent and a banking subagent once the tool count grows past what one prompt handles well.
- **Observability:** forward the OpenTelemetry-shaped transcripts that ElevenLabs exports to a tracing backend.
- **Compliance:**
  - Data retention days, and turning audio saving off.
  - Zero Retention Mode, which restricts LLM choice and requires the post-call webhook from Phase 4.
  - PII redaction and HIPAA support are Enterprise features, recorded here as requirements for a real bank.
  - A second verification factor, such as an OTP to the registered phone.
