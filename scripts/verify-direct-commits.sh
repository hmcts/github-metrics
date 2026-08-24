#!/usr/bin/env bash
#
# Verify the direct commits in an evidence report against git's own view of the default branch.
#
#   ./verify-direct-commits.sh [path-to-report.json]
#
# Defaults to last.json. Everything else — organisation, repository, window — is read out of the
# report, so there is nothing to edit.
#
# WHY GIT CAN ANSWER THIS INDEPENDENTLY. A commit that a pull request merge brought onto the default
# branch hangs off the merge commit's SECOND parent, so it is not on the branch's first-parent line.
# A commit pushed straight to the branch is. So `git rev-list --first-parent --no-merges` is git's
# own answer to "pushed directly", reached without GitHub's `associatedPullRequests` field, which is
# what the tool relies on — with one blind spot the next paragraph explains.
#
# THE TWO DIRECTIONS ARE NOT EQUALLY INTERESTING.
#
#   OVER-COUNT  — the tool says direct, git says a merge brought it in. This is the one that matters:
#                 any commit here is a tool defect. Expect zero.
#   UNDER-COUNT — git says direct, the tool does not. EXPECT THIS TO BE LARGE on any repository that
#                 squash-merges. A squash merge puts exactly one commit per pull request onto the
#                 first-parent line and it is not a merge commit, so git cannot tell it from a push.
#                 GitHub can, because it records which pull request the commit came from. Measured on
#                 cath-service 2026-08-13: git said 295, the tool said 159, and all 136 of the
#                 difference were squash commits, one for each of 136 distinct pull requests.
#
# Step 3 tests the under-count against git's own record rather than asking GitHub again: GitHub's
# default squash commit subject ends with "(#1234)", so a run of them is visible in the log alone.

set -euo pipefail

REPORT="${1:-last.json}"
CLONE_DIR="${CLONE_DIR:-/tmp/verify-direct-commits}"

for tool in python3 git; do
    command -v "$tool" >/dev/null || { echo "required tool not found: $tool" >&2; exit 2; }
done
[ -f "$REPORT" ] || { echo "report not found: $REPORT" >&2; exit 2; }

read -r ORGANIZATION REPOSITORY BRANCH STARTS_AT ENDS_AT <<EOF
$(python3 - "$REPORT" <<'PYTHON'
"""Print the organisation, repository, branch and window one report covers."""
import json
import sys

report = json.loads(open(sys.argv[1]).read())  # noqa: SIM115, PTH123
repository = report["repositories"][0]
gate = repository.get("merge_gate", {}).get("gate") or {}
print(
    report["organization"],
    repository["repository"],
    gate.get("branch", "HEAD"),
    repository["starts_at"],
    repository["ends_at"],
)
PYTHON
)
EOF

echo "report        $REPORT"
echo "repository    $ORGANIZATION/$REPOSITORY  (branch $BRANCH)"
echo "window        $STARTS_AT to $ENDS_AT"
echo

# ---------------------------------------------------------------------------------------------
echo "== 1. clone (commits only, no working tree) =================================="
if [ -d "$CLONE_DIR/.git" ]; then
    echo "reusing $CLONE_DIR"
    git -C "$CLONE_DIR" fetch --quiet origin
else
    git clone --filter=blob:none --no-checkout --quiet \
        "https://github.com/$ORGANIZATION/$REPOSITORY.git" "$CLONE_DIR"
    echo "cloned into $CLONE_DIR"
fi
echo

# ---------------------------------------------------------------------------------------------
echo "== 2. compare git's answer with the tool's ==================================="
# --until is inclusive, so a whole second is taken off the half-open window end.
UNTIL=$(python3 -c "
from datetime import datetime, timedelta
print((datetime.fromisoformat('$ENDS_AT') - timedelta(seconds=1)).isoformat())
")

git -C "$CLONE_DIR" rev-list --first-parent --no-merges \
    --since="$STARTS_AT" --until="$UNTIL" "origin/$BRANCH" | sort > /tmp/git-direct.txt

python3 - "$REPORT" <<'PYTHON' | sort > /tmp/tool-direct.txt
"""Print every direct commit the report names."""
import json
import sys

report = json.loads(open(sys.argv[1]).read())  # noqa: SIM115, PTH123
for repository in report["repositories"]:
    for finding in repository["behaviour"]:
        for commit in finding.get("direct_commits") or []:
            print(commit["sha"])
PYTHON

echo "git says direct   $(wc -l < /tmp/git-direct.txt | tr -d ' ')"
echo "tool says direct  $(wc -l < /tmp/tool-direct.txt | tr -d ' ')"
echo
echo "-- in the tool but NOT on git's first-parent line (over-count) --"
comm -13 /tmp/git-direct.txt /tmp/tool-direct.txt | tee /tmp/over-count.txt
echo "-- on git's first-parent line but MISSING from the tool (under-count) --"
comm -23 /tmp/git-direct.txt /tmp/tool-direct.txt | tee /tmp/under-count.txt
echo
DISAGREEMENTS=$(( $(wc -l < /tmp/over-count.txt) + $(wc -l < /tmp/under-count.txt) ))
echo "over-count  $(wc -l < /tmp/over-count.txt | tr -d ' ')  <- any of these is a tool defect"
echo "under-count $(wc -l < /tmp/under-count.txt | tr -d ' ')  <- expected on a repository that squash-merges; step 3 explains them"
echo "commits the two methods disagree about: $DISAGREEMENTS"
echo

# ---------------------------------------------------------------------------------------------
echo "== 3. explain the under-count from git alone =================================="
UNDER=$(wc -l < /tmp/under-count.txt | tr -d ' ')
if [ "$UNDER" -eq 0 ]; then
    echo "nothing to explain."
else
    SQUASHED=0
    while read -r sha; do
        SUBJECT=$(git -C "$CLONE_DIR" log -1 --format=%s "$sha")
        case "$SUBJECT" in
            *"(#"[0-9]*")") SQUASHED=$((SQUASHED + 1)) ;;
            *) echo "  not a squash subject: $sha  $SUBJECT" ;;
        esac
    done < /tmp/under-count.txt
    echo
    echo "$SQUASHED of $UNDER carry GitHub's squash-merge subject, so git counted a pull request as a push."
    echo "Any listed above are worth a look: they are commits git calls direct, the tool does not, and"
    echo "whose subject does not show a squash merge."
fi
echo

echo "== 3b. merge settings, if the token can read them =============================="
if command -v gh >/dev/null; then
    gh api "repos/$ORGANIZATION/$REPOSITORY" \
        --jq '{merge_commit: .allow_merge_commit, squash: .allow_squash_merge, rebase: .allow_rebase_merge}' || true
    echo "(nulls mean the token cannot read repository settings; step 3 answers the same question.)"
else
    echo "gh not found; check Settings > General > Pull Requests for the repository instead."
fi
echo

# ---------------------------------------------------------------------------------------------
echo "== 4. pull requests associated with in-window commits but merged outside ======"
echo "All three were confirmed on 2026-08-13: #879 merged 08-10, #913 merged 08-10, #921 merged 08-13,"
echo "every one after the window closed. Re-run only if a later report raises the same question."
if command -v gh >/dev/null; then
    for number in 913 921; do
        gh pr view "$number" --repo "$ORGANIZATION/$REPOSITORY" --json number,state,mergedAt || true
    done
else
    echo "gh not found; open the pull requests in a browser instead."
fi
echo

# ---------------------------------------------------------------------------------------------
echo "== next, when you want it ====================================================="
cat <<'NEXT'
The commit-collection query changed after the run that produced this report, so cached commit facts
are stranded under the old signature and will be refetched. Measure the new cost on a short window
before anything org-sized — this is also what proves the status-check rollup does not time out at
50 commits a page:

    uv run metrics --logging debug collect --config hmcts.yml --days 7 2> collect.log

Then count the calls it made, which should be one per 50 commits and none per commit:

    grep -c 'GitHub request POST' collect.log
NEXT
