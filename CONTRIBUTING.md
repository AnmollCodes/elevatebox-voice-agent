# Contributing to ElevateBox Voice Agent

Thank you for your interest in contributing. This is an open-source project — contributions are welcome whether it's a bug fix, a new language's signal bank, or a new buyer persona.

---

## Getting started

```bash
git clone https://github.com/yourusername/elevate-voice-agent.git
cd elevate-voice-agent

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

make dev        # installs all dev + test dependencies
make test       # run the full test suite (should show 164 passing)
make demo-full  # try the system without any API keys
```

---

## Project structure

```
src/
├── intelligence.py  ← Evidence-based classifier (start here for classification changes)
├── persona.py       ← Buyer persona detection (add new personas here)
├── diagram.py       ← Per-call SVG generator
├── classifier.py    ← Simple keyword classifier (legacy, kept for reference)
├── scheduler.py     ← Natural language → datetime resolver
├── whatsapp.py      ← Twilio WhatsApp sender
├── call_handler.py  ← Vapi webhook event router
├── vapi_client.py   ← Vapi API client
├── metrics.py       ← Circuit breaker, rate limiter, call analytics
├── config.py        ← Environment variable config
├── prompts.py       ← Agent system prompt and WhatsApp templates
└── main.py          ← FastAPI application
```

---

## How to add a new language

The classifier and persona detector both use plain phrase lists — no ML, no model retraining.

**Step 1:** Open `src/intelligence.py` and find the signal bank you want to extend, e.g. `_HOT_SIGNALS`. Add your phrases in the same format:

```python
_HOT_SIGNALS: list[tuple[str, str, float]] = [
    # ... existing signals ...

    # Tamil — add your language here
    ("Tamil: how much", "evvalavu aagum", 0.85),
    ("Tamil: send details", "details anuppu", 0.90),
    ("Tamil: start now", "ippo start pannunga", 0.95),
]
```

**Step 2:** Add your language's marker words to `_detect_language()` in the same file:

```python
def _detect_language(text: str) -> str:
    hindi_markers   = [...]
    telugu_markers  = [...]
    tamil_markers   = ["aamaa", "illai", "eppo", "sollunga", "nandri"]  # ← add this

    tamil_count  = sum(1 for w in tamil_markers if w in text)
    # ... add detection logic ...
```

**Step 3:** Add equivalent signals to `src/persona.py` for the new language.

**Step 4:** Write tests for the new language in the relevant test file:

```python
# tests/test_intelligence.py
def test_tamil_hot_signal(self):
    result = classify_with_evidence("evvalavu aagum? details anuppu.")
    assert result.status == LeadStatus.HOT
```

**Step 5:** Run `make test` — all 164 existing tests must still pass.

---

## How to add a new buyer persona

Open `src/persona.py`:

1. Add your persona to the `BuyerPersona` enum
2. Create a `_YOUR_SIGNALS` list with weighted phrases
3. Add it to the scoring in `detect_persona()`
4. Add its coaching template to `_COACHING_TEMPLATES`
5. Add its WhatsApp angle to `_FOLLOWUP_ANGLES`
6. Write tests in `tests/test_persona.py`

---

## Code style

- Python 3.11+
- `ruff` for linting: `make lint`
- Type hints on all public functions
- Docstrings on all modules and public functions
- No external ML dependencies (keep the cold-start time low)
- No secrets in source code — everything via environment variables

Run `make check` before opening a PR (runs lint + security scan + all tests).

---

## Pull request checklist

- [ ] `make test` passes (164+ tests, no regressions)
- [ ] `make lint` passes (no ruff errors)
- [ ] `make security` passes (no bandit findings)
- [ ] New public functions have docstrings
- [ ] New signals have corresponding tests
- [ ] No API keys, tokens, or real phone numbers in any file
- [ ] `.env` is NOT committed (check `.gitignore`)

---

## Reporting bugs

Open an issue with:
- What you expected to happen
- What actually happened
- The transcript or input that triggered it
- Your Python version and OS

Mask any real phone numbers before pasting (e.g. +91868866**\*\***).

---

## Questions?

Open a GitHub Discussion or file an issue with the `question` label.
