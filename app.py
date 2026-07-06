import os
import json
import uuid
import re
import statistics
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from dotenv import load_dotenv
from groq import Groq
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
load_dotenv()

app = Flask(__name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)
LOG_FILE = "audit_log.json"


# ---------- Audit log helpers ----------

def get_log():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        return json.load(f)


def write_log_entry(entry):
    entries = get_log()
    entries.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)
def update_log_entry(content_id, updates):
    """
    Finds a log entry by content_id and merges in the given updates.
    Returns True if found and updated, False if not found.
    """
    entries = get_log()
    found = False
    for entry in entries:
        if entry.get("content_id") == content_id:
            entry.update(updates)
            found = True
            break
    if found:
        with open(LOG_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    return found


# ---------- Signal 1: LLM-based classification (Groq) ----------

def llm_signal(text):
    """
    Sends text to Groq and asks it to judge human vs AI authorship.
    Returns a float 0-1 (probability the text is AI-generated) plus reasoning.
    """
    prompt = f"""You are an AI content detector. Analyze the following text and
estimate the probability that it was written by an AI rather than a human.

Respond with ONLY a JSON object in this exact format, nothing else:
{{"ai_probability": 0.0, "reasoning": "brief explanation"}}

Text to analyze:
\"\"\"{text}\"\"\"
"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(raw)
        score = float(parsed.get("ai_probability", 0.5))
        reasoning = parsed.get("reasoning", "")
    except (json.JSONDecodeError, ValueError):
        score = 0.5
        reasoning = "Could not parse model output; defaulted to uncertain."

    return score, reasoning


# ---------- Signal 2: Stylometric heuristics ----------

def stylometric_signal(text):
    """
    Computes structural/statistical properties of the text.
    Returns a float 0-1 (higher = more "AI-like," i.e., more uniform).
    """
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) < 2:
        return 0.5

    sentence_lengths = [len(s.split()) for s in sentences]
    length_stdev = statistics.pstdev(sentence_lengths)
    mean_length = statistics.mean(sentence_lengths) if sentence_lengths else 1
    cv = length_stdev / mean_length if mean_length > 0 else 0
    length_uniformity_score = max(0.0, min(1.0, 1 - (cv / 1.0)))

    words = re.findall(r"\b\w+\b", text.lower())
    if len(words) == 0:
        ttr = 1.0
    else:
        ttr = len(set(words)) / len(words)
    vocab_uniformity_score = max(0.0, min(1.0, 1 - ttr))

    punctuation_count = len(re.findall(r'[,;:\-]', text))
    punctuation_density = punctuation_count / len(words) if words else 0
    punctuation_score = max(0.0, min(1.0, punctuation_density * 5))

    combined = (
        0.5 * length_uniformity_score +
        0.35 * vocab_uniformity_score +
        0.15 * punctuation_score
    )

    return round(combined, 3)


# ---------- Routes ----------

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(force=True)
    text = data.get("text", "")
    creator_id = data.get("creator_id", "unknown")

    if not text:
        return jsonify({"error": "text field is required"}), 400

    content_id = str(uuid.uuid4())

    llm_score, llm_reasoning = llm_signal(text)
    style_score = stylometric_signal(text)

    confidence = round((0.6 * llm_score) + (0.4 * style_score), 3)
    if confidence >= 0.75:
        label = "This content appears to be AI-generated (confidence: high). Our system detected strong indicators of AI authorship based on writing style and structural patterns."
    elif confidence <= 0.34:
        label = "This content appears to be human-written (confidence: high). Our system found writing patterns consistent with typical human authorship."
    else:
        label = "We're not confident whether this content is AI-generated or human-written. The creator can appeal this classification if they believe it's inaccurate."

    entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attribution": "likely_ai" if confidence >= 0.5 else "likely_human",
        "confidence": confidence,
        "llm_score": llm_score,
        "llm_reasoning": llm_reasoning,
        "stylometric_score": style_score,
        "status": "classified",
    }

    write_log_entry(entry)

    return jsonify({
        "content_id": content_id,
        "attribution": entry["attribution"],
        "confidence": confidence,
        "label": label,
    })
@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(force=True)
    content_id = data.get("content_id", "")
    creator_reasoning = data.get("creator_reasoning", "")

    if not content_id or not creator_reasoning:
        return jsonify({"error": "content_id and creator_reasoning are required"}), 400

    updated = update_log_entry(content_id, {
        "status": "under_review",
        "appeal_reasoning": creator_reasoning,
        "appeal_timestamp": datetime.now(timezone.utc).isoformat(),
    })

    if not updated:
        return jsonify({"error": "content_id not found"}), 404

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Appeal received and logged. This content is now under review.",
    })

@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
