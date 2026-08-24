#!/usr/bin/env bash
#
# Probe whether the three GitHub security-alert REST APIs are reachable, before security-posture
# collection is built on top of them.
#
#   ./scripts/probe-alert-access.sh [--config config.yml] [repository...]
#
# Defaults to hmcts.yml and every repository it lists if no repository names are given.
# Requires GH_TOKEN in the environment — this script is USER-RUN ONLY. The loop that maintains
# plan.md never holds a GitHub token (architecture.md, "Access"), so this probe cannot be
# run, or its results faked, inside that loop; only a human with a real token can produce them.
#
# WHY THREE SEPARATE PROBES. Dependabot alerts, code-scanning alerts and secret-scanning alerts
# are three independent REST endpoints, each gated by its own permission. A 403 on one says
# nothing about the other two — exactly the lesson the merge-gate work already paid for, where
# branch-protection detail turned out unreadable on 4 of 5 repositories (architecture.md,
# "Access"). Security-posture collection is only worth building if at least one reachable
# repository clears at least one of the three.
#
# WHY STATUS CODE ONLY. A repository with zero open alerts of a kind still answers 200 with an
# empty list — that is access, not absence. The body is not informative here, so this script
# reports HTTP status only and never treats "no alerts" as "no access" or vice versa.

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

# pyyaml is a runtime dependency already; "uv run" resolves it from the project's own environment
# instead of assuming a bare `python3` has it installed. Read in ONE command substitution, not a
# process substitution: `<(...)` reports no exit status, so a `uv` that fails to build its
# environment would look exactly like a config listing no repositories, and the probe would print a
# confident empty table. The organisation is the first line, one repository per line after it.
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

echo "organisation  $ORGANIZATION"
echo "repositories  ${REPOSITORIES[*]}"
echo

ENDPOINTS=("dependabot/alerts" "code-scanning/alerts" "secret-scanning/alerts")

printf "%-24s %-22s %-22s %-22s\n" "repository" "${ENDPOINTS[0]}" "${ENDPOINTS[1]}" "${ENDPOINTS[2]}"

REACHABLE=0
for repository in "${REPOSITORIES[@]}"; do
    ROW="$repository"
    for endpoint in "${ENDPOINTS[@]}"; do
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer $GH_TOKEN" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            "https://api.github.com/repos/$ORGANIZATION/$repository/$endpoint?per_page=1")
        if [ "$STATUS" = "200" ]; then
            REACHABLE=$((REACHABLE + 1))
        fi
        ROW="$ROW	$STATUS"
    done
    printf "%s\n" "$ROW" | awk -F'\t' '{printf "%-24s %-22s %-22s %-22s\n", $1, $2, $3, $4}'
done

echo
echo "200 = reachable, 403 = permission denied, 404 = disabled or not found for this repository"
echo "$REACHABLE of $(( ${#REPOSITORIES[@]} * ${#ENDPOINTS[@]} )) probes answered 200"
if [ "$REACHABLE" -eq 0 ]; then
    echo
    echo "no reachable repository exposes any alert family — record this in docs/architecture.md"
    echo "under \"Access\" and stop; the plan says not to build an untestable feature."
fi
