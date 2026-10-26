"""Orchestrate the engine over the scraper's shortlist and render the email section."""

import datetime, html, json, os, pathlib, re, time

from . import llm as llm_mod
from .extract import fetch_jd_text
from .match import analyze
from .generate import cover_letter

PROFILE_DIR = pathlib.Path(__file__).resolve().parents[2] / "profile"
CV_PATH = PROFILE_DIR / "cv.json"                    # your real profile, git-ignored
EXAMPLE_CV_PATH = PROFILE_DIR / "cv.example.json"    # committed example, used when cv.json is missing


def load_cv(path=None) -> dict:
    path = pathlib.Path(path) if path else (CV_PATH if CV_PATH.exists() else EXAMPLE_CV_PATH)
    cv = json.loads(path.read_text(encoding="utf-8"))
    cv.pop("_note", None)
    return cv


STALE_POSTED_DAYS = 42      # older postings are often filled even when the board still lists them
STALE_START_DAYS = 30       # a planned start date this far in the past means the role is probably gone
_INTERN = re.compile(r"prakti|intern\b|internship|trainee", re.I)
_STUDENT = re.compile(r"werkstudent|working student|thesis|abschlussarbeit|masterarbeit", re.I)


def _date(v):
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def staleness(job: dict, today=None) -> str:
    """Why a listing is probably no longer open, or '' if it looks current."""
    today = today or datetime.date.today()
    posted, start = _date(job.get("posted", "")), _date(job.get("start", ""))
    if posted and (today - posted).days > STALE_POSTED_DAYS:
        return f"posted {posted:%d %b %Y}, {(today - posted).days} days ago"
    if start and (today - start).days > STALE_START_DAYS:
        return f"planned start {start:%d %b %Y} has passed"
    return ""


def tier(job: dict) -> int:
    """0 = internship (what Arjun is applying for), 1 = working student / thesis, 2 = anything else."""
    title = job.get("title", "")
    return 0 if _INTERN.search(title) else 1 if _STUDENT.search(title) else 2


def shortlist_order(jobs: list, today=None) -> list:
    """Spend the limited AI slots on fresh internships first; keeps the scraper's score order within each group."""
    return sorted(jobs, key=lambda j: (bool(staleness(j, today)), tier(j)))


def run(jobs: list, getter, llm=None, max_analyze=None, max_letters=None, pause=0.5) -> list:
    """jobs: scraper dicts, already sorted best-first. Returns analysed jobs sorted by final_score."""
    max_analyze = int(max_analyze or os.environ.get("AI_MAX_JOBS", 15))
    max_letters = int(max_letters or os.environ.get("AI_MAX_LETTERS", 5))
    screen = llm or llm_mod.LLM(role="screen")
    writer = llm or llm_mod.LLM(role="write")
    cv = load_cv()

    results, failed, errors = [], 0, []
    todo = shortlist_order(jobs)[:max_analyze]
    for n, job in enumerate(todo):
        try:
            jd = fetch_jd_text(job, getter)
            a = analyze(screen, cv, jd)
            stale = staleness(job)
            if stale:                                # still shown, but ranked below current openings
                a["final_score"] = max(0, a["final_score"] - 20)
                a.setdefault("adjustments", []).append(f"-20 possibly filled ({stale})")
                a["stale"] = stale
            results.append({**job, "ai": a, "_jd": jd})
            print(f"  [AI] {a['final_score']:3d}  {a.get('verdict','?'):5s}  {job['title'][:60]}")
        except llm_mod.QuotaExhausted as e:          # no point trying the rest today
            failed += len(todo) - n
            errors.append(f"QuotaExhausted: {e} - stopped screening")
            print(f"  [AI WARN] {e}; skipping the remaining {len(todo) - n} job(s)")
            break
        except Exception as e:                       # one bad job never kills the run
            failed += 1
            errors.append(f"{type(e).__name__}: {str(e)[:160]}")
            print(f"  [AI WARN] {job.get('title','')[:50]}: {type(e).__name__}: {str(e)[:200]}")
        time.sleep(pause)

    results.sort(key=lambda j: j["ai"]["final_score"], reverse=True)
    letter_errors = []

    for job in [j for j in results if j["ai"].get("verdict") != "SKIP" and not j["ai"].get("stale")][:max_letters]:
        try:
            job["ai"]["cover_letter"] = cover_letter(writer, cv, job["_jd"], job["ai"])
        except llm_mod.QuotaExhausted as e:
            errors.append(f"QuotaExhausted: {e} - stopped cover letters")
            print(f"  [AI WARN] {e}; no more cover letters today")
            break
        except Exception as e:
            letter_errors.append(f"{type(e).__name__}: {str(e)[:160]}")
            print(f"  [AI WARN] letter for {job['title'][:50]}: {type(e).__name__}: {e}")
    for j in results:
        j.pop("_jd", None)
    calls = screen.calls + (writer.calls if writer is not screen else 0)
    print(f"[OK] AI analysed {len(results)} jobs ({failed} failed) with {calls} API calls "
          f"(screening: {screen.model}, letters: {writer.model})")
    run.failed = failed
    if letter_errors:
        print(f"::warning title=Cover letters failed for {len(letter_errors)} job(s)::"
              + " || ".join(list(dict.fromkeys(letter_errors))[:3]))
    if errors:   # GitHub annotation, readable on the run page without downloading logs
        uniq = list(dict.fromkeys(errors))[:3]
        print(f"::warning title=AI screening failed for {failed} job(s)::" + " || ".join(uniq))
    return results


# ── Email rendering ───────────────────────────────────────────────────────────
COLORS = {"APPLY": "#15803d", "MAYBE": "#b45309", "SKIP": "#b91c1c"}


def _li(items):
    return "".join(f"<li>{html.escape(str(i))}</li>" for i in (items or []))


def render_html(results: list, top: int = 5, failed: int = None) -> str:
    failed = getattr(run, "failed", 0) if failed is None else failed
    note = (f"<p style='color:#b45309;font-family:Arial'><b>{failed} job(s) could not be screened today</b> "
            f"(see the [AI WARN] lines in the GitHub Actions log).</p>" if failed else "")
    if not results:
        return note + "<p><i>AI matching: no jobs analysed today.</i></p>"
    blocks = []
    for i, j in enumerate(results[:top], 1):
        a = j["ai"]
        color = COLORS.get(a.get("verdict"), "#374151")
        letter = a.get("cover_letter")
        posted_html = f" &middot; posted {html.escape(j['posted'])}" if j.get("posted") else ""
        stale_html = (f"<div style='color:#b45309;font-size:13px'><b>Possibly filled:</b> {html.escape(a['stale'])}. "
                      f"Check the employer's own careers page before applying.</div>" if a.get("stale") else "")
        highlights_html = ("" if a.get("verdict") == "SKIP" else
            f"<div style='margin-top:6px'><b>Put these first on your CV:</b><ol style='margin:4px 0'>{_li(a.get('highlights'))}</ol></div>")
        letter_html = (
            f"<details><summary><b>Draft cover letter</b> (check before sending)</summary>"
            f"<pre style='white-space:pre-wrap;font-family:inherit;background:#f3f4f6;padding:10px'>"
            f"{html.escape(letter)}</pre></details>" if letter else ""
        )
        blocks.append(f"""
<div style="border:1px solid #e5e7eb;border-left:4px solid {color};padding:12px 14px;margin:0 0 14px">
  <div style="font-size:16px"><b>{i}. {html.escape(j['title'])}</b> &mdash; {html.escape(j['company'])}</div>
  <div style="color:#6b7280;font-size:13px">{html.escape(a.get('location') or j['location'])} &middot;
    {html.escape(str(a.get('contract_type','')))} &middot; start {html.escape(str(a.get('start','?')))} &middot;
    German: {html.escape(str(a.get('german_level_required','?')))} &middot; via {html.escape(j['source'])}{posted_html}</div>
  {stale_html}
  <div style="margin:6px 0"><span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px">
    {html.escape(str(a.get('verdict','?')))} &middot; {a['final_score']}/100</span>
    <span style="color:#6b7280;font-size:12px"> {html.escape(', '.join(a.get('adjustments', [])))}</span></div>
  <div>{html.escape(str(a.get('why','')))}</div>
  {highlights_html}
  <div><b>Gaps:</b> {html.escape('; '.join(a.get('gaps') or []) or 'none flagged')}</div>
  {letter_html}
  <div style="margin-top:6px"><a href="{html.escape(j['url'])}">Open the posting &rarr;</a></div>
</div>""")
    return (
        "<h2 style='font-family:Arial'>Today's top matches (AI-screened)</h2>" + note +
        "<div style='font-family:Arial;font-size:14px'>" + "".join(blocks) + "</div>"
    )
