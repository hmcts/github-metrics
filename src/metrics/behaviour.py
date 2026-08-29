"""Collect compact behaviour facts for the merges into a default branch."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from metrics.config import Configuration
from metrics.cost import CostMeter, combined
from metrics.domain import (
    AvailabilityReason,
    BehaviourProvenance,
    CacheStatus,
    CheckConclusion,
    CheckFact,
    CheckRollupState,
    CollectionStatus,
    DirectCommitFact,
    EvidenceKind,
    EvidenceSource,
    Merges,
    OpenPullRequestSummary,
    PullRequestFact,
    ReportingWindow,
    RepositoryInventory,
    RepositoryInventoryIssue,
    RepositoryInventoryItem,
    ReviewFact,
    ReviewState,
    SourceCoverage,
)
from metrics.github import GitHubClient, GitHubError
from metrics.progress import log_progress
from metrics.storage import (
    cache_direct_commit_facts,
    cache_pull_request_facts,
    find_missing_cached_coverage,
    load_cached_direct_commit_facts,
    load_cached_pull_request_facts,
)


class GitHubModel(BaseModel):
    """Provide permissive immutable parsing for GitHub-owned payloads."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class GitHubActor(GitHubModel):
    """Select the actor identity fields needed for eligibility classification."""

    login: str
    typename: str = Field(alias="__typename")


class GitHubPageInfo(GitHubModel):
    """Select GraphQL connection pagination fields."""

    has_next_page: bool = Field(alias="hasNextPage")
    end_cursor: str | None = Field(alias="endCursor")


class GitHubCommentConnection(GitHubModel):
    """Select the total count of comments on one review, without fetching any comment node.

    `first: 0` on the query keeps this a count-only connection: a comment carries no eligibility
    rule of its own, so nothing here ever needs the comment nodes themselves.
    """

    total_count: int = Field(alias="totalCount")


class GitHubReview(GitHubModel):
    """Select compact review fields from a GitHub response."""

    database_identifier: int = Field(alias="databaseId")
    submitted_at: AwareDatetime = Field(alias="submittedAt")
    state: ReviewState
    author: GitHubActor | None
    comments: GitHubCommentConnection
    body: str | None = None


class GitHubReviewConnection(GitHubModel):
    """Select one bounded page of pull-request reviews."""

    nodes: tuple[GitHubReview, ...]
    page_info: GitHubPageInfo = Field(alias="pageInfo")


class GitHubCheckContext(GitHubModel):
    """Select one check run or legacy commit status from a rollup.

    GitHub returns a union: check runs carry `name`/`conclusion`/`completedAt`, while legacy status
    contexts carry `context`/`state`/`createdAt`. Both are normalised by `check_fact()`.
    """

    typename: str = Field(alias="__typename")
    name: str | None = None
    status: str | None = None
    conclusion: str | None = None
    completed_at: AwareDatetime | None = Field(default=None, alias="completedAt")
    context: str | None = None
    state: str | None = None
    created_at: AwareDatetime | None = Field(default=None, alias="createdAt")


class GitHubCheckConnection(GitHubModel):
    """Select one bounded page of status-check rollup contexts."""

    nodes: tuple[GitHubCheckContext, ...]
    page_info: GitHubPageInfo = Field(alias="pageInfo")


class GitHubStatusCheckRollup(GitHubModel):
    """Select the combined status-check rollup for one commit."""

    contexts: GitHubCheckConnection


class GitHubCommit(GitHubModel):
    """Select the status-check rollup attached to one commit."""

    status_check_rollup: GitHubStatusCheckRollup | None = Field(default=None, alias="statusCheckRollup")


class GitHubPullRequestCommit(GitHubModel):
    """Select one commit entry from a pull-request commit connection."""

    commit: GitHubCommit


class GitHubCommitConnection(GitHubModel):
    """Select the head commit of one pull request."""

    nodes: tuple[GitHubPullRequestCommit, ...]


class GitHubReadyForReviewEvent(GitHubModel):
    """Select the instant a pull request was taken out of draft."""

    created_at: AwareDatetime = Field(alias="createdAt")


class GitHubTimelineItems(GitHubModel):
    """Select the ready-for-review events on one pull request's timeline."""

    nodes: tuple[GitHubReadyForReviewEvent, ...] = ()


class GitHubPullRequest(GitHubModel):
    """Select compact merged pull-request fields from a search response."""

    database_identifier: int = Field(alias="databaseId")
    number: int
    created_at: AwareDatetime = Field(alias="createdAt")
    merged_at: AwareDatetime = Field(alias="mergedAt")
    draft: bool = Field(alias="isDraft")
    timeline_items: GitHubTimelineItems = Field(default=GitHubTimelineItems(), alias="timelineItems")
    author: GitHubActor | None
    reviews: GitHubReviewConnection
    commits: GitHubCommitConnection
    title: str | None = None
    body: str | None = None
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = Field(default=None, alias="changedFiles")


class GitHubPullRequestSearch(GitHubModel):
    """Select one GraphQL pull-request search page."""

    issue_count: int = Field(alias="issueCount")
    nodes: tuple[GitHubPullRequest, ...]
    page_info: GitHubPageInfo = Field(alias="pageInfo")


class GitHubSearchData(GitHubModel):
    """Select the pull-request search connection from GraphQL data."""

    search: GitHubPullRequestSearch


class GitHubRepositoryPullRequest(GitHubModel):
    """Select one pull request containing a review continuation page."""

    reviews: GitHubReviewConnection


class GitHubRepository(GitHubModel):
    """Select a repository pull request from GraphQL data."""

    pull_request: GitHubRepositoryPullRequest | None = Field(alias="pullRequest")


class GitHubReviewData(GitHubModel):
    """Select a repository from a review continuation response."""

    repository: GitHubRepository | None


class GitHubRepositoryCheckPullRequest(GitHubModel):
    """Select one pull request containing a check continuation page."""

    commits: GitHubCommitConnection


class GitHubCheckRepository(GitHubModel):
    """Select a repository pull request from a check continuation response."""

    pull_request: GitHubRepositoryCheckPullRequest | None = Field(alias="pullRequest")


class GitHubCheckData(GitHubModel):
    """Select a repository from a check continuation response."""

    repository: GitHubCheckRepository | None


class GitHubCommitAuthor(GitHubModel):
    """Select the GitHub account behind one commit's Git authorship.

    `user` is null when the commit's email address matches no account, which is why an
    unattributable commit keeps a login of None rather than being dropped or blamed on someone.
    """

    user: GitHubActor | None = None


class GitHubAssociatedPullRequest(GitHubModel):
    """Select the number of a pull request that introduced one commit."""

    number: int


class GitHubAssociatedPullRequests(GitHubModel):
    """Select whether any pull request introduced one commit."""

    nodes: tuple[GitHubAssociatedPullRequest, ...]


class GitHubCommitRollup(GitHubModel):
    """Select the combined state of every check that ran on one commit."""

    state: str | None = None


class GitHubHistoryCommit(GitHubModel):
    """Select the compact fields needed to judge one default-branch commit."""

    oid: str
    committed_at: AwareDatetime = Field(alias="committedDate")
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = Field(default=None, alias="changedFilesIfAvailable")
    author: GitHubCommitAuthor | None = None
    associated_pull_requests: GitHubAssociatedPullRequests = Field(alias="associatedPullRequests")
    status_check_rollup: GitHubCommitRollup | None = Field(default=None, alias="statusCheckRollup")


class GitHubCommitHistory(GitHubModel):
    """Select one bounded page of default-branch history."""

    nodes: tuple[GitHubHistoryCommit, ...]
    page_info: GitHubPageInfo = Field(alias="pageInfo")


class GitHubHistoryTarget(GitHubModel):
    """Select the history reachable from the tip of the default branch."""

    history: GitHubCommitHistory


class GitHubDefaultBranchRef(GitHubModel):
    """Select the tip of one repository's default branch."""

    target: GitHubHistoryTarget | None = None


class GitHubHistoryRepository(GitHubModel):
    """Select the default branch from a history response."""

    default_branch_ref: GitHubDefaultBranchRef | None = Field(default=None, alias="defaultBranchRef")


class GitHubHistoryData(GitHubModel):
    """Select a repository from a default-branch history response."""

    repository: GitHubHistoryRepository | None


class GitHubIssueCount(GitHubModel):
    """Select the total count of one bounded search page, without reading its nodes."""

    issue_count: int = Field(alias="issueCount")


class GitHubOpenPullRequestData(GitHubModel):
    """Select the four open-pull-request search counts from one bundled GraphQL response."""

    opened_in_window: GitHubIssueCount = Field(alias="openedInWindow")
    closed_without_merge: GitHubIssueCount = Field(alias="closedWithoutMerge")
    currently_open: GitHubIssueCount = Field(alias="currentlyOpen")
    stale_open: GitHubIssueCount = Field(alias="staleOpen")


def check_context_selection() -> str:
    """Return the rollup context selection shared by the search and continuation queries."""
    return """
        __typename
        ... on CheckRun { name status conclusion completedAt }
        ... on StatusContext { context state createdAt }
    """


def pull_request_query() -> str:
    """Return the shallow merged pull-request search query.

    Page sizes are deliberately below GitHub's 100 maximum. Status-check rollups are expensive to
    compute, and a full page of pull requests each carrying a full page of reviews and rollup
    contexts times out on the server (502/504) rather than returning slowly.

    They are balanced, not minimised. Both nested connections page on overflow, so a page too small
    is not wrong, merely slow: every pull request exceeding it costs an extra sequential round trip,
    which dominates a cold backfill far more than page size does. Measured on cath-service, a
    90-day backfill of 182 pull requests took three minutes at `contexts(first: 20)` and forty
    seconds at 50, because its pull requests carry between 21 and 50 checks each and every one was
    paying for a follow-up query. Widen a connection if follow-up queries are frequent; narrow the
    search page if the server times out again.

    `body` widens `query_signature()` — on the pull request for traceability and description
    quality, and on each review for review depth: cached pull-request coverage stops matching and
    the next `collect` refetches the window. All three widenings land in one release, so they cost
    one refetch between them. Traceability needs the description text itself, unlike the count-only
    comment connection, but a body is plain text GitHub already returns on a node it is being asked
    for anyway — nothing here adds a round trip, and the review body is reduced to
    `ReviewFact.has_body` rather than cached.

    `timelineItems` widens the signature the same way and for the same price: no round trip, one
    refetch. It is filtered to `READY_FOR_REVIEW_EVENT` and to `first: 1`, so it returns the
    EARLIEST instant the change left draft and asked to be reviewed — the anchor the two waiting-time
    metrics need. `isDraft` alone could never answer this: it is the state at merge, which is false
    for every merged pull request, so a change that sat in draft for a fortnight was indistinguishable
    from one opened ready. A later conversion back to draft is deliberately not read: rework after
    review has begun is part of the cycle being measured, whereas the wait before anyone was asked to
    look is not.
    """
    return f"""
        query PullRequests($searchQuery: String!, $cursor: String) {{
          search(query: $searchQuery, type: ISSUE, first: 25, after: $cursor) {{
            issueCount
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              ... on PullRequest {{
                databaseId number title body createdAt mergedAt isDraft
                additions deletions changedFiles
                timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT], first: 1) {{
                  nodes {{ ... on ReadyForReviewEvent {{ createdAt }} }}
                }}
                author {{ login __typename }}
                reviews(first: 50) {{
                  pageInfo {{ hasNextPage endCursor }}
                  nodes {{ databaseId submittedAt state author {{ login __typename }} body comments(first: 0) {{ totalCount }} }}
                }}
                commits(last: 1) {{
                  nodes {{
                    commit {{
                      statusCheckRollup {{
                        contexts(first: 50) {{
                          pageInfo {{ hasNextPage endCursor }}
                          nodes {{ {check_context_selection()} }}
                        }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
          rateLimit {{ cost limit remaining resetAt }}
        }}
    """


def check_query() -> str:
    """Return the focused query used only for an overflowing status-check rollup."""
    return f"""
        query Checks($organization: String!, $repository: String!, $number: Int!, $cursor: String) {{
          repository(owner: $organization, name: $repository) {{
            pullRequest(number: $number) {{
              commits(last: 1) {{
                nodes {{
                  commit {{
                    statusCheckRollup {{
                      contexts(first: 50, after: $cursor) {{
                        pageInfo {{ hasNextPage endCursor }}
                        nodes {{ {check_context_selection()} }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
          rateLimit {{ cost limit remaining resetAt }}
        }}
    """


def review_query() -> str:
    """Return the focused query used only for an overflowing review connection."""
    return """
        query Reviews($organization: String!, $repository: String!, $number: Int!, $cursor: String) {
          repository(owner: $organization, name: $repository) {
            pullRequest(number: $number) {
              reviews(first: 100, after: $cursor) {
                pageInfo { hasNextPage endCursor }
                nodes { databaseId submittedAt state author { login __typename } body comments(first: 0) { totalCount } }
              }
            }
          }
          rateLimit { cost limit remaining resetAt }
        }
    """


def commit_history_query() -> str:
    """Return the default-branch history query used to find changes that skipped a pull request.

    `defaultBranchRef` resolves the branch server-side, so nothing here needs the branch name.
    History follows every parent rather than first parents alone, which is what makes
    `associatedPullRequests` the right discriminator: the commits a merge brought in carry their
    pull request, and only a commit no pull request introduced is left without one. GitHub returns
    the MERGED pull request that introduced a default-branch commit, never an open one, so an open
    pull request whose branch contains a commit cannot hide a direct push.

    `statusCheckRollup { state }` is the BARE SCALAR, and it is the whole reason this query is cheap.
    Fetching the `contexts` connection per direct commit cost one round trip each — measured at 159
    of the 173 calls a cath-service window took, 92% of the total — for check detail nothing reads.
    The rollup state is rejected for merged pull requests, where a check completing after the merge
    would repaint it; a direct commit is judged by conclusion rather than as at an instant, because
    nothing gated the push, so the objection does not apply. See architecture.md, "The mutable edge".

    `history(first: 50)` is deliberately below GitHub's 100. Each node makes the server compute a
    rollup, which is what returned 502/504 for the pull-request search in Iteration 2, and a 90-day
    cath-service window is only 14 pages at this size. Raise it if measurement says the pages are
    cheap; do not raise it and the contexts connection together.
    """
    return """
        query DefaultBranchCommits(
          $organization: String!
          $repository: String!
          $since: GitTimestamp!
          $until: GitTimestamp!
          $cursor: String
        ) {
          repository(owner: $organization, name: $repository) {
            defaultBranchRef {
              target {
                ... on Commit {
                  history(first: 50, since: $since, until: $until, after: $cursor) {
                    pageInfo { hasNextPage endCursor }
                    nodes {
                      oid committedDate additions deletions changedFilesIfAvailable
                      author { user { login __typename } }
                      associatedPullRequests(first: 1) { nodes { number } }
                      statusCheckRollup { state }
                    }
                  }
                }
              }
            }
          }
          rateLimit { cost limit remaining resetAt }
        }
    """


def open_pull_request_query() -> str:
    """Return the bundled open-pull-request state query.

    Four aliased searches share one round trip and read only `issueCount`, never a node: this state
    is NEVER CACHED (see architecture.md, "Open and unmerged pull requests"), so keeping the shape
    cheap and constant matters more than for a source that is fetched once and reused. `first: 1` is
    the minimum GitHub's search connection accepts; nothing under it is read.
    """
    return """
        query OpenPullRequests(
          $openedQuery: String!
          $closedWithoutMergeQuery: String!
          $openQuery: String!
          $staleOpenQuery: String!
        ) {
          openedInWindow: search(query: $openedQuery, type: ISSUE, first: 1) { issueCount }
          closedWithoutMerge: search(query: $closedWithoutMergeQuery, type: ISSUE, first: 1) { issueCount }
          currentlyOpen: search(query: $openQuery, type: ISSUE, first: 1) { issueCount }
          staleOpen: search(query: $staleOpenQuery, type: ISSUE, first: 1) { issueCount }
          rateLimit { cost limit remaining resetAt }
        }
    """


def open_pull_request_search_queries(
    organization: str,
    repository: str,
    window: ReportingWindow,
    stale_cutoff: datetime,
) -> dict[str, str]:
    """Build the four search qualifiers the open-pull-request query bundles into one call.

    Each bounded qualifier uses GitHub's inclusive `start..end` range, the same shape the merged
    search shards with, NOT a pair of `>=` / `<` comparisons. Two comparisons on one qualifier read
    as a half-open window but GitHub does not intersect them: measured against hmcts/cath-service on
    2026-08-14, `created:>=2026-05-09T00:00:00Z created:<2026-08-08T00:00:00Z` returned 614 pull
    requests for a window whose merged, closed and still-open counts together account for roughly
    240 — the count of every pull request the repository has ever opened, i.e. only the last
    qualifier applied. The window's exclusive upper bound is therefore expressed by ending the
    inclusive range one second early; GitHub search resolves timestamps to the second.
    """
    base = f"repo:{organization}/{repository} is:pr"
    starts_at = github_timestamp(window.starts_at)
    last_instant = github_timestamp(window.ends_at - timedelta(seconds=1))
    return {
        "openedQuery": f"{base} created:{starts_at}..{last_instant}",
        "closedWithoutMergeQuery": f"{base} is:closed -is:merged closed:{starts_at}..{last_instant}",
        "openQuery": f"{base} is:open",
        "staleOpenQuery": f"{base} is:open updated:<{github_timestamp(stale_cutoff)}",
    }


def parse_open_pull_request_state(data: dict[str, object]) -> OpenPullRequestSummary:
    """Parse one bundled open-pull-request response or report invalid evidence."""
    try:
        parsed = GitHubOpenPullRequestData.model_validate(data)
    except ValidationError as exception:
        message = "GitHub returned invalid open pull-request data"
        raise GitHubError(message, reason=AvailabilityReason.COLLECTION_FAILED) from exception
    return OpenPullRequestSummary(
        opened_in_window=parsed.opened_in_window.issue_count,
        closed_without_merge=parsed.closed_without_merge.issue_count,
        currently_open=parsed.currently_open.issue_count,
        stale_open=parsed.stale_open.issue_count,
    )


def collect_open_pull_request_state(
    client: GitHubClient,
    organization: str,
    repository: str,
    window: ReportingWindow,
    stale_open_days: int,
    reference: datetime,
) -> OpenPullRequestSummary:
    """Fetch open pull-request counts in one call, fresh every time. Never cached — see architecture.md.

    `stale_open` is measured from each pull request's LAST UPDATE, not from when it was opened, per
    `lookback.stale_open_days`.
    """
    stale_cutoff = reference - timedelta(days=stale_open_days)
    queries = open_pull_request_search_queries(organization, repository, window, stale_cutoff)
    return parse_open_pull_request_state(client.graphql(open_pull_request_query(), queries))


def query_signature() -> str:
    """Identify the shape of the data these queries cache, so widening them invalidates the cache."""
    documents = " ".join((pull_request_query() + review_query() + check_query()).split())
    return sha256(documents.encode()).hexdigest()[:16]


def commit_query_signature() -> str:
    """Identify the shape of the cached commit data, separately from the pull-request queries.

    Its own signature so that widening one source does not discard the other's settled history: the
    two are cached under different `EvidenceSource` values and are refetched independently.
    """
    documents = " ".join(commit_history_query().split())
    return sha256(documents.encode()).hexdigest()[:16]


def date_shards(starts_at: datetime, ends_at: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Split a collection window into bounded search intervals."""
    cursor = starts_at
    while cursor < ends_at:
        boundary = min(cursor + timedelta(days=30), ends_at)
        yield cursor, boundary
        cursor = boundary


def github_timestamp(value: datetime) -> str:
    """Format a UTC timestamp for a GitHub search qualifier."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_search(data: dict[str, object]) -> GitHubPullRequestSearch:
    """Parse one GitHub pull-request search page or report invalid evidence."""
    try:
        return GitHubSearchData.model_validate(data).search
    except ValidationError as exception:
        message = "GitHub returned invalid pull-request behaviour data"
        raise GitHubError(message, reason=AvailabilityReason.COLLECTION_FAILED) from exception


def parse_reviews(data: dict[str, object]) -> GitHubReviewConnection:
    """Parse one review continuation page or report missing evidence."""
    try:
        repository = GitHubReviewData.model_validate(data).repository
    except ValidationError as exception:
        message = "GitHub returned invalid review behaviour data"
        raise GitHubError(message, reason=AvailabilityReason.COLLECTION_FAILED) from exception
    if repository is None or repository.pull_request is None:
        message = "GitHub omitted a pull request while collecting review behaviour"
        raise GitHubError(message, reason=AvailabilityReason.COLLECTION_FAILED)
    return repository.pull_request.reviews


def head_commit_checks(commits: GitHubCommitConnection) -> GitHubCheckConnection | None:
    """Return the rollup contexts of a pull request's head commit, if it has any."""
    if not commits.nodes or commits.nodes[0].commit.status_check_rollup is None:
        return None
    return commits.nodes[0].commit.status_check_rollup.contexts


def parse_checks(data: dict[str, object]) -> GitHubCheckConnection:
    """Parse one status-check continuation page or report missing evidence."""
    try:
        repository = GitHubCheckData.model_validate(data).repository
    except ValidationError as exception:
        message = "GitHub returned invalid status-check data"
        raise GitHubError(message, reason=AvailabilityReason.COLLECTION_FAILED) from exception
    connection = head_commit_checks(repository.pull_request.commits) if repository and repository.pull_request else None
    if connection is None:
        message = "GitHub omitted a pull request while collecting status checks"
        raise GitHubError(message, reason=AvailabilityReason.COLLECTION_FAILED)
    return connection


def status_conclusion(state: str) -> CheckConclusion | None:
    """Map a legacy commit-status state onto a check conclusion."""
    return {
        "SUCCESS": CheckConclusion.SUCCESS,
        "FAILURE": CheckConclusion.FAILURE,
        "ERROR": CheckConclusion.FAILURE,
    }.get(state)


def check_fact(context: GitHubCheckContext) -> CheckFact:
    """Normalise a check run or a legacy status context into one compact fact.

    A check that has not settled carries no conclusion and no completion instant, so a check still
    running at merge stays distinguishable from one that finished and failed.
    """
    if context.typename == "CheckRun":
        completed = context.status == "COMPLETED"
        return CheckFact(
            name=context.name or "",
            conclusion=CheckConclusion(context.conclusion) if completed and context.conclusion else None,
            completed_at=context.completed_at if completed else None,
        )
    conclusion = status_conclusion(context.state or "")
    return CheckFact(
        name=context.context or "",
        conclusion=conclusion,
        completed_at=context.created_at if conclusion is not None else None,
    )


def collect_checks(
    client: GitHubClient,
    organization: str,
    repository: str,
    number: int,
    connection: GitHubCheckConnection | None,
) -> tuple[CheckFact, ...]:
    """Collect all rollup pages, issuing follow-ups only after a bounded connection overflows."""
    if connection is None:
        return ()
    checks = tuple(check_fact(context) for context in connection.nodes)
    page_info = connection.page_info
    while page_info.has_next_page:
        data = client.graphql(
            check_query(),
            {
                "organization": organization,
                "repository": repository,
                "number": number,
                "cursor": page_info.end_cursor,
            },
        )
        connection = parse_checks(data)
        checks += tuple(check_fact(context) for context in connection.nodes)
        page_info = connection.page_info
    return checks


def parse_commit_history(data: dict[str, object]) -> GitHubCommitHistory | None:
    """Parse one history page, returning None for a repository with no default branch to walk."""
    try:
        repository = GitHubHistoryData.model_validate(data).repository
    except ValidationError as exception:
        message = "GitHub returned invalid default-branch history"
        raise GitHubError(message, reason=AvailabilityReason.COLLECTION_FAILED) from exception
    if repository is None:
        message = "GitHub omitted a repository while collecting default-branch history"
        raise GitHubError(message, reason=AvailabilityReason.COLLECTION_FAILED)
    if repository.default_branch_ref is None or repository.default_branch_ref.target is None:
        return None
    return repository.default_branch_ref.target.history


def direct_commit_fact(commit: GitHubHistoryCommit) -> DirectCommitFact:
    """Convert one unassociated history commit into a compact fact.

    Everything needed comes back on the history node itself, so a direct commit costs no call of its
    own. A commit no check ever ran on has no rollup, which stays None rather than becoming a state.
    """
    account = commit.author.user if commit.author else None
    rollup = commit.status_check_rollup
    return DirectCommitFact(
        sha=commit.oid,
        committed_at=commit.committed_at,
        author_login=account.login if account else None,
        author_type=account.typename if account else None,
        additions=commit.additions,
        deletions=commit.deletions,
        changed_files=commit.changed_files,
        check_state=CheckRollupState(rollup.state) if rollup and rollup.state else None,
    )


def collect_direct_commits(
    client: GitHubClient,
    organization: str,
    repository: str,
    starts_at: datetime,
    ends_at: datetime,
) -> tuple[DirectCommitFact, ...]:
    """Collect the default-branch commits in a window that no pull request introduced.

    GitHub's `since` and `until` bound the walk but are inclusive, so membership is decided again
    here against the half-open window every other cohort is built from.
    """
    facts: dict[str, DirectCommitFact] = {}
    cursor: str | None = None
    while True:
        history = parse_commit_history(
            client.graphql(
                commit_history_query(),
                {
                    "organization": organization,
                    "repository": repository,
                    "since": github_timestamp(starts_at),
                    "until": github_timestamp(ends_at),
                    "cursor": cursor,
                },
            ),
        )
        if history is None:
            break
        facts.update(
            (commit.oid, direct_commit_fact(commit))
            for commit in history.nodes
            if not commit.associated_pull_requests.nodes and starts_at <= commit.committed_at < ends_at
        )
        if not history.page_info.has_next_page:
            break
        cursor = history.page_info.end_cursor
    return tuple(sorted(facts.values(), key=lambda fact: (fact.committed_at, fact.sha)))


def review_fact(review: GitHubReview) -> ReviewFact:
    """Convert one GitHub review into a compact internal fact."""
    return ReviewFact(
        identifier=review.database_identifier,
        submitted_at=review.submitted_at,
        state=review.state,
        author_login=review.author.login if review.author else None,
        author_type=review.author.typename if review.author else None,
        comment_count=review.comments.total_count,
        has_body=bool((review.body or "").strip()),
    )


def collect_reviews(
    client: GitHubClient,
    organization: str,
    repository: str,
    number: int,
    connection: GitHubReviewConnection,
) -> tuple[ReviewFact, ...]:
    """Collect all review pages, issuing follow-ups only after a bounded connection overflows."""
    reviews = tuple(review_fact(review) for review in connection.nodes)
    page_info = connection.page_info
    while page_info.has_next_page:
        data = client.graphql(
            review_query(),
            {
                "organization": organization,
                "repository": repository,
                "number": number,
                "cursor": page_info.end_cursor,
            },
        )
        connection = parse_reviews(data)
        reviews += tuple(review_fact(review) for review in connection.nodes)
        page_info = connection.page_info
    return reviews


def pull_request_fact(
    client: GitHubClient,
    organization: str,
    repository: str,
    pull_request: GitHubPullRequest,
) -> PullRequestFact:
    """Convert one GitHub pull request and its complete reviews into a compact fact."""
    return PullRequestFact(
        identifier=pull_request.database_identifier,
        repository=repository,
        number=pull_request.number,
        created_at=pull_request.created_at,
        merged_at=pull_request.merged_at,
        draft=pull_request.draft,
        ready_for_review_at=next(
            (event.created_at for event in pull_request.timeline_items.nodes),
            None,
        ),
        author_login=pull_request.author.login if pull_request.author else None,
        author_type=pull_request.author.typename if pull_request.author else None,
        reviews=collect_reviews(client, organization, repository, pull_request.number, pull_request.reviews),
        title=pull_request.title,
        body=pull_request.body,
        additions=pull_request.additions,
        deletions=pull_request.deletions,
        changed_files=pull_request.changed_files,
        checks=collect_checks(
            client,
            organization,
            repository,
            pull_request.number,
            head_commit_checks(pull_request.commits),
        ),
    )


def page_facts(
    client: GitHubClient,
    organization: str,
    repository: str,
    search: GitHubPullRequestSearch,
    starts_at: datetime,
    ends_at: datetime,
) -> tuple[PullRequestFact, ...]:
    """Convert the in-window pull requests from one search page into facts."""
    return tuple(
        pull_request_fact(client, organization, repository, pull_request)
        for pull_request in search.nodes
        if starts_at <= pull_request.merged_at < ends_at
    )


def collect_merged_pull_requests(
    client: GitHubClient,
    organization: str,
    repository: str,
    starts_at: datetime,
    ends_at: datetime,
) -> tuple[PullRequestFact, ...]:
    """Collect merged pull requests in date shards and deduplicate inclusive search boundaries."""
    facts: dict[int, PullRequestFact] = {}
    search_result_limit = 1000
    for shard_start, shard_end in date_shards(starts_at, ends_at):
        page_cursor: str | None = None
        while True:
            search = parse_search(
                client.graphql(
                    pull_request_query(),
                    {
                        "searchQuery": (
                            f"repo:{organization}/{repository} is:pr is:merged "
                            f"merged:{github_timestamp(shard_start)}..{github_timestamp(shard_end)}"
                        ),
                        "cursor": page_cursor,
                    },
                ),
            )
            if search.issue_count > search_result_limit:
                message = "GitHub pull-request search exceeded its 1,000-result limit"
                raise GitHubError(message, reason=AvailabilityReason.INCOMPLETE_HISTORY)
            facts.update(
                (fact.identifier, fact)
                for fact in page_facts(client, organization, repository, search, starts_at, ends_at)
            )
            if not search.page_info.has_next_page:
                break
            page_cursor = search.page_info.end_cursor
    return tuple(sorted(facts.values(), key=lambda fact: (fact.merged_at, fact.identifier)))


class FactCache[FactT](Protocol):
    """Replace one cached interval of collected facts."""

    def __call__(self, path: Path, coverage: SourceCoverage, facts: tuple[FactT, ...], *, complete: bool) -> None:
        """Replace the facts cached for one interval, recording coverage only for a complete one."""


def mutable_edge(configuration: Configuration, window: ReportingWindow, reference: datetime) -> datetime:
    """Return the instant a window stops being settled history.

    A merge is anchored to the instant it reached the branch, so this only absorbs GitHub's eventually
    consistent search index and checks still running on a very recent change.
    """
    return max(
        window.starts_at,
        min(window.ends_at, reference - timedelta(hours=configuration.lookback.mutable_hours)),
    )


def fill_cached_source[FactT](
    database: Path,
    requested: SourceCoverage,
    mutable_starts_at: datetime,
    collect: Callable[[datetime, datetime], tuple[FactT, ...]],
    cache: FactCache[FactT],
) -> tuple[SourceCoverage, ...]:
    """Fill one source's missing stable history, refresh its mutable edge, and report what was fetched.

    Only the stable side records coverage, which is what makes `--offline` refuse a window reaching
    into the mutable edge rather than serving a remembered answer for it.
    """
    missing_intervals: tuple[SourceCoverage, ...] = ()
    if requested.starts_at < mutable_starts_at:
        stable = requested.model_copy(update={"ends_at": mutable_starts_at})
        missing_intervals = find_missing_cached_coverage(database, stable)
        for missing in missing_intervals:
            cache(database, missing, collect(missing.starts_at, missing.ends_at), complete=True)
    if mutable_starts_at < requested.ends_at:
        mutable = requested.model_copy(update={"starts_at": mutable_starts_at})
        cache(database, mutable, collect(mutable.starts_at, mutable.ends_at), complete=False)
    return missing_intervals


def requested_coverage(
    configuration: Configuration,
    repository: str,
    window: ReportingWindow,
    source: EvidenceSource,
) -> SourceCoverage:
    """Describe the coverage one window requires from one independently cached source."""
    signatures = {EvidenceSource.PULL_REQUEST: query_signature, EvidenceSource.COMMIT: commit_query_signature}
    return SourceCoverage(
        organization=configuration.organization,
        repository=repository,
        source=source,
        query_hash=signatures[source](),
        starts_at=window.starts_at,
        ends_at=window.ends_at,
    )


def cache_status(missing_intervals: tuple[SourceCoverage, ...], window: ReportingWindow) -> CacheStatus:
    """Describe how much of one window's stable history the cache already held."""
    if not missing_intervals:
        return CacheStatus.FULLY_REUSED
    if len(missing_intervals) == 1 and missing_intervals[0].starts_at == window.starts_at:
        return CacheStatus.FETCHED
    return CacheStatus.PARTIALLY_REUSED


def synchronize_pull_request_facts(
    configuration: Configuration,
    client: GitHubClient,
    repository: str,
    window: ReportingWindow,
    reference: datetime,
) -> tuple[tuple[PullRequestFact, ...], tuple[SourceCoverage, ...]]:
    """Fill missing merged pull-request history and refresh the mutable edge for one window."""
    requested = requested_coverage(configuration, repository, window, EvidenceSource.PULL_REQUEST)
    missing_intervals = fill_cached_source(
        configuration.database,
        requested,
        mutable_edge(configuration, window, reference),
        lambda starts_at, ends_at: collect_merged_pull_requests(
            client,
            configuration.organization,
            repository,
            starts_at,
            ends_at,
        ),
        cache_pull_request_facts,
    )
    return load_cached_pull_request_facts(configuration.database, requested), missing_intervals


def synchronize_direct_commit_facts(
    configuration: Configuration,
    client: GitHubClient,
    repository: str,
    window: ReportingWindow,
    reference: datetime,
) -> tuple[tuple[DirectCommitFact, ...], tuple[SourceCoverage, ...]]:
    """Fill missing direct-commit history and refresh the mutable edge for one window."""
    requested = requested_coverage(configuration, repository, window, EvidenceSource.COMMIT)
    missing_intervals = fill_cached_source(
        configuration.database,
        requested,
        mutable_edge(configuration, window, reference),
        lambda starts_at, ends_at: collect_direct_commits(
            client,
            configuration.organization,
            repository,
            starts_at,
            ends_at,
        ),
        cache_direct_commit_facts,
    )
    return load_cached_direct_commit_facts(configuration.database, requested), missing_intervals


def synchronize_merges(
    configuration: Configuration,
    client: GitHubClient,
    repository: str,
    window: ReportingWindow,
    reference: datetime,
) -> tuple[Merges, BehaviourProvenance]:
    """Fill the cache for both routes a change can take onto the default branch."""
    pull_requests, pull_request_intervals = synchronize_pull_request_facts(
        configuration,
        client,
        repository,
        window,
        reference,
    )
    direct_commits, commit_intervals = synchronize_direct_commit_facts(
        configuration,
        client,
        repository,
        window,
        reference,
    )
    return Merges(pull_requests=pull_requests, direct_commits=direct_commits), BehaviourProvenance(
        stable_history=cache_status(pull_request_intervals, window),
        stable_intervals_fetched=len(pull_request_intervals),
        mutable_starts_at=mutable_edge(configuration, window, reference),
        pull_request_facts_loaded=len(pull_requests),
        review_facts_loaded=sum(len(fact.reviews) for fact in pull_requests),
        direct_commit_facts_loaded=len(direct_commits),
        direct_commit_intervals_fetched=len(commit_intervals),
    )


def collect_repository_window(
    configuration: Configuration,
    client: GitHubClient,
    item: RepositoryInventoryItem,
    window: ReportingWindow,
    reference: datetime,
) -> BehaviourProvenance | RepositoryInventoryIssue:
    """Fill one repository's cache for a window while preserving structured unavailability."""
    try:
        return synchronize_merges(configuration, client, item.repository.name, window, reference)[1]
    except GitHubError as exception:
        return RepositoryInventoryIssue(
            team_identifier=item.team_identifier,
            repository=item.repository.name,
            evidence=EvidenceKind.BEHAVIOUR,
            reason=exception.reason,
            detail=str(exception),
        )


def window_progress(result: BehaviourProvenance | RepositoryInventoryIssue) -> str:
    """Say in one clause how one repository's window was satisfied, or why it was not.

    The intervals of both windowed sources are counted TOGETHER and the reuse verdict is taken from
    the total rather than from `stable_history` alone: pull requests and direct commits are cached
    under separate coverage, so a repository can reuse every settled pull-request interval while
    still paying for commit ones, and reporting that as "fully reused" would tell a reader the
    window cost nothing when the figures beside it say otherwise.
    """
    if isinstance(result, RepositoryInventoryIssue):
        return f"window unavailable: {result.reason.value.replace('_', ' ')}"
    intervals = result.stable_intervals_fetched + result.direct_commit_intervals_fetched
    if not intervals:
        return "window fully reused"
    reuse = "fetched" if result.stable_history is CacheStatus.FETCHED else "partially reused"
    return f"window {reuse} {intervals} interval{'' if intervals == 1 else 's'}"


def collect_window(
    configuration: Configuration,
    client: GitHubClient,
    inventory: RepositoryInventory,
    window: ReportingWindow,
    reference: datetime,
) -> RepositoryInventory:
    """Fill the pull-request cache for every inventoried repository over one collection window.

    The window is the expensive half of a run, so each repository's cost here is ADDED to what its
    current state already cost rather than replacing it: a reader wants what the repository cost,
    not what one phase of it did.
    """
    results: list[tuple[RepositoryInventoryItem, BehaviourProvenance | RepositoryInventoryIssue]] = []
    costs = inventory.costs
    for position, item in enumerate(inventory.repositories, start=1):
        meter = CostMeter(client)
        with meter.measure():
            result = collect_repository_window(configuration, client, item, window, reference)
        results.append((item, result))
        cost = meter.cost(item.repository.name)
        log_progress(position, len(inventory.repositories), cost, window_progress(result))
        costs += (cost,)
    repositories = tuple(
        item.model_copy(update={"collection": result}) if isinstance(result, BehaviourProvenance) else item
        for item, result in results
    )
    failures = inventory.failures + tuple(
        result for _, result in results if isinstance(result, RepositoryInventoryIssue)
    )
    if not failures:
        status = CollectionStatus.COMPLETE
    elif repositories:
        status = CollectionStatus.PARTIAL
    else:
        status = CollectionStatus.FAILED
    return inventory.model_copy(
        update={
            "status": status,
            "repositories": repositories,
            "failures": failures,
            "costs": combined(costs),
        },
    )
