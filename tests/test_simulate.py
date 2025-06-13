"""
Tests for POST /simulate — full pipeline endpoint (no API keys needed).

The /simulate endpoint is the centrepiece of the open-source demo:
it runs classify_with_evidence + detect_persona + generate_call_diagram
on any transcript and returns structured JSON. These tests verify the
contract is stable and correct.
"""

import pytest
from fastapi.testclient import TestClient

from src.main import app

# ---------------------------------------------------------------------------
# We stub Config.from_env so tests don't need a real .env
# ---------------------------------------------------------------------------

import os
os.environ.setdefault("VAPI_API_KEY",           "test-vapi-key")
os.environ.setdefault("VAPI_PHONE_NUMBER_ID",   "test-phone-number-id")
os.environ.setdefault("VAPI_ASSISTANT_ID",      "test-assistant-id")
os.environ.setdefault("OPENAI_API_KEY",         "test-openai-key")
os.environ.setdefault("TWILIO_ACCOUNT_SID",     "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN",      "test-auth-token")
os.environ.setdefault("TWILIO_WHATSAPP_FROM",   "whatsapp:+14155238886")
os.environ.setdefault("TARGET_PHONE",           "+911234567890")
os.environ.setdefault("ADMIN_API_KEY",          "test-admin-key")
os.environ.setdefault("CANDIDATE_MOBILE",       "+919999999999")
os.environ.setdefault("CANDIDATE_RESUME_URL",   "https://example.com/resume.pdf")
os.environ.setdefault("ARCHITECTURE_IMAGE_URL", "https://example.com/arch.png")

HOT_TRANSCRIPT = """
Agent: Hi, online store banana chahte hain?
Lead:  Let's do it! How do I pay? Abhi shuru karte hain.
       I want to move forward today. Send me details on WhatsApp.
"""

WARM_TRANSCRIPT = """
Agent: Would you like a website for your business?
Lead:  Maybe. I'm thinking about it. Not sure about the budget right now.
       Can you call me next month?
"""

COLD_TRANSCRIPT = """
Agent: Hi, we offer website development services.
Lead:  Not interested at all. Please don't call again. I am not interested.
"""


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

class TestSimulateResponseShape:
    def test_returns_200_on_valid_input(self, client):
        resp = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT})
        assert resp.status_code == 200

    def test_response_has_call_id(self, client):
        resp = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT})
        assert "call_id" in resp.json()

    def test_response_has_classification_block(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert "classification" in data
        clf = data["classification"]
        for field in ("status", "confidence", "confidence_pct", "language_detected",
                      "reasoning_summary", "readable_evidence", "top_evidence"):
            assert field in clf, f"Missing field: {field}"

    def test_response_has_persona_block(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert "persona" in data
        p = data["persona"]
        for field in ("persona", "confidence", "coaching_instruction", "followup_angle"):
            assert field in p, f"Missing field: {field}"

    def test_response_has_diagram_block(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert "diagram" in data
        d = data["diagram"]
        assert "svg_base64" in d
        assert "url_path" in d
        assert "size_bytes" in d
        assert d["size_bytes"] > 0

    def test_response_has_whatsapp_preview(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert "whatsapp_preview" in data
        assert len(data["whatsapp_preview"]) > 20

    def test_response_has_pipeline_ms(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert "pipeline_ms" in data
        assert isinstance(data["pipeline_ms"], (int, float))
        assert data["pipeline_ms"] >= 0


# ---------------------------------------------------------------------------
# Classification correctness via /simulate
# ---------------------------------------------------------------------------

class TestSimulateClassification:
    def test_hot_transcript_classifies_hot(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert data["classification"]["status"].upper() == "HOT"

    def test_warm_transcript_classifies_warm(self, client):
        data = client.post("/simulate", json={"transcript": WARM_TRANSCRIPT}).json()
        assert data["classification"]["status"].upper() == "WARM"

    def test_cold_transcript_classifies_cold(self, client):
        data = client.post("/simulate", json={"transcript": COLD_TRANSCRIPT}).json()
        assert data["classification"]["status"].upper() == "COLD"

    def test_confidence_is_between_0_and_1(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        c = data["classification"]["confidence"]
        assert 0.0 <= c <= 1.0

    def test_confidence_pct_matches_confidence(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        clf = data["classification"]
        assert clf["confidence_pct"] == int(clf["confidence"] * 100)

    def test_top_evidence_is_list(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        ev = data["classification"]["top_evidence"]
        assert isinstance(ev, list)
        assert len(ev) > 0

    def test_top_evidence_has_required_fields(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        for item in data["classification"]["top_evidence"]:
            assert "matched_text" in item
            assert "signal_name" in item
            assert "weight" in item


# ---------------------------------------------------------------------------
# WhatsApp preview correctness
# ---------------------------------------------------------------------------

class TestSimulateWhatsAppPreview:
    def test_hot_preview_contains_fire_emoji(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert "🔥" in data["whatsapp_preview"]

    def test_warm_preview_is_non_empty(self, client):
        # WARM transcripts with objections get objection-aware rebuttals instead
        # of the generic 😊 template — verify the preview is still meaningful.
        data = client.post("/simulate", json={"transcript": WARM_TRANSCRIPT}).json()
        assert len(data["whatsapp_preview"]) > 20

    def test_cold_preview_contains_wave_emoji(self, client):
        data = client.post("/simulate", json={"transcript": COLD_TRANSCRIPT}).json()
        assert "👋" in data["whatsapp_preview"]

    def test_hot_preview_contains_resume_url(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert "resume" in data["whatsapp_preview"].lower()

    def test_hot_preview_contains_diagram_url(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert "/diagram/" in data["whatsapp_preview"]


# ---------------------------------------------------------------------------
# Custom call_id
# ---------------------------------------------------------------------------

class TestSimulateCallId:
    def test_custom_call_id_is_echoed(self, client):
        data = client.post("/simulate", json={
            "transcript": HOT_TRANSCRIPT,
            "call_id": "my-demo-call-42",
        }).json()
        assert data["call_id"] == "my-demo-call-42"

    def test_auto_call_id_starts_with_sim(self, client):
        data = client.post("/simulate", json={"transcript": HOT_TRANSCRIPT}).json()
        assert data["call_id"].startswith("sim-")

    def test_diagram_url_path_uses_call_id(self, client):
        data = client.post("/simulate", json={
            "transcript": HOT_TRANSCRIPT,
            "call_id": "test-abc",
        }).json()
        assert data["diagram"]["url_path"] == "/diagram/test-abc.svg"

    def test_diagram_is_cached_at_svg_endpoint(self, client):
        """After /simulate, the SVG should be retrievable at /diagram/{id}.svg"""
        call_id = "cache-test-001"
        client.post("/simulate", json={"transcript": HOT_TRANSCRIPT, "call_id": call_id})
        svg_resp = client.get(f"/diagram/{call_id}.svg")
        assert svg_resp.status_code == 200
        assert svg_resp.headers["content-type"] == "image/svg+xml"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestSimulateErrors:
    def test_missing_transcript_returns_422(self, client):
        resp = client.post("/simulate", json={})
        assert resp.status_code == 422

    def test_empty_transcript_returns_422(self, client):
        resp = client.post("/simulate", json={"transcript": ""})
        assert resp.status_code == 422

    def test_whitespace_transcript_returns_422(self, client):
        resp = client.post("/simulate", json={"transcript": "   \n  "})
        assert resp.status_code == 422

    def test_non_json_body_returns_400(self, client):
        resp = client.post("/simulate", content="not json",
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# SVG diagram endpoint
# ---------------------------------------------------------------------------

class TestDiagramEndpoint:
    def test_unknown_call_id_returns_404(self, client):
        resp = client.get("/diagram/nonexistent-call-xyz.svg")
        assert resp.status_code == 404

    def test_svg_content_type(self, client):
        call_id = "diagram-ct-test"
        client.post("/simulate", json={"transcript": HOT_TRANSCRIPT, "call_id": call_id})
        resp = client.get(f"/diagram/{call_id}.svg")
        assert resp.status_code == 200
        assert "svg" in resp.headers["content-type"]
