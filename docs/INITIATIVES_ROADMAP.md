# Initiatives roadmap

An incremental plan to take the Banco Austral voice agent from a laptop demo to a production-shaped deployment. Each phase ships on its own and leaves the system working. Later phases build on earlier ones, but none of them needs the full plan to land.

The words used here (Caller, Customer, Call, Verification, Session) are defined in [`GLOSSARY.md`](../GLOSSARY.md).

## Where we are

- The agent and its four webhook tools live in the ElevenLabs CLI layout (`agent_configs/`, `tool_configs/`, `agents.json`, `tools.json`) and are pushed with `elevenlabs agents push` and `elevenlabs tools push`.
- The FastAPI backend stores the bank in MySQL 8 (Docker Compose). Each Session is bound to its Call through `system__conversation_id`, with a 30-minute expiry.
- Nine behavioral evals run on the ElevenLabs agent-testing API from `scripts/run_evals.py`.
- GitHub Actions checks every PR and runs the cheap evals on the PR's own agent branch. Deploys are manual.
- The backend reaches ElevenLabs through a cloudflared quick tunnel, which dies when the laptop sleeps. The tools find it through the `backend_host` workspace variable, so a new tunnel needs no push.
- Webhooks authenticate with a shared bearer token (`Authorization: Bearer`), sent from the `tool_secret` workspace secret.
- The only way to talk to the agent is the dashboard Preview.

## Phase 0: Hardening the current client

Small fixes that the next phases depend on.

- Retry HTTP 504 with exponential backoff in `scripts/elevenlabs_api.py`. ElevenLabs starts enforcing server-side timeouts on 2026-11-26.
- Add a LICENSE.

**Done when:** a deploy or eval run survives one injected 504.

## Phase 1: Agents as code and CI — done

Moved the agent definition to the official ElevenLabs CLI and gated every change behind CI.

- The agent and tools use the CLI layout. `scripts/deploy.py` and `agent/` are gone.
- Tool URLs and the secret header reference workspace variables (`{{system__env_backend_host}}`, `env_var_label: tool_secret`), so the configs are committed as they are pushed. `tests/test_agent_configs.py` guards those references and `conversation_id`.
- `ci.yml` runs pytest against a MySQL service, ruff and pyright on every PR. On a PR it also pushes the agent config to an ElevenLabs branch, `pr-<number>`, and runs the 7 cheap evals there; a failed eval fails the PR. `eval-branch-cleanup.yml` archives the branch when the PR closes.
- `deploy.yml` pushes tools and agent from `main`, only when started by hand.

Decided differently from the plan:

- **Evals stay in `evals/tests.json`.** Moving them to the CLI's `test_configs/` was skipped; `run_evals.py` matches remote tests by name instead, so CI reuses them without a state file.
- **No bootstrap script for the workspace variables.** They were created once in the dashboard, and the README lists them.
- **Deploy is manual, not on merge.** Evals gate the PR on its own branch instead of after the deploy.

Known limits: tools are shared by every agent branch, so a PR's tool changes are not tested in isolation; and all PRs share the same remote eval tests.

## Phase 2: Secrets and authentication — done

Took credentials out of the tool config.

- The tool secret lives in the `tool_secret` workspace variable, and the tools only reference it (done in Phase 1). Git history never held the value.
- The secret was rotated, because the old value had sat in plain tool configs and may survive in old agent versions. The README documents the rotation.
- The custom `X-Tool-Secret` header became a standard `Authorization: Bearer` header, checked with FastAPI's `HTTPBearer`. ElevenLabs' auth connections cover OAuth2 and Basic, not bearer tokens; their documented bearer setup is this secret-valued header.

Deferred: **mTLS**, so that a leaked token alone can't call the backend. It needs TLS to end at the backend, and the quick tunnel ends it at Cloudflare. It belongs after Phase 3.

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
