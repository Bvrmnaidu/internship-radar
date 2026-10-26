"""
Internship Radar - daily internship search pipeline (Arjun Bhogadi).
Sources: Bundesagentur fuer Arbeit API, Arbeitnow API, Praktika.de (Algolia), StepStone, Unicum,
EnglishJobs.de, and LinkedIn / Glassdoor / Indeed via Apify. Portals that kept failing are kept below, switched off.
"""

import os, re, time, datetime, smtplib, pathlib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── Profile filters ────────────────────────────────────────────────────────────
SEARCH_QUERIES = [
    "data science internship",
    "machine learning Praktikum",
    "AI engineer Pflichtpraktikum",
    "data engineer Praktikum",
    "LLM RAG Praktikum",
    "NLP Praktikum",
    "data analyst Praktikum",
]

GOOD_KEYWORDS = [
    "python", "machine learning", "data science", "nlp", "rag", "llm",
    "langchain", "openai", "data engineer", "kafka", "spark", "gcp",
    "tableau", "power bi", "deep learning", "ai engineer", "analytics",
    "pflichtpraktikum", "mandatory internship", "english", "praktikum",
    "artificial intelligence", "big data", "postgresql", "vector",
]

REJECT_KEYWORDS = [
    "b2 german required", "german c1", "german b2", "deutschkenntnisse b2",
    "no visa", "citizen only", "must be eu", "eu citizen",
    "visa sponsorship not", "no sponsorship",
]

GOOD_LOCATIONS = [
    "hamburg", "berlin", "munich", "munchen", "remote", "homeoffice",
    "hybrid", "deutschland", "germany", "frankfurt", "koln", "cologne",
    "deutschlandweit", "bundesweit",
]

ROLE_TYPES = {
    "AI/LLM Engineer":  ["llm", "rag", "langchain", "agentic", "generative ai", "openai", "genai"],
    "ML Engineer":      ["machine learning", "deep learning", "pytorch", "tensorflow", "ml"],
    "Data Scientist":   ["data scien", "nlp", "text mining"],
    "Data Engineer":    ["data engineer", "kafka", "spark", "etl", "pipeline", "data warehouse", "big data"],
    "Data Analyst":     ["data analyst", "tableau", "power bi", "dashboard", "reporting", "analytics"],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Shared helpers ─────────────────────────────────────────────────────────────
HTTP_LOG = {}   # domain -> last outcome, shown in the run's diagnostics

def _domain(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]

def safe_get(url: str, timeout: int = 14) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        HTTP_LOG[_domain(url)] = f"HTTP {r.status_code}, {len(r.text)//1024} KB"
        r.raise_for_status()
        return r.text
    except Exception as e:
        HTTP_LOG.setdefault(_domain(url), f"{type(e).__name__}")
        print(f"  [WARN] {url[:80]}: {e}")
        return ""

def enc(q: str) -> str:
    return requests.utils.quote(q)

STUDENT_TITLE = re.compile(r"intern|praktik|werkstudent|working student|thesis|abschlussarbeit|trainee|masterand", re.I)
SENIOR_TITLE = re.compile(r"\b(senior|sr\.|lead|principal|head of|director|manager|staff)\b", re.I)


def score_job(title: str, desc: str, location: str) -> int:
    """Keyword score. Internship titles rank up, senior/manager titles and German-required postings rank down
    (26 Oct 2026: senior roles from EnglishJobs.de were topping the Excel)."""
    from engine.extract import language_signals
    text = (title + " " + desc + " " + location).lower()
    score = sum(2 for kw in GOOD_KEYWORDS if kw in text)
    score -= sum(5 for kw in REJECT_KEYWORDS if kw in text)
    if any(loc in location.lower() for loc in GOOD_LOCATIONS):
        score += 3
    if STUDENT_TITLE.search(title):
        score += 4
    if SENIOR_TITLE.search(title):
        score -= 8
    if language_signals(desc)["german_required"]:
        score -= 5
    return max(0, score)

def classify_role(title: str, desc: str) -> str:
    text = (title + " " + desc).lower()
    for role, kws in ROLE_TYPES.items():
        if any(kw in text for kw in kws):
            return role
    return "Data Science / ML"

def match_stars(score: int) -> str:
    if score >= 10: return "***"
    if score >= 6:  return "**"
    return "*"

DATA_SIGNAL = re.compile(r"\bdata\b|daten|python|\bsql\b|machine learning|\bml\b|\bai\b|\bki\b|künstliche intelligenz|"
                         r"artificial intelligence|llm|genai|analytics|analyst|data scien|nlp|deep learning|business intelligence|\bbi\b", re.I)
OFFTOPIC_TITLE = re.compile(r"sales|vertrieb|marketing|business development|privatkunden|kundenberatung|recruit|\bhr\b|"
                            r"personal(wesen|abteilung)|controlling|accounting|buchhaltung|steuer|tax|legal|recht|logistik|logistics|"
                            r"einkauf|procurement|strategy|strategie|consulting|beratung|security engineer|embedded|"
                            r"mechanical|maschinenbau|process engineering|verfahrenstechnik|design|ux|content|social media|event", re.I)


def is_relevant(title: str, desc: str) -> bool:
    """Data/AI roles only (26 Oct 2026: business-development, sales and logistics internships reached the top).
    An off-topic area in the title is fine when the title also names data/AI ('Intern HR Data Analytics & AI')."""
    if OFFTOPIC_TITLE.search(title) and not DATA_SIGNAL.search(title):
        return False
    return bool(DATA_SIGNAL.search(f"{title} {desc}"))


def make_job(company, title, location, url, desc, source, start="ASAP", duration="TBC", posted=""):
    if not title.strip() or not is_relevant(title, desc or ""):
        return None
    score = score_job(title, desc, location)
    if score < 2:
        return None
    return {
        "company":   company.strip(),
        "title":     title.strip(),
        "location":  location.strip() or "Germany",
        "start":     start,
        "duration":  duration,
        "role_type": classify_role(title, desc),
        "match":     match_stars(score),
        "source":    source,
        "url":       url,
        "score":     score,
        "desc":      (desc or "")[:4000],   # snippet, used by the AI engine
        "posted":    posted,                 # YYYY-MM-DD when the source provides it
    }

# ── 1. Indeed DE  (RSS) ───────────────────────────────────────────────────────
def scrape_indeed(query: str) -> list:
    url  = f"https://de.indeed.com/rss?q={enc(query)}&l=Deutschland&fromage=3&sort=date"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "xml")
    for item in soup.find_all("item")[:10]:
        title    = item.title.get_text(strip=True)  if item.title       else ""
        link     = item.link.get_text(strip=True)   if item.link        else ""
        desc     = item.description.get_text(" ")   if item.description else ""
        company  = item.find("source") or item.find("author")
        company  = company.get_text(strip=True) if company else "Unknown"
        loc_m    = re.search(r"Ort[:\s]+([^\n<,]+)", desc)
        location = loc_m.group(1).strip() if loc_m else "Germany"
        j = make_job(company, title, location, link, desc, "Indeed DE")
        if j: jobs.append(j)
    return jobs

# ── 2. StepStone ──────────────────────────────────────────────────────────────
def scrape_stepstone(query: str) -> list:
    url  = f"https://www.stepstone.de/jobs/{enc(query)}/in-deutschland"
    html = safe_get(url, timeout=35)   # StepStone is often slow to answer GitHub's servers (ReadTimeout at 14 s)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("article[data-at='job-item']")[:10]:
        t  = card.select_one("[data-at='job-item-title']")
        c  = card.select_one("[data-at='job-item-company-name']")
        lo = card.select_one("[data-at='job-item-location']")
        a  = card.select_one("a[data-at='job-item-title']")
        href = (a["href"] if a else "")
        full_url = href if href.startswith("http") else "https://www.stepstone.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "StepStone"
        )
        if j: jobs.append(j)
    return jobs

# ── 3. Monster DE ─────────────────────────────────────────────────────────────
def scrape_monster(query: str) -> list:
    url  = f"https://www.monster.de/jobs/suche/?q={enc(query)}&where=Deutschland"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in (soup.select("div[data-testid='jobCard']") or
                 soup.select("section.card-content"))[:10]:
        t  = card.select_one("h2,h3,[class*='title']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://www.monster.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "Monster.de"
        )
        if j: jobs.append(j)
    return jobs

# ── 4. Jobware ────────────────────────────────────────────────────────────────
def scrape_jobware(query: str) -> list:
    url  = f"https://www.jobware.de/suche/{enc(query)}/in-Deutschland"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in (soup.select("article.job") or soup.select("li.job-item"))[:10]:
        t  = card.select_one("h2 a,.jobtitle a,[class*='title'] a")
        c  = card.select_one("[class*='company'],[class*='arbeitgeber']")
        lo = card.select_one("[class*='location'],[class*='ort']")
        href = t["href"] if (t and t.has_attr("href")) else ""
        full_url = href if href.startswith("http") else "https://www.jobware.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "Jobware.de"
        )
        if j: jobs.append(j)
    return jobs

# ── 5. Karriere.de ────────────────────────────────────────────────────────────
def scrape_karriere(query: str) -> list:
    url  = f"https://www.karriere.de/jobs/{enc(query)}"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in (soup.select("article.job-listing") or
                 soup.select("[class*='JobCard'],[class*='job-card']"))[:10]:
        t  = card.select_one("h2,h3,[class*='title']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://www.karriere.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "Karriere.de"
        )
        if j: jobs.append(j)
    return jobs

# ── 6. Absolventa ─────────────────────────────────────────────────────────────
def scrape_absolventa(query: str) -> list:
    url  = f"https://www.absolventa.de/jobs?q={enc(query)}&contract_type=internship"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("article,[class*='job-item'],[class*='JobCard']")[:10]:
        t  = card.select_one("h2 a,h3 a,[class*='title'] a,[class*='headline'] a")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        href = t["href"] if (t and t.has_attr("href")) else ""
        full_url = href if href.startswith("http") else "https://www.absolventa.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "Absolventa.de"
        )
        if j: jobs.append(j)
    return jobs

# ── 7. Praktika.de ────────────────────────────────────────────────────────────
def scrape_praktika(query: str) -> list:
    # Search results are rendered in the browser from Algolia (Oct 2026), so read that index
    return _from_api(sources_api.praktika, "Praktika.de", query)

# ── 8. Praktikum.de ───────────────────────────────────────────────────────────
def scrape_praktikum_de(query: str) -> list:
    url  = f"https://www.praktikum.de/praktikum/{enc(query)}"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("[class*='InternshipItem'],[class*='job-card'],article")[:10]:
        t  = card.select_one("h2,h3,[class*='title'],[class*='headline']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://www.praktikum.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "Praktikum.de"
        )
        if j: jobs.append(j)
    return jobs

# ── 9. Praktikum.info ─────────────────────────────────────────────────────────
def scrape_praktikum_info(query: str) -> list:
    url  = f"https://www.praktikum.info/praktikumssuche?query={enc(query)}"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select(".internship-listing,article,[class*='ListItem']")[:10]:
        t  = card.select_one("h2,h3,[class*='title']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://www.praktikum.info" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "Praktikum.info"
        )
        if j: jobs.append(j)
    return jobs

# ── 10. Unicum ────────────────────────────────────────────────────────────────
def scrape_unicum(query: str) -> list:
    url  = f"https://karriere.unicum.de/praktikum?keywords={enc(query)}"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("article,[class*='job-item'],[class*='JobCard']")[:10]:
        t  = card.select_one("h2,h3,[class*='title'],[class*='headline']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://karriere.unicum.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "Unicum"
        )
        if j: jobs.append(j)
    return jobs

# ── 11. e-fellows.net ─────────────────────────────────────────────────────────
def scrape_efellows(query: str) -> list:
    url  = f"https://www.e-fellows.net/karriere/jobs-praktika?q={enc(query)}&type=internship"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("article,[class*='JobCard'],[class*='job-item']")[:10]:
        t  = card.select_one("h2,h3,[class*='title'],[class*='headline']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://www.e-fellows.net" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "e-fellows.net"
        )
        if j: jobs.append(j)
    return jobs

# ── 12. HeyJobs ───────────────────────────────────────────────────────────────
def scrape_heyjobs(query: str) -> list:
    url  = f"https://www.heyjobs.de/en-de/jobs?q={enc(query)}&location=Deutschland"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("[data-testid='jobCard'],article,[class*='JobCard']")[:10]:
        t  = card.select_one("h2,h3,[class*='title'],[data-testid='job-title']")
        c  = card.select_one("[class*='company'],[data-testid='company-name']")
        lo = card.select_one("[class*='location'],[data-testid='location']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://www.heyjobs.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "HeyJobs"
        )
        if j: jobs.append(j)
    return jobs

# ── 13. EnglishJobs.de ────────────────────────────────────────────────────────
def scrape_englishjobs(query: str) -> list:
    url  = f"https://englishjobs.de/search?q={enc(query)}"   # old /jobs/q-.../l-germany now redirects home
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("div.js-job")[:10]:
        t  = card.select_one("[itemprop='title']")
        a  = card.select_one("a.js-joblink[href]")
        li = [x.get_text(" ", strip=True) for x in card.select("ul li")]   # company, city, date, tags
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://englishjobs.de" + href
        j = make_job(
            li[0] if li else "",
            t.get_text(" ", strip=True) if t else "",
            li[1] if len(li) > 1 else "Germany",
            full_url, card.get_text(" "), "EnglishJobs.de"
        )
        if j: jobs.append(j)
    return jobs

# ── 14. GermanyStartUpJobs ────────────────────────────────────────────────────
def scrape_startup_jobs(query: str) -> list:
    url  = f"https://germanystartupjobs.com/jobs?search={enc(query)}"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("article,.job-item,[class*='card'],[class*='listing']")[:10]:
        t  = card.select_one("h2,h3,[class*='title'],[class*='role']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://germanystartupjobs.com" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "GermanyStartUpJobs"
        )
        if j: jobs.append(j)
    return jobs

# ── 15. EURES ─────────────────────────────────────────────────────────────────
def scrape_eures(query: str) -> list:
    url = (
        f"https://eures.europa.eu/jobs-and-cts/search_en"
        f"?query={enc(query)}&country=DE&jobType=INTERNSHIP"
    )
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select(".job-result,article,[class*='JobCard'],li.job")[:10]:
        t  = card.select_one("h2,h3,[class*='title']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://eures.europa.eu" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "EURES"
        )
        if j: jobs.append(j)
    return jobs

# ── 16. Cesar.de ─────────────────────────────────────────────────────────────
def scrape_cesar(query: str) -> list:
    url  = f"https://www.cesar.de/jobs/suche?q={enc(query)}&country=DE"
    html = safe_get(url)
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("article,.job-item,[class*='listing'],[class*='card']")[:10]:
        t  = card.select_one("h2,h3,[class*='title']")
        c  = card.select_one("[class*='company'],[class*='employer']")
        lo = card.select_one("[class*='location'],[class*='city']")
        a  = card.find("a", href=True)
        href = a["href"] if a else ""
        full_url = href if href.startswith("http") else "https://www.cesar.de" + href
        j = make_job(
            c.get_text(strip=True) if c else "",
            t.get_text(strip=True) if t else "",
            lo.get_text(strip=True) if lo else "Germany",
            full_url, card.get_text(" "), "Cesar.de"
        )
        if j: jobs.append(j)
    return jobs

# ── API sources (official/public JSON APIs - more reliable than scraping) ───────
import sources_api

def _from_api(fetch, source, query):
    try:
        raw = fetch(query)
        HTTP_LOG[source] = f"API ok, {len(raw)} raw" + (
            f" (BA {sources_api.BA_DEBUG})" if source == "Arbeitsagentur" else "")
    except Exception as e:
        HTTP_LOG[source] = f"API error {type(e).__name__}: {str(e)[:80]}"
        return []
    return [j for j in (make_job(r["company"], r["title"], r["location"], r["url"], r["desc"], source,
                                 start=r.get("start") or "ASAP", posted=r.get("posted", ""))
                        for r in raw) if j]

def scrape_arbeitsagentur(query: str) -> list:
    return _from_api(sources_api.arbeitsagentur, "Arbeitsagentur", query)

def scrape_arbeitnow(query: str) -> list:
    return _from_api(sources_api.arbeitnow, "Arbeitnow", query)

def _apify(source):
    """Portal function for one Apify source. All Apify searches run once per day (see ONCE_PER_RUN)."""
    def fetch(_query):
        return sources_api.apify_results()[source]
    def scrape(query: str) -> list:
        jobs = _from_api(fetch, f"{source} (Apify)", query)
        HTTP_LOG["Apify"] = f"{sources_api.APIFY_DEBUG}"
        return jobs
    return scrape

# ── Portal registry — (name, fn, delay_sec) ───────────────────────────────────
# Portals marked 'off' failed on every run; re-enable one after fixing its scraper.
# Indeed is still searchable from Claude chat via the Indeed connector.
PORTALS = [
    ("Arbeitsagentur (API)", scrape_arbeitsagentur, 1.0),
    ("Arbeitnow (API)",     scrape_arbeitnow,      0.2),
    # ("Indeed DE",           scrape_indeed,        1.5),   # off: 403 blocked on 2 runs (22 Oct 2026)
    ("StepStone",           scrape_stepstone,      2.0),
    # ("Monster.de",          scrape_monster,        2.0),   # off: 403 blocked on 2 runs (22 Oct 2026)
    # ("Jobware.de",          scrape_jobware,        1.5),   # off: 404 URL changed on 2 runs (22 Oct 2026)
    # ("Karriere.de",         scrape_karriere,       2.0),   # off: 404 URL changed on 2 runs (22 Oct 2026)
    # ("Absolventa.de",       scrape_absolventa,     1.5),   # off: 405 blocked on 2 runs (22 Oct 2026)
    ("Praktika.de",         scrape_praktika,       1.5),
    # ("Praktikum.de",        scrape_praktikum_de,   1.5),   # off: 404 URL changed on 2 runs (22 Oct 2026)
    # ("Praktikum.info",      scrape_praktikum_info, 1.5),   # off: 405 blocked on 2 runs (22 Oct 2026)
    ("Unicum",              scrape_unicum,         1.5),
    # ("e-fellows.net",       scrape_efellows,       1.5),   # off: 404 URL changed on 2 runs (22 Oct 2026)
    # ("HeyJobs",             scrape_heyjobs,        2.0),   # off: 202 JavaScript-only page on 2 runs (22 Oct 2026)
    ("EnglishJobs.de",      scrape_englishjobs,    1.5),
    # ("GermanyStartUpJobs",  scrape_startup_jobs,   1.5),   # off: 403 blocked on 2 runs (22 Oct 2026)
    # ("EURES",               scrape_eures,          2.0),   # off: 404 URL changed on 2 runs (22 Oct 2026)
    # ("Cesar.de",            scrape_cesar,          1.5),   # off: 403 blocked on 2 runs (22 Oct 2026)
]

# Apify (LinkedIn, Glassdoor, Indeed) runs only when the APIFY_TOKEN secret is set, once per day, not per query.
APIFY_ON = bool(os.environ.get("APIFY_TOKEN"))
ONCE_PER_RUN = {"LinkedIn (Apify)", "Glassdoor (Apify)", "Indeed (Apify)"}
if APIFY_ON:
    PORTALS += [("LinkedIn (Apify)",  _apify("LinkedIn"),  0),
                ("Glassdoor (Apify)", _apify("Glassdoor"), 0),
                ("Indeed (Apify)",    _apify("Indeed"),    0)]

# ── Deduplicate ────────────────────────────────────────────────────────────────
_LEGAL = re.compile(r"\b(gmbh|gbr|ag|se|kg|kgaa|co|inc|ltd|mbh|ug|group|deutschland|germany)\b|[^a-z0-9]", re.I)

def dedup_key(j: dict) -> tuple:
    """Same posting from two portals: 'Think3DDD' vs 'Think3DDD GbR', '(m/w/d)' vs '(m/f/d)', extra spaces."""
    title = re.sub(r"\((m|w|f|d|x|div|all genders?)[/|,]?.*?\)", "", j["title"].lower())
    return (re.sub(r"[^a-z0-9]", "", title)[:40], _LEGAL.sub("", j["company"].lower())[:16])

def deduplicate(jobs: list) -> list:
    seen, unique = set(), []
    for j in sorted(jobs, key=lambda x: -x["score"]):
        key = dedup_key(j)
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique

# ── Excel builder ──────────────────────────────────────────────────────────────
DARK_NAVY  = "0D1B2A"
TEAL_HDR   = "1A3A5C"
LIGHT_TEAL = "90E0EF"
MID_BLUE   = "CAF0F8"
PALE_BLUE  = "E9F7FB"

thin   = Side(style="thin", color="BFC9CA")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

def sfill(h): return PatternFill("solid", fgColor=h)

def build_excel(jobs: list, portal_stats: dict, output_path: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    today = datetime.date.today().strftime("%d %b %Y")

    ws.merge_cells("A1:K1")
    ws["A1"] = f"Daily Internship Scrape - Arjun Bhogadi - {today}"
    ws["A1"].font = Font(name="Arial", size=13, bold=True, color="FFFFFF")
    ws["A1"].fill = sfill(DARK_NAVY)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    portals_str = " / ".join(portal_stats.keys())
    ws.merge_cells("A2:K2")
    ws["A2"] = (
        f"Total: {len(jobs)} listings  |  "
        f"{len(portal_stats)} portals: {portals_str}  |  "
        "Filters: Germany / English OK / Pflichtpraktikum / DS-ML-AI-Analytics-Data Eng"
    )
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="FFFFFF")
    ws["A2"].fill = sfill(TEAL_HDR)
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 20

    headers    = ["#", "Company", "Role Title", "Location", "Start", "Posted",
                  "Role Type", "Match", "Score", "Source", "Apply URL"]
    col_widths = [4, 22, 34, 18, 12, 12, 18, 8, 7, 18, 55]
    for ci, (h, w) in enumerate(zip(headers, col_widths), 1):
        c = ws.cell(row=3, column=ci, value=h)
        c.font      = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        c.fill      = sfill(TEAL_HDR)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border    = BORDER
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.row_dimensions[3].height = 22

    match_color = {"***": LIGHT_TEAL, "**": MID_BLUE, "*": PALE_BLUE}

    for idx, j in enumerate(jobs, 1):
        r    = idx + 3
        filt = sfill(match_color.get(j["match"], "FFFFFF"))
        vals = [idx, j["company"], j["title"], j["location"], j["start"],
                j.get("posted") or "", j["role_type"], j["match"], j["score"],
                j["source"], j["url"]]
        for ci, val in enumerate(vals, 1):
            c = ws.cell(row=r, column=ci, value=val)
            c.font      = Font(name="Arial", size=9, bold=(ci == 1))
            c.fill      = filt
            c.border    = BORDER
            c.alignment = Alignment(
                horizontal="center" if ci in (1, 8, 9) else "left",
                vertical="center", wrap_text=True,
            )
        ws.row_dimensions[r].height = 32

    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A3:K{3 + len(jobs)}"

    # Portal Stats sheet
    ps = wb.create_sheet("Portal Stats")
    ps.column_dimensions["A"].width = 24
    ps.column_dimensions["B"].width = 14

    ps.merge_cells("A1:B1")
    ps["A1"] = f"Portal Stats - {today}"
    ps["A1"].font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    ps["A1"].fill = sfill(DARK_NAVY)
    ps["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ps.row_dimensions[1].height = 24

    for hi, h in enumerate(["Portal", "Jobs found"], 1):
        c = ps.cell(row=2, column=hi, value=h)
        c.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        c.fill = sfill(TEAL_HDR)
        c.alignment = Alignment(horizontal="center")
        c.border = BORDER
    ps.row_dimensions[2].height = 20

    for ri, (portal, count) in enumerate(sorted(portal_stats.items(), key=lambda x: -x[1]), 3):
        bg = sfill(LIGHT_TEAL if count >= 5 else MID_BLUE if count >= 2 else PALE_BLUE)
        for ci, val in enumerate([portal, count], 1):
            c = ps.cell(row=ri, column=ci, value=val)
            c.font = Font(name="Arial", size=9)
            c.fill = bg
            c.border = BORDER
            c.alignment = Alignment(horizontal="left" if ci == 1 else "center",
                                    vertical="center")
        ps.row_dimensions[ri].height = 18

    wb.save(output_path)
    print(f"[OK] Excel saved -> {output_path}")

# ── Email ──────────────────────────────────────────────────────────────────────
def send_email(excel_path: str, job_count: int, portal_count: int, ai_html: str = ""):
    sender    = os.environ["GMAIL_USER"]
    password  = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("RECIPIENT_EMAIL", sender)
    today     = datetime.date.today().strftime("%d %b %Y")

    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = recipient
    msg["Subject"] = f"Daily Jobs - {job_count} listings across {portal_count} portals - {today}"

    body = (
        f"Hi Arjun,\n\n"
        f"Your daily scrape found {job_count} relevant listings from "
        f"{portal_count} portals on {today}.\n\n"
        "Portals searched:\n"
        f"  {' / '.join(p[0] for p in PORTALS)}\n\n"
        "Filters:\n"
        "  Germany only  /  English working language  /  Pflichtpraktikum\n"
        "  DS / ML / AI / Analytics / Data Engineering roles\n\n"
        "*** = perfect RAG/LLM/agents match  "
        "**  = strong DS/ML  "
        "*   = analytics/data eng\n\n"
        "Good luck!\n-- Your daily job bot"
    )
    if ai_html:
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain"))
        alt.attach(MIMEText(ai_html + "<hr><pre style='font-family:Arial'>" + body + "</pre>", "html"))
        msg.attach(alt)
    else:
        msg.attach(MIMEText(body, "plain"))

    with open(excel_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
                    f'attachment; filename="{pathlib.Path(excel_path).name}"')
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())
    print(f"[OK] Email sent -> {recipient}")

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"[START] {datetime.datetime.now().isoformat()}")
    print(f"  Portals: {len(PORTALS)}  |  Queries: {len(SEARCH_QUERIES)}")

    all_jobs     = []
    portal_stats = {}

    for portal_name, scraper_fn, delay in PORTALS:
        portal_hits = []
        for query in (SEARCH_QUERIES[:1] if portal_name in ONCE_PER_RUN else SEARCH_QUERIES):
            hits = scraper_fn(query)
            portal_hits.extend(hits)
            time.sleep(delay)
        portal_stats[portal_name] = len(portal_hits)
        all_jobs.extend(portal_hits)
        print(f"  [{portal_name:22s}] {len(portal_hits):3d} raw hits")

    jobs = deduplicate(all_jobs)
    print(f"[OK] {len(jobs)} unique jobs after dedup & filtering")

    # Diagnostics as GitHub annotations (visible on the run page, no log download needed)
    dead = [p for p, n in portal_stats.items() if n == 0]
    print("::notice title=Portal results::" + " | ".join(f"{p}: {n}" for p, n in portal_stats.items()))
    if dead:
        print("::notice title=HTTP outcomes::" + " | ".join(f"{d}: {o}" for d, o in sorted(HTTP_LOG.items())))
    if APIFY_ON:
        print(f"::notice title=Apify::{sources_api.APIFY_DEBUG or HTTP_LOG.get('LinkedIn (Apify)', 'no result')}")
    else:
        print("::notice title=Apify off::Add the APIFY_TOKEN repository secret to include LinkedIn, Glassdoor and Indeed")

    today     = datetime.date.today().strftime("%Y-%m-%d")
    out_dir   = pathlib.Path("output")
    out_dir.mkdir(exist_ok=True)
    xlsx_path = str(out_dir / f"jobs_{today}.xlsx")

    build_excel(jobs, portal_stats, xlsx_path)

    inbox_html = ""
    try:
        import inbox
        items = inbox.fetch()
        inbox_html = inbox.render_html(items)
        print(f"[OK] Inbox: {len(items)} employer emails in last 48h")
        print(f"::notice title=Inbox::{len(items)} employer emails, labels: " +
              ", ".join(sorted({i['label'] for i in items})) )
    except Exception as e:          # inbox check must never break the daily email
        print(f"[INBOX WARN] {type(e).__name__}: {e}")
        print(f"::warning title=Inbox check failed::{type(e).__name__}: {str(e)[:150]}")

    ai_html = ""
    try:
        from engine import llm as ai_llm, run as ai_run
        if ai_llm.available():
            print(f"[AI] Screening shortlist with {ai_llm.detect_provider()}...")
            results = ai_run.run(jobs, getter=lambda u: sources_api.smart_get(u, safe_get))
            ai_html = ai_run.render_html(results)
            (out_dir / f"ai_{today}.html").write_text(ai_html, encoding="utf-8")
        else:
            print("[AI] No LLM key set (GEMINI_API_KEY / ANTHROPIC_API_KEY) - skipping AI screening")
    except Exception as e:          # AI must never break the daily email
        print(f"[AI WARN] engine failed: {e}")

    send_email(xlsx_path, len(jobs), len(PORTALS), inbox_html + ai_html)
    print("[DONE]")

if __name__ == "__main__":
    main()
