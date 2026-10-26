"""Draft a tailored cover letter for the top jobs. Facts come only from cv.json."""

import json

SYSTEM = (
    "You write concise, specific internship cover letters for the German market. "
    "Hard rules: use ONLY facts in the candidate profile; never invent projects, metrics, tools, "
    "grades or German ability. No cliches ('I am writing to express my interest', 'passionate'). "
    "Plain text, no markdown."
)

PROMPT = """CANDIDATE PROFILE (JSON):
{cv}

JOB DESCRIPTION:
{jd}

SCREENING NOTES (what to emphasise / what is missing):
{notes}

Write a cover letter of 180-230 words in English:
- Line 1: "Subject: Application - <role> - <location>"
- Paragraph 1: who he is + Pflichtpraktikum status in one sentence (350 h, certificate issued, no sponsorship needed).
- Paragraph 2: the single most relevant piece of experience mapped to the job's main task.
- Paragraph 3: 3-4 other matching skills, in the job's own wording.
- If a key requirement is missing, do NOT mention it.
- Close with availability and the signature: name, email, phone, LinkedIn.
"""


def cover_letter(llm, cv: dict, jd_text: str, analysis: dict) -> str:
    notes = {k: analysis.get(k) for k in ("matched", "gaps", "highlights")}
    letter = llm.text(
        SYSTEM,
        PROMPT.format(cv=json.dumps(cv, ensure_ascii=False), jd=jd_text, notes=json.dumps(notes, ensure_ascii=False)),
        max_tokens=6000,
    )
    if len(letter.split()) < 60:            # an empty or truncated letter is a failure, not a letter
        raise ValueError(f"letter too short ({len(letter.split())} words) - output likely cut off")
    return letter
