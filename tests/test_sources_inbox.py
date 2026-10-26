"""Offline tests for the API sources and the Gmail reply tracker (no network, no real mailbox)."""

import email.message, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sources_api, inbox   # noqa: E402


# ── API sources ───────────────────────────────────────────────────────────────
def test_arbeitsagentur_maps_fields_and_builds_links():
    calls = []
    def fake(url, headers=None, params=None):
        calls.append((url, headers, params))
        return {"stellenangebote": [
            {"titel": "Praktikum Data Science (m/w/d)", "beruf": "Data Scientist", "arbeitgeber": "Hapag-Lloyd AG",
             "refnr": "10000-123-S", "arbeitsort": {"ort": "Hamburg", "region": "Hamburg"}},
            {"beruf": "Praktikant/in - Informatik", "arbeitgeber": "X GmbH", "refnr": "",
             "externeUrl": "https://x.example/job", "arbeitsort": {}},
        ]}
    out = sources_api.arbeitsagentur("data science", get_json=fake)
    assert calls[0][1]["X-API-Key"] == "jobboerse-jobsuche" and calls[0][2]["angebotsart"] == 34
    assert out[0]["title"] == "Praktikum Data Science (m/w/d)" and out[0]["company"] == "Hapag-Lloyd AG"
    assert out[0]["url"] == sources_api.BA_PUBLIC + "10000-123-S" and out[0]["location"].startswith("Hamburg")
    assert out[1]["title"] == "Praktikant/in - Informatik" and out[1]["url"] == "https://x.example/job"


def test_smart_get_uses_details_api_for_ba_links(monkeypatch=None):
    orig = sources_api.ba_details_text
    sources_api.ba_details_text = lambda refnr: f"Full description for {refnr}"
    try:
        html = sources_api.smart_get(sources_api.BA_PUBLIC + "10000-123-S", fallback_get=lambda u: "FALLBACK")
        assert "Full description for 10000-123-S" in html
        assert sources_api.smart_get("https://other.example", fallback_get=lambda u: "FALLBACK") == "FALLBACK"
    finally:
        sources_api.ba_details_text = orig


def test_arbeitnow_filters_to_matching_internships_and_caches():
    sources_api._arbeitnow_cache["jobs"] = None
    pages = {1: {"data": [
        {"title": "Data Science Intern (English)", "company_name": "A", "location": "Berlin", "remote": True,
         "tags": ["Data"], "job_types": ["internship"], "url": "https://www.arbeitnow.com/jobs/a", "description": "<p>Python</p>"},
        {"title": "Senior Data Engineer", "company_name": "B", "location": "Munich", "remote": False,
         "tags": [], "job_types": ["full time"], "url": "https://www.arbeitnow.com/jobs/b", "description": ""},
        {"title": "Data Science Internship (6 months)", "company_name": "FR", "location": "Germany", "remote": False,
         "tags": [], "job_types": ["internship"], "url": "https://www.arbeitnow.fr/jobs/x", "description": ""},
        {"title": "Marketing Intern", "company_name": "C", "location": "Hamburg", "remote": False,
         "tags": [], "job_types": ["internship"], "url": "https://www.arbeitnow.com/jobs/c", "description": ""},
    ], "links": {"next": None}}}
    n = {"calls": 0}
    def fake(url, headers=None, params=None):
        n["calls"] += 1
        return pages[params["page"]]
    out = sources_api.arbeitnow("data science internship", get_json=fake)
    assert [j["company"] for j in out] == ["A"] and out[0]["location"].startswith("Remote")
    assert "<p>" not in out[0]["desc"]
    sources_api.arbeitnow("machine learning", get_json=fake)
    assert n["calls"] == 1                      # fetched once per run, then cached
    sources_api._arbeitnow_cache["jobs"] = None


# ── Reply tracker ─────────────────────────────────────────────────────────────
def test_classify_real_world_emails():
    assert inbox.classify("Lufthansa Group: Your Application - please complete online tests!", "")[0].startswith("ACTION")
    assert inbox.classify("Deine Bewerbung bei Capgemini Invent", "Leider müssen wir dir heute mitteilen")[0] == "REJECTION"
    assert inbox.classify("Ihre Bewerbung bei DHL Group – Einladung zum Gespräch", "")[0] == "INTERVIEW"
    assert inbox.classify("Deine Bewerbung bei BRL Risk Consulting ist eingegangen", "")[0] == "RECEIVED"


class FakeIMAP:
    def __init__(self, host):
        msg = email.message.EmailMessage()
        msg["Subject"] = "Lufthansa Group: Your Application - please complete online tests!"
        msg["From"] = "career@services.dlh.de"
        msg["Date"] = "Sat, 10 Oct 2026 12:43:46 +0000"
        msg.set_content("Please complete our online tests within the next 10 days.")
        self.raw, self.selected, self.query = msg.as_bytes(), None, None
    def login(self, u, p): self.user = u
    def select(self, box, readonly=False): self.selected = (box, readonly)
    def search(self, charset, kind, q): self.query = q; return "OK", [b"1"]
    def fetch(self, i, what): return "OK", [(b"1", self.raw)]
    def logout(self): pass


def test_fetch_reads_inbox_read_only_and_renders_action_first():
    box = {}
    def factory(host):
        box["m"] = FakeIMAP(host); return box["m"]
    items = inbox.fetch("u@x", "pw", imap_factory=factory)
    assert box["m"].selected == ("INBOX", True)                 # never marks mail as read
    assert "newer_than:2d" in box["m"].query
    assert items[0]["label"].startswith("ACTION") and "online tests" in items[0]["snippet"]
    html = inbox.render_html(items + [{"subject": "Absage", "from": "x", "date": "", "snippet": "leider",
                                       "label": "REJECTION", "colour": "#6b7280"}])
    assert html.index("ACTION") < html.index("REJECTION") and "need action" in html
    assert "No new replies" in inbox.render_html([])


def test_arbeitsagentur_skips_dead_endpoints_and_strips_internship_words():
    seen = []
    def fake(url, headers=None, params=None):
        path = url.rsplit("jobsuche-service", 1)[1]
        seen.append((path, params["was"]))
        if path in ("/pc/v6/jobs", "/pc/v4/jobs"):
            raise RuntimeError("403 Client Error: No match found for request")
        return {"stellenangebote": [{"titel": "Praktikum ML", "arbeitgeber": "Y", "refnr": "1", "arbeitsort": {}}]}
    out = sources_api.arbeitsagentur("machine learning Praktikum", get_json=fake)
    assert seen == [("/pc/v6/jobs", "machine learning"), ("/pc/v4/jobs", "machine learning"),
                    ("/pc/v4/app/jobs", "machine learning")]
    assert out[0]["title"] == "Praktikum ML" and sources_api.BA_DEBUG["path"] == "/pc/v4/app/jobs"


def test_arbeitsagentur_reads_v6_schema():
    # Shape of a live /pc/v6/jobs answer (22 Oct 2026), trimmed
    def fake(url, headers=None, params=None):
        return {"maxErgebnisse": 198, "page": 1, "size": 50, "ergebnisliste": [
            {"stellenangebotsTitel": "Internship Data Science & Business Analytics", "hauptberuf": "Data Scientist",
             "firma": "Lufthansa Technik AG", "referenznummer": "13348-1642182-1-S",
             "stellenlokationen": [{"adresse": {"plz": "22335", "ort": "Hamburg", "region": "HAMBURG"}}]},
            {"stellenangebotsTitel": "Data Science & Analytics (m/w/d)", "hauptberuf": "Data-Analyst/in",
             "firma": "Tepto GmbH", "referenznummer": "10001-1002922907-S", "externeURL": "https://tepto.example/job",
             "stellenlokationen": [{"adresse": {"ort": "Bielefeld", "region": "NORDRHEIN_WESTFALEN"}}]},
        ]}
    out = sources_api.arbeitsagentur("data science", get_json=fake)
    assert sources_api.BA_DEBUG["path"] == "/pc/v6/jobs" and len(out) == 2
    assert out[0]["title"] == "Internship Data Science & Business Analytics"
    assert out[0]["company"] == "Lufthansa Technik AG" and out[0]["location"] == "Hamburg, Hamburg"
    assert out[0]["url"] == sources_api.BA_PUBLIC + "13348-1642182-1-S" and "Data Scientist" in out[0]["desc"]
    assert out[1]["url"] == "https://tepto.example/job" and out[1]["location"] == "Bielefeld, Nordrhein Westfalen"


def test_ba_details_text_reads_v6_fields():
    d = {"stellenangebotsTitel": "Internship Data Science", "firma": "Lufthansa Technik AG",
         "stellenangebotsBeschreibung": "Your tasks: build ML models.", "arbeitgeberKundennummerHash": "bS9k=="}
    text = sources_api.ba_details_text("13348-1642182-1-S", get_json=lambda url, headers=None: d)
    assert "Your tasks: build ML models." in text and "Lufthansa Technik AG" in text and "bS9k" not in text


def test_praktika_reads_algolia_hits_in_germany_only():
    sent = []
    def fake(url, body, headers=None):
        sent.append((url, body, headers))
        return {"hits": [
            {"title": "(Pflicht-) Praktikum Data Science (m/w/d)", "company_name": "FUSE-AI GmbH", "location": "Hamburg",
             "state_de_name": "Hamburg", "country_de_name": "Deutschland", "status": "Online",
             "job_url": "praktikum/data-science-140084",
             "section_desc_1": "<ul><li>Python&nbsp;&amp; ML</li></ul>", "section_desc_2": None},
            {"title": "Praktikum Spain", "company_name": "X", "country_de_name": "Spanien", "status": "Online",
             "job_url": "praktikum/spain-1"},
            {"title": "Old listing", "company_name": "Y", "country_de_name": "Deutschland", "status": "Offline",
             "job_url": "praktikum/old-2"},
        ]}
    out = sources_api.praktika("data science Praktikum", post_json=fake)
    assert sent[0][1]["params"].endswith("query=data%20science") and "X-Algolia-API-Key" in sent[0][2]
    assert len(out) == 1 and out[0]["company"] == "FUSE-AI GmbH" and out[0]["location"] == "Hamburg"
    assert out[0]["url"] == "https://www.praktika.de/praktikum/data-science-140084"
    assert out[0]["desc"] == "Python & ML"


def test_englishjobs_parses_search_cards():
    import scraper
    card = """<div class="job js-job" id="a1"><div class="p-3">
      <a class="js-joblink flex" href="/clickout/a1?sig=x" itemprop="url"><h3 itemprop="title"><em>Internship</em>
      Data Science (f/m/x)</h3></a><ul><li> ZEISS</li><li> Hamburg</li><li> October 12</li><li>student</li></ul>
      <div>Python, machine learning and data science in an English-speaking team.</div></div></div>"""
    orig = scraper.safe_get
    scraper.safe_get = lambda url, timeout=14: f"<html><body>{card}</body></html>"
    try:
        out = scraper.scrape_englishjobs("data science internship")
    finally:
        scraper.safe_get = orig
    assert len(out) == 1 and out[0]["title"] == "Internship Data Science (f/m/x)"
    assert out[0]["company"] == "ZEISS" and out[0]["location"] == "Hamburg"
    assert out[0]["url"] == "https://englishjobs.de/clickout/a1?sig=x"


def test_arbeitsagentur_reports_when_every_endpoint_fails():
    def fake(url, headers=None, params=None):
        raise RuntimeError("403 no match")
    try:
        sources_api.arbeitsagentur("data", get_json=fake)
        assert False, "should raise"
    except RuntimeError as e:
        assert "/pc/v4/jobs" in str(e) and "/pc/v6/jobs" in str(e)


def test_confirmation_mentioning_interview_is_not_an_interview():
    label = inbox.classify("Indeed Application: Data Science Intern (f/m/d)",
                           "We'll help you get started. Tips to prepare for your interview: research the company.")[0]
    assert label != "INTERVIEW"
    assert inbox.classify("Invitation", "We would like to invite you to an interview next week.")[0] == "INTERVIEW"


def test_dates_are_captured_from_ba_and_arbeitnow():
    item = {"stellenangebotsTitel": "Praktikum Data", "firma": "X", "referenznummer": "1",
            "aktuelleVeroeffentlichungsdatum": "2026-06-11", "eintrittsdatum": "2026-07-01T00:00:00"}
    j = sources_api._ba_job(item)
    assert j["posted"] == "2026-06-11" and j["start"] == "2026-07-01"
    nested = {"titel": "Intern", "zeitraum": {"veroeffentlichungsdatum": "2026-10-01"}}
    assert sources_api._ba_job(nested)["posted"] == "2026-10-01"
    sources_api._arbeitnow_cache["jobs"] = [{"title": "Data Intern", "company_name": "A", "tags": ["data"],
        "job_types": ["internship"], "url": "https://www.arbeitnow.com/jobs/a", "created_at": 1792652468}]
    assert sources_api.arbeitnow("data")[0]["posted"] == "2026-10-22"
    sources_api._arbeitnow_cache["jobs"] = None


def test_inbox_query_is_plain_ascii():
    inbox.QUERY.encode("ascii")
