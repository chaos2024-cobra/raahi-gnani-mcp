# Raahi Gnani MCP Server

A remote MCP server that exposes the six Gnani voice capabilities from the Raahi Round 2 design
(KEN.pdf) as MCP tools. Built on the official MCP Python SDK v2 (`mcp>=2.3.0,<3`, `MCPServer`,
Streamable HTTP transport). No MCP v1 compatibility APIs are used anywhere in this repository.

This repository is an adapter, not the agent brain. It makes no visa decisions.

---

## 1. Purpose

Raahi needs to turn a consulate document checklist into a verified application, and part of that
workflow happens by voice: hearing the applicant, speaking the reply, chasing the bank by phone,
reading the real call outcome, pressing IVR keys, and reading live checklist state during a call.

This MCP server exists so AgenticOrg (or any MCP client) can drive those six voice actions through
one small, typed, safety-bounded service:

- AgenticOrg / Raahi connects over MCP / Streamable HTTP.
- This server validates inputs, calls real Gnani APIs, and returns structured results with typed
  error categories.
- Safety boundaries are enforced here and restated in every tool description:
  - never guess an unclear destination, date, or amount,
  - never dial an unapproved number,
  - never guess unmapped IVR menu options,
  - never convert "call connected" into "checklist resolved",
  - never leave an external caller waiting on our backend (bounded timeouts).

## 2. Six supported Gnani capabilities

The MCP server exposes exactly six tools:

| # | MCP tool | Gnani capability | Used at (KEN.pdf) | Rail |
|---|----------|------------------|-------------------|------|
| 1 | `transcribe_speech` | Gnani Prisma v2.5 STT Realtime | Intake | Voice |
| 2 | `speak_reply` | Gnani Timbre v2.0 TTS Realtime | Voice states | Voice |
| 3 | `call_bank_rm_or_desk` | Gnani Trigger Call API | GapResolution -> VoiceChase | Voice |
| 4 | `read_call_outcome` | Gnani Conversation Statistics API | After VoiceChase | Voice |
| 5 | `navigate_ivr` | Gnani DTMF Collection | VoiceChase IVR calls | Voice |
| 6 | `pull_case_status_into_call` | Gnani Custom Integrations | VoiceChase | Voice |

Tool-by-tool behaviour:

1. **`transcribe_speech`** - sends 16 kHz mic audio (base64) plus a language hint to the Gnani STT
   REST endpoint; returns the final transcript, and an interim transcript only when the configured
   endpoint actually supplies one. Partial/error responses from the provider are preserved. It
   never infers a destination, date, or amount from unclear speech.
2. **`speak_reply`** - sends text, language, and voice ID to the Gnani TTS endpoint; returns the
   generated audio as MCP audio content plus metadata (MIME type, size, model, request id). Only
   the exact text supplied is ever spoken; provider failure is reported as an error, never as
   reused/stale audio.
3. **`call_bank_rm_or_desk`** - strict local validation (digits only, 6-15 national digits, valid
   dialing code, name, checklist item id, optional local `GNANI_ALLOWED_NUMBERS` allowlist) before
   any network call, then triggers an authorised Gnani call. Returns trigger status, current
   status, client reference, and provider errors (400 unwhitelisted / 403 environment) without
   retrying invalid numbers. A triggered or connected call never resolves a checklist item.
4. **`read_call_outcome`** - fetches conversation statistics (status, disposition, transcript
   turns, analytics readiness). While analytics are still processing it performs bounded polling
   (`GNANI_OUTCOME_POLL_MAX_ATTEMPTS`, `GNANI_OUTCOME_POLL_INTERVAL_SECONDS`) and then returns
   `ANALYTICS_PENDING`. `resolved` is true only when the provider itself supplies explicit
   resolution evidence (`resolution_evidence` / `checklist_resolved`).
5. **`navigate_ivr`** - sends an explicitly supplied DTMF sequence (digits, `*`, `#`) with a
   conversation id and returns the tone-registration confirmation. No menu guessing: an invalid or
   changed menu from the provider is an explicit failure, and until `GNANI_DTMF_URL` is configured
   the tool fails with `NOT_CONFIGURED`.
6. **`pull_case_status_into_call`** - returns live checklist state for a case id or applicant id
   from an isolated case-status adapter (local demo store or `GNANI_CASE_STATUS_URL`) with a
   bounded timeout (`GNANI_CASE_STATUS_TIMEOUT_SECONDS`); on timeout it returns a TIMEOUT result
   immediately so the external call never waits on our backend. The server also exposes
   `GET /case-status` endpoints for Gnani Custom Integrations to call.

Every tool returns a typed structured result with `success`, `error_category`, `request_id`,
`provider`, and `duration_ms`. Error categories: `SUCCESS`, `VALIDATION_ERROR`, `AUTH_ERROR`,
`FORBIDDEN`, `NOT_FOUND`, `TIMEOUT`, `RATE_LIMITED`, `PROVIDER_ERROR`, `MALFORMED_RESPONSE`,
`ANALYTICS_PENDING`, `NOT_CONFIGURED`.

## 3. Mapping to KEN.pdf

KEN.pdf is the source of truth for required functionality. Terminology below is reproduced exactly
from the PDF:

- `transcribe_speech` = Gnani Prisma v2.5 STT Realtime from KEN.pdf (Capability 1, Intake, Voice
  rail; build status EXISTS).
- `speak_reply` = Gnani Timbre v2.0 TTS Realtime from KEN.pdf (Capability 2, Voice states, Voice
  rail; build status EXISTS).
- `call_bank_rm_or_desk` = Gnani Trigger Call API, PDF marks PARTIAL and says production scale
  requires commercial access (Capability 3, GapResolution -> VoiceChase, Voice rail).
- `read_call_outcome` = Gnani Conversation Statistics API from KEN.pdf (Capability 4, after
  VoiceChase, Voice rail; build status EXISTS).
- `navigate_ivr` = Gnani DTMF Collection from KEN.pdf (Capability 5, VoiceChase IVR calls, Voice
  rail; build status EXISTS).
- `pull_case_status_into_call` = Gnani Custom Integrations, PDF marks PARTIAL and says our
  endpoint must be built (Capability 6, VoiceChase, Voice rail).

PDF requirements that are enforced in code:

- Capability 1: never guess unclear destination / date / amount; return interim + final transcript
  where available, partial transcript + error when the provider fails.
- Capability 2: never read an unverified document, date, or amount; failure returns an error
  instead of stale audio.
- Capability 3: never call an unapproved number; reject invalid numbers before the provider call;
  do not automatically retry them.
- Capability 4: never mark an item resolved just because a call connected; bounded polling only.
- Capability 5: never guess unmapped menu options; stop when the menu structure changes.
- Capability 6: never leave the caller waiting on our backend; times out and continues the call.

## 4. Architecture

```
AgenticOrg / Raahi
        |
        | MCP / Streamable HTTP  (POST /mcp)
        v
Raahi Gnani MCP Server        server.py (ASGI/Starlette via MCP SDK v2)
        |                     src/gnani_tools.py (six tools + /health + /case-status)
        |                     src/gnani_client.py (GnaniClient, retries, auth)
        +------> Gnani STT                 POST https://api.vachana.ai/stt/v3
        +------> Gnani TTS                 POST https://api.vachana.ai/api/v1/tts/inference
        +------> Gnani Trigger Call        POST https://api.inya.ai/platform/v1/agents/{bot_id}/trigger_call
        +------> Gnani Conversation Stats  GET  https://api.inya.ai/platform/v1/conversations/{id}/stats
        +------> Gnani DTMF                GNANI_DTMF_URL (configurable, not publicly documented)
        +------> Gnani Custom Integration  Gnani calls GET /case-status on this server;
                                           tool reads local store or GNANI_CASE_STATUS_URL
```

Module layout:

```
raahi-gnani-mcp/
├── server.py                 ASGI entrypoint: create_app(), /mcp, uvicorn runner
├── requirements.txt
├── render.yaml
├── .env.example              variable names + placeholders only
├── .gitignore
├── README.md
├── src/
│   ├── config.py             Settings.from_env(), transport security, defaults
│   ├── gnani_client.py       GnaniClient: transcribe_speech, speak_reply, trigger_call,
│   │                         get_call_outcome, send_dtmf, get_case_status (+ retries)
│   ├── gnani_tools.py        MCPServer instance, six @mcp.tool functions, /health,
│   │                         /case-status routes, validation helpers
│   ├── case_status.py        isolated get_case_status() adapter (local / HTTP)
│   ├── models.py             Pydantic result models (structured tool output)
│   ├── errors.py             ErrorCategory, ErrorDetail, GnaniError
│   └── logging_utils.py      structured JSON logging + redaction
└── tests/                    deterministic mocked tests (see "How to run tests")
```

Transport: MCP Streamable HTTP at `/mcp`, Starlette ASGI app, DNS-rebinding protection with a
loopback allowlist by default (disabled in `render.yaml` for the public Render hostname).
Authentication headers are added centrally in `src/gnani_client.py`; API keys are never logged,
never returned, and never included in tool output.

Retry policy: only timeouts, connection errors, and 5xx are retried, with bounded attempts and
exponential backoff. 4xx and 429 are never retried (`RATE_LIMITED` is returned directly).

## 5. Environment variables

All variables are defined in `.env.example` (placeholders only, no credentials). Defaults for the
Gnani endpoints are applied in code, so only `GNANI_API_KEY` (and `GNANI_BOT_ID` for calls) are
strictly required.

| Variable | Default | Purpose |
|---|---|---|
| `GNANI_API_KEY` | (required) | Gnani credential; speech APIs use header `X-API-Key-ID`, platform APIs use header `x-api-key` |
| `GNANI_STT_URL` | `https://api.vachana.ai/stt/v3` | Gnani Prisma STT endpoint |
| `GNANI_TTS_URL` | `https://api.vachana.ai/api/v1/tts/inference` | Gnani Timbre TTS endpoint |
| `GNANI_TTS_MODEL` | `timbre-v2.5` | TTS model name |
| `GNANI_TTS_CONTAINER` | `wav` | TTS audio container/format |
| `GNANI_SPEECH_AUTH_HEADER` | `X-API-Key-ID` | Header name for speech APIs |
| `GNANI_TRIGGER_CALL_URL` | `https://api.inya.ai/platform/v1/agents/{bot_id}/trigger_call` | Trigger Call API |
| `GNANI_CALL_OUTCOME_URL` | `https://api.inya.ai/platform/v1/conversations/{conversation_id}/stats` | Conversation Statistics API |
| `GNANI_PLATFORM_AUTH_HEADER` | `x-api-key` | Header name for platform APIs |
| `GNANI_BOT_ID` | (required for calls) | Gnani agent/bot id substituted into the trigger URL |
| `GNANI_CALL_ENVIRONMENT` | `development` | `environment=` query param for trigger calls |
| `GNANI_DTMF_URL` | (empty) | DTMF endpoint; `navigate_ivr` returns `NOT_CONFIGURED` until set |
| `GNANI_CASE_STATUS_URL` | (empty) | Remote case-status endpoint; empty = built-in local demo store |
| `GNANI_CASE_STATUS_TOKEN` | (empty) | Optional bearer token required by `GET /case-status` |
| `GNANI_CASE_STATUS_TIMEOUT_SECONDS` | `3` | Bounded case-status lookup timeout |
| `GNANI_ALLOWED_NUMBERS` | (empty) | Optional local allowlist (comma separated); empty = rely on Gnani's whitelist |
| `GNANI_HTTP_TIMEOUT_SECONDS` | `10` | Per-request provider timeout |
| `GNANI_MAX_RETRIES` | `2` | Max retries for timeout/5xx only |
| `GNANI_RETRY_BACKOFF_SECONDS` | `0.5` | Exponential backoff base |
| `GNANI_MAX_AUDIO_BYTES` | `10485760` | Max decoded STT audio size |
| `GNANI_MAX_TTS_TEXT_CHARS` | `5000` | Max TTS text length |
| `GNANI_OUTCOME_POLL_MAX_ATTEMPTS` | `3` | Bounded analytics polling attempts |
| `GNANI_OUTCOME_POLL_INTERVAL_SECONDS` | `1.0` | Delay between polls |
| `MCP_ENABLE_DNS_REBINDING_PROTECTION` | `true` | Transport security toggle (`false` in render.yaml) |
| `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` | (empty = loopback) | Host/origin allowlists when protection is on |
| `LOG_LEVEL` | `INFO` | Log level for the `raahi.gnani` logger |

Live-only test toggle: `RUN_LIVE_GNANI_TESTS=true` (never set in CI; live tests are skipped by
default).

## 6. How to run locally

Requirements: Python 3.12+ (Render uses 3.12).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in GNANI_API_KEY (and GNANI_BOT_ID for calls)
uvicorn server:app --host 0.0.0.0 --port 8000
```

Or simply:

```bash
python server.py                   # honours $PORT, defaults to 8000
```

Verify:

```bash
curl http://127.0.0.1:8000/health
# {"ok": true, "service": "raahi-gnani-mcp"}

curl http://127.0.0.1:8000/case-status/RAAHI-DEMO-001
# {"case_id": "RAAHI-DEMO-001", "checklist": [...]}
```

MCP clients connect to `http://127.0.0.1:8000/mcp` (Streamable HTTP). DNS-rebinding protection is
enabled by default and allows loopback hosts, so local clients work without extra configuration.

## 7. How to run tests

```bash
pip install -r requirements.txt
python -m pytest            # full suite, all provider calls mocked
python -m pytest tests/test_server.py -k discovery   # targeted run
```

Test files:

- `tests/test_server.py` - server import, `/health` in-process and over HTTP, `/mcp` initialize,
  full MCP tool discovery over Streamable HTTP (exactly six tools, descriptions, input schemas),
  startup on an arbitrary port with a real uvicorn subprocess, repository scan proving zero
  matches for MCP v1 API strings, `.gitignore`/`render.yaml` sanity.
- `tests/test_gnani_stt.py` - STT validation (empty/invalid base64/bad language/oversize audio/path
  traversal), success, interim+final, partial transcript + error, timeout, 401, 5xx retry limit,
  malformed response, never-invented content, missing API key.
- `tests/test_gnani_tts.py` - TTS validation, success (audio bytes + MCP audio block + request
  body/auth check), provider error, timeout, rate limit (no retry), forbidden, malformed empty body.
- `tests/test_calls.py` - trigger-call validation, local allowlist reject/accept, trigger-only
  semantics (never "resolved"), phone normalisation, provider 400/403/5xx, missing bot id;
  outcome success, connected-is-never-resolved, explicit resolution evidence, analytics pending,
  poll-until-ready, timeout, 404, empty response, no-answer, bad conversation id.
- `tests/test_dtmf.py` - NOT_CONFIGURED default, accepted sequence, `*`/`#`, validation, menu
  changed, provider invalid sequence, timeout, request shape.
- `tests/test_case_status.py` - local success/not found/validation, HTTP success (bounded timeout
  propagated), timeout, 404, malformed/invalid JSON, 5xx; `/case-status` routes incl. optional
  bearer token auth.
- `tests/test_security.py` - no API key in any tool output (success or error), no secrets in
  structured logs (key, auth header, full phone number, audio never logged), required log fields.
- `tests/test_retry.py` - bounded retries, configurable to zero, transient recovery, no retry on
  400/403/429, analytics polling bounds.
- `tests/test_live_integration.py` - optional live tests, skipped unless
  `RUN_LIVE_GNANI_TESTS=true` and a real `GNANI_API_KEY` is present; never run automatically.

No live Gnani account is required for the normal suite.

## 8. How to deploy to Render

`render.yaml` is included and already validated:

- `runtime: python-3.12`
- `buildCommand: pip install -r requirements.txt`
- `startCommand: uvicorn server:app --host 0.0.0.0 --port $PORT`
- `healthCheckPath: /health`
- env vars: `GNANI_API_KEY` and `GNANI_BOT_ID` (sync: false, set in the dashboard),
  `GNANI_CALL_ENVIRONMENT=development`, `GNANI_DTMF_URL`, `GNANI_CASE_STATUS_URL`,
  `MCP_ENABLE_DNS_REBINDING_PROTECTION=false`, `LOG_LEVEL=INFO`

Steps:

1. Push this repository to GitHub.
2. In Render: New -> Blueprint, select the repository; `render.yaml` is picked up automatically.
3. Set the required secrets in the dashboard: `GNANI_API_KEY`, `GNANI_BOT_ID` (and optionally
   `GNANI_DTMF_URL`, `GNANI_CASE_STATUS_URL`, `GNANI_ALLOWED_NUMBERS`).
4. Deploy. Render exposes the app on `0.0.0.0:$PORT` and probes `/health`.

Note on transport security: the MCP SDK rejects requests whose Host header is not allowlisted when
DNS-rebinding protection is enabled. On Render the public hostname would be rejected, so
`render.yaml` sets `MCP_ENABLE_DNS_REBINDING_PROTECTION=false`. Alternatively keep protection on
and set `MCP_ALLOWED_HOSTS=<your-service>.onrender.com`. Never disable protection when exposing
unauthenticated admin routes; this server only exposes `/health`, `/mcp`, and `/case-status`.

## 9. MCP endpoint

- Local: `http://127.0.0.1:8000/mcp` (Streamable HTTP; path is `/mcp`)
- Render: `https://<your-render-service>.onrender.com/mcp`
- Health: `https://<your-render-service>.onrender.com/health`

Example initialization:

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"raahi-smoke","version":"0.0.1"}}}'
```

The server answers with `serverInfo.name = "raahi-gnani-mcp"`. Tool discovery over the same
endpoint returns exactly the six tools listed in section 2.

## 10. Example AgenticOrg MCP registration

Streamable HTTP connector (HTTP/SSE client config):

```json
{
  "mcpServers": {
    "raahi-gnani": {
      "type": "streamable-http",
      "url": "https://<your-render-service>.onrender.com/mcp"
    }
  }
}
```

Local development variant:

```json
{
  "mcpServers": {
    "raahi-gnani": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

If you front the MCP endpoint with an authenticating proxy, send the proxy's bearer token as an
`Authorization` header; the Gnani API key itself is never sent by MCP clients - it stays on the
server side. After registration, list tools: the connector must expose exactly
`transcribe_speech`, `speak_reply`, `call_bank_rm_or_desk`, `read_call_outcome`, `navigate_ivr`,
and `pull_case_status_into_call`.

## 11. Real vs mock components

Real (talks to officially documented Gnani endpoints):

| Component | Endpoint | Status |
|---|---|---|
| `transcribe_speech` | `POST https://api.vachana.ai/stt/v3` (header `X-API-Key-ID`) | Real, documented |
| `speak_reply` | `POST https://api.vachana.ai/api/v1/tts/inference` (header `X-API-Key-ID`) | Real, documented |
| `call_bank_rm_or_desk` | `POST https://api.inya.ai/platform/v1/agents/{bot_id}/trigger_call?environment=...` (header `x-api-key`) | Real, documented |
| `read_call_outcome` | `GET https://api.inya.ai/platform/v1/conversations/{id}/stats` (header `x-api-key`) | Real, documented |

Mock / configurable / built by us:

| Component | Reality |
|---|---|
| `navigate_ivr` (DTMF) | Gnani publishes no public DTMF send endpoint (the documented feature is *collecting* DTMF from callers). The URL is configurable via `GNANI_DTMF_URL`; unset, the tool fails `NOT_CONFIGURED`. No endpoint is invented. |
| `pull_case_status_into_call` (local mode) | Deterministic local demo store (`RAAHI-DEMO-001`, `RAAHI-DEMO-002`) - the KEN.pdf custom-integration side that our backend must supply. |
| `pull_case_status_into_call` (HTTP mode) | When `GNANI_CASE_STATUS_URL` is set, calls that endpoint with a bounded timeout; Gnani calls back into this server's `GET /case-status` routes. |
| Tests | All provider calls are served by an in-memory fake sender; live tests are opt-in only. |

## 12. Known limitations

- The selected STT endpoint (`/stt/v3`) returns only the final transcript. `interim_transcript`
  stays `null` and `interim_available=false` unless a configured endpoint supplies interim data;
  interim text is never fabricated.
- TTS uses the documented synchronous inference endpoint, which returns the complete audio in one
  response; chunked streaming metadata is therefore not available (`streaming=false`).
- The Trigger Call API does not return a conversation id. `conversation_id` is `null` after
  triggering; read it from conversation logs or a post-call webhook before calling
  `read_call_outcome`.
- Gnani's DTMF Collection feature is documented as *collecting* keypresses from callers; sending
  tones into a bank's IVR may require a different Gnani/telephony capability. Until Gnani provides
  the send endpoint, `GNANI_DTMF_URL` must be supplied and is left unconfirmed on purpose.
- Provider payloads are parsed defensively; fields outside the documented schema are preserved in
  structured results only when present (no inference, no defaults that invent data).
- Local demo case data (`RAAHI-DEMO-*`) is a backend adapter for Capability 6 - it is not agent
  state and must be replaced by the real AgenticOrg/backend endpoint in production.
- `render.yaml` disables MCP DNS-rebinding protection because Render's Host header is not
  loopback; see section 8 for the allowlist alternative.
- Voice-quality heuristics (e.g. confidence thresholds for "unclear speech") are provider-driven;
  this adapter surfaces whatever Gnani reports rather than re-scoring audio.

## 13. Production/commercial caveat for Gnani Trigger Call

KEN.pdf marks Capability 3 (Gnani Trigger Call API, used at GapResolution -> VoiceChase) as
**PARTIAL**: *"production scale requires commercial access."* The tool is implemented against the
documented trigger endpoint and is fully covered by mocked tests, but placing real calls at
production volume requires a Gnani commercial agreement/whitelisted numbers. Until then:

- expect provider 400 for numbers not on Gnani's whitelist (surfaced as `VALIDATION_ERROR`),
- expect provider 403 outside the approved environment (surfaced as `FORBIDDEN`),
- keep `GNANI_ALLOWED_NUMBERS` set locally so unapproved numbers are rejected before any network
  call,
- never treat a triggered or connected call as a resolved checklist item.

## 14. Partial/custom-integration caveat for pull_case_status_into_call

KEN.pdf marks Capability 6 (Gnani Custom Integrations, used at VoiceChase) as **PARTIAL**: *"Gnani
capability exists; our endpoint must be built."* This repository therefore provides both sides it
can honestly provide:

1. The MCP tool `pull_case_status_into_call` reads an isolated adapter (`src/case_status.py`)
   with a bounded timeout - local demo cases by default, or `GNANI_CASE_STATUS_URL` when set. On
   timeout it returns immediately so an in-progress external call is never blocked.
2. HTTP endpoints `GET /case-status` and `GET /case-status/{case_id}` (optional
   `Authorization: Bearer $GNANI_CASE_STATUS_TOKEN`) for Gnani's custom integration to call back
   into this server.

Gnani does not publish the exact custom-integration callback contract (header/parameter templating
is documented at a high level only), so no protocol is invented here: the callback routes accept a
simple `case_id` / `applicant_id` lookup and are the surface to adapt once the exact contract is
confirmed with Gnani. Do not present the local demo store as the real case backend.
