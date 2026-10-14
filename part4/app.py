"""Part 4: one page, two tools.

    python part4/app.py      then open http://127.0.0.1:5000

 1. Ask the document assistant (Part 1: mgc.Assistant) -> answer + sources.
 2. Score a lead (Part 3: part3/model.joblib)          -> probability + priority band.

Run it from the repo root.
"""
import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from flask import Flask, render_template_string, request

ROOT = Path(__file__).resolve().parent.parent        # repo root (this file lives in part4/)
sys.path.insert(0, str(ROOT / "part1"))               # makes `import mgc` find part1/mgc
from mgc import Assistant                             # noqa: E402  (must come after the path line)

app = Flask(__name__)

# Offline (deterministic) by default. Set MGC_USE_LLM=1 to let Part 1 use its optional LLM path.
bot = Assistant(use_llm=os.environ.get("MGC_USE_LLM") == "1")

MODEL_PATHS = [ROOT / "part3" / "model.joblib"]
MODEL = next((joblib.load(p) for p in MODEL_PATHS if p.exists()), None)

NO_BEDROOM_TYPES = {"Plot", "Commercial Shop"}   # bedrooms is "not applicable" for these (as in training)
BAND_LABEL = {
    "call_first": "CALL FIRST",
    "medium": "MEDIUM PRIORITY",
    "low": "LOW PRIORITY",
}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>MGC sales assistant</title></head>
<body style="font-family: sans-serif; max-width: 860px; margin: 24px auto; padding: 0 12px;">
<h1>MGC sales assistant</h1>
<p>Assistant mode: <b>{{ 'LLM' if llm_mode else 'offline (deterministic)' }}</b></p>

<h2 id="ask">1. Ask the document assistant</h2>
<form method="post" action="/ask#ask">
  <input name="question" size="80" value="{{ question or '' }}" placeholder="e.g. What's the transfer fee?" required>
  <button type="submit">Ask</button>
</form>
{% if answer %}
  <p><b>Status:</b> {{ answer.status }}</p>
  <pre style="white-space: pre-wrap; background: #f4f4f4; padding: 10px;">{{ answer.text }}</pre>
  {% if answer.notes %}
    <p><b>Flags / assumptions</b></p>
    <ul>{% for n in answer.notes %}<li>{{ n }}</li>{% endfor %}</ul>
  {% endif %}
  <p><b>Sources</b></p>
  {% if sources %}
    <ul>{% for doc, section in sources %}<li>{{ doc }} &gt; {{ section }}</li>{% endfor %}</ul>
  {% else %}
    <p>(none: nothing in the documents supports an answer)</p>
  {% endif %}
{% endif %}

<hr>
<h2 id="score">2. Score a lead</h2>
{% if not model_ok %}
  <p style="color: #b00;">Scoring model not found. Run <code>python part3/part3_model.py leads.csv</code> first, then restart this app.</p>
{% else %}
<form method="post" action="/score#score">
  <table cellpadding="4">
    {% for col, label in [('source', 'Lead source'), ('city', 'City'), ('area', 'Area'), ('property_type', 'Property type')] %}
    <tr><td>{{ label }}</td><td>
      <select name="{{ col }}">
        {% for o in options[col] %}<option {{ 'selected' if f.get(col) == o }}>{{ o }}</option>{% endfor %}
      </select></td></tr>
    {% endfor %}
    <tr><td>Budget (PKR lac)</td><td><input name="budget" type="number" step="any" min="0" value="{{ f.get('budget', '') }}" required></td></tr>
    <tr><td>Bedrooms</td><td><input name="bedrooms" type="number" min="0" max="10" value="{{ f.get('bedrooms', '') }}"> (leave blank for plots and shops)</td></tr>
    <tr><td>Agent experience (years)</td><td><input name="agent_exp" type="number" step="any" min="0" value="{{ f.get('agent_exp', '') }}"> (optional)</td></tr>
    <tr><td></td><td><label><input type="checkbox" name="is_overseas" {{ 'checked' if f.get('is_overseas') }}> Overseas buyer</label></td></tr>
    <tr><td></td><td><label><input type="checkbox" name="referred" {{ 'checked' if f.get('referred') }}> Referred by an existing client</label></td></tr>
    <tr><td></td><td><label><input type="checkbox" name="financing" {{ 'checked' if f.get('financing') }}> Financing approved</label></td></tr>
  </table>
  <button type="submit">Score lead</button>
</form>
{% if errors %}<ul style="color: #b00;">{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>{% endif %}
{% if result %}
  <p style="font-size: 1.3em;"><b>{{ result.label }}</b> &mdash; estimated conversion probability {{ result.pct }}%</p>
  <p>On leads scored in this band in the held-out test set (newest leads, never seen in training),
     <b>{{ result.band_rate }}%</b> converted ({{ result.band_leads }} leads). Average over all leads there: {{ result.base_rate }}%.</p>
{% endif %}
{% endif %}
</body></html>"""


def page(**ctx):
    ctx.setdefault("question", "")
    ctx.setdefault("answer", None)
    ctx.setdefault("sources", [])
    ctx.setdefault("errors", [])
    ctx.setdefault("result", None)
    ctx.setdefault("f", {})
    return render_template_string(
        PAGE, llm_mode=bot.use_llm, model_ok=MODEL is not None,
        options=MODEL["categories"] if MODEL else {}, **ctx)


@app.get("/")
def index():
    return page()


@app.post("/ask")
def ask():
    question = request.form.get("question", "").strip()
    answer = bot.ask(question)
    seen, sources = set(), []
    for s in answer.sources:                      # show each (document, section) once
        if (s.doc, s.section) not in seen:
            seen.add((s.doc, s.section))
            sources.append((s.doc, s.section))
    return page(question=question, answer=answer, sources=sources)


def parse_lead(form):
    """Turn the form into one model-ready row, applying the same rules as Part 3 training."""
    errors, row = [], {}
    for col in ("source", "city", "area", "property_type"):
        value = form.get(col, "")
        if value not in MODEL["categories"][col]:
            errors.append(f"Unknown {col}: {value!r}")
        row[col] = value

    try:
        budget = float(form.get("budget", ""))
        if budget <= 0:
            raise ValueError
        row["budget_log"] = float(np.log1p(budget))
    except ValueError:
        errors.append("Budget must be a positive number (PKR lac).")

    bedrooms = form.get("bedrooms", "").strip()
    if row["property_type"] in NO_BEDROOM_TYPES:
        row["bedrooms"] = 0                       # not applicable; training filled 0 for these too
    elif not bedrooms:
        errors.append("Bedrooms is required for this property type.")
    else:
        try:
            row["bedrooms"] = int(bedrooms)
            if not 0 <= row["bedrooms"] <= 10:
                raise ValueError
        except ValueError:
            errors.append("Bedrooms must be a whole number from 0 to 10.")

    exp = form.get("agent_exp", "").strip()
    try:
        row["agent_experience_years"] = float(exp) if exp else np.nan   # blank -> model's median imputer
        if exp and row["agent_experience_years"] < 0:
            raise ValueError
    except ValueError:
        errors.append("Agent experience must be a non-negative number.")

    row["is_overseas"] = int("is_overseas" in form)
    row["referred_by_existing_client"] = int("referred" in form)
    row["has_financing_approved"] = int("financing" in form)
    return row, errors


@app.post("/score")
def score():
    if MODEL is None:
        return page()
    row, errors = parse_lead(request.form)
    if errors:
        return page(errors=errors, f=request.form)

    X = pd.DataFrame([row])[MODEL["features"]]
    p = float(MODEL["pipeline"].predict_proba(X)[0, 1])
    th = MODEL["band_thresholds"]
    band = "call_first" if p >= th["call_first"] else "medium" if p >= th["medium"] else "low"
    stats = MODEL["band_stats"]
    total = sum(s["leads"] for s in stats.values())
    base = sum(s["leads"] * s["conversion_rate"] for s in stats.values()) / total
    result = {
        "label": BAND_LABEL[band],
        "pct": round(100 * p, 1),
        "band_rate": round(100 * stats[band]["conversion_rate"], 1),
        "band_leads": stats[band]["leads"],
        "base_rate": round(100 * base, 1),
    }
    return page(result=result, f=request.form)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
