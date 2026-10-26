"""Offline tests for the Apify source (LinkedIn, Glassdoor, Indeed) and the Excel ranking fix."""

import datetime, os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sources_api   # noqa: E402

TODAY = datetime.date(2026, 10, 26)

# Shapes copied from real Apify results of 26 Oct 2026 (trimmed).
LINKEDIN = [
    {"title": "Business Intelligence Engineer Intern Germany", "companyName": "Amazon",
     "location": "Munich, Bavaria, Germany", "postedDate": "2026-10-24T00:00:00.000Z",
     "url": "https://de.linkedin.com/jobs/view/4474789913", "applyUrl": "",
     "description": "<p>SQL, Python, Power BI. 6 month internship.</p>"},
]
GLASSDOOR = [
    {"title": "AI Engineer Intern", "employer": {"name": "Enzo"}, "location": {"name": "Heidelberg"}, "ageInDays": 1,
     "seoUrl": "https://www.glassdoor.de/job-listing/x?jl=1", "description": "<div>Design RAG solutions &amp; agents</div>"},
    {"title": "Data Engineer (all Genders)", "employer": {"name": "rheindata"}, "location": {"name": "Köln"}, "ageInDays": 1,
     "seoUrl": "https://www.glassdoor.de/job-listing/y?jl=2", "description": "full-time"},
]
INDEED = [
    {"title": "Pflichtpraktikum Backend & KI", "employer": {"name": "Think3DDD"}, "location": {"city": "Berlin"},
     "dateOnIndeed": "2026-10-26T08:00:00.000Z", "url": "https://de.indeed.com/viewjob?jk=a",
     "description": {"text": "Python, APIs"}},
    {"title": "Vertriebsmitarbeiter (m/w/d)", "employer": {"name": None}, "location": {"city": "Home Office"},
     "dateOnIndeed": "2026-10-24T16:22:28.400Z", "url": "https://de.indeed.com/viewjob?jk=b", "description": {"text": "Sales"}},
]


def fake_post(calls):
    def post(url, body, token):
        calls.append((url, body, token))
        if "linkedin" in url:
            return LINKEDIN
        if "glassdoor" in url:
            return GLASSDOOR
        return INDEED
    return post


def fresh():
    sources_api._apify_cache["by_source"] = None
    sources_api.APIFY_DEBUG.clear()


def test_apify_maps_all_three_sources_and_keeps_student_titles_only():
    fresh()
    calls = []
    out = sources_api.apify_results(token="t", post=fake_post(calls), today=TODAY, workers=1)
    assert len(calls) == len(sources_api.APIFY_SEARCHES) == 10
    li, gd, ind = out["LinkedIn"][0], out["Glassdoor"], out["Indeed"]
    assert li["company"] == "Amazon" and li["posted"] == "2026-10-24" and "SQL" in li["desc"] and "<p>" not in li["desc"]
    assert [j["title"] for j in gd] == ["AI Engineer Intern"]            # full-time Data Engineer dropped
    assert gd[0]["company"] == "Enzo" and gd[0]["posted"] == "2026-10-25" and "RAG solutions & agents" in gd[0]["desc"]
    assert [j["company"] for j in ind] == ["Think3DDD"] and ind[0]["posted"] == "2026-10-26"
    assert sources_api.APIFY_DEBUG["Glassdoor"] == 1                     # same posting from 3 overlapping searches kept once
    fresh()


def test_apify_runs_once_per_day_and_caps_cost():
    fresh()
    calls = []
    sources_api.apify_results(token="t", post=fake_post(calls), today=TODAY, workers=1)
    sources_api.apify_results(token="t", post=fake_post(calls), today=TODAY, workers=1)
    assert len(calls) == 10                                              # second call served from cache
    li_inputs = [b for u, b, _ in calls if "linkedin" in u]
    assert all(b["datePosted"] == "r86400" and b["limit"] <= 50 for b in li_inputs)
    assert sources_api.APIFY_MAX_USD <= 0.05
    fresh()


def test_apify_without_token_raises_and_one_failing_search_is_tolerated():
    fresh()
    old = os.environ.pop("APIFY_TOKEN", None)
    try:
        sources_api.apify_results(post=fake_post([]))
        assert False, "should raise without a token"
    except RuntimeError as e:
        assert "APIFY_TOKEN" in str(e)
    finally:
        if old:
            os.environ["APIFY_TOKEN"] = old
    fresh()

    def flaky(url, body, token):
        if "glassdoor" in url:
            raise TimeoutError("slow")
        return fake_post([])(url, body, token)
    out = sources_api.apify_results(token="t", post=flaky, today=TODAY, workers=1)
    assert out["Glassdoor"] == [] and out["LinkedIn"] and sources_api.APIFY_DEBUG["errors"]
    fresh()


def test_internships_outrank_senior_roles_and_german_required_ranks_down():
    import scraper
    desc = "Python, machine learning, LLM, RAG, data science, English"
    intern = scraper.score_job("AI Engineer Intern", desc, "Heidelberg")
    senior = scraper.score_job("Senior Machine Learning Engineer", desc, "Munich")
    manager = scraper.score_job("Engineering Manager (Data Science)", desc, "Berlin")
    german = scraper.score_job("Praktikum Data Analytics", desc + ". Very good written and spoken German and English skills", "Hamburg")
    english = scraper.score_job("Praktikum Data Analytics", desc, "Hamburg")
    assert intern > senior and intern > manager
    assert english > german


def test_same_posting_from_two_portals_is_kept_once():
    import scraper
    a = {"title": "Pflichtpraktikum Backend Softwareentwicklung & KI (m/w/d) - Python", "company": "Think3DDD", "score": 13}
    b = {"title": "Pflichtpraktikum Backend Softwareentwicklung  & KI (m/w/d) - Python", "company": "Think3DDD GbR", "score": 12}
    c = {"title": "Intern HR Data Analytics & AI (m/f/d)", "company": "Tesla, Inc.", "score": 9}
    d = {"title": "Intern HR Data Analytics & AI (m/w/d)", "company": "Tesla", "score": 8}
    assert [j["company"] for j in scraper.deduplicate([a, b, c, d])] == ["Think3DDD", "Tesla, Inc."]


def test_confirmation_subject_is_received_even_with_rejection_words_in_boilerplate():
    import inbox
    subj = 'We have received your application - "Intern / Thesis AI Transformation of Knowledge Work in R&D (m/f/d)"'
    body = "Thank you. Unfortunately we cannot answer every enquiry personally."
    assert inbox.classify(subj, body)[0] == "RECEIVED"
    assert inbox.classify("Your application at X", "Unfortunately we decided to move forward with other candidates")[0] == "REJECTION"
    assert inbox.classify("We have received your application", "Please complete the online test within 5 days")[0].startswith("ACTION")


def test_html_style_blocks_do_not_leak_into_snippets():
    import email.message, inbox
    m = email.message.EmailMessage()
    m.set_content("<html><head><style>* { font-family: sans-serif; }</style></head><body><p>Danke für Ihre Bewerbung</p></body></html>", subtype="html")
    assert inbox._body_text(m) == "Danke für Ihre Bewerbung"


def test_off_topic_internships_are_dropped_but_data_roles_in_other_areas_stay():
    import scraper
    data_desc = "Python, SQL and machine learning on real data"
    assert scraper.make_job("X", "Praktikum im Business Development (all genders)", "Berlin", "u", data_desc, "s") is None
    assert scraper.make_job("X", "Praktikant Privatkundenberatung (m/w/d)", "Regensburg", "u", "Kunden beraten", "s") is None
    assert scraper.make_job("Tesla", "Intern HR Data Analytics & AI (m/f/d)", "Berlin", "u", data_desc, "s") is not None
    assert scraper.make_job("BSH", "Intern / Thesis AI Transformation of Knowledge Work in R&D", "Traunreut", "u", data_desc, "s") is not None
    assert scraper.make_job("X", "Praktikum Office Management", "Hamburg", "u", "Ablage und Telefon", "s") is None   # no data signal
