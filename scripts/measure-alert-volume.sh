#!/usr/bin/env bash
#
# Measure how many security alerts a repository holds, so the security-posture collection shape
# is chosen against a number rather than an estimate.
#
#   ./scripts/measure-alert-volume.sh [--config config.yml] [repository...]
#
# Defaults to hmcts.yml and every repository it lists if no repository names are given.
# Requires GH_TOKEN — USER-RUN ONLY, like scripts/probe-alert-access.sh, because the loop that
# maintains the plan never holds a token (architecture.md, "Access"). Run the access probe first:
# this script assumes the endpoints answer 200 and reports "-" where they do not.
#
# WHY THIS EXISTS. architecture.md ("Collection cost and performance") records that the direct-commit
# source was accepted on an estimate and measured at 18x it, and rules: AN ESTIMATE OF A NEW SOURCE'S
# COST IS NOT EVIDENCE. Collecting alerts means enumerating them — severity counts and resolution
# times both need the alert records themselves — so the per-repository call cost is
# ceil(total / 100) per family, and that is only knowable from the totals below.
#
# WHY IT IS ITSELF CHEAP. Counting by enumeration would beg the question it is asking. Instead each
# count asks for ONE alert per page and reads the page number GitHub's `rel="last"` link points at,
# which is the total. That is one call per count and TWO CALLS PER FAMILY regardless of whether the
# repository holds ten alerts or ten thousand.

set -euo pipefail

CONFIG="hmcts.yml"
if [ "${1:-}" = "--config" ]; then
    CONFIG="$2"
    shift 2
fi
[ -f "$CONFIG" ] || { echo "config not found: $CONFIG" >&2; exit 2; }
[ -n "${GH_TOKEN:-}" ] || { echo "GH_TOKEN is not set; this script must be run by hand with a real token" >&2; exit 2; }
command -v uv >/dev/null || { echo "required tool not found: uv" >&2; exit 2; }
command -v curl >/dev/null || { echo "required tool not found: curl" >&2; exit 2; }

# Read in one command substitution, not a process substitution: `<(...)` reports no exit status, so
# a `uv` that failed to build its environment would look like a config listing no repositories.
CONFIGURED=$(uv run python3 -c "
import sys
import yaml

configuration = yaml.safe_load(open(sys.argv[1]))  # noqa: SIM115, PTH123
print(configuration['organization'])
seen = set()
for team in configuration.get('teams', []):
    for repository in team.get('repositories', []):
        if repository not in seen:
            seen.add(repository)
            print(repository)
" "$CONFIG") || { echo "could not read $CONFIG (see the error above)" >&2; exit 2; }

# Split with a `while` loop rather than `mapfile`/`readarray`: macOS ships bash 3.2, where those
# builtins do not exist, and this script is USER-RUN on exactly that platform.
ORGANIZATION=""
CONFIGURED_REPOSITORIES=()
while IFS= read -r line; do
    [ -n "$line" ] || continue
    if [ -z "$ORGANIZATION" ]; then
        ORGANIZATION="$line"
    else
        CONFIGURED_REPOSITORIES+=("$line")
    fi
done <<EOF
$CONFIGURED
EOF

[ -n "$ORGANIZATION" ] || { echo "no organisation in $CONFIG" >&2; exit 2; }

if [ "$#" -gt 0 ]; then
    REPOSITORIES=("$@")
elif [ "${#CONFIGURED_REPOSITORIES[@]}" -gt 0 ]; then
    REPOSITORIES=("${CONFIGURED_REPOSITORIES[@]}")
else
    # Guarded before any "${...[@]}" expansion: under `set -u`, bash 3.2 treats an empty array as
    # unset and would abort with a message saying nothing about the config that came up empty.
    echo "no repositories to probe: $CONFIG lists none, and none were given on the command line" >&2
    exit 2
fi

HEADERS=$(mktemp)
trap 'rm -f "$HEADERS"' EXIT

# Echo the number of alerts a query matches, or "-" if the endpoint does not answer 200.
alert_total() {
    query_url="$1"
    response=$(curl -s -D "$HEADERS" -w '\n%{http_code}' \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $GH_TOKEN" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "$query_url")
    status=$(printf '%s' "$response" | tail -n 1)
    if [ "$status" != "200" ]; then
        printf '%s' "-"
        return
    fi
    # The `rel="last"` link carries the final page number, which at one item per page is the total.
    # It is absent when the result fits on a single page, so that case is read from the body: an
    # empty JSON array means none, anything else means exactly one.
    last=$(tr -d '\r' < "$HEADERS" \
        | grep -i '^link:' \
        | tr ',' '\n' \
        | grep 'rel="last"' \
        | sed -n 's/.*[?&]page=\([0-9][0-9]*\).*/\1/p' \
        | head -n 1)
    if [ -n "$last" ]; then
        printf '%s' "$last"
    elif printf '%s' "$response" | sed '$d' | tr -d '[:space:]' | grep -q '^\[\]$'; then
        printf '%s' "0"
    else
        printf '%s' "1"
    fi
}

echo "organisation  $ORGANIZATION"
echo "repositories  ${REPOSITORIES[*]}"
echo

printf "%-24s %-22s %8s %8s %10s\n" "repository" "family" "open" "total" "calls"

FAMILIES=("dependabot/alerts" "code-scanning/alerts" "secret-scanning/alerts")
GRAND_CALLS=0
for repository in "${REPOSITORIES[@]}"; do
    for family in "${FAMILIES[@]}"; do
        base="https://api.github.com/repos/$ORGANIZATION/$repository/$family"
        OPEN=$(alert_total "$base?per_page=1&state=open")
        TOTAL=$(alert_total "$base?per_page=1")
        if [ "$TOTAL" = "-" ]; then
            CALLS="-"
        else
            # What enumeration at GitHub's 100-per-page maximum would cost for this family. Written
            # as an `if` rather than `[ ... ] && CALLS=1`, whose exit status under `set -e` is the
            # kind of subtlety that already cost this script one user-run failure.
            if [ "$TOTAL" -eq 0 ]; then
                CALLS=1
            else
                CALLS=$(( (TOTAL + 99) / 100 ))
            fi
            GRAND_CALLS=$((GRAND_CALLS + CALLS))
        fi
        printf "%-24s %-22s %8s %8s %10s\n" "$repository" "$family" "$OPEN" "$TOTAL" "$CALLS"
    done
done

echo
echo "open  = alerts currently open;  total = open plus every resolved one GitHub still holds"
echo "calls = ceil(total / 100), what enumerating that family at per_page=100 would cost per run"
echo
echo "$GRAND_CALLS calls per run to enumerate every family across ${#REPOSITORIES[@]} repository/ies"
echo "compare against architecture.md: a whole cath-service collection is about 14 calls today,"
echo "and GRAPHQL BINDS FIRST at roughly 500 repositories/hour — these are REST and budget separately."
