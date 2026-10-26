"""Score one job description against cv.json.

The LLM does the reading; the final score is adjusted by plain rules Arjun controls
(English-first bonus, German-required penalty, location, Pflichtpraktikum fit), so the
ranking never depends on the model alone.
"""

import json
from .extract import language_signals

SYSTEM = (
    "You are a strict technical recruiter screening internship applications in Germany. "
    "Judge fit honestly. Use ONLY facts present in the candidate profile; never invent experience, "
    "tools, grades or languages. If the candidate lacks something, say so."
)

PROMPT = """CANDIDATE PROFILE (JSON):
{cv}

JOB DESCRIPTION:
{jd}

Return JSON with exactly these keys:
{{
  "role_summary": "one sentence: what the job actually is",
  "contract_type": "Pflichtpraktikum | Praktikum | Werkstudent | Full-time | Other",
  "location": "city or Remote",
  "start": "start date as written, or 'unknown'",
  "german_level_required": "none | basic | B2 | C1 | unclear",
  "english_ok": true/false,
  "must_have": ["up to 6 hard requirements from the JD"],
  "matched": ["requirements the candidate clearly meets, with the CV evidence in brackets"],
  "gaps": ["requirements the candidate does not meet"],
  "fit_score": 0-100,
  "verdict": "APPLY | MAYBE | SKIP",
  "why": "two sentences max, honest",
  "highlights": ["exactly 5 short bullets: which CV facts to put first for THIS job, reworded to the JD's language but factually unchanged"]
}}"""


def analyze(llm, cv: dict, jd_text: str) -> dict:
    data = llm.json(SYSTEM, PROMPT.format(cv=json.dumps(cv, ensure_ascii=False), jd=jd_text), max_tokens=4096)
    data["final_score"], data["adjustments"] = adjust(data, jd_text)
    return data


def adjust(data: dict, jd_text: str):
    """Deterministic rules on top of the model's fit_score. Returns (score, notes)."""
    score = int(data.get("fit_score", 0) or 0)
    notes = []
    sig = language_signals(jd_text)
    german = str(data.get("german_level_required", "")).upper()

    if sig["english_first"] or (data.get("english_ok") and german in ("NONE", "BASIC")):
        score += 15; notes.append("+15 English-first")
    if sig["german_required"] or german in ("B2", "C1"):
        score -= 25; notes.append("-25 strong German required")

    loc = str(data.get("location", "")).lower()
    if "hamburg" in loc or "remote" in loc:
        score += 5; notes.append("+5 Hamburg/remote")

    ctype = str(data.get("contract_type", "")).lower()
    if "pflicht" in ctype:
        score += 5; notes.append("+5 Pflichtpraktikum")
    elif "full-time" in ctype:
        score -= 20; notes.append("-20 full-time job, not internship")

    return max(0, min(100, score)), notes
