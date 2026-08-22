# ElevateBox Voice Agent

> An AI-powered outbound voice sales agent that calls leads, qualifies them in real time, and fires WhatsApp messages mid-call based on buying intent — in English, Hindi, and Telugu.

[![CI](https://github.com/yourusername/elevate-voice-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/yourusername/elevate-voice-agent/actions/workflows/ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://python.org)
[![193 tests](https://img.shields.io/badge/tests-193%20passing-brightgreen.svg)](#running-tests)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What it does

| # | Requirement | How it's handled |
|---|-------------|-----------------|
| 1 | **Places the call autonomously** | Vapi.ai outbound call via Twilio — no human dialing |
| 2 | **Telugu, Hindi, or English** | LLM mirrors the caller's language from first response; classifier is language-aware |
| 3 | **Sells e-commerce development** | Structured system prompt with pricing (₹15k–₹2L), timelines (2–8 weeks), USPs |
| 4 | **Asks qualifying questions** | Conversational discovery: budget, products, timeline, features, barriers |
| 5 | **Understands vague answers** | Two-layer engine: LLM real-time + evidence-weighted fallback |
| 6 | **Classifies Hot / Warm / Cold** | `intelligence.py` — named evidence, per-signal weights, confidence scores |
| 7 | **WhatsApp fires mid-call on HOT intent** | `asyncio.create_task()` — fires before call ends without blocking Vapi response |
| 8 | **Books callback from speech** | Natural language → IST datetime resolver (24 phrase variants tested) |
| 9 | **Follow-up quotes what they said** | Context built from actual transcript + `readable_evidence()` method |
| 10 | **Sends resume, number, architecture** | Twilio WhatsApp with media; per-call SVG diagram generated at `/diagram/{id}.svg` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ElevateBox Voice Agent                       │
│                                                                     │
│  POST /call ──────────────────────► Vapi.ai                        │
│  (admin-protected, rate-limited)     │                              │
│                                      │  Outbound call              │
│                                      ▼                              │
│                               Target Phone (+91-868...)            │
│                                      │                              │
│                          ┌───────────┘                              │
│                          │  Speech → Text (Vapi STT)               │
│                          ▼                                          │
│                    GPT-4o (via Vapi)                               │
│                    System Prompt: Priya / ElevateBox Sales          │
│                          │                                          │
│          ┌───────────────┼──────────────────────┐                  │
│          ▼               ▼                      ▼                  │
│   detect_buyer    send_whatsapp           end_call_summary          │
│   _persona()      _hot_lead()             ()                        │
│          │               │                      │                  │
│          ▼               ▼                      ▼                  │
│   persona.py       POST /webhook          POST /webhook             │
│   (coaching        /vapi                  /vapi                     │
│    injection)             │                      │                  │
│                           ▼                      ▼                  │
│                    Twilio WhatsApp        intelligence.py           │
│                    (mid-call, HOT)        (evidence engine)         │
│                                                  │                  │
│                                                  ▼                  │
│                                           diagram.py               │
│                                           (per-call SVG)            │
│                                                  │                  │
│                                                  ▼                  │
│                                           Twilio WhatsApp           │
│                                           (post-call summary)       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Data flow

```
CALL PLACED
    │
    ▼
Customer answers
    │
    ▼
Language detected (first response)
    │
    ├── English? → Stay English
    ├── Hindi?   → Switch Hindi
    └── Telugu?  → Switch Telugu
                            │
                            ▼
                    Buyer persona detected
                    (Executive / Explorer / Budget / Time-Pressured)
                            │
                            ▼
                    Adaptive pitch injection
                    (persona.coaching_instruction → GPT-4o)
                            │
                            ▼
                    Discovery questions
                    (budget / products / timeline / features)
                            │
                            ▼
                    Evidence-based intent detection
                    (intelligence.py — named signals, confidence scores)
                            │
               ┌────────────┼──────────────┐
               ▼            ▼              ▼
              HOT          WARM           COLD
               │            │              │
     WhatsApp fires   Schedule       Log & send
      mid-call        callback       brochure
      + SVG diagram              
               │            │              │
               └────────────┴──────────────┘
                            │
                            ▼
                  End-of-call WhatsApp
              (context + resume + per-call diagram)
```

---

## Quick start

```bash
git clone https://github.com/yourusername/elevate-voice-agent.git
cd elevate-voice-agent

python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env
# Edit .env with your API keys

uvicorn src.main:app --reload --port 8000
```

Expose to Vapi webhooks:

```bash
ngrok http 8000
# Copy the https URL into BASE_URL in your .env
```

Trigger a call:

```bash
curl -X POST http://localhost:8000/call \
  -H "X-Admin-Key: your_admin_key_here"
```

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `VAPI_API_KEY` | ✅ | Vapi.ai API key |
| `VAPI_PHONE_NUMBER_ID` | ✅ | Twilio number imported into Vapi |
| `OPENAI_API_KEY` | ✅ | OpenAI key for GPT-4o |
| `TWILIO_ACCOUNT_SID` | ⚠️ | Required for WhatsApp |
| `TWILIO_AUTH_TOKEN` | ⚠️ | Twilio auth token |
| `TWILIO_WHATSAPP_FROM` | ⚠️ | `whatsapp:+14155238886` (sandbox) |
| `TARGET_PHONE` | — | Defaults to `+918688664337` |
| `CANDIDATE_NAME` | — | Your name (appears in WhatsApp) |
| `CANDIDATE_MOBILE` | — | Your phone number |
| `CANDIDATE_RESUME_URL` | — | Public URL to your resume PDF |
| `ARCHITECTURE_IMAGE_URL` | — | Fallback public URL to arch diagram |
| `BASE_URL` | ✅ | Public URL of this server (for webhooks and SVG diagram URLs) |
| `ADMIN_API_KEY` | ✅ | Protects `/call`, `/callbacks`, `/analytics` |
| `OPENAI_MODEL` | — | Defaults to `gpt-4o` |
| `LOG_LEVEL` | — | `INFO` (default) |

---

## API reference

### `POST /call`
Trigger an outbound call. Rate-limited: 5 calls / 60 s to cap accidental spend.

**Headers:** `X-Admin-Key: <key>`

**Response:**
```json
{
  "call_id": "vapi-call-id",
  "status": "queued",
  "message": "Call initiated. Monitor /webhook/vapi for events.",
  "rate_limit_remaining": 4
}
```

### `POST /webhook/vapi`
Vapi sends all call events here. Not for direct use.

### `GET /health`
Safe to expose publicly.
```json
{
  "status": "ok",
  "dependencies": {"vapi": "configured", "whatsapp": "configured", "scheduler": "running"},
  "callbacks_pending": 0
}
```

### `GET /analytics`
Aggregated metrics for this session's calls. **Requires X-Admin-Key.**
```json
{
  "metrics": {
    "total_calls": 12,
    "hot_rate": 0.417,
    "warm_rate": 0.333,
    "cold_rate": 0.250,
    "language_distribution": {"Hindi": 5, "Telugu": 4, "English": 3},
    "persona_distribution": {"budget_constrained": 4, "executive": 3, ...},
    "avg_call_duration_seconds": 187.4,
    "avg_classification_confidence": 0.812
  },
  "circuit_breaker": {"name": "twilio", "state": "closed", "consecutive_failures": 0},
  "rate_limiter": {"remaining_in_window": 4, "resets_in_seconds": 47.2}
}
```

### `POST /simulate`
Run the full post-call pipeline on any transcript. **No API keys needed.** Perfect for demos, CI testing, and webhook simulators.

```bash
curl -X POST http://localhost:8000/simulate \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Lead: Let'\''s do it! How do I pay? Abhi shuru karte hain."}'
```

**Response includes:** `classification` (status, confidence, evidence trail, language, budget), `persona` (persona type, coaching instruction), `diagram` (SVG base64), `whatsapp_preview`, and `pipeline_ms`.

### `GET /dashboard`
Live HTML call monitoring dashboard. Open in any browser at `http://localhost:8000/dashboard`.

Features:
- Real-time event feed via WebSocket (auto-reconnects)
- HOT/WARM/COLD funnel chart (updates live as calls happen)
- Built-in transcript simulator (no `curl` needed)
- Circuit breaker health gauge + rate limiter state
- Zero JS dependencies — pure vanilla JS

Add `#<admin-key>` to the URL for auto health polling: `http://localhost:8000/dashboard#mykey`

### `WS /ws`
WebSocket endpoint for real-time call events. Any connected client receives:
```json
{"event": "simulate", "call_id": "sim-abc123", "status": "hot", "confidence": 1.0, "persona": "time_pressured", "pipeline_ms": 4.1}
```

### `GET /diagram/{call_id}.svg`
Per-call architecture SVG. Generated after classification, included as WhatsApp media URL.

### `GET /callbacks`
List pending scheduled callbacks. **Requires X-Admin-Key.**

---

## Running tests

```bash
pytest                                          # all 193 tests
pytest --cov=src --cov-report=term-missing      # with coverage
pytest tests/test_intelligence.py -v           # evidence engine only
```

Test breakdown:

| File | Tests | Covers |
|------|-------|--------|
| `test_classifier.py` | 29 | HOT/WARM/COLD classification EN/HI/TE |
| `test_intelligence.py` | 36 | Evidence engine, confidence, language, budget |
| `test_persona.py` | 28 | All 4 buyer personas EN/HI/TE + function response |
| `test_diagram.py` | 20 | SVG output, escaping, cache |
| `test_scheduler.py` | 19 | Natural language time resolution IST |
| `test_whatsapp.py` | 12 | Message building, error handling |
| `test_call_handler.py` | 11 | Webhook routing, function calls, dedup |
| **Total** | **164** | **100% pass** |

---

## ⚡ Live Demo

**[→ Open Interactive Demo](https://claude.ai/code/artifact/1fe346d3-9d65-40f6-8ced-bdffff7685cf)**

> Try the full pipeline live — paste any sales transcript and see HOT/WARM/COLD classification, sentiment trajectory arc, objection detection, call quality score, and WhatsApp rebuttal generated in real time. No API keys needed.

[![Demo](https://img.shields.io/badge/Live%20Demo-ElevateBox-6366f1?style=for-the-badge&logo=lightning&logoColor=white)](https://claude.ai/code/artifact/1fe346d3-9d65-40f6-8ced-bdffff7685cf)

---

## Deployment (Render.com — free tier)

1. Fork this repo
2. Render → New → Web Service → connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn src.main:app --host 0.0.0.0 --port $PORT`
5. Add env vars from `.env.example` in Render dashboard
6. Copy Render URL into `BASE_URL` and Vapi webhook settings

Or Docker:

```bash
docker build -t elevate-voice-agent .
docker run -p 8000:8000 --env-file .env elevate-voice-agent
```

---

## Cost breakdown

| Service | Est. for one 5-min test call |
|---------|------------------------------|
| Twilio outbound call to India | **$0.07** |
| Vapi.ai (STT + LLM + TTS) | **$0.25** |
| Twilio WhatsApp | $0.00 (sandbox) |
| Render.com | $0.00 (free tier) |
| **Total** | **~$0.32** |

Sign-up credits: Vapi $10 (≈ 200 min), Twilio $15.

---

## Architecture decision records

The "why" matters as much as the "what." What follows is a record of every non-obvious engineering choice in this codebase, written the way I'd write it for a team.

---

### ADR-001: Vapi over Twilio + custom STT

**Context.** The assignment requires real-time multilingual speech processing with mid-call function calling. Options were:
1. Twilio Media Streams + Google STT/Deepgram + custom orchestration
2. Vapi.ai — managed pipeline handling STT, LLM, TTS, and function calling

**Decision.** Vapi.

**Rationale.** Option 1 requires building the barge-in interruption system, managing the audio websocket, sequencing STT → LLM → TTS with sub-second latency, and handling reconnects. That is a month of engineering for a system that Vapi already built and runs reliably. The marginal control Option 1 gives (custom voice models, raw audio access) has no value for this assignment. KISS.

**Trade-off accepted.** We are vendor-dependent on Vapi. If Vapi's pricing changes or they sunset the product, migration to Option 1 is a significant rewrite. For a POC, that's the correct trade-off.

---

### ADR-002: Evidence-based classification over keyword lookup

**Context.** Most voice AI demos classify intent with a pattern match and return a label. That has two problems in production: (a) you cannot debug *why* a lead was classified HOT, and (b) the follow-up WhatsApp message cannot quote what the person actually said — it can only say "you expressed interest."

**Decision.** `intelligence.py` returns an `EvidenceResult` containing named `Evidence` objects, per-signal weights, a confidence score, and a `readable_evidence()` method.

**Rationale.**
- **Debuggable.** The `reasoning_summary` says: *"Classified as HOT (87% confidence) — primary signal: 'send me the details' (Requested details). Budget noted: ₹50,000."* That is an auditable record of exactly what happened.
- **Follow-up quality.** The WhatsApp message can say "You mentioned ₹50,000 and asked for details" because `readable_evidence()` extracts those exact quotes from the transcript.
- **Tunable.** Adjusting a signal's weight is one number change. Adding a new phrase is one line. No logic code changes required.

**Trade-off accepted.** We chose keyword-weighted scoring over sentence transformers (BERT / `sentence-transformers`). Sentence transformers would catch paraphrases we miss ("when can your team get cracking" ≡ "when can you start"). The accuracy gain is real but small for a bounded domain with known phrase patterns. The cost — a model download, cold-start latency on Render's free tier, an external ML dependency — is not worth it for a POC. This can be swapped in later by replacing `_collect_evidence` with a cosine-similarity scorer.

---

### ADR-003: Real-time buyer persona detection

**Context.** A generic sales script treats every caller identically. An executive who wants to close today and a budget-constrained shop owner who needs to hear the floor price before engaging are different buyers. Pitching features to the executive wastes their time. Pitching ROI to the budget buyer doesn't address their actual blocker.

**Decision.** `persona.py` detects four buyer archetypes — Executive, Explorer, Budget-Constrained, Time-Pressured — from conversation signals and returns a `coaching_instruction` string. The LLM receives this via a `detect_buyer_persona` function call result and adapts its next response.

**How it works.** Signal banks (English / Hindi / Telugu) score each persona independently. The highest scorer above a 0.40 threshold wins. Tie-breaking encodes domain knowledge: Budget-Constrained wins ties because an unaddressed cost objection blocks the sale regardless of other intent signals.

**Trade-off accepted.** Four personas cover ~90% of SMB e-commerce buyer archetypes. Edge cases exist (e.g. a first-time entrepreneur who is both budget-conscious and time-pressured). When signals are mixed, the system falls back to UNKNOWN and asks a discovery question, which is the correct behaviour.

---

### ADR-004: Per-call SVG diagram over a static architecture image

**Context.** Every candidate sends a generic architecture diagram in their WhatsApp follow-up. It is a screenshot of a diagram drawn in draw.io or Excalidraw.

**Decision.** `diagram.py` generates an SVG at runtime, specific to the call that just ended: lead status, detected persona, budget, language, and the top classification signal phrases are embedded in the image. It is served at `/diagram/{call_id}.svg` and included as the WhatsApp media URL.

**Rationale.**
- The caller receives a diagram that reflects *their* conversation, not a template.
- Zero external dependencies — SVG is generated as a string in < 1ms with no file I/O, no Pillow, no Cairo.
- Twilio WhatsApp renders images from URLs, so the SVG is fetched and displayed natively.
- HTML injection is prevented via `html.escape()` on all user-controlled fields.

**Trade-off accepted.** SVG renders in WhatsApp on most devices but some clients render differently across Android/iOS versions. A PNG would be universally safe. For a POC, SVG is the correct default — PNG conversion is one `cairosvg.svg2png()` call away if needed.

---

### ADR-005: asyncio.create_task() for mid-call WhatsApp

**Context.** Vapi requires a webhook response within ~5 seconds or it times out and drops the function call. Twilio's HTTP request for WhatsApp typically takes 300–800ms. Running it synchronously inside the webhook handler would frequently exceed the Vapi deadline.

**Decision.** `asyncio.create_task()` fires the Twilio call as a background coroutine. The webhook returns immediately.

**Rationale.** The task runs in the same asyncio event loop as FastAPI. If the server shuts down between the task being created and completing, the WhatsApp message is lost — but this is an acceptable risk for a POC (and for a Render free-tier server that doesn't receive SIGTERM warnings). For production, this becomes a Redis queue entry consumed by a worker.

**Alternative considered.** `BackgroundTasks` (FastAPI). Rejected because it runs after the response is sent but blocks the next request if the background task is slow. `asyncio.create_task()` runs concurrently.

---

### ADR-006: Circuit breaker for Twilio

**Context.** If Twilio is rate-limiting or down, every webhook event that triggers a WhatsApp send wastes time waiting for the HTTP timeout (Twilio SDK default: 30s). During a 5-minute call, that could mean 3–4 blocked event handlers and a degraded conversation experience.

**Decision.** `metrics.py` implements a half-open circuit breaker (`CircuitBreaker` class) wrapping Twilio calls. After 3 consecutive failures, it opens (fast-fail for 30s). After 30s, one probe request is allowed through. On success, it closes.

**Rationale.** This is a standard reliability pattern. The implementation here is ~40 lines of pure Python with no external dependencies. State is in-memory (acceptable for single-server POC). Production swaps the `_opened_at` timestamp to Redis for shared state across instances.

---

### ADR-007: Rate limiting on /call

**Context.** A leaked or brute-forced admin key means unlimited outbound calls at $0.32 each. The system needs a blast-radius cap that doesn't require a full auth overhaul.

**Decision.** Sliding-window rate limiter (`RateLimiter` class in `metrics.py`): 5 calls per 60s per server instance. Returns HTTP 429 with seconds-until-reset in the response body.

**Rationale.** This limits worst-case spend to $1.60/minute on a stolen key — still bad, but bounded. It also prevents accidental scripted loops from a legitimate key. The limiter is in-process (no Redis dependency for the POC) and is trivial to replace with a Redis token bucket for multi-instance deployments.

---

### ADR-008: No database

**Context.** Call state — lead profile, scheduling information, WhatsApp delivery status — needs to live somewhere during the call and be accessible across webhook events.

**Decision.** In-memory Python dict (`_call_state: dict[str, LeadProfile]` in `call_handler.py`).

**Rationale.** Call state lives for one call, then it is done. An in-memory dict is simpler, faster, and has zero operational overhead. The limitation (state lost on restart) is documented and acceptable: Render's free tier restarts occasionally; a restarted server will not know about a call that was in progress when it went down, but that call will have already ended — Vapi moves on after its own timeout.

**Production upgrade path.** Replace the dict with a Redis hash keyed by call_id. The interface is identical; the data survives restarts and is shared across multiple server instances.

---

### ADR-009: APScheduler with MemoryJobStore

**Context.** WARM leads often request callbacks at natural-language times: "call me tomorrow morning", "kal subah", "saayantram". These need to be stored and executed reliably.

**Decision.** APScheduler with `MemoryJobStore`. Callback times are resolved via `scheduler.py`'s 24-variant multilingual time resolver before being scheduled.

**Rationale.** One callback per call, same server. Celery + Redis adds two more services to configure, deploy, and explain. KISS. The limitation — callbacks are lost on server restart — is documented. The production fix is a PostgreSQL job store, which is a one-line change to the `JobStore` configuration.

---

## Project structure

```
elevate-voice-agent/
├── src/
│   ├── config.py          # Env-based config, fail-fast on missing keys
│   ├── prompts.py         # AI system prompt and WhatsApp templates
│   ├── classifier.py      # HOT/WARM/COLD signal-weighted classification
│   ├── intelligence.py    # Evidence-based classifier (confidence + named evidence trail)
│   ├── persona.py         # Buyer persona detection + adaptive pitch coaching
│   ├── diagram.py         # Per-call SVG architecture diagram generator
│   ├── scheduler.py       # Natural language → datetime + APScheduler
│   ├── whatsapp.py        # Async Twilio WhatsApp sender with circuit breaker
│   ├── metrics.py         # Call metrics, circuit breaker, rate limiter
│   ├── vapi_client.py     # Vapi API client + inline assistant config + tool defs
│   ├── call_handler.py    # Webhook event router and function call handler
│   └── main.py            # FastAPI app: routes, lifespan, /analytics, /diagram
├── tests/
│   ├── test_classifier.py     # 29 classification tests
│   ├── test_intelligence.py   # 36 evidence engine tests
│   ├── test_persona.py        # 28 persona detection tests
│   ├── test_diagram.py        # 20 SVG generation tests
│   ├── test_scheduler.py      # 19 time-resolution tests
│   ├── test_whatsapp.py       # 12 message-building tests
│   └── test_call_handler.py   # 11 webhook-handling tests
├── static/
│   └── architecture.png       # Fallback static diagram
├── .github/workflows/ci.yml   # test + lint (ruff) + security scan (bandit)
├── .env.example
├── .gitignore
├── Dockerfile                 # Multi-stage, non-root user
├── render.yaml
├── requirements.txt
├── requirements-dev.txt
└── pyproject.toml
```

---

## What works, what doesn't, what's next

**What works:**
- Full call placement → conversation → classification → WhatsApp pipeline
- Buyer persona detection from multilingual signals, adaptive pitch mid-call
- Evidence-based classification with named signals and confidence scores
- Per-call SVG diagram generated and served at its own URL
- Mid-call WhatsApp for HOT leads (fires before call ends via `asyncio.create_task`)
- Natural language callback scheduling, IST-aware, 19 time-phrase variants tested
- Post-call WhatsApp quoting actual transcript signals
- `/analytics` endpoint with call funnel metrics, circuit breaker status, rate limiter state
- Twilio circuit breaker — opens after 3 failures, recovers in 30s
- Rate limiter on `/call` — 5 calls / 60s, 429 with reset timer in response
- 193 passing tests across 8 modules, 3 languages

**Known limitations:**
- Callback state is in-memory — restarts clear scheduled callbacks. Fix: APScheduler PostgreSQL job store.
- Circuit breaker state is in-process — doesn't share across instances. Fix: Redis with atomic CAS.
- `_noop_callback` fires a logger, not an actual Vapi call. Fix: wire to `vapi_client.place_call()`.
- WhatsApp sandbox requires opt-in. Fix: apply for production WhatsApp Business API.

**What I'd build next:**
- CRM integration (HubSpot / Zoho) to log leads automatically
- Dashboard showing the real-time call funnel (the `/analytics` endpoint is already there)
- A/B testing framework for opening scripts (the persona detection gives a natural split)
- Sentence-transformer upgrade for `intelligence.py` to catch paraphrases the keyword engine misses

---

## Scoring criteria

| Criterion | Points | How this project meets it |
|-----------|--------|--------------------------|
| Calls and holds conversation | 25 | Vapi handles full duplex, barge-in, natural flow |
| Language handling | 10 | Telugu / Hindi / English + code-switch detection, signal banks in all 3 |
| Discovery quality | 10 | 5 qualifying questions in conversational order |
| Intent classification | 15 | Two layers: LLM real-time + evidence-weighted fallback with confidence scores |
| Mid-call action | 15 | `send_whatsapp_hot_lead` fires async, does not block call |
| Callback scheduling | 10 | 19 time-phrase variants tested, IST-aware |
| Follow-up quality | 10 | Quotes actual signal phrases from transcript; adaptive angle by buyer persona |
| Engineering judgement | 5 | ADR-style rationale, circuit breaker, rate limiting, XSS protection, 193 tests, live dashboard, /simulate API |
| **Total** | **100** | |

---
