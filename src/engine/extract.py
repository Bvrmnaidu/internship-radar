"""Get the fullest job-description text we can for a listing.

Portal search pages only give a snippet; for the shortlisted jobs we open the posting itself.
Best effort: if the page is blocked or JS-only, fall back to the snippet.
"""

import re
from bs4 import BeautifulSoup

MAX_CHARS = 9000   # keep prompts cheap

GERMAN_REQ_PATTERNS = [
    r"sehr gute? deutsch", r"fließend(e|es)? deutsch", r"verhandlungssicher(e|es)? deutsch",
    r"deutsch\s*\(?c1", r"deutsch\s*\(?c2", r"deutsch\s*\(?b2", r"german\s*\(?c1", r"german\s*\(?b2",
    r"fluent (in )?german", r"business fluent german", r"german (is )?(a )?must",
    r"excellent german", r"very good (written and spoken |spoken and written )?german", r"fluently in german",
    r"sehr gute deutsch- und englischkenntnisse", r"(business[- ]fluent|full professional proficiency in) german",
]
ENGLISH_FIRST_PATTERNS = [
    r"working language (is )?english", r"english[- ]speaking (team|environment|company)",
    r"no german (required|needed)", r"german (is )?(a )?(plus|nice to have)",
    r"company language (is )?english", r"international team",
]


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg", "form"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def fetch_jd_text(job: dict, getter) -> str:
    """getter: function(url) -> html string ('' on failure). Injected so tests need no network."""
    snippet = job.get("desc", "") or ""
    html = getter(job["url"]) if job.get("url") else ""
    page = html_to_text(html) if html else ""
    text = page if len(page) > len(snippet) + 200 else snippet
    return f"{job.get('title','')} at {job.get('company','')} ({job.get('location','')})\n\n{text}"[:MAX_CHARS]


def language_signals(text: str) -> dict:
    t = text.lower()
    return {
        "german_required": any(re.search(p, t) for p in GERMAN_REQ_PATTERNS),
        "english_first":   any(re.search(p, t) for p in ENGLISH_FIRST_PATTERNS),
    }
