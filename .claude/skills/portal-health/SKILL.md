---
name: portal-health
description: Check which job portals in src/scraper.py are returning results, diagnose the broken ones (blocked, URL changed, selectors broken, JS-only), and propose fixes or switch them off. Use after a daily run with few jobs or when the Portal Stats sheet shows zeros.
disable-model-invocation: true
argument-hint: "[--all] [--only <portal>] [--query \"<search>\"]"
---

# Portal health check

Find out which portals are working, why the others are not, and what to do about each one.

## 1. Gather evidence

**Live probe (always).** From the repo root:

```
python .claude/skills/portal-health/probe.py $ARGUMENTS
```

Without arguments it probes the enabled portals in `PORTALS`. Pass `--all` to include the ones
switched off with `# off:` comments, `--only <name>` to probe one, `--query "<text>"` to try another
search term. Columns: **Parsed** = cards the scraper found (calls to `make_job`), **Kept** = jobs that
passed the filters, plus the HTTP outcome from `HTTP_LOG`.

If `python` is not found, use `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`.

**Latest CI run (when `gh` is installed and authenticated).** CI is the ground truth: GitHub runners
use datacenter IPs that many portals block, while the probe runs from the user's home connection.

```
gh run list --workflow daily_scrape.yml -L 3
gh run view <run-id> --log | grep -E "raw hits|::notice|::warning|\[WARN\]"
```

The run prints `::notice title=Portal results::` (jobs per portal) and, when any portal returned 0,
`::notice title=HTTP outcomes::` (status per domain). If `gh` is missing, say so, continue with the
probe alone, and mention that `winget install GitHub.cli` + `gh auth login` would add CI evidence.

## 2. Diagnose each portal that kept 0 jobs

| Probe verdict | Likely cause | What to do |
|---|---|---|
| BLOCKED (403/405/429) | Bot protection | Not fixable with selectors. If it is blocked in CI too, keep it off. If it works locally but is blocked in CI, it is IP blocking: say so, keep it off. |
| URL CHANGED (404) | Search URL format changed | Fetch the portal's homepage, run a search there, find the new URL pattern, update the `url = f"..."` line in its `scrape_*` function. |
| JS-ONLY PAGE (202 / tiny HTML) | Results rendered by JavaScript | Look for a JSON endpoint or embedded `__NEXT_DATA__` / `application/ld+json` in the HTML; otherwise keep it off. |
| NO CARDS FOUND (200, Parsed 0) | Markup changed, or genuinely no results | First re-probe with `--only <portal> --query "<another query>"`. If still 0, fetch the page with `scraper.safe_get(url)` and inspect it to find the new card/title/company/location selectors. For the API sources, compare the response keys in the `BA ...` debug info with what `src/sources_api.py` parses. |
| ALL FILTERED (Parsed > 0, Kept 0) | `make_job` filters reject everything | Not a portal problem: check `REJECT_KEYWORDS` / scoring in `src/scraper.py` against a few of the parsed titles. |

When inspecting HTML, save it to the scratchpad and grep it there; do not paste whole pages into the
conversation.

## 3. Report, then fix with permission

Start with a table: portal, on/off, CI result (if available), probe verdict, cause, proposed action.
Then ask before changing code. When the user agrees:

- **Fix a scraper:** edit only that portal's `scrape_*` function, keep the existing style
  (`safe_get`, `BeautifulSoup`, `[:10]` card limit, `make_job(...)`), then re-run
  `probe.py --only <portal>` and confirm Kept > 0.
- **Switch a portal off:** comment out its `PORTALS` entry and add the reason in the established
  format: `# off: <status> <reason> on <N> runs (<D Mon YYYY>)`. Keep the function.
- **Re-enable a fixed portal:** uncomment its `PORTALS` entry and remove the `# off:` note.

The PostToolUse hook runs the test suite after each edit in `src/`; fix any failure before finishing.
Do not commit unless the user asks.
