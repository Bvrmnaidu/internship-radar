"""Live health probe for every portal scraper in src/scraper.py.

Runs each scraper once with one query and reports, per portal:
  HTTP outcome, cards parsed (make_job calls), jobs kept (passed the filters), seconds.

Usage (from the repo root):
  python .claude/skills/portal-health/probe.py            # enabled portals only
  python .claude/skills/portal-health/probe.py --all      # also portals switched off in PORTALS
  python .claude/skills/portal-health/probe.py --only StepStone --query "data engineer Praktikum"
"""

import argparse, pathlib, re, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import scraper  # noqa: E402

OFF_LINE = re.compile(r'#\s*\("([^"]+)",\s*(scrape_\w+),.*?#\s*off:\s*(.*)$')


def registry(include_off):
    rows = [(name, fn, "on") for name, fn, _ in scraper.PORTALS]
    if include_off:
        for line in (ROOT / "src" / "scraper.py").read_text(encoding="utf-8").splitlines():
            m = OFF_LINE.search(line)
            if m:
                rows.append((m.group(1), getattr(scraper, m.group(2)), "off: " + m.group(3).strip()))
    return rows


def probe(fn, query):
    parsed = 0
    real_make_job = scraper.make_job

    def counting_make_job(*a, **kw):
        nonlocal parsed
        parsed += 1
        return real_make_job(*a, **kw)

    scraper.make_job = counting_make_job
    scraper.HTTP_LOG.clear()
    start = time.time()
    try:
        kept, error = len(fn(query)), ""
    except Exception as e:
        kept, error = 0, f"{type(e).__name__}: {e}"[:80]
    finally:
        scraper.make_job = real_make_job
    http = "; ".join(f"{d}: {o}" for d, o in scraper.HTTP_LOG.items()) or "no request logged"
    return parsed, kept, round(time.time() - start, 1), error or http


def verdict(parsed, kept, outcome):
    if kept:
        return "HEALTHY"
    if re.search(r"HTTP (403|405|429)", outcome):
        return "BLOCKED"
    if "HTTP 404" in outcome:
        return "URL CHANGED"
    if "HTTP 202" in outcome:
        return "JS-ONLY PAGE"
    if parsed:
        return "ALL FILTERED"        # cards found, but make_job rejected every one
    if "HTTP 200" in outcome or "API ok" in outcome:
        return "NO CARDS FOUND"      # loaded fine: selectors/parser broken, or no results for this query
    return "ERROR"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include portals switched off in PORTALS")
    ap.add_argument("--only", help="probe only portals whose name contains this text")
    ap.add_argument("--query", default=scraper.SEARCH_QUERIES[0])
    args = ap.parse_args()

    rows = [r for r in registry(args.all) if not args.only or args.only.lower() in r[0].lower()]
    print(f"Query: {args.query!r}\n")
    print(f"{'Portal':24} {'Status':6} {'Verdict':17} {'Parsed':>6} {'Kept':>5} {'Secs':>5}  HTTP / error")
    for name, fn, status in rows:
        parsed, kept, secs, outcome = probe(fn, args.query)
        print(f"{name:24} {status[:3]:6} {verdict(parsed, kept, outcome):17} "
              f"{parsed:6} {kept:5} {secs:5}  {outcome}")
        if status != "on":
            print(f"{'':24} registry note -> {status}")


if __name__ == "__main__":
    main()
