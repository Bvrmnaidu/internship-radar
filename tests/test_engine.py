"""Offline tests: a fake Claude client, real job descriptions, no network, no API key.
Run:  python -m pytest -q tests   (or: python tests/test_engine.py)
"""

import json, pathlib, sys, types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from engine import llm as llm_mod, run as run_mod                       # noqa: E402
from engine.extract import fetch_jd_text, language_signals, html_to_text  # noqa: E402
from engine.match import adjust                                          # noqa: E402

FIX = ROOT / "tests" / "fixtures"


class FakeClient:
    """Mimics anthropic.Anthropic().messages.create; answers based on the prompt."""
    def __init__(self):
        self.messages = self
        self.prompts = []

    def create(self, model, max_tokens, system, messages):
        prompt = messages[0]["content"]
        self.prompts.append((system, prompt))
        if "Return JSON with exactly these keys" in prompt:
            german = "C1" if "Deutsch (C1)" in prompt else "none"
            out = "```json\n" + json.dumps({
                "role_summary": "Data internship", "contract_type": "Praktikum",
                "location": "Hamburg", "start": "January 2027",
                "german_level_required": german, "english_ok": german == "none",
                "must_have": ["Python", "SQL"], "matched": ["Python (AIcoo)"], "gaps": [],
                "fit_score": 70, "verdict": "APPLY", "why": "Good fit.",
                "highlights": ["RAG pipeline", "PostgreSQL", "Kafka", "Power BI", "Python"],
            }) + "\n```"
        else:
            out = ("Subject: Application - Data Science Intern - Hamburg\n\nDear team, "
                   + "I am applying for this internship with relevant experience in Python and data. " * 8)
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=out)])


def job(title, company, fixture):
    return {"title": title, "company": company, "location": "Hamburg", "url": "https://x",
            "source": "test", "desc": (FIX / fixture).read_text(encoding="utf-8")}


def test_parse_json_handles_fences_and_prose():
    assert llm_mod.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm_mod.parse_json('Sure! {"a": 2} hope that helps') == {"a": 2}


def test_language_signals_on_real_jds():
    deloitte = (FIX / "deloitte_ai_data_engineering.txt").read_text(encoding="utf-8")
    assert language_signals(deloitte)["german_required"] is True
    lh = (FIX / "lufthansa_technik_ds.txt").read_text(encoding="utf-8")
    assert language_signals(lh)["german_required"] is False
    assert language_signals("Our working language is English.")["english_first"] is True


def test_adjust_rules():
    eng, notes = adjust({"fit_score": 60, "english_ok": True, "german_level_required": "none",
                         "location": "Hamburg", "contract_type": "Pflichtpraktikum"}, "")
    ger, _ = adjust({"fit_score": 60, "german_level_required": "C1", "location": "Munich",
                     "contract_type": "Praktikum"}, "")
    assert eng == 85 and "+15 English-first" in notes
    assert ger == 35
    assert adjust({"fit_score": 99, "english_ok": True, "german_level_required": "none",
                   "location": "remote", "contract_type": "Pflichtpraktikum"}, "")[0] == 100


def test_fetch_falls_back_to_snippet_when_page_blocked():
    j = job("DS Intern", "Lufthansa Technik", "lufthansa_technik_ds.txt")
    text = fetch_jd_text(j, getter=lambda url: "")
    assert "Lufthansa Technik" in text and "January 2027" in text


def test_html_to_text_strips_scripts():
    assert html_to_text("<html><script>x=1</script><p>Python &amp; SQL</p></html>") == "Python & SQL"


def test_full_run_ranks_english_first_and_renders_email():
    fake = FakeClient()
    llm = llm_mod.LLM(client=fake, model="test-model")
    jobs = [job("Praktikant AI & Data Engineering", "Deloitte", "deloitte_ai_data_engineering.txt"),
            job("Internship Data Science & Business Analytics", "Lufthansa Technik", "lufthansa_technik_ds.txt")]
    results = run_mod.run(jobs, getter=lambda url: "", llm=llm, max_analyze=5, max_letters=5, pause=0)

    assert [r["company"] for r in results] == ["Lufthansa Technik", "Deloitte"]   # German C1 ranked down
    assert results[0]["ai"]["final_score"] > results[1]["ai"]["final_score"]
    assert results[0]["ai"]["cover_letter"].startswith("Subject:")
    assert llm.calls == 4                       # 2 analyses + 2 letters

    # the CV sent to the model is the real profile, and the honesty rule is in every system prompt
    assert all("never invent" in s for s, _ in fake.prompts)
    assert run_mod.load_cv()["experience"][0]["company"] in fake.prompts[0][1]   # facts come from the profile

    html = run_mod.render_html(results)
    assert "Lufthansa Technik" in html and "Draft cover letter" in html and "<script" not in html


def test_one_failing_job_does_not_kill_the_run():
    class Boom(FakeClient):
        def create(self, **kw):
            raise RuntimeError("API down")
    llm = llm_mod.LLM(client=Boom(), model="m")
    out = run_mod.run([job("X", "Y", "lufthansa_technik_ds.txt")], getter=lambda u: "", llm=llm, pause=0)
    assert out == []


def test_provider_detection_prefers_free_gemini(monkeypatch=None):
    import os
    saved = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY","LLM_API_KEY","LLM_BASE_URL","ANTHROPIC_API_KEY")}
    try:
        assert llm_mod.available() is False
        os.environ["ANTHROPIC_API_KEY"] = "a"; assert llm_mod.detect_provider() == "anthropic"
        os.environ["GEMINI_API_KEY"] = "g";    assert llm_mod.detect_provider() == "gemini"
        l = llm_mod.LLM()
        assert l.model == "gemini-flash-lite-latest" and l.min_interval > 0
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None: os.environ[k] = v


def test_openai_compat_client_parses_and_retries_429():
    from engine.llm import _OpenAICompatClient
    calls = []
    class R:
        def __init__(s, code): s.status_code = code
        def raise_for_status(s):
            if s.status_code >= 400: raise RuntimeError(s.status_code)
        def json(s): return {"choices": [{"message": {"content": '{"ok": true}'}}]}
    c = _OpenAICompatClient("https://x/v1/", "k")
    seq = [R(429), R(200)]
    c._post = lambda payload: (calls.append(payload), seq.pop(0))[1]
    import engine.llm as m; orig = m.time.sleep; m.time.sleep = lambda s: None
    try:
        l = llm_mod.LLM(client=c, model="m")
        assert l.json("sys", "p") == {"ok": True}
        assert len(calls) == 2 and calls[0]["messages"][0] == {"role": "system", "content": "sys"}
    finally:
        m.time.sleep = orig


def test_json_retries_once_on_cut_off_answer():
    class Cut(FakeClient):
        n = 0
        def create(self, **kw):
            Cut.n += 1
            txt = '{"verdict": "APP' if Cut.n == 1 else '{"verdict": "APPLY"}'
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=txt)])
    l = llm_mod.LLM(client=Cut(), model="m")
    assert l.json("s", "p") == {"verdict": "APPLY"} and l.calls == 2


def test_skip_hides_highlights_and_failures_are_reported():
    r = [{"title": "T", "company": "C", "location": "L", "source": "s", "url": "u",
          "ai": {"verdict": "SKIP", "final_score": 0, "highlights": ["Physical endurance"], "gaps": []}}]
    html = run_mod.render_html(r, failed=7)
    assert "Physical endurance" not in html and "Put these first" not in html
    assert "7 job(s) could not be screened" in html
    r[0]["ai"]["verdict"] = "APPLY"
    assert "Put these first" in run_mod.render_html(r, failed=0)


def test_daily_quota_stops_screening_immediately():
    class Quota(FakeClient):
        def create(self, **kw):
            self.n = getattr(self, "n", 0) + 1
            raise llm_mod.QuotaExhausted("daily free-tier quota used up for m")
    client = Quota()
    llm = llm_mod.LLM(client=client, model="m")
    jobs = [job(f"Job {i}", "C", "lufthansa_technik_ds.txt") for i in range(5)]
    out = run_mod.run(jobs, getter=lambda u: "", llm=llm, pause=0)
    assert out == [] and client.n == 1 and run_mod.run.failed == 5   # one call, then stop


def test_openai_compat_raises_quota_on_per_day_429_without_waiting():
    from engine.llm import _OpenAICompatClient, QuotaExhausted
    class R:
        status_code = 429
        text = '{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}'
    c = _OpenAICompatClient("https://x/v1", "k")
    c._post = lambda payload: R()
    import engine.llm as m; slept = []; orig = m.time.sleep; m.time.sleep = lambda s: slept.append(s)
    try:
        c.create(model="m", max_tokens=10, system="s", messages=[])
        assert False, "should raise"
    except QuotaExhausted:
        assert slept == []                      # did not waste 60 s
    finally:
        m.time.sleep = orig


def test_gemini_uses_lite_for_screening_and_flash_for_letters():
    import os
    saved = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "LLM_MODEL", "LLM_WRITER_MODEL", "ANTHROPIC_API_KEY", "LLM_API_KEY", "LLM_BASE_URL")}
    try:
        os.environ["GEMINI_API_KEY"] = "g"
        assert llm_mod.LLM(role="screen").model == "gemini-flash-lite-latest"
        assert llm_mod.LLM(role="write").model == "gemini-flash-latest"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None: os.environ[k] = v


# ── freshness, ordering, letter failures (23 Oct 2026 fixes) ─────────────────
import datetime as _dt


def test_staleness_flags_old_postings_and_past_start_dates():
    today = _dt.date(2026, 10, 23)
    assert "days ago" in run_mod.staleness({"posted": "2026-06-11"}, today)            # the Airbus case
    assert "has passed" in run_mod.staleness({"start": "2026-07-01"}, today)
    assert run_mod.staleness({"posted": "2026-10-20", "start": "ASAP"}, today) == ""
    assert run_mod.staleness({}, today) == ""


def test_shortlist_puts_fresh_internships_first():
    today = _dt.date(2026, 10, 23)
    jobs = [{"title": "Senior Data Platform Engineer", "score": 20},
            {"title": "Werkstudent Data Science", "score": 15},
            {"title": "Praktikum Data Science", "score": 10, "posted": "2026-06-01"},   # stale
            {"title": "Internship Data Science", "score": 8}]
    order = [j["title"] for j in run_mod.shortlist_order(jobs, today)]
    assert order == ["Internship Data Science", "Werkstudent Data Science",
                     "Senior Data Platform Engineer", "Praktikum Data Science"]


def test_stale_job_is_penalised_and_gets_no_letter():
    fake = FakeClient()
    stale = {**job("Praktikum Data", "Old Co", "lufthansa_technik_ds.txt"), "posted": "2020-01-01"}
    fresh = job("Internship Data Science", "New Co", "lufthansa_technik_ds.txt")
    out = run_mod.run([stale, fresh], getter=lambda u: "", llm=llm_mod.LLM(client=fake, model="m"), pause=0)
    by = {r["company"]: r["ai"] for r in out}
    assert by["Old Co"]["final_score"] < by["New Co"]["final_score"] and "possibly filled" in " ".join(by["Old Co"]["adjustments"])
    assert "cover_letter" not in by["Old Co"] and "cover_letter" in by["New Co"]
    assert "Possibly filled" in run_mod.render_html(out)


def test_empty_or_short_letter_is_an_error_not_silence():
    from engine.generate import cover_letter
    class Short(FakeClient):
        def create(self, **kw):
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="Subject: hi")])
    try:
        cover_letter(llm_mod.LLM(client=Short(), model="m"), {}, "jd", {})
        assert False, "should raise"
    except ValueError as e:
        assert "too short" in str(e)


def test_compat_client_reports_output_cut_off_by_token_limit():
    from engine.llm import _OpenAICompatClient
    class R:
        status_code = 200
        text = ""
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
    c = _OpenAICompatClient("https://x/v1", "k"); c._post = lambda p: R()
    try:
        c.create(model="m", max_tokens=10, system="s", messages=[])
        assert False, "should raise"
    except ValueError as e:
        assert "token limit" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok ", name)
