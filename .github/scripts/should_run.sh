#!/usr/bin/env bash
# Decides whether an *automatic* trigger (GitHub cron, or the external 09:00 trigger) should do
# today's scrape and send the email. Manual "Run workflow" clicks skip this check and always run.
#
# Rules:
#   1. Not before 09:00 Hamburg time: an early trigger leaves the job to a later one.
#   2. At most one email per Hamburg day: skip if any run today already ran the scraper
#      successfully (manual runs count too).
# There is deliberately NO upper time limit. GitHub can start scheduled runs hours late
# (24-25 Oct 2026: the 07:17 UTC trigger started around 12:00 UTC). The old "only 09:xx" rule
# made every late run skip, so no email arrived at all. Now a late run still sends, just later.
#
# Prints run=true|false to $GITHUB_OUTPUT. Test overrides:
#   BERLIN_HOUR, SINCE_EPOCH (Hamburg midnight as epoch seconds),
#   RUNS_JSON (output of `gh run list --json databaseId,createdAt,status`),
#   JOBS_FIXTURE_DIR (directory with one <run id>.json per run, shaped like the jobs API).
set -euo pipefail
hour="${BERLIN_HOUR:-$(TZ=Europe/Berlin date +%H)}"
since="${SINCE_EPOCH:-$(TZ=Europe/Berlin date -d 'today 00:00' +%s)}"
out="${GITHUB_OUTPUT:-/dev/stdout}"

if [ "$((10#$hour))" -lt 9 ]; then
  echo "Hamburg time is ${hour}:xx, before 09:00: a later trigger will send today's email."
  echo "run=false" >> "$out"; exit 0
fi

# How many "Run scraper" steps succeeded in run $1 (0 if it was a skipped run).
scraper_ran() {
  local q='[.jobs[].steps[] | select(.name=="Run scraper" and .conclusion=="success")] | length'
  if [ -n "${JOBS_FIXTURE_DIR:-}" ]; then jq "$q" "$JOBS_FIXTURE_DIR/$1.json"
  else gh api "repos/$GITHUB_REPOSITORY/actions/runs/$1/jobs" --jq "$q"; fi
}

runs="${RUNS_JSON:-$(gh run list -R "$GITHUB_REPOSITORY" --workflow "Daily Job Scrape" \
        --limit 40 --json databaseId,createdAt,status)}"
ids=$(echo "$runs" | jq -r --argjson s "$since" --arg me "${GITHUB_RUN_ID:-0}" \
  '.[] | select(.status=="completed" and (.databaseId|tostring)!=$me
                and (.createdAt|fromdateiso8601) >= $s) | .databaseId')

for id in $ids; do
  if [ "$(scraper_ran "$id")" -gt 0 ]; then
    echo "Run $id already sent today's email: skipping."
    echo "run=false" >> "$out"; exit 0
  fi
done
echo "No email sent yet today (Hamburg time ${hour}:xx): running the scrape."
echo "run=true" >> "$out"
