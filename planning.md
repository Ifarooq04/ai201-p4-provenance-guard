# Provenance Guard — Planning

## 1. Detection Signals

**Signal 1: LLM-based classification (Groq — llama-3.3-70b-versatile)**
- Measures: holistic semantic/stylistic coherence. The model is prompted to assess whether text reads as human- or AI-generated and return a score.
- Output format: float 0–1 (probability text is AI-generated), plus a short text justification from the model.
- Why chosen: captures things structural analysis can't — tone, argument balance, hedging language, unnatural "evenness" in how ideas are presented.
- Blind spot: black-box reasoning; can be fooled by lightly-edited AI text, and can misjudge human writers whose natural style is formal/hedged (e.g., academics, ESL writers).

**Signal 2: Stylometric heuristics (pure Python)**
- Measures: structural/statistical properties — sentence length variance, type-token ratio (vocabulary diversity), punctuation density.
- Output format: each metric computed independently, then normalized and averaged into a single float 0–1 (higher = more "AI-like," i.e., more uniform).
- Why chosen: AI-generated text tends toward statistical uniformity; human writing tends to be "messier." This is a fully independent, transparent signal — no black box.
- Blind spot: purely structural, has no sense of meaning. A human with a disciplined/repetitive style (technical writers, children's books) can score identically to AI text. Can also miss AI text that's been heavily human-edited.

**Combining into one score:**
Combined confidence = `(0.6 × llm_score) + (0.4 × stylometric_score)`
LLM signal is weighted higher since it's semantically aware and generally more reliable on its own; stylometrics acts as a supporting/sanity-check signal.

## 2. Uncertainty Representation

- Combined confidence score is a float 0–1, where scores near 1 = "likely AI," near 0 = "likely human," and the middle band = genuine uncertainty.
- Thresholds:
  - **≥ 0.75** → "Likely AI-generated"
  - **0.35 – 0.74** → "Uncertain" (deliberately wide band — false positives on human writers are worse than false negatives, so we default to uncertainty rather than confident misclassification)
  - **≤ 0.34** → "Likely human-written"
- A 0.51 score and a 0.95 score must produce different labels — this is enforced directly by the threshold bands above, not a single 0.5 cutoff.
- Calibration approach: raw signal outputs (LLM score, stylometric score) are each already normalized 0–1 before combination, so no additional rescaling is needed — validated by testing against known AI/human/borderline text samples (see Milestone 4 testing).

## 3. Transparency Label Design

- **High-confidence AI:** `"This content appears to be AI-generated (confidence: high). Our system detected strong indicators of AI authorship based on writing style and structural patterns."`
- **High-confidence human:** `"This content appears to be human-written (confidence: high). Our system found writing patterns consistent with typical human authorship."`
- **Uncertain:** `"We're not confident whether this content is AI-generated or human-written. The creator can appeal this classification if they believe it's inaccurate."`

## 4. Appeals Workflow

- **Who can appeal:** the original creator (identified by `creator_id` tied to the `content_id`).
- **What they provide:** `content_id` + `creator_reasoning` (free text explaining why they believe the classification is wrong).
- **What the system does:**
  1. Looks up the content_id in storage.
  2. Updates status to `"under_review"`.
  3. Logs the appeal in the audit log alongside the original decision (timestamp, reasoning, status change).
  4. Returns a confirmation response to the creator.
- **What a human reviewer would see:** the original submission text, both signal scores, the combined confidence score, the label that was shown, and the creator's appeal reasoning — all in one audit log entry, so they have full context without cross-referencing separate systems.
- Automated re-classification is out of scope — a human makes the final call.

## 5. Anticipated Edge Cases

1. **ESL/non-native English writing:** Non-native speakers often use more uniform sentence structures and formal/hedged phrasing (a small set of "safe" grammatical constructions), which can trip both signals toward "AI-like" even though the writing is genuinely human. This is the core false-positive risk this system needs to guard against.
2. **Repetitive-by-design creative writing:** A poem or piece using deliberate repetition and simple vocabulary as a stylistic device (e.g., children's poetry, minimalist prose) will likely score high on stylometric uniformity, even though it's human-authored and repetition is an intentional artistic choice, not evidence of AI generation.

## Architecture

### Submission Flow
Creator → POST /submit → Rate Limiter → Signal 1 (LLM) + Signal 2 (Stylometrics)
→ Confidence Scoring → Label Generation → Audit Log Write
→ Response {content_id, attribution, confidence, label}
### Appeal Flow
Creator → POST /appeal {content_id, creator_reasoning} → Look up content_id
→ Update status to "under_review" → Audit Log Update
→ Response {status, content_id, message}
Narrative: A submission passes through rate limiting first to block abuse, then both detection signals run against the raw text independently, producing two separate scores. Those scores are combined into one confidence value, which is mapped to a transparency label before everything is written to the audit log and returned to the creator. Appeals are a separate, simpler path: they don't re-run detection, they just attach human context to an existing decision and flag it for manual review.

## AI Tool Plan

**M3 (submission endpoint + first signal):**
- Spec sections provided: Detection Signals (Signal 1) + Architecture diagram.
- Ask for: Flask app skeleton with `POST /submit` route stub, plus the Groq LLM signal function.
- Verify: call the signal function directly with 2–3 test strings and check the score/reasoning shape before wiring into the endpoint.

**M4 (second signal + confidence scoring):**
- Spec sections provided: Detection Signals (Signal 2) + Uncertainty Representation + Architecture diagram.
- Ask for: stylometric heuristic function + the confidence scoring/combination logic.
- Verify: run the 4 test inputs (clearly AI, clearly human, 2 borderline) and confirm scores land in the expected threshold bands from section 2.

**M5 (production layer):**
- Spec sections provided: Transparency Label Design + Appeals Workflow + Architecture diagram.
- Ask for: label generation function (score → label text) + `POST /appeal` endpoint.
- Verify: submit inputs that hit all three label bands and confirm exact text matches section 3; submit an appeal and confirm status flips to `under_review` and appears correctly in `GET /log`.
