"""Calculate neutral aggregate evidence from cached facts."""

from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from datetime import datetime
from typing import Protocol

from metrics.domain import (
    CheckConclusion,
    CheckFact,
    DistributionObservation,
    ObservationStatus,
    PullRequestFact,
    RateObservation,
    ReviewFact,
    ReviewState,
)


class Merge(Protocol):
    """Describe what any merge into the default branch can be asked, by either route.

    A merged pull request and a direct commit are different evidence and stay separate models, but
    attribution and size are asked of both, so the predicates answering those two questions are
    structural rather than duplicated per model.

    Attribution is BOTH fields: a login says who, and the account type says whether that who is a
    person. A caller counting people needs the pair, and a protocol carrying only the login would
    push each caller into reading the type off the concrete model it was handed.
    """

    author_login: str | None
    author_type: str | None
    additions: int | None
    deletions: int | None
    changed_files: int | None


def percentile(values: Sequence[float], proportion: float) -> float:
    """Return a linearly interpolated percentile from a non-empty sample."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def is_human_account(login: str | None, account_type: str | None) -> bool:
    """Report whether an authored artefact came from a person rather than from a machine account.

    Two independent signals, because neither settles it alone: GitHub types an account as `Bot` only
    where it is a GitHub App, so an ordinary user account driven by automation is typed `User` and
    gives itself away instead by the `[bot]` suffix convention its login follows. An anonymous
    author — a commit GitHub could match to no account at all — is nobody, so it is not a person
    either.

    One implementation, shared by the review predicate and the contributor count: two spellings of
    "is this a bot" would eventually disagree about the same account, and a report would then say a
    review was human and its author was not.
    """
    return login is not None and account_type != "Bot" and not login.casefold().endswith("[bot]")


def is_human_review(review: ReviewFact) -> bool:
    """Report whether a review has an attributable non-bot author."""
    return is_human_account(review.author_login, review.author_type)


def contributor_logins(changes: Iterable[Merge]) -> frozenset[str]:
    """Return the distinct people who authored the given merges, leaving out every bot account.

    Case-folded, because a GitHub login is unique case-insensitively: `Alice` and `alice` are one
    person, and two spellings of one login would count as two contributors.

    Bots are excluded here even though the cohort deliberately KEEPS agent-authored merges — see
    `cohort.excluded_authors`, which drops dependency automation and nothing else. The two rules
    answer different questions and are meant to differ: agent-authored work is work this report
    covers, and an agent is still not a person who became active.
    """
    return frozenset(
        login.casefold()
        for change in changes
        if (login := change.author_login) is not None and is_human_account(login, change.author_type)
    )


def is_human_commit_author(
    login: str | None,
    account_type: str | None,
    author_name: str | None,
    excluded: Collection[str],
) -> bool:
    """Report whether one commit was authored by a person maintaining the repository.

    LINKED ACCOUNT FIRST: when GitHub matched the commit's authorship to an account, that account
    settles the answer — it must pass `is_human_account` (no `Bot` type, no `[bot]` login suffix)
    and its login must not match `cohort.excluded_authors` under the same normalisation the cohort
    uses. When GitHub links no account, the git author NAME is tested against the same two checks
    instead, and otherwise counts as human. That fallback is a stated limitation: a name is whatever
    the committer's tooling wrote, so automation signing an unrecognisable name reads as a person,
    and a person whose commit email matches no account is judged by their name alone.

    Deliberately wider than `cohort.excluded_authors` on its own: ALL bot accounts fail, not just
    dependency automation. An agent-authored commit is work the cohort keeps, but it is not a person
    maintaining the repository — the same distinction `contributor_logins` draws.
    """
    identity = login if login is not None else author_name
    linked_type = account_type if login is not None else None
    return is_human_account(identity, linked_type) and comparable_login(identity) not in excluded


def comparable_login(login: str | None) -> str:
    """Return a login without GitHub's bot suffix, for configuration matching."""
    return (login or "").casefold().removesuffix("[bot]")


def in_cohort(change: Merge, excluded_authors: Collection[str]) -> bool:
    """Report whether a merge belongs to the reported cohort."""
    return comparable_login(change.author_login) not in excluded_authors


def excluded_authors(logins: Iterable[str]) -> frozenset[str]:
    """Build the comparable set of authors excluded from the cohort."""
    return frozenset(comparable_login(login) for login in logins)


def eligible_reviews(pull_request: PullRequestFact) -> tuple[ReviewFact, ...]:
    """Return submitted human reviews made by someone other than the pull-request author before merge."""
    return tuple(
        review
        for review in pull_request.reviews
        if review.submitted_at <= pull_request.merged_at
        and review.state is not ReviewState.PENDING
        and is_human_review(review)
        and review.author_login is not None
        and review.author_login.casefold() != (pull_request.author_login or "").casefold()
    )


def review_started_at(pull_request: PullRequestFact) -> datetime:
    """Return the instant one pull request entered review, which is what a waiting time is measured from.

    NOT `created_at`. A change opened as a draft and worked on for a fortnight has not been waiting
    for anybody during that fortnight, and counting the draft period made the two waiting-time
    metrics measure how long a branch existed rather than how long a finished change waited. The
    anchor is the earliest ready-for-review event GitHub recorded.

    An eligible review submitted before that event overrides it. GitHub allows reviewing a draft, and
    where someone did, review demonstrably began then — taking the earlier of the two keeps that
    honest and is also what stops a wait from coming out negative.

    Falls back to `created_at` when neither is available: a pull request opened ready has no event,
    and so does a fact cached before the event was collected. Both mean "nothing better is known",
    which is the same answer the metrics gave before.
    """
    anchor = pull_request.ready_for_review_at or pull_request.created_at
    earliest_review = min((review.submitted_at for review in eligible_reviews(pull_request)), default=None)
    return min(anchor, earliest_review) if earliest_review else anchor


def review_state_counts(pull_requests: Iterable[PullRequestFact]) -> dict[str, int]:
    """Count one cohort's review events by the state they were submitted in.

    Every review event is counted, eligible or not: the breakdown exists to show what reviewing
    looked like, and filtering it to eligible reviews would answer the coverage question twice.
    """
    states = Counter(review.state.value for pull_request in pull_requests for review in pull_request.reviews)
    return dict(sorted(states.items()))


def eligible_checks(pull_request: PullRequestFact) -> tuple[CheckFact, ...]:
    """Return the checks that had finished by the merge instant.

    A check completing after a merge is not evidence the merge gate saw it, exactly as a review
    submitted after a merge is ineligible. This is what keeps settled history immutable.
    """
    return tuple(
        check
        for check in pull_request.checks
        if check.completed_at is not None and check.completed_at <= pull_request.merged_at
    )


def is_passing_check(check: CheckFact) -> bool:
    """Report whether one completed check left the merge unblocked.

    Neutral and skipped checks do not block a merge, so GitHub's own rollup treats them as passing.
    """
    return check.conclusion in {CheckConclusion.SUCCESS, CheckConclusion.NEUTRAL, CheckConclusion.SKIPPED}


def change_size(change: Merge) -> tuple[int, int] | None:
    """Return one change's lines and files, or None when GitHub did not size it."""
    if change.additions is None or change.deletions is None or change.changed_files is None:
        return None
    return change.additions + change.deletions, change.changed_files


def size_class(change: Merge, maximum_lines: int, maximum_files: int) -> str:
    """Classify a change as trivial, substantial, or unsized.

    An unsized change stays its own class rather than defaulting either way, so a missing size never
    silently excuses an unreviewed merge nor inflates the substantial count.
    """
    measured = change_size(change)
    if measured is None:
        return "unsized"
    lines, files = measured
    return "trivial" if lines <= maximum_lines and files <= maximum_files else "substantial"


def rate(numerator: int, denominator: int) -> RateObservation:
    """Build an observed rate or an explicit not-applicable result."""
    return RateObservation(
        status=ObservationStatus.OBSERVED if denominator else ObservationStatus.NOT_APPLICABLE,
        numerator=numerator,
        denominator=denominator,
    )


def rate_percentage(observation: RateObservation) -> float | None:
    """Return one rate as a percentage to the tenth, or None when there was no denominator.

    One implementation, shared by the readiness assessment that phrases it and the trend that
    compares it between windows: two roundings of the same rate would eventually disagree in the
    last digit, and the two reports would then describe the same window differently.
    """
    if observation.status is not ObservationStatus.OBSERVED:
        return None
    return round(observation.numerator / observation.denominator * 100, 1)


def distribution(values: Sequence[float], unit: str) -> DistributionObservation:
    """Build selected percentiles for one sample, or an explicit not-applicable result."""
    if not values:
        return DistributionObservation(
            status=ObservationStatus.NOT_APPLICABLE,
            sample_size=0,
            unit=unit,
            median=None,
            percentile_75=None,
            percentile_90=None,
        )
    return DistributionObservation(
        status=ObservationStatus.OBSERVED,
        sample_size=len(values),
        unit=unit,
        median=percentile(values, 0.5),
        percentile_75=percentile(values, 0.75),
        percentile_90=percentile(values, 0.9),
    )
