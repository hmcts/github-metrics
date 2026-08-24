#!/usr/bin/env bash
#
# Probe whether GitHub's Copilot usage and billing APIs are reachable with this token, and what
# shape of history they would offer if they are — before any cost axis is designed on top of them.
#
#   ./scripts/probe-copilot-usage.sh [--config config.yml] [--org hmcts] [--days 90]
#
# Reads the organisation from hmcts.yml unless --org names one. Requires GH_TOKEN in the
# environment — USER-RUN ONLY, like the other four scripts here, because the loop that maintains
# plan.md never holds a GitHub token (architecture.md, "Access"). Neither the probe nor its results
# can be produced inside that loop, and an estimate of them is not evidence.
#
# WHY PROBE BEFORE BUILDING. These are the first ORG-LEVEL sources this tool would have: every
# existing source is keyed on a repository, so a Copilot series has no home in the per-repository
# blocks and its shape cannot be designed until its data is real. Two facts have to come from a
# real response rather than from documentation:
#
#   GRANULARITY - aggregate, or per user. The tool reports per repository and never rolls up
#                 (architecture.md, "Scope boundaries"), so an org-wide aggregate is a different
#                 kind of number from everything else it prints, and where it can honestly sit is a
#                 question the answer decides.
#   REACH       - how far back the endpoint actually serves. This is the one that costs money to get
#                 wrong. If retention is short, history exists ONLY if something snapshots it, in
#                 the way open-alert counts are snapshotted (architecture.md, "Storage rule"), and
#                 every day nobody snapshots is a day that cannot be recovered later.
#
# WHY THESE THREE ENDPOINTS. They answer three different questions and are gated separately, so a
# status on one says nothing about the other two — the same lesson the three alert families taught:
#
#   /orgs/{org}/copilot/metrics                    usage over time: the only one with a series
#   /orgs/{org}/copilot/billing                    seats: current state, no dates, no history
#   /organizations/{org}/settings/billing/usage    the enhanced billing platform's usage report,
#                                                  which is where credits and net amounts live
#
# READ THE NON-200s, THEY ARE INFORMATIVE HERE. Unlike the alert probe, where a body says nothing,
# these endpoints explain themselves: a 403 may be a missing scope OR an org policy that disables
# the metrics API, and the metrics endpoint answers 422 when an organisation has too few active
# Copilot seats to report on. Those are different findings with different fixes, so this script
# prints GitHub's own message alongside every failing status.

set -euo pipefail

CONFIG="hmcts.yml"
ORGANIZATION=""
DAYS=90

while [ "$#" -gt 0 ]; do
    case "$1" in
        --config) CONFIG="$2"; shift 2 ;;
        --org) ORGANIZATION="$2"; shift 2 ;;
        --days) DAYS="$2"; shift 2 ;;
        -h|--help) sed -n '2,39p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

case "$DAYS" in
    ''|*[!0-9]*) echo "--days must be a whole number, got: $DAYS" >&2; exit 2 ;;
esac

[ -n "${GH_TOKEN:-}" ] || { echo "GH_TOKEN is not set; this script must be run by hand with a real token" >&2; exit 2; }
command -v uv >/dev/null || { echo "required tool not found: uv" >&2; exit 2; }
command -v curl >/dev/null || { echo "required tool not found: curl" >&2; exit 2; }

if [ -z "$ORGANIZATION" ]; then
    [ -f "$CONFIG" ] || { echo "config not found: $CONFIG (or name an organisation with --org)" >&2; exit 2; }
    # pyyaml is a runtime dependency already; "uv run" resolves it from the project's own
    # environment rather than assuming a bare python3 has it. Read in ONE command substitution, not
    # a process substitution: `<(...)` reports no exit status, so a `uv` that failed to build its
    # environment would look exactly like a config naming no organisation.
    ORGANIZATION=$(uv run python3 -c "
import sys
import yaml

configuration = yaml.safe_load(open(sys.argv[1]))  # noqa: SIM115, PTH123
print(configuration['organization'])
" "$CONFIG") || { echo "could not read $CONFIG (see the error above)" >&2; exit 2; }
fi
[ -n "$ORGANIZATION" ] || { echo "no organisation to probe" >&2; exit 2; }

# Every instant is computed in Python rather than with `date`, because BSD `date -v-90d` and GNU
# `date -d '90 days ago'` disagree and this script is run on macOS.
# `|| true` because an empty heredoc makes `read` return 1, which under `set -e` would abort with no
# message at all; the guard below is what should report a uv that could not build its environment.
read -r SINCE TODAY BILLING_YEAR BILLING_MONTH <<EOF || true
$(uv run python3 -c "
from datetime import UTC, datetime, timedelta

today = datetime.now(UTC).date()
print((today - timedelta(days=$DAYS)).isoformat(), today.isoformat(), today.year, today.month)
")
EOF
[ -n "${SINCE:-}" ] || { echo "could not compute the probe window (see the error above)" >&2; exit 2; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Report what one response offers. Reads the body from a file rather than an argument: a month of
# billing usage is far larger than a command line may be, and truncation would be silent.
summarise() {
    body_file="$1"
    body_status="$2"
    since_wanted="$3"
    uv run python3 - "$body_file" "$body_status" "$since_wanted" <<'PYTHON'
"""Report the granularity and reach of one Copilot usage response.

Prints a tab-separated `granularity<TAB>reach` first line for the summary table, then any number
of detail lines. Nothing here interprets the numbers; it reports the SHAPE of what came back, which
is the only thing a probe is entitled to say.
"""

import json
import re
import sys

# Anything naming a person makes the response per-user rather than aggregate.
IDENTITY_KEYS = {"login", "user", "username", "user_login", "assignee", "seat_holder", "actor"}
# Anything that dates a row makes a series possible; a response with none is current state only.
DATE_KEYS = {"date", "day", "usage_at", "created_at", "last_activity_at", "timestamp"}
DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")
MAX_LISTED_KEYS = 10

body_path, status, requested_since = sys.argv[1], sys.argv[2], sys.argv[3]
with open(body_path) as handle:  # noqa: PTH123
    raw = handle.read()

if not raw.strip():
    print("empty\tempty")
    print(f"the response body is empty, at status {status}")
    sys.exit(0)

try:
    payload = json.loads(raw)
except ValueError:
    print("unreadable\tunreadable")
    print(f"the response body is not JSON ({len(raw)} bytes)")
    sys.exit(0)

if status != "200":
    message = payload.get("message") if isinstance(payload, dict) else None
    print("-\t-")
    print(f"github says: {message}" if message else "no message in the response body")
    sys.exit(0)

identity_keys_seen: set[str] = set()
dates: set[str] = set()


def walk(node: object) -> None:
    """Collect every identity key and every dated value anywhere in the response."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in IDENTITY_KEYS and value:
                identity_keys_seen.add(key)
            if key in DATE_KEYS and isinstance(value, str):
                matched = DATE_PREFIX.match(value)
                if matched:
                    dates.add(matched.group(1))
            walk(value)
    elif isinstance(node, list):
        for item in node:
            walk(item)


walk(payload)

if isinstance(payload, list):
    rows = len(payload)
    shape = f"array of {rows} object" + ("" if rows == 1 else "s")
    keys = sorted({key for item in payload if isinstance(item, dict) for key in item})
elif isinstance(payload, dict):
    listed = [(key, value) for key, value in payload.items() if isinstance(value, list)]
    rows = max((len(value) for _, value in listed), default=1)
    plural = "" if rows == 1 else "s"
    shape = "object" + (f", holding {rows} row{plural} under {listed[0][0]}" if listed else " (no row list)")
    keys = sorted(payload)
else:
    rows, shape, keys = 0, f"neither an object nor an array ({type(payload).__name__})", []

granularity = "per-user (" + ", ".join(sorted(identity_keys_seen)) + ")" if identity_keys_seen else "aggregate"
if dates:
    # Distinct DATES, not rows: several rows may share a date and one row may carry several dates,
    # so counting rows here would overstate what the response actually dates.
    earliest, latest = min(dates), max(dates)
    dated = f"{len(dates)} distinct date" + ("" if len(dates) == 1 else "s")
    reach = f"{earliest} .. {latest} ({dated})"
else:
    earliest, reach = "", "undated"

print(f"{granularity}\t{reach}")
print(shape)
if keys:
    shown = ", ".join(keys[:MAX_LISTED_KEYS])
    print(f"keys: {shown}" + (f", and {len(keys) - MAX_LISTED_KEYS} more" if len(keys) > MAX_LISTED_KEYS else ""))
if not dates:
    print("CURRENT STATE ONLY: nothing in the response says when it was true, so there is no history")
    print("to read back. A series would exist only if collection snapshots it, run by run.")
elif not requested_since:
    print("dated rows, but this endpoint was not asked for a window; re-probe with one to find the limit")
elif earliest > requested_since:
    print(f"RETENTION IS SHORTER THAN THE WINDOW ASKED FOR: requested since {requested_since}, earliest")
    print(f"row {earliest}. Anything before {earliest} is unreachable and can only be snapshotted from now on.")
else:
    print(f"reaches at least as far back as the {requested_since} asked for; ask for more to find the limit")
PYTHON
}

probe() {
    index="$1"
    label="$2"
    url="$3"
    requested_since="${4:-}"

    response=$(curl -s -w '\n%{http_code}' \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $GH_TOKEN" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "$url")
    status=$(printf '%s' "$response" | tail -n 1)
    printf '%s' "$response" | sed '$d' > "$WORK/body-$index.json"

    echo "== $label"
    echo "url           $url"
    echo "status        $status"
    summary=$(summarise "$WORK/body-$index.json" "$status" "$requested_since")
    granularity=$(printf '%s' "$summary" | head -n 1 | cut -f 1)
    reach=$(printf '%s' "$summary" | head -n 1 | cut -f 2)
    echo "granularity   $granularity"
    echo "reach         $reach"
    printf '%s\n' "$summary" | tail -n +2 | sed 's/^/  /'
    echo

    # Kept in files rather than an associative array: macOS ships bash 3.2, which has none.
    printf '%s' "$status" > "$WORK/status-$index"
    printf '%s' "$granularity" > "$WORK/granularity-$index"
    printf '%s' "$reach" > "$WORK/reach-$index"
}

echo "organisation  $ORGANIZATION"
echo "today         $TODAY (UTC)"
echo "metrics since $SINCE (--days $DAYS)"
printf 'billing month %s-%02d\n' "$BILLING_YEAR" "$BILLING_MONTH"
echo

LABEL_1="GET /orgs/{org}/copilot/metrics"
LABEL_2="GET /orgs/{org}/copilot/billing"
LABEL_3="GET /organizations/{org}/settings/billing/usage"

probe 1 "$LABEL_1" "https://api.github.com/orgs/$ORGANIZATION/copilot/metrics?since=$SINCE&per_page=100" "$SINCE"
probe 2 "$LABEL_2" "https://api.github.com/orgs/$ORGANIZATION/copilot/billing" ""
probe 3 "$LABEL_3" "https://api.github.com/organizations/$ORGANIZATION/settings/billing/usage?year=$BILLING_YEAR&month=$BILLING_MONTH" ""

echo "== per-endpoint table — paste this into plan.md, Task 8 ======================"
printf "%-48s %-7s %-28s %s\n" "endpoint" "status" "granularity" "reach"
index=1
REACHABLE=0
while [ "$index" -le 3 ]; do
    # Indirect expansion rather than `eval`, and an `if` rather than `[ ... ] && count=...`, whose
    # exit status under `set -e` already cost measure-alert-volume.sh one user-run failure.
    label_variable="LABEL_$index"
    status=$(cat "$WORK/status-$index")
    printf "%-48s %-7s %-28s %s\n" \
        "${!label_variable}" \
        "$status" \
        "$(cat "$WORK/granularity-$index")" \
        "$(cat "$WORK/reach-$index")"
    if [ "$status" = "200" ]; then
        REACHABLE=$((REACHABLE + 1))
    fi
    index=$((index + 1))
done

echo
echo "200 = reachable, 401 = the token is not accepted, 403 = permission or an org policy that"
echo "disables the metrics API, 404 = not an organisation or not available on this plan,"
echo "422 = reachable but too few active Copilot seats for GitHub to report on."
echo "$REACHABLE of 3 endpoints answered 200."
if [ "$REACHABLE" -eq 0 ]; then
    echo
    echo "Nothing is reachable: record that in docs/architecture.md beside the other access findings"
    echo "and build nothing. A cost axis designed against an unreachable source is a guess."
else
    echo
    echo "Record the table in docs/architecture.md beside the other access findings BEFORE any"
    echo "collection code. The two open design questions it does not answer: where an org-level"
    echo "series sits in a per-repository report, and whether short retention forces a snapshot."
fi
