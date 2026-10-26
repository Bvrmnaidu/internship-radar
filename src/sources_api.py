"""Job sources that use official / public JSON APIs instead of scraping HTML.

APIs don't break when a site redesigns its pages, and they aren't blocked like
scrapers often are. Each function returns raw dicts:
    {company, title, location, url, desc}
which scraper.py turns into scored jobs with make_job().
"""

import base64, datetime, html, json, os, re, time
from concurrent.futures import ThreadPoolExecutor

import requests

UA = {"User-Agent": "Mozilla/5.0 (job-scraper; personal use)", "Accept": "application/json"}

# ── Bundesagentur für Arbeit — Jobsuche API ───────────────────────────────────
# Germany's federal job board; most German employers post here.
# Docs: https://jobsuche.api.bund.dev  (public clientId as API key)
BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
BA_HEADERS = {**UA, "X-API-Key": "jobboerse-jobsuche"}
BA_PUBLIC = "https://www.arbeitsagentur.de/jobsuche/jobdetail/"


def _get_json(url, headers=UA, params=None, timeout=20):
    r = requests.get(url, headers=headers, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post_json(url, body, headers=UA, timeout=20):
    r = requests.post(url, json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


BA_DEBUG = {}   # last response shape, surfaced in the run diagnostics


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _date_like(it: dict, needles) -> str:
    """First YYYY-MM-DD value whose key contains one of `needles` (searches one level of nesting).
    Field names differ between BA API versions, so match on key fragments instead of exact names."""
    stack = [it]
    for d in stack:
        for k, v in d.items():
            if isinstance(v, dict) and d is it:
                stack.append(v)
            elif isinstance(v, str) and _DATE.match(v) and any(n in k.lower() for n in needles):
                return v[:10]
    return ""


def _ba_hits(data: dict) -> list:
    # v6 lists hits under 'ergebnisliste', v4 under 'stellenangebote'
    return data.get("ergebnisliste") or data.get("stellenangebote") or []


def _ba_job(it: dict) -> dict:
    """One search hit -> raw dict. Reads the v6 field names (Oct 2026) and falls back to the v4 ones."""
    refnr = it.get("referenznummer") or it.get("refnr") or ""
    ort = ((it.get("stellenlokationen") or [{}])[0] or {}).get("adresse") or it.get("arbeitsort") or {}
    region = ort.get("region") or ""
    if region.isupper():                     # v6 sends 'NORDRHEIN_WESTFALEN'
        region = region.replace("_", " ").title()
    title = it.get("stellenangebotsTitel") or it.get("titel")
    beruf = it.get("hauptberuf") or it.get("beruf") or ""
    return {
        "company":  it.get("firma") or it.get("arbeitgeber") or "",
        "title":    title or beruf,
        "location": ", ".join(x for x in (ort.get("ort"), region) if x) or "Germany",
        "url":      it.get("externeURL") or it.get("externeUrl") or (BA_PUBLIC + refnr if refnr else ""),
        "desc":     " ".join(x for x in (beruf, title) if x),
        "refnr":    refnr,
        "posted":   _date_like(it, ("veroeffentlich", "publish", "erstell")),
        "start":    _date_like(it, ("eintritt", "beginn", "startdatum")),
    }


def arbeitsagentur(query: str, get_json=_get_json) -> list:
    """Praktikum/Trainee offers (angebotsart=34) published in the last 30 days, all of Germany.
    The search words drop 'Praktikum'/'internship' (angebotsart already filters for that).
    v6 is the live endpoint (v4 answers 403 since Oct 2026); v4 stays as a fallback."""
    words = " ".join(w for w in query.split() if w.lower() not in {"praktikum", "pflichtpraktikum", "internship"})
    params = {"was": words or query, "angebotsart": 34, "veroeffentlichtseit": 30, "size": 50, "page": 1}
    data, errors = {}, []
    for path in ("/pc/v6/jobs", "/pc/v4/jobs", "/pc/v4/app/jobs"):   # first one that answers wins
        try:
            data = get_json(f"{BA_BASE}{path}", headers=BA_HEADERS, params=params) or {}
        except Exception as e:
            errors.append(f"{path}: {str(e)[:60]}")
            continue
        BA_DEBUG.update(path=path, keys=sorted(data.keys())[:8], total=data.get("maxErgebnisse"))
        if _ba_hits(data):
            BA_DEBUG["item_keys"] = sorted(_ba_hits(data)[0].keys())[:30]   # schema visible in diagnostics
            break
    if not data and errors:
        raise RuntimeError("; ".join(errors))
    return [_ba_job(it) for it in _ba_hits(data)]


def ba_details_text(refnr: str, get_json=_get_json) -> str:
    """Full job description via the details endpoint (the public page is JavaScript-only)."""
    enc = base64.b64encode(refnr.encode()).decode()
    d = get_json(f"{BA_BASE}/pc/v4/jobdetails/{enc}", headers=BA_HEADERS)
    parts = [d.get("stellenangebotsTitel") or d.get("titel"), d.get("firma") or d.get("arbeitgeber"),
             d.get("stellenangebotsBeschreibung") or d.get("stellenbeschreibung")]
    if not any(parts):                       # schema changed? keep every string we got
        parts = [v for v in d.values() if isinstance(v, str)]
    return re.sub(r"\s+\n", "\n", "\n\n".join(p for p in parts if p))


# ── Praktika.de — the site's own Algolia search index ─────────────────────────
# The search page is an Angular app that queries Algolia from the browser; the app id,
# search-only key and index name are the public ones in praktika.de's frontend (Oct 2026).
PRAKTIKA_ALGOLIA = "https://ORECYTXZ5A-dsn.algolia.net/1/indexes/PRAKTIKA_PRODUCTION/query"
PRAKTIKA_HEADERS = {**UA, "X-Algolia-Application-Id": "ORECYTXZ5A",
                    "X-Algolia-API-Key": "3ad5fab11047d8bb3c4c82db4c1d30aa"}
PRAKTIKA_PUBLIC = "https://www.praktika.de/"


def praktika(query: str, post_json=_post_json) -> list:
    """Online listings in Germany. Internship words are dropped from the query: every listing is one,
    and the index requires all words to match."""
    words = " ".join(w for w in query.split() if w.lower() not in {"praktikum", "pflichtpraktikum", "internship"})
    data = post_json(PRAKTIKA_ALGOLIA, {"params": "hitsPerPage=20&query=" + requests.utils.quote(words or query)},
                     headers=PRAKTIKA_HEADERS)
    out = []
    for it in data.get("hits") or []:
        if it.get("status", "Online") != "Online" or it.get("country_de_name") not in (None, "Deutschland"):
            continue
        desc = " ".join(it.get(f"section_desc_{i}") or "" for i in range(1, 5))
        out.append({
            "company":  it.get("company_name") or "",
            "title":    it.get("title") or "",
            "location": it.get("location") or it.get("state_de_name") or "Germany",
            "url":      PRAKTIKA_PUBLIC + it["job_url"] if it.get("job_url") else "",
            "desc":     re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", desc))).strip()[:2000],
        })
    return out


# ── Arbeitnow — free job board API, many English-speaking roles in Germany ────
# Docs: https://www.arbeitnow.com/blog/job-board-api  (no key needed)
ARBEITNOW = "https://www.arbeitnow.com/api/job-board-api"
_arbeitnow_cache = {"jobs": None}


def _arbeitnow_all(get_json=_get_json, pages=4) -> list:
    if _arbeitnow_cache["jobs"] is None:
        jobs = []
        for p in range(1, pages + 1):
            try:
                data = get_json(ARBEITNOW, params={"page": p})
            except Exception:
                if p == 1:
                    raise                    # first page failed: report it, don't hide it
                break
            batch = data.get("data") or []
            jobs.extend(batch)
            if not batch or not (data.get("links") or {}).get("next"):
                break
            time.sleep(1)
        _arbeitnow_cache["jobs"] = jobs
    return _arbeitnow_cache["jobs"]


def arbeitnow(query: str, get_json=_get_json) -> list:
    """The API has no search parameter, so fetch recent jobs once and match the query words locally."""
    words = [w for w in re.findall(r"[a-zäöü]+", query.lower()) if w not in {"praktikum", "internship", "intern"}]
    out = []
    for it in _arbeitnow_all(get_json):
        if not str(it.get("url", "")).startswith("https://www.arbeitnow.com/"):
            continue                 # .fr / .co.uk listings are jobs outside Germany
        text = " ".join([it.get("title", ""), " ".join(it.get("tags") or []), " ".join(it.get("job_types") or [])]).lower()
        if words and not all(w in text for w in words):
            continue
        is_intern = any(k in text for k in ("intern", "praktik", "working student", "werkstudent", "student"))
        if not is_intern:
            continue
        out.append({
            "company":  it.get("company_name", ""),
            "title":    it.get("title", ""),
            "location": ("Remote, " if it.get("remote") else "") + (it.get("location") or "Germany"),
            "url":      it.get("url", ""),
            "desc":     re.sub(r"<[^>]+>", " ", it.get("description", ""))[:2000],
            "posted":   (time.strftime("%Y-%m-%d", time.gmtime(it["created_at"]))
                         if isinstance(it.get("created_at"), (int, float)) else ""),
        })
    return out


# ── Apify — LinkedIn, Glassdoor and Indeed through hosted scrapers ────────────
# Needs the APIFY_TOKEN secret. One fixed set of searches per day (not per query),
# last 24 h only, small result limits and a hard USD cap per run keep this inside
# Apify's free monthly credit (roughly $0.15 per day at most).
# Actors by 'valig': no login or cookies, pay per result.
APIFY_RUN = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
APIFY_MAX_USD = 0.05                      # cost cap per actor run
APIFY_STUDENT_TITLE = re.compile(r"intern|praktik|trainee|werkstudent|working student|thesis|abschlussarbeit|masterand", re.I)
_INTERN_WORDS = ["intern", "internship", "praktikum", "praktikant", "pflichtpraktikum", "trainee"]
APIFY_SEARCHES = (
    [("valig~linkedin-jobs-scraper", "LinkedIn",
      {"keywords": q, "location": "Germany", "datePosted": "r86400", "limit": 50, "titleInclude": _INTERN_WORDS})
     for q in ("Data Science Intern", "Praktikum Data", "AI Machine Learning Intern", "Data Analyst Intern")]
    + [("valig~glassdoor-jobs-scraper", "Glassdoor",
        {"keywords": q, "location": "Germany", "daysOld": 1, "sortBy": "date_desc", "limit": 40})
       for q in ("Data Science Intern", "Praktikum Data", "AI Intern")]
    + [("valig~indeed-jobs-scraper", "Indeed",
        {"country": "de", "title": q, "location": "Deutschland", "datePosted": "1", "limit": 40})
       for q in ("Praktikum Data Science", "Data Intern", "Praktikum KI Machine Learning")]
)
APIFY_DEBUG = {}                          # per-source counts and errors, shown in the run diagnostics
_apify_cache = {"by_source": None}


def _apify_post(url, body, token, timeout=270):
    r = requests.post(url, json=body, timeout=timeout,
                      headers={**UA, "Authorization": f"Bearer {token}"},
                      params={"timeout": 240, "maxTotalChargeUsd": APIFY_MAX_USD})
    r.raise_for_status()
    return r.json()


def _g(d: dict, path: str, default=""):
    """Read 'a.b' from nested dicts, or from an already-flattened 'a.b' key."""
    if path in d:
        return d[path] if d[path] is not None else default
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or cur.get(part) is None:
            return default
        cur = cur[part]
    return cur


def _apify_job(source: str, it: dict, today: datetime.date) -> dict:
    if source == "LinkedIn":
        return {"company": it.get("companyName") or "", "title": it.get("title") or "",
                "location": it.get("location") or "Germany", "url": it.get("url") or it.get("applyUrl") or "",
                "desc": html.unescape(re.sub(r"<[^>]+>", " ", it.get("description") or "")),
                "posted": (it.get("postedDate") or "")[:10]}
    if source == "Glassdoor":
        age = it.get("ageInDays")
        posted = (today - datetime.timedelta(days=int(age))).isoformat() if isinstance(age, (int, float)) else ""
        return {"company": _g(it, "employer.name"), "title": it.get("title") or "",
                "location": _g(it, "location.name") or "Germany", "url": it.get("seoUrl") or it.get("url") or "",
                "desc": html.unescape(re.sub(r"<[^>]+>", " ", it.get("description") or "")), "posted": posted}
    return {"company": _g(it, "employer.name") or _g(it, "parentEmployer.name"), "title": it.get("title") or "",
            "location": _g(it, "location.city") or "Germany", "url": it.get("url") or it.get("jobUrl") or "",
            "desc": _g(it, "description.text"),
            "posted": (it.get("dateOnIndeed") or it.get("datePublished") or "")[:10]}


def apify_results(token=None, post=_apify_post, today=None, workers=4) -> dict:
    """Runs every Apify search once per day (cached) and returns {"LinkedIn": [...], "Glassdoor": [...], "Indeed": [...]}.
    Keeps internship / working-student / thesis titles only. A failing search is logged, the rest still count."""
    if _apify_cache["by_source"] is not None:
        return _apify_cache["by_source"]
    token = token or os.environ.get("APIFY_TOKEN", "")
    if not token:
        raise RuntimeError("APIFY_TOKEN not set")
    today = today or datetime.date.today()
    by_source = {"LinkedIn": [], "Glassdoor": [], "Indeed": []}
    errors, seen = [], set()

    def one(search):
        actor, source, body = search
        return source, post(APIFY_RUN.format(actor=actor), body, token)

    with ThreadPoolExecutor(max_workers=workers) as pool:       # Apify free plan allows 5 parallel runs
        futures = [pool.submit(one, s) for s in APIFY_SEARCHES]
        for f in futures:
            try:
                source, items = f.result()
            except Exception as e:
                errors.append(f"{type(e).__name__}: {str(e)[:60]}")
                continue
            for it in items if isinstance(items, list) else []:
                job = _apify_job(source, it, today)
                key = job["url"] or (job["title"], job["company"])
                if job["title"] and APIFY_STUDENT_TITLE.search(job["title"]) and key not in seen:
                    seen.add(key)                                # searches overlap: keep each posting once
                    by_source[source].append(job)
    APIFY_DEBUG.update({s: len(v) for s, v in by_source.items()}, errors=errors[:3])
    if errors and not any(by_source.values()):
        raise RuntimeError("; ".join(errors[:3]))
    _apify_cache["by_source"] = by_source
    return by_source


def smart_get(url: str, fallback_get) -> str:
    """Page getter for the AI engine: BA postings via the details API, everything else via HTML."""
    if url.startswith(BA_PUBLIC):
        try:
            return "<html><body><pre>" + ba_details_text(url[len(BA_PUBLIC):]) + "</pre></body></html>"
        except Exception as e:
            print(f"  [WARN] BA details {url[-20:]}: {e}")
            return ""
    return fallback_get(url)
