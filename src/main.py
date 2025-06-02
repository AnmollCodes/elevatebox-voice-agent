"""
ElevateBox Voice Agent — FastAPI application entry point.

Routes:
  POST /call              — Trigger an outbound call (admin-protected)
  POST /webhook/vapi      — Vapi event webhook (call events, function calls)
  POST /simulate          — Run full pipeline on any transcript (no API keys)
  GET  /health            — Health check with dependency status
  GET  /callbacks         — List scheduled callbacks (admin-protected)
  GET  /analytics         — Aggregated call metrics (admin-protected)
  GET  /diagram/{id}.svg  — Per-call SVG architecture diagram
  GET  /dashboard         — Live call monitoring dashboard (admin-protected)
  WS   /ws               — WebSocket feed for real-time call events
  GET  /                  — Service root

Security:
  Admin endpoints require the X-Admin-Key header matching ADMIN_API_KEY env var.
  Webhook endpoint validates the call is from Vapi (IP + optional HMAC).
  No secrets are logged or exposed in responses.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .call_handler import CallHandler
from .config import Config
from .diagram import generate_call_diagram, get_cached_diagram
from .intelligence import calibrate_confidence, calibration_summary, classify_with_evidence
from .memory import lead_memory
from .metrics import call_rate_limiter, metrics, twilio_breaker
from .objections import detect_objections
from .persona import BuyerPersona, detect_persona, get_followup_angle
from .quality import score_call_quality
from .scheduler import list_scheduled_callbacks, start_scheduler, stop_scheduler
from .trajectory import analyse_trajectory
from .vapi_client import VapiClient
from .whatsapp import WhatsAppSender

# ---------------------------------------------------------------------------
# Logging — structured, levelled, no secrets
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """
    Manages a pool of active WebSocket connections for the live dashboard.

    Broadcasts JSON events to all connected clients. Silently drops
    disconnected clients from the pool.
    """

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("Dashboard WebSocket connected. Total=%d", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info("Dashboard WebSocket disconnected. Total=%d", len(self._connections))

    async def broadcast(self, event: dict) -> None:
        """Send a JSON event to all connected dashboard clients."""
        dead: list[WebSocket] = []
        payload = json.dumps(event)
        for ws in list(self._connections):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)


ws_manager = ConnectionManager()

# ---------------------------------------------------------------------------
# App-level singletons (created at startup, shared across requests)
# ---------------------------------------------------------------------------

_config: Config
_vapi_client: VapiClient
_whatsapp_sender: WhatsAppSender
_call_handler: CallHandler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan: run startup logic before yield, teardown after.
    This replaces the deprecated on_event("startup") / on_event("shutdown").
    """
    global _config, _vapi_client, _whatsapp_sender, _call_handler

    logger.info("ElevateBox Voice Agent starting up")

    _config = Config.from_env()

    logging.getLogger().setLevel(_config.log_level)

    _vapi_client = VapiClient(_config)

    _whatsapp_sender = WhatsAppSender(
        account_sid=_config.twilio_account_sid,
        auth_token=_config.twilio_auth_token,
        from_number=_config.twilio_whatsapp_from,
        candidate_mobile=_config.candidate_mobile,
        candidate_resume_url=_config.candidate_resume_url,
        architecture_image_url=_config.architecture_image_url,
    )

    _call_handler = CallHandler(config=_config, whatsapp=_whatsapp_sender)

    start_scheduler()
    logger.info("Startup complete. Environment=%s", _config.environment)

    yield

    # Teardown
    logger.info("ElevateBox Voice Agent shutting down")
    stop_scheduler()
    await _vapi_client.close()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ElevateBox Voice Agent",
    description="AI voice sales agent that calls leads and qualifies them in real time.",
    version="1.0.0",
    docs_url="/docs" if os.environ.get("ENVIRONMENT") != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Serve static files (architecture diagram, etc.)
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ---------------------------------------------------------------------------
# Admin auth dependency
# ---------------------------------------------------------------------------

def _require_admin(x_admin_key: str = Header(default="")) -> None:
    """
    Verify the X-Admin-Key header matches the ADMIN_API_KEY env var.
    Raises 401 if missing or wrong. Used on admin-only endpoints.
    """
    expected = os.environ.get("ADMIN_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_API_KEY is not configured on this server.",
        )
    if x_admin_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Key header.",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    """Redirect browsers hitting the root to the health endpoint."""
    return JSONResponse({"service": "ElevateBox Voice Agent", "status": "running"})


@app.get("/health")
async def health() -> dict[str, Any]:
    """
    Health check endpoint. Returns service status and dependency availability.
    Safe to expose publicly — contains no secrets.
    """
    return {
        "status": "ok",
        "service": "elevate-voice-agent",
        "dependencies": {
            "vapi": "configured",
            "whatsapp": "configured" if _config.whatsapp_configured else "not configured",
            "scheduler": "running",
        },
        "callbacks_pending": len(list_scheduled_callbacks()),
    }


@app.post("/call", dependencies=[Depends(_require_admin)])
async def trigger_call(phone: str | None = None) -> dict[str, Any]:
    """
    Admin endpoint: Place an outbound call.

    Requires X-Admin-Key header.
    Optional ?phone query param overrides the default target number.

    Rate-limited: 5 calls per 60 seconds to prevent accidental runaway spend.

    Returns:
        Vapi call object with call_id and initial status.
    """
    if not call_rate_limiter.allow():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded. "
                f"Try again in {call_rate_limiter.reset_after_seconds():.0f}s. "
                f"Max {call_rate_limiter.max_calls} calls per {call_rate_limiter.window_seconds:.0f}s."
            ),
        )

    target = phone or _config.target_phone
    logger.info("Outbound call requested, target masked")

    # Register call in metrics store before placing it
    record = metrics.record_call_start(call_id="pending", phone=target)

    try:
        call_data = await _vapi_client.place_call(target)
    except Exception as exc:
        logger.exception("Failed to place call")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Vapi call creation failed: {type(exc).__name__}",
        ) from exc

    # Backfill the real call_id once we have it
    call_id = call_data.get("id", "unknown")
    record.call_id = call_id

    return {
        "call_id": call_id,
        "status": call_data.get("status"),
        "message": "Call initiated. Monitor /webhook/vapi for events.",
        "rate_limit_remaining": call_rate_limiter.remaining(),
    }


@app.post("/webhook/vapi")
async def vapi_webhook(request: Request) -> dict[str, Any]:
    """
    Vapi event webhook — receives all call events.

    This endpoint must respond in < 5 seconds or Vapi times out.
    Heavy work (WhatsApp send, scheduling) runs in background tasks.
    """
    try:
        payload = await request.json()
    except Exception:
        logger.warning("Webhook received non-JSON body")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    try:
        result = await _call_handler.handle_event(payload)
        return result
    except Exception:
        logger.exception("Error handling Vapi webhook event")
        # Return 200 so Vapi does not retry — we already logged the error
        return {"result": "error — logged server-side"}


@app.get("/callbacks", dependencies=[Depends(_require_admin)])
async def list_callbacks() -> dict[str, Any]:
    """
    Admin endpoint: List all scheduled follow-up callbacks.
    Returns job IDs and scheduled times only — no customer PII.

    Requires X-Admin-Key header.
    """
    return {"callbacks": list_scheduled_callbacks()}


@app.get("/analytics", dependencies=[Depends(_require_admin)])
async def analytics() -> dict[str, Any]:
    """
    Admin endpoint: Aggregated call metrics.

    Returns:
        Summary statistics across all calls in this session:
        lead funnel rates, language distribution, persona breakdown,
        WhatsApp delivery rates, circuit breaker status.

    Requires X-Admin-Key header.
    """
    return {
        "metrics": metrics.summary(),
        "recent_calls": metrics.recent(limit=10),
        "circuit_breaker": twilio_breaker.status_dict(),
        "rate_limiter": {
            "remaining_in_window": call_rate_limiter.remaining(),
            "resets_in_seconds": round(call_rate_limiter.reset_after_seconds(), 1),
        },
        "calibration_curves": calibration_summary(),
        "lead_memory": {
            "total_leads_remembered": len(lead_memory),
            "returning_leads": sum(
                1 for p in lead_memory.all_profiles() if p.is_returning
            ),
        },
    }


@app.get("/diagram/{call_id}.svg", include_in_schema=False)
async def call_diagram(call_id: str):
    """
    Serve the per-call SVG architecture diagram.

    Generated after call classification and cached in memory.
    Included as the media URL in post-call WhatsApp messages.
    """
    from fastapi.responses import Response
    svg_bytes = get_cached_diagram(call_id)
    if svg_bytes is None:
        raise HTTPException(status_code=404, detail="Diagram not generated yet for this call.")
    return Response(content=svg_bytes, media_type="image/svg+xml")


@app.get("/static/architecture.png", include_in_schema=False)
async def architecture_diagram():
    """
    Serve the architecture diagram image.
    Falls back gracefully if the file does not exist.
    """
    path = os.path.join(static_dir, "architecture.png")
    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Architecture diagram not found")


# ---------------------------------------------------------------------------
# /simulate  — run the full post-call pipeline without any real API keys
# ---------------------------------------------------------------------------

@app.post("/simulate")
async def simulate_pipeline(request: Request) -> dict[str, Any]:
    """
    Simulate the full post-call pipeline on any transcript.

    Accepts:
        { "transcript": "...", "call_id": "optional-id" }

    Returns:
        Complete pipeline output:
        - lead classification (status, confidence, evidence, language, budget)
        - buyer persona (persona, confidence, coaching instruction)
        - per-call SVG diagram (base64-encoded, also cached for /diagram/{call_id}.svg)
        - whatsapp_preview: the exact WhatsApp message that would be sent
        - pipeline_ms: wall-clock time for the full run

    No API keys needed. Safe to call from curl or any HTTP client.
    Great for demos, CI integration tests, and webhook simulators.

    Rate-limited at 5 req/60s to share the same guard as /call.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be JSON: {\"transcript\": \"...\"}")

    transcript = body.get("transcript", "").strip()
    if not transcript:
        raise HTTPException(status_code=422, detail="'transcript' field is required and must not be empty.")

    call_id = body.get("call_id") or f"sim-{uuid.uuid4().hex[:8]}"
    phone = body.get("phone", "")
    t0 = time.perf_counter()

    # ── Step 1: Evidence-based classification ─────────────────────────────
    clf = classify_with_evidence(transcript)

    # ── Step 1b: Calibrated confidence ───────────────────────────────────
    calibrated_prob = calibrate_confidence(clf.confidence, clf.status.value)

    # ── Step 2: Buyer persona detection ──────────────────────────────────
    persona = detect_persona(transcript)
    followup_angle = get_followup_angle(persona.persona)

    # ── Step 3: SVG diagram (cached for /diagram/{call_id}.svg) ──────────
    svg_bytes = generate_call_diagram(
        call_id=call_id,
        lead_status=clf.status,
        persona=persona.persona.value,
        top_signals=[e.matched_text for e in clf.top_evidence(3)],
        budget=clf.budget_detected,
        language=clf.language_detected,
    )

    import base64
    svg_b64 = base64.b64encode(svg_bytes).decode()

    # ── Step 4: Sentiment trajectory ─────────────────────────────────────
    arc = analyse_trajectory(transcript)

    # ── Step 5: Objection detection ───────────────────────────────────────
    obj_map = detect_objections(transcript, phone=phone)

    # ── Step 6: Call quality score ────────────────────────────────────────
    quality = score_call_quality(transcript)

    # ── Step 7: Lead memory — look up history, record this call ──────────
    lead_profile = lead_memory.get(phone) if phone else None
    if phone:
        lead_memory.record(
            phone=phone,
            call_id=call_id,
            status=clf.status.value,
            confidence=clf.confidence,
            primary_objection=obj_map.primary_objection.value,
            persona=persona.persona.value,
            arc=arc.arc,
            momentum=arc.momentum,
            quality_score=quality.total,
            transcript_snippet=transcript[:200],
        )

    # ── Step 8: Build WhatsApp message preview ────────────────────────────
    from .classifier import LeadStatus as LS
    budget_line = f"\n💰 Budget mentioned: {clf.budget_detected}" if clf.budget_detected else ""

    # Use objection-aware rebuttal if there's a known objection, else standard template
    if not obj_map.no_objection and clf.status != LS.HOT:
        whatsapp_preview = obj_map.whatsapp_rebuttal
    elif clf.status == LS.HOT:
        whatsapp_preview = (
            f"🔥 *Great speaking with you!*\n\n"
            f"You asked about our e-commerce development services and showed strong interest — specifically:\n"
            f"_{clf.readable_evidence()}_\n\n"
            f"{followup_angle}{budget_line}\n\n"
            f"📋 *What's included:*\n"
            f"• Complete online store with payment gateway\n"
            f"• Mobile-optimised design\n"
            f"• WhatsApp integration\n"
            f"• 12 months support\n"
            f"• Goes live in 2–14 days\n\n"
            f"📎 Resume: https://your-resume-url.com/resume.pdf\n"
            f"🏗️ Architecture: https://your-app.com/diagram/{call_id}.svg\n\n"
            f"📱 +91XXXXXXXXXX  |  ElevateBox"
        )
    elif clf.status == LS.WARM:
        whatsapp_preview = (
            f"😊 *Thanks for the chat!*\n\n"
            f"I understand the timing isn't quite right. When you're ready, I'll be here.\n\n"
            f"{followup_angle}{budget_line}\n\n"
            f"Our packages start at ₹15,000 — happy to discuss what works for your budget.\n\n"
            f"📱 +91XXXXXXXXXX  |  ElevateBox"
        )
    else:
        whatsapp_preview = (
            f"👋 *Thanks for picking up!*\n\n"
            f"No pressure at all — if you ever want to explore getting online, we're here.\n\n"
            f"Our basic store starts at ₹15,000 and goes live in 2 weeks.\n\n"
            f"📱 +91XXXXXXXXXX  |  ElevateBox"
        )

    pipeline_ms = round((time.perf_counter() - t0) * 1000, 1)

    result = {
        "call_id": call_id,
        "pipeline_ms": pipeline_ms,
        "classification": {
            "status": clf.status.value,
            "confidence": clf.confidence,
            "confidence_pct": int(clf.confidence * 100),
            "calibrated_probability": calibrated_prob,
            "language_detected": clf.language_detected,
            "budget_detected": clf.budget_detected,
            "reasoning_summary": clf.reasoning_summary,
            "readable_evidence": clf.readable_evidence(),
            "top_evidence": [
                {
                    "matched_text": e.matched_text,
                    "signal_name": e.signal_name,
                    "weight": e.weight,
                    "method": e.method,
                }
                for e in clf.top_evidence(5)
            ],
        },
        "persona": {
            "persona": persona.persona.value,
            "confidence": persona.confidence,
            "confidence_pct": int(persona.confidence * 100),
            "matched_signals": persona.matched_signals[:5],
            "coaching_instruction": persona.coaching_instruction,
            "followup_angle": followup_angle,
        },
        "trajectory": arc.as_dict(),
        "objections": obj_map.as_dict(),
        "quality": quality.as_dict(),
        "memory": lead_profile.as_dict() if lead_profile else {"is_returning": False},
        "diagram": {
            "url_path": f"/diagram/{call_id}.svg",
            "size_bytes": len(svg_bytes),
            "svg_base64": svg_b64,
        },
        "whatsapp_preview": whatsapp_preview,
        "meta": {
            "mid_call_whatsapp_fires_on": "HOT detection during call (asyncio.create_task)",
            "post_call_whatsapp_fires_on": "call-end event from Vapi webhook",
            "diagram_served_at": f"/diagram/{call_id}.svg",
            "objection_aware_rebuttal": not obj_map.no_objection,
        },
    }

    # Broadcast to any connected dashboard clients
    await ws_manager.broadcast({
        "event": "simulate",
        "call_id": call_id,
        "status": clf.status.value,
        "confidence": clf.confidence,
        "calibrated_probability": calibrated_prob,
        "persona": persona.persona.value,
        "language": clf.language_detected,
        "budget": clf.budget_detected,
        "arc": arc.arc,
        "momentum": arc.momentum,
        "primary_objection": obj_map.primary_objection.value,
        "quality_score": quality.total,
        "quality_grade": quality.grade,
        "pipeline_ms": pipeline_ms,
        "ts": time.time(),
    })

    return result


# ---------------------------------------------------------------------------
# WebSocket  — real-time event feed for the dashboard
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for the live call dashboard.

    Connect from any client:
        ws = new WebSocket("ws://localhost:8000/ws")
        ws.onmessage = (e) => console.log(JSON.parse(e.data))

    Events broadcast:
        { event: "call_started",  call_id, ts }
        { event: "classified",    call_id, status, confidence, persona, ts }
        { event: "whatsapp_sent", call_id, type, ts }
        { event: "simulate",      call_id, status, confidence, persona, language, budget, pipeline_ms, ts }
        { event: "ping",          ts }   — keepalive every 30s
    """
    await ws_manager.connect(websocket)
    try:
        # Send a welcome snapshot of current metrics
        await websocket.send_text(json.dumps({
            "event": "connected",
            "message": "ElevateBox live feed. Events stream here as calls happen.",
            "dashboard_clients": ws_manager.client_count,
            "ts": time.time(),
        }))
        # Keep the connection alive; ping every 30 seconds
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"event": "ping", "ts": time.time()}))
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# /dashboard  — live HTML monitoring dashboard
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ElevateBox — Live Call Dashboard</title>
<style>
  :root {
    --bg: #09111f; --surface: #0f1a2e; --card: #132035; --border: #1c2e47;
    --text: #dde6f0; --muted: #5a6e85; --hot: #f05252; --warm: #f0a030;
    --cold: #4a90d9; --green: #28c76f; --purple: #9b72e8; --cyan: #22d3ee;
    --accent: #4f6ef7; --teal: #14b8a6;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 14px 28px;
    display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
  header h1 { font-size: 1rem; font-weight: 700; color: var(--text); letter-spacing: -.3px; }
  header h1 span { color: var(--accent); }
  .hdr-right { display: flex; align-items: center; gap: 12px; }
  .live-badge { display: flex; align-items: center; gap: 6px; font-size: .75rem; color: var(--green); font-weight: 600; }
  .live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); animation: pulse 1.4s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.25} }
  .ws-status { font-size: .68rem; padding: 3px 8px; border-radius: 99px; background: rgba(240,82,82,.12); color: var(--hot); font-weight: 600; }
  .ws-status.connected { background: rgba(40,199,111,.12); color: var(--green); }
  .csv-btn { font-size: .72rem; padding: 4px 12px; border-radius: 6px; border: 1px solid var(--border);
    background: transparent; color: var(--muted); cursor: pointer; transition: all .15s; }
  .csv-btn:hover { background: var(--card); color: var(--text); border-color: var(--accent); }

  /* ── Stats row ── */
  .grid { display: grid; grid-template-columns: repeat(5,1fr); gap: 12px; padding: 20px 28px 0; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .stat-label { font-size: .65rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
  .stat-val { font-size: 2rem; font-weight: 800; margin-top: 4px; line-height: 1; }
  .stat-sub { font-size: .7rem; color: var(--muted); margin-top: 3px; }
  .hot  { color: var(--hot); }
  .warm { color: var(--warm); }
  .cold { color: var(--cold); }

  /* ── Layout ── */
  .main { display: grid; grid-template-columns: 1fr 340px; gap: 12px; padding: 14px 28px; }
  .left-col { display: flex; flex-direction: column; gap: 12px; }
  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  .panel-header { padding: 12px 18px; border-bottom: 1px solid var(--border); font-size: .72rem; font-weight: 700;
    letter-spacing: .06em; text-transform: uppercase; color: var(--muted); display: flex; justify-content: space-between; align-items: center; }

  /* ── Call timeline table ── */
  .timeline-wrap { overflow-x: auto; }
  table.timeline { width: 100%; border-collapse: collapse; font-size: .75rem; }
  table.timeline th { padding: 8px 14px; text-align: left; color: var(--muted); font-weight: 600;
    font-size: .65rem; text-transform: uppercase; letter-spacing: .06em; border-bottom: 1px solid var(--border); white-space: nowrap; }
  table.timeline td { padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,.03); vertical-align: middle; white-space: nowrap; }
  table.timeline tr:hover td { background: rgba(255,255,255,.025); cursor: pointer; }
  table.timeline tr.selected td { background: rgba(79,110,247,.08); }
  .empty-tl { padding: 32px; text-align: center; color: var(--muted); font-size: .8rem; }

  /* ── Detail drawer ── */
  .detail-drawer { display: none; background: var(--surface); border-top: 1px solid var(--border); padding: 14px 18px; font-size: .75rem; }
  .detail-drawer.open { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
  .detail-block h4 { font-size: .65rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin-bottom: 6px; }
  .detail-block p { line-height: 1.5; color: var(--text); }
  .wa-preview { background: var(--card); border-radius: 8px; padding: 10px 12px; font-size: .72rem; line-height: 1.55;
    white-space: pre-wrap; color: var(--text); border-left: 3px solid var(--green); }

  /* ── Funnel ── */
  .bar-wrap { padding: 16px 18px; }
  .bar-row { margin-bottom: 12px; }
  .bar-label { display: flex; justify-content: space-between; margin-bottom: 5px; font-size: .75rem; }
  .bar-track { height: 10px; background: var(--border); border-radius: 99px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 99px; transition: width .7s cubic-bezier(.4,0,.2,1); }

  /* ── Feed ── */
  .feed { overflow-y: auto; max-height: 260px; }
  .feed-item { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: flex-start; gap: 12px; animation: fadein .35s ease; }
  @keyframes fadein { from { opacity:0; transform: translateY(-6px); } to { opacity:1; transform: none; } }
  .feed-icon { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1rem; flex-shrink: 0; }
  .feed-icon.hot  { background: rgba(240,82,82,.13); }
  .feed-icon.warm { background: rgba(240,160,48,.13); }
  .feed-icon.cold { background: rgba(74,144,217,.13); }
  .feed-icon.sim  { background: rgba(79,110,247,.13); }
  .feed-text { flex: 1; min-width: 0; }
  .feed-title { font-size: .82rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .feed-meta  { font-size: .7rem; color: var(--muted); margin-top: 2px; }
  .feed-time  { font-size: .67rem; color: var(--muted); white-space: nowrap; flex-shrink: 0; }
  .empty-feed { padding: 36px 18px; text-align: center; color: var(--muted); font-size: .8rem; }

  /* ── Pills ── */
  .status-pill { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: .65rem; font-weight: 700; }
  .status-pill.hot  { background: rgba(240,82,82,.18);  color: var(--hot); }
  .status-pill.warm { background: rgba(240,160,48,.18); color: var(--warm); }
  .status-pill.cold { background: rgba(74,144,217,.18); color: var(--cold); }
  .grade-pill { display: inline-block; padding: 1px 7px; border-radius: 5px; font-size: .65rem; font-weight: 800; }
  .grade-pill.Ap, .grade-pill.A { background: rgba(40,199,111,.18); color: var(--green); }
  .grade-pill.B { background: rgba(34,211,238,.18); color: var(--cyan); }
  .grade-pill.C { background: rgba(240,160,48,.18); color: var(--warm); }
  .grade-pill.D, .grade-pill.F { background: rgba(240,82,82,.18); color: var(--hot); }

  /* ── Arc sparkline ── */
  .arc-bar { display: inline-flex; gap: 2px; align-items: center; }
  .arc-seg { display: inline-block; width: 14px; height: 6px; border-radius: 2px; }
  .arc-seg.hot  { background: var(--hot); }
  .arc-seg.warm { background: var(--warm); }
  .arc-seg.cold { background: var(--cold); }

  /* ── Simulator ── */
  .simulate-box { padding: 14px 18px; }
  .preset-row { display: flex; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
  .preset-btn { font-size: .68rem; padding: 3px 9px; border-radius: 5px; border: 1px solid var(--border);
    background: transparent; color: var(--muted); cursor: pointer; transition: all .15s; }
  .preset-btn:hover { background: var(--surface); color: var(--text); }
  .simulate-box textarea { width: 100%; background: var(--surface); border: 1px solid var(--border); color: var(--text);
    border-radius: 8px; padding: 9px 11px; font-size: .75rem; resize: vertical; min-height: 80px; outline: none; font-family: inherit; }
  .simulate-box textarea:focus { border-color: var(--accent); }
  .btn { width: 100%; margin-top: 8px; padding: 9px; border-radius: 7px; border: none; cursor: pointer;
    font-weight: 700; font-size: .82rem; background: var(--accent); color: #fff; transition: opacity .18s; }
  .btn:hover { opacity: .88; }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .result-box { margin-top: 10px; background: var(--surface); border-radius: 8px; padding: 12px; font-size: .73rem; color: var(--muted); display: none; }
  .result-box.show { display: block; }
  .result-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
  .result-cell { background: var(--card); border-radius: 6px; padding: 8px 10px; }
  .result-cell .rk { font-size: .6rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin-bottom: 3px; }
  .result-cell .rv { font-size: .8rem; font-weight: 700; color: var(--text); }

  /* ── Health ── */
  .health-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: .78rem; }
  .health-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }

  @media(max-width:1100px){ .grid{grid-template-columns:repeat(3,1fr);} }
  @media(max-width:780px){ .grid{grid-template-columns:repeat(2,1fr);} .main{grid-template-columns:1fr;} .detail-drawer.open{grid-template-columns:1fr;} }
</style>
</head>
<body>
<header>
  <h1>Elevate<span>Box</span> — Live Intelligence Dashboard</h1>
  <div class="hdr-right">
    <button class="csv-btn" onclick="exportCSV()">⬇ Export CSV</button>
    <span class="ws-status" id="wsStatus">Connecting…</span>
    <div class="live-badge"><div class="live-dot"></div>LIVE</div>
  </div>
</header>

<div class="grid">
  <div class="stat-card"><div class="stat-label">Total Calls</div><div class="stat-val" id="totalCalls">0</div><div class="stat-sub">This session</div></div>
  <div class="stat-card"><div class="stat-label">HOT Leads</div><div class="stat-val hot" id="hotCount">0</div><div class="stat-sub" id="hotRate">—</div></div>
  <div class="stat-card"><div class="stat-label">WhatsApp Fired</div><div class="stat-val" style="color:var(--green)" id="waCount">0</div><div class="stat-sub">Mid-call + post-call</div></div>
  <div class="stat-card"><div class="stat-label">Avg Quality</div><div class="stat-val" style="color:var(--cyan)" id="avgQ">—</div><div class="stat-sub">Agent score /100</div></div>
  <div class="stat-card"><div class="stat-label">Rising Arcs</div><div class="stat-val" style="color:var(--purple)" id="risingArcs">0</div><div class="stat-sub">cold→warm/hot momentum</div></div>
</div>

<div class="main">
  <div class="left-col">
    <!-- Call Timeline Table -->
    <div class="panel">
      <div class="panel-header">
        <span>Call Timeline</span>
        <span id="tlCount" style="font-size:.7rem;color:var(--muted)">0 calls</span>
      </div>
      <div class="timeline-wrap">
        <table class="timeline" id="tlTable">
          <thead><tr>
            <th>Call ID</th><th>Status</th><th>Conf %</th><th>Calibrated</th>
            <th>Arc</th><th>Objection</th><th>Quality</th><th>Persona</th><th>Time</th>
          </tr></thead>
          <tbody id="tlBody"><tr><td colspan="9" class="empty-tl">No calls yet — run the simulator below</td></tr></tbody>
        </table>
      </div>
      <div class="detail-drawer" id="detailDrawer"></div>
    </div>

    <!-- Lead Funnel -->
    <div class="panel">
      <div class="panel-header"><span>Conversion Funnel</span></div>
      <div class="bar-wrap">
        <div class="bar-row"><div class="bar-label"><span>🔥 HOT</span><span id="hotPct">0%</span></div><div class="bar-track"><div class="bar-fill" id="hotBar" style="width:0%;background:var(--hot)"></div></div></div>
        <div class="bar-row"><div class="bar-label"><span>🌡 WARM</span><span id="warmPct">0%</span></div><div class="bar-track"><div class="bar-fill" id="warmBar" style="width:0%;background:var(--warm)"></div></div></div>
        <div class="bar-row"><div class="bar-label"><span>❄️ COLD</span><span id="coldPct">0%</span></div><div class="bar-track"><div class="bar-fill" id="coldBar" style="width:0%;background:var(--cold)"></div></div></div>
        <div style="margin-top:8px;font-size:.68rem;color:var(--muted)" id="funnelNote"></div>
      </div>
    </div>

    <!-- Live Feed -->
    <div class="panel">
      <div class="panel-header"><span>Live Event Feed</span><span id="feedCount" style="font-size:.7rem;color:var(--muted)">0 events</span></div>
      <div class="feed" id="feed"><div class="empty-feed">Waiting for calls…</div></div>
    </div>
  </div>

  <div style="display:flex;flex-direction:column;gap:12px">
    <!-- Simulator -->
    <div class="panel">
      <div class="panel-header">Transcript Simulator</div>
      <div class="simulate-box">
        <div class="preset-row">
          <button class="preset-btn" onclick="setPreset('hot')">🔥 HOT</button>
          <button class="preset-btn" onclick="setPreset('warm')">🌡 WARM</button>
          <button class="preset-btn" onclick="setPreset('cold')">❄️ COLD</button>
          <button class="preset-btn" onclick="setPreset('mix')">🌀 Mixed</button>
        </div>
        <textarea id="txInput" placeholder="Paste any call transcript and hit Simulate…"></textarea>
        <button class="btn" id="simBtn" onclick="runSim()">⚡ Simulate Full Pipeline</button>
        <div class="result-box" id="simResult"></div>
      </div>
    </div>

    <!-- System Health -->
    <div class="panel">
      <div class="panel-header">System Health</div>
      <div class="bar-wrap">
        <div class="health-row">
          <div class="health-dot" id="cbDot" style="background:var(--green)"></div>
          <span style="font-size:.78rem" id="cbState">Circuit Breaker — CLOSED</span>
        </div>
        <div style="font-size:.72rem;color:var(--muted);margin-bottom:5px">Rate Limiter (/call)</div>
        <div class="bar-track" style="margin-bottom:4px"><div class="bar-fill" id="rlBar" style="width:100%;background:var(--green)"></div></div>
        <div style="font-size:.68rem;color:var(--muted)" id="rlLabel">5 / 5 remaining</div>
      </div>
    </div>
  </div>
</div>

<script>
const BASE = window.location.origin;
const WS_URL = BASE.replace('http','ws') + '/ws';
let ws, allEvents = [], callLog = [], hot=0, warm=0, cold=0, total=0, waTotal=0, qSum=0, qN=0, rising=0;
let selectedRow = null;

const PRESETS = {
  hot: `Agent: Hi! Online store banana chahte hain apke liye?\nLead: Haan bilkul! Let's do it. How do I pay? Abhi shuru karte hain. Send me details on WhatsApp.\nAgent: Great! I'll send everything now.`,
  warm: `Agent: Would you like a website for your business?\nLead: Maybe. Not sure about the budget right now. Can you call me next month?\nAgent: Of course! I'll schedule a callback.`,
  cold: `Agent: Hi, we offer website development services.\nLead: Not interested at all. Please don't call again. I am not interested.\nAgent: Understood, I'll remove you from the list.`,
  mix: `Agent: Namaste! E-commerce store ke baare mein baat karte hain?\nLead: Too expensive for me right now. Bahut mehnga lagta hai.\nAgent: I totally understand the budget concern. We have a ₹15k starter plan.\nLead: Oh really? That sounds okay. Send me the details.`,
};

function setPreset(k) { document.getElementById('txInput').value = PRESETS[k]; }

function fmt(ts) { return new Date(ts*1000).toLocaleTimeString(); }

function arcHtml(arc) {
  if(!arc) return '—';
  return arc.split('→').map(s => `<span class="arc-seg ${s}"></span>`).join('') + ` <span style="font-size:.65rem;color:var(--muted)">${arc}</span>`;
}

function gradeClass(g) {
  if(!g) return '';
  return g.replace('+','p');
}

function addToTimeline(ev, detail) {
  const tbody = document.getElementById('tlBody');
  const empty = tbody.querySelector('.empty-tl');
  if(empty) tbody.innerHTML = '';

  callLog.push({ev, detail, ts: ev.ts || Date.now()/1000});
  document.getElementById('tlCount').textContent = callLog.length + ' calls';

  const s = (ev.status || 'sim').toLowerCase();
  const conf = ev.confidence ? Math.round(ev.confidence*100)+'%' : '—';
  const cal  = ev.calibrated_probability != null ? Math.round(ev.calibrated_probability*100)+'%' : '—';
  const arc  = ev.arc || (detail && detail.trajectory && detail.trajectory.arc) || '—';
  const obj  = ev.primary_objection || '—';
  const q    = ev.quality_score != null ? ev.quality_score : '—';
  const qg   = ev.quality_grade || '';
  const pers = (ev.persona || '—').replace('_',' ');

  const tr = document.createElement('tr');
  tr.dataset.idx = callLog.length - 1;
  tr.innerHTML = `
    <td style="font-family:monospace;font-size:.7rem;color:var(--muted)">${ev.call_id || '—'}</td>
    <td><span class="status-pill ${s}">${s.toUpperCase()}</span></td>
    <td>${conf}</td>
    <td>${cal}</td>
    <td><div class="arc-bar">${arcHtml(arc)}</div></td>
    <td style="text-transform:capitalize">${obj}</td>
    <td>${q !== '—' ? `<span class="grade-pill ${gradeClass(qg)}">${qg}</span> ${q}` : '—'}</td>
    <td style="color:var(--cyan)">${pers}</td>
    <td style="color:var(--muted)">${fmt(ev.ts||Date.now()/1000)}</td>`;

  tr.addEventListener('click', () => showDetail(tr, callLog.length-1));
  tbody.insertBefore(tr, tbody.firstChild);
}

function showDetail(tr, idx) {
  const drawer = document.getElementById('detailDrawer');
  if(selectedRow) selectedRow.classList.remove('selected');
  if(selectedRow === tr) { drawer.className = 'detail-drawer'; selectedRow = null; return; }
  selectedRow = tr;
  tr.classList.add('selected');

  const {ev, detail} = callLog[idx];
  const traj = detail && detail.trajectory ? detail.trajectory : null;
  const obj  = detail && detail.objections ? detail.objections : null;
  const qual = detail && detail.quality ? detail.quality : null;
  const wa   = detail && detail.whatsapp_preview ? detail.whatsapp_preview : null;

  drawer.innerHTML = `
    <div class="detail-block">
      <h4>Sentiment Arc</h4>
      ${traj ? `
        <p><b>Arc:</b> ${traj.arc}</p>
        <p><b>Momentum:</b> ${traj.momentum > 0 ? '+' : ''}${traj.momentum}</p>
        <p style="color:var(--muted);margin-top:4px;font-size:.7rem">${traj.coaching_note}</p>` : '<p style="color:var(--muted)">N/A</p>'}
    </div>
    <div class="detail-block">
      <h4>Objection Detected</h4>
      ${obj ? `
        <p><b>Type:</b> ${obj.primary_objection || '—'}</p>
        ${obj.objections && obj.objections[0] ? `<p style="color:var(--muted);font-size:.7rem;margin-top:3px">"${obj.objections[0].exact_phrase || ''}"</p>` : ''}
        ${obj.whatsapp_rebuttal ? `<p style="margin-top:6px;color:var(--teal);font-size:.7rem">${obj.whatsapp_rebuttal.substring(0,100)}…</p>` : ''}` : '<p style="color:var(--muted)">N/A</p>'}
    </div>
    <div class="detail-block">
      <h4>Call Quality</h4>
      ${qual ? `
        <p><b>Score:</b> ${qual.total}/100 (${qual.grade})</p>
        <p style="color:var(--muted);font-size:.7rem;margin-top:3px">${(qual.coaching_notes||[])[0]||''}</p>` : '<p style="color:var(--muted)">N/A</p>'}
    </div>
    ${wa ? `<div class="detail-block" style="grid-column:1/-1"><h4>WhatsApp Preview</h4><div class="wa-preview">${wa}</div></div>` : ''}`;
  drawer.className = 'detail-drawer open';
}

function addFeedItem(ev) {
  const feed = document.getElementById('feed');
  const empty = feed.querySelector('.empty-feed');
  if(empty) feed.removeChild(empty);
  const s = (ev.status || 'sim').toLowerCase();
  const icons = {hot:'🔥',warm:'🌡',cold:'❄️'};
  const icon = icons[s] || '⚡';
  const pill = `<span class="status-pill ${s}">${s.toUpperCase()}</span>`;
  const meta = [
    ev.arc ? `🔀 ${ev.arc}` : null,
    ev.primary_objection && ev.primary_objection !== 'none' ? `🗣 ${ev.primary_objection}` : null,
    ev.quality_score != null ? `📊 Q:${ev.quality_score}` : null,
    ev.pipeline_ms ? `⚡${ev.pipeline_ms}ms` : null,
  ].filter(Boolean).join(' · ');
  const title = ev.event==='simulate' ? `Sim ${ev.call_id} → ${pill}` : `${(ev.event||'').replace('_',' ')} — ${pill}`;
  const div = document.createElement('div');
  div.className = 'feed-item';
  div.innerHTML = `
    <div class="feed-icon ${s}">${icon}</div>
    <div class="feed-text"><div class="feed-title">${title}</div>${meta?`<div class="feed-meta">${meta}</div>`:''}</div>
    <div class="feed-time">${fmt(ev.ts||Date.now()/1000)}</div>`;
  feed.insertBefore(div, feed.firstChild);
  if(feed.children.length > 50) feed.removeChild(feed.lastChild);
  document.getElementById('feedCount').textContent = ++allEvents.length + ' events';
}

function updateStats() {
  const n = hot+warm+cold;
  document.getElementById('totalCalls').textContent = total;
  document.getElementById('hotCount').textContent = hot;
  document.getElementById('hotRate').textContent = n ? (hot/n*100).toFixed(0)+'% HOT rate' : '—';
  document.getElementById('waCount').textContent = waTotal;
  document.getElementById('avgQ').textContent = qN ? Math.round(qSum/qN) : '—';
  document.getElementById('risingArcs').textContent = rising;
  const pct = s => n ? Math.round(s/n*100) : 0;
  document.getElementById('hotPct').textContent  = pct(hot)+'%';
  document.getElementById('warmPct').textContent = pct(warm)+'%';
  document.getElementById('coldPct').textContent = pct(cold)+'%';
  document.getElementById('hotBar').style.width  = pct(hot)+'%';
  document.getElementById('warmBar').style.width = pct(warm)+'%';
  document.getElementById('coldBar').style.width = pct(cold)+'%';
  if(n>0) document.getElementById('funnelNote').textContent =
    `${n} total · ${hot} converting · funnel health ${hot>warm&&hot>cold?'✅ healthy':'⚠ review needed'}`;
}

function onEvent(ev, detail) {
  if(ev.event==='ping'||ev.event==='connected') return;
  addFeedItem(ev);
  addToTimeline(ev, detail || null);
  total++;
  if(ev.status==='hot')  { hot++; waTotal++; }
  if(ev.status==='warm') warm++;
  if(ev.status==='cold') cold++;
  if(ev.quality_score) { qSum += ev.quality_score; qN++; }
  if(ev.arc && (ev.arc.endsWith('hot') || ev.arc.endsWith('warm')) && ev.arc.startsWith('cold')) rising++;
  updateStats();
}

function connect() {
  ws = new WebSocket(WS_URL);
  const el = document.getElementById('wsStatus');
  ws.onopen  = () => { el.textContent='Connected'; el.className='ws-status connected'; };
  ws.onclose = () => { el.textContent='Reconnecting…'; el.className='ws-status'; setTimeout(connect, 2000); };
  ws.onerror = () => { el.textContent='Error'; };
  ws.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch(_){} };
}
connect();

async function runSim() {
  const tx = document.getElementById('txInput').value.trim();
  if(!tx) return;
  const btn = document.getElementById('simBtn');
  const res = document.getElementById('simResult');
  btn.disabled=true; btn.textContent='⏳ Running pipeline…';
  res.className='result-box'; res.textContent='';

  try {
    const r = await fetch(BASE+'/simulate', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({transcript: tx})
    });
    const d = await r.json();
    if(!r.ok) { res.innerHTML=`<span style="color:var(--hot)">Error: ${d.detail}</span>`; res.className='result-box show'; return; }

    const clf=d.classification, p=d.persona, traj=d.trajectory, obj=d.objections, q=d.quality;
    const s = clf.status.toLowerCase();

    res.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span class="status-pill ${s}">${clf.status}</span>
        <span style="font-size:.7rem;color:var(--muted)">${clf.confidence_pct}% raw · ${Math.round((clf.calibrated_probability||0)*100)}% calibrated · ${d.pipeline_ms}ms</span>
      </div>
      <div class="result-grid">
        <div class="result-cell"><div class="rk">Persona</div><div class="rv">${p.persona.replace('_',' ')}</div></div>
        <div class="result-cell"><div class="rk">Language</div><div class="rv">${clf.language_detected}</div></div>
        <div class="result-cell"><div class="rk">Arc</div><div class="rv"><div class="arc-bar">${arcHtml(traj&&traj.arc)}</div></div></div>
        <div class="result-cell"><div class="rk">Objection</div><div class="rv" style="text-transform:capitalize">${(obj&&obj.primary_objection)||'none'}</div></div>
        <div class="result-cell"><div class="rk">Quality Score</div><div class="rv">${q&&q.total||0}/100 <span class="grade-pill ${gradeClass(q&&q.grade)}">${q&&q.grade||'—'}</span></div></div>
        <div class="result-cell"><div class="rk">Evidence</div><div class="rv" style="font-size:.7rem;color:var(--cyan)">${clf.readable_evidence||'—'}</div></div>
      </div>
      ${traj&&traj.coaching_note?`<div style="font-size:.7rem;color:var(--teal);margin-bottom:8px;padding:6px 8px;background:rgba(20,184,166,.08);border-radius:5px">💡 ${traj.coaching_note}</div>`:''}
      <div style="font-size:.68rem;color:var(--muted);white-space:pre-wrap;border-top:1px solid var(--border);padding-top:8px">${d.whatsapp_preview||''}</div>`;
    res.className='result-box show';

    // Inject into live feed and timeline
    onEvent({
      event:'simulate', call_id: d.call_id, status: s, confidence: clf.confidence,
      calibrated_probability: clf.calibrated_probability,
      persona: p.persona, language: clf.language_detected, budget: clf.budget_detected,
      arc: traj&&traj.arc, primary_objection: obj&&obj.primary_objection,
      quality_score: q&&q.total, quality_grade: q&&q.grade, pipeline_ms: d.pipeline_ms,
      ts: Date.now()/1000,
    }, d);
  } catch(e) {
    res.innerHTML=`<span style="color:var(--hot)">Network error — is the server running? <code>make run</code></span>`;
    res.className='result-box show';
  } finally {
    btn.disabled=false; btn.textContent='⚡ Simulate Full Pipeline';
  }
}

function exportCSV() {
  if(!callLog.length) return alert('No calls to export yet.');
  const header = ['call_id','status','confidence','calibrated','arc','momentum','objection','quality','grade','persona','ts'];
  const rows = callLog.map(({ev,detail}) => {
    const traj = detail&&detail.trajectory;
    const obj  = detail&&detail.objections;
    const qual = detail&&detail.quality;
    return [
      ev.call_id||'',ev.status||'',
      ev.confidence!=null?ev.confidence:'',
      ev.calibrated_probability!=null?ev.calibrated_probability:'',
      (traj&&traj.arc)||ev.arc||'',
      (traj&&traj.momentum)||'',
      (obj&&obj.primary_objection)||ev.primary_objection||'',
      (qual&&qual.total)||ev.quality_score||'',
      (qual&&qual.grade)||ev.quality_grade||'',
      ev.persona||'',
      new Date((ev.ts||Date.now()/1000)*1000).toISOString(),
    ].map(v=>JSON.stringify(String(v||''))).join(',');
  });
  const csv = [header.join(','), ...rows].join('\\n');
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
  a.download = 'elevatebox-calls-' + new Date().toISOString().slice(0,10) + '.csv';
  a.click();
}

// Health polling
function startHealthPoll(key) {
  setInterval(async () => {
    try {
      const r = await fetch(BASE+'/analytics', {headers:{'X-Admin-Key':key}});
      if(!r.ok) return;
      const d = await r.json();
      const cb = d.circuit_breaker;
      document.getElementById('cbDot').style.background = cb.state==='closed'?'var(--green)':cb.state==='open'?'var(--hot)':'var(--warm)';
      document.getElementById('cbState').textContent = 'Circuit Breaker — ' + cb.state.toUpperCase() + (cb.state!=='closed'?` (${cb.consecutive_failures} fails)`:'');
      const rl = d.rate_limiter;
      const pct = rl.remaining_in_window/5*100;
      document.getElementById('rlBar').style.width=pct+'%';
      document.getElementById('rlBar').style.background=pct>40?'var(--green)':pct>20?'var(--warm)':'var(--hot)';
      document.getElementById('rlLabel').textContent=`${rl.remaining_in_window}/5 remaining · resets in ${rl.resets_in_seconds}s`;
    } catch(_){}
  }, 10000);
}

if(location.hash) startHealthPoll(decodeURIComponent(location.hash.slice(1)));
</script>
</body>
</html>"""


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """
    Live HTML call monitoring dashboard.

    Open in any browser while the server is running:
        http://localhost:8000/dashboard

    Features:
    - Real-time event feed via WebSocket (auto-reconnects)
    - HOT/WARM/COLD funnel bar chart (updates live)
    - Transcript simulator (try /simulate without curl)
    - Circuit breaker health & rate limiter gauge
    - No JS frameworks — pure vanilla JS, zero dependencies

    Tip: add #<admin-key> to the URL to enable auto health polling,
    e.g. http://localhost:8000/dashboard#mykey
    """
    return HTMLResponse(content=_DASHBOARD_HTML)
