# Examples

These scripts demonstrate the system's intelligence layer with **no API keys required**.
All demos run entirely offline using only the Python standard library.

---

## Quick start

```bash
# From the project root
cd elevate-voice-agent

python examples/demo_classifier.py       # Intent classification demo
python examples/demo_persona.py          # Buyer persona detection demo
python examples/demo_diagram.py          # SVG diagram generation demo
python examples/demo_full_pipeline.py    # Everything together
```

---

## What each demo shows

### `demo_classifier.py`
Runs 6 sample transcripts (English / Hindi / Telugu / code-switching) through the
evidence-based classifier. Prints the HOT/WARM/COLD decision, confidence score,
named evidence trail, budget detection, and the reasoning summary that goes
into the WhatsApp follow-up message.

### `demo_persona.py`
Runs 7 sample transcripts through the buyer persona detector. Shows:
- Which of the 4 personas was detected (Executive / Explorer / Budget / Time-Pressured)
- Confidence score and matched signal names
- The mid-call coaching instruction that gets injected into GPT-4o
- The tailored WhatsApp follow-up angle for each persona

### `demo_diagram.py`
Generates 3 sample SVG architecture diagrams (HOT / WARM / COLD) and saves
them to `examples/output/`. Open any `.svg` file in a browser to preview.
In production, each diagram is served at `/diagram/{call_id}.svg` and
included as the media URL in the post-call WhatsApp.

### `demo_full_pipeline.py`
The most complete offline demo. Simulates a full post-call pipeline run:
1. Shows a realistic Hindi+English code-switching transcript
2. Classifies it with the evidence engine (status, confidence, evidence trail)
3. Detects the buyer persona and mid-call coaching instruction
4. Generates the per-call SVG diagram
5. Builds and prints the WhatsApp message that would be sent

---

## To test with your own transcript

```python
from src.intelligence import classify_with_evidence
from src.persona import detect_persona

transcript = """
Agent: Hi, can I help you with your website?
Lead:  Yes, I need it by Diwali. How much does it cost?
"""

classification = classify_with_evidence(transcript)
print(classification.status, classification.confidence)
print(classification.readable_evidence())

persona = detect_persona(transcript)
print(persona.persona, persona.coaching_instruction)
```

---

## Output files

`demo_diagram.py` and `demo_full_pipeline.py` write SVG files to `examples/output/`.
This directory is gitignored — generated files are not committed.
