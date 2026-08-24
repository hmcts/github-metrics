"""Test pure behaviour evidence calculations."""

from datetime import UTC, datetime, timedelta

from metrics.analysis import contributor_logins, review_state_counts
from metrics.behaviour_metrics import behaviour_metrics
from metrics.behaviour_metrics.approval_coverage import ApprovalCoverage
from metrics.behaviour_metrics.checks_passing_at_merge import ChecksPassingAtMerge
from metrics.behaviour_metrics.description_quality import DescriptionQuality
from metrics.behaviour_metrics.independent_review_coverage import IndependentReviewCoverage
from metrics.behaviour_metrics.merge_cycle_time import MergeCycleTime
from metrics.behaviour_metrics.review_depth import ReviewDepth
from metrics.behaviour_metrics.time_to_first_review import TimeToFirstReview
from metrics.behaviour_metrics.traceability_reference import TraceabilityReference
from metrics.config import TraceabilityConfiguration
from metrics.domain import (
    CheckConclusion,
    CheckFact,
    CheckRollupState,
    DirectCommitFact,
    DistributionObservation,
    Merges,
    ObservationStatus,
    PullRequestFact,
    RateObservation,
    ReviewFact,
    ReviewState,
)


def review(
    identifier: int,
    submitted_at: datetime,
    state: ReviewState,
    author_login: str | None,
    author_type: str | None = "User",
    comment_count: int = 0,
    *,
    has_body: bool = False,
) -> ReviewFact:
    """Build one review fact for a calculation test."""
    return ReviewFact(
        identifier=identifier,
        submitted_at=submitted_at,
        state=state,
        author_login=author_login,
        author_type=author_type,
        comment_count=comment_count,
        has_body=has_body,
    )


def pull_request(
    identifier: int,
    created_at: datetime,
    merged_at: datetime,
    reviews: tuple[ReviewFact, ...] = (),
    checks: tuple[CheckFact, ...] = (),
    title: str | None = None,
    body: str | None = None,
) -> PullRequestFact:
    """Build one merged pull-request fact for a calculation test."""
    return PullRequestFact(
        identifier=identifier,
        repository="cath-service",
        number=identifier,
        created_at=created_at,
        merged_at=merged_at,
        draft=False,
        author_login="author",
        author_type="User",
        title=title,
        body=body,
        reviews=reviews,
        checks=checks,
    )


def check(name: str, conclusion: CheckConclusion | None, completed_at: datetime | None) -> CheckFact:
    """Build one status-check fact for a calculation test."""
    return CheckFact(name=name, conclusion=conclusion, completed_at=completed_at)


def cohort(
    facts: tuple[PullRequestFact, ...] = (),
    direct_commits: tuple[DirectCommitFact, ...] = (),
) -> Merges:
    """Build one merge cohort for a calculation test."""
    return Merges(pull_requests=facts, direct_commits=direct_commits)


def commit(
    sha: str,
    committed_at: datetime,
    check_state: CheckRollupState | None = None,
    author_login: str = "author",
) -> DirectCommitFact:
    """Build one direct-commit fact for a calculation test."""
    return DirectCommitFact(
        sha=sha,
        committed_at=committed_at,
        author_login=author_login,
        author_type="User",
        check_state=check_state,
    )


def observations(facts: tuple[PullRequestFact, ...]) -> dict[str, RateObservation | DistributionObservation]:
    """Summarise every registered metric over one merged cohort."""
    metrics = behaviour_metrics(TraceabilityConfiguration())
    return {metric.identifier: metric.summary(cohort(facts)) for metric in metrics}


def test_metric_summaries_exclude_author_and_bot_reviews() -> None:
    """Count only independent human review activity submitted before merge."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=10)
    facts = (
        pull_request(
            1,
            starts_at,
            merged_at,
            (
                review(11, merged_at - timedelta(days=2), ReviewState.APPROVED, "author"),
                review(12, merged_at - timedelta(days=1), ReviewState.APPROVED, "dependabot[bot]", "Bot"),
            ),
        ),
        pull_request(
            2,
            merged_at - timedelta(hours=4),
            merged_at + timedelta(days=1),
            (review(21, merged_at, ReviewState.COMMENTED, "reviewer"),),
        ),
        pull_request(
            3,
            merged_at,
            merged_at + timedelta(days=2),
            (review(31, merged_at + timedelta(days=1), ReviewState.APPROVED, "reviewer"),),
        ),
    )

    summaries = observations(facts)
    coverage = summaries["independent-review-coverage"]
    approval = summaries["approval-coverage"]
    cycle = summaries["merge-cycle-time"]

    assert isinstance(coverage, RateObservation)
    assert isinstance(approval, RateObservation)
    assert isinstance(cycle, DistributionObservation)
    assert coverage.numerator == 2
    assert coverage.denominator == 3
    assert approval.numerator == 1
    assert cycle.sample_size == 3
    assert cycle.median == 48
    assert cycle.percentile_75 == 144
    assert cycle.percentile_90 == 201.6


def test_checks_passing_at_merge_classifies_the_state_at_the_merge_instant() -> None:
    """Judge only checks that had finished by merge, so a later completion cannot repaint history."""
    merged_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    before = merged_at - timedelta(minutes=30)
    after = merged_at + timedelta(minutes=30)
    metric = ChecksPassingAtMerge()
    facts = (
        pull_request(1, merged_at, merged_at, checks=(check("build", CheckConclusion.SUCCESS, before),)),
        pull_request(2, merged_at, merged_at, checks=(check("build", CheckConclusion.FAILURE, before),)),
        pull_request(3, merged_at, merged_at, checks=(check("build", None, None),)),
        pull_request(4, merged_at, merged_at),
        # A check that went green only after the merge is not evidence the gate ever saw it green.
        pull_request(5, merged_at, merged_at, checks=(check("build", CheckConclusion.SUCCESS, after),)),
        pull_request(6, merged_at, merged_at, checks=(check("lint", CheckConclusion.SKIPPED, before),)),
    )

    classifications = tuple(metric.classification(fact) for fact in facts)
    summary = metric.summary(cohort(facts))

    assert classifications == (
        "passing",
        "failing",
        "incomplete-at-merge",
        "no-checks",
        "incomplete-at-merge",
        "passing",
    )
    assert summary.numerator == 2
    assert summary.denominator == 6


def test_metric_summaries_report_empty_cohort_as_not_applicable() -> None:
    """Do not present an absent merged cohort as observed zero behaviour."""
    summaries = observations(())
    cycle = summaries["merge-cycle-time"]

    assert isinstance(cycle, DistributionObservation)
    assert cycle.status is ObservationStatus.NOT_APPLICABLE
    assert cycle.median is None
    assert summaries["independent-review-coverage"].status is ObservationStatus.NOT_APPLICABLE
    assert summaries["approval-coverage"].status is ObservationStatus.NOT_APPLICABLE


def test_review_state_counts_cover_every_review_event_in_the_cohort() -> None:
    """Count reviews by submitted state, including the ones no metric treats as eligible."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    facts = (
        pull_request(
            1,
            starts_at,
            merged_at,
            reviews=(
                review(1, starts_at, ReviewState.APPROVED, "reviewer"),
                review(2, starts_at, ReviewState.COMMENTED, "author"),
                review(3, merged_at + timedelta(hours=1), ReviewState.APPROVED, "reviewer"),
            ),
        ),
        pull_request(2, starts_at, merged_at, reviews=(review(4, starts_at, ReviewState.PENDING, "reviewer"),)),
        pull_request(3, starts_at, merged_at),
    )

    # An author's own review, a review after merge, and a pending one are all reviewing activity:
    # the breakdown describes what reviewing looked like, and eligibility is the coverage question.
    assert review_state_counts(facts) == {"APPROVED": 2, "COMMENTED": 1, "PENDING": 1}
    assert review_state_counts(()) == {}


def test_governance_rates_count_direct_commits_and_flow_distributions_do_not() -> None:
    """Grow the governance denominators by every bypass, and leave the flow samples alone."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    merges = cohort(
        (
            pull_request(
                1,
                starts_at,
                merged_at,
                (review(11, starts_at, ReviewState.APPROVED, "reviewer"),),
            ),
        ),
        (commit("abc123", merged_at), commit("def456", merged_at + timedelta(hours=1))),
    )
    summaries = {metric.identifier: metric.summary(merges) for metric in behaviour_metrics(TraceabilityConfiguration())}
    coverage = summaries["independent-review-coverage"]
    approval = summaries["approval-coverage"]
    cycle = summaries["merge-cycle-time"]

    assert isinstance(coverage, RateObservation)
    assert isinstance(approval, RateObservation)
    assert isinstance(cycle, DistributionObservation)
    # One reviewed pull request among three merges, not one among one.
    assert (coverage.numerator, coverage.denominator) == (1, 3)
    assert (approval.numerator, approval.denominator) == (1, 3)
    # A direct commit has no cycle to time, so padding this sample would invent data.
    assert cycle.sample_size == 1


def test_direct_commit_checks_are_judged_by_conclusion_rather_than_an_instant() -> None:
    """Read the pushed commit's own rollup, since nothing gated the push to judge checks against."""
    committed_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    metric = ChecksPassingAtMerge()
    commits = (
        commit("aaa", committed_at),
        commit("bbb", committed_at, CheckRollupState.SUCCESS),
        commit("ccc", committed_at, CheckRollupState.FAILURE),
        commit("ddd", committed_at, CheckRollupState.ERROR),
        commit("eee", committed_at, CheckRollupState.PENDING),
        commit("fff", committed_at, CheckRollupState.EXPECTED),
    )

    classifications = tuple(metric.commit_classification(item) for item in commits)
    summary = metric.summary(cohort(direct_commits=commits))

    assert classifications == (
        "no-checks",
        "passing",
        "failing",
        "failing",
        "checks-unfinished",
        "checks-unfinished",
    )
    assert (summary.numerator, summary.denominator) == (1, 6)


def test_review_metrics_classify_every_direct_commit_as_a_bypass() -> None:
    """Name the route a change took, so a grown denominator can be explained."""
    committed_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    pushed = commit("aaa", committed_at)

    assert IndependentReviewCoverage().commit_classification(pushed) == "direct-commit"
    assert ApprovalCoverage().commit_classification(pushed) == "direct-commit"
    assert MergeCycleTime().commit_classification(pushed) is None


def test_review_depth_rates_commented_approvals_not_merges() -> None:
    """Rate over eligible approving reviews, so a repository cannot dilute a shallow rate by size."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    metric = ReviewDepth()
    facts = (
        pull_request(
            1,
            starts_at,
            merged_at,
            (review(11, merged_at - timedelta(hours=1), ReviewState.APPROVED, "reviewer", comment_count=2),),
        ),
        pull_request(
            2,
            starts_at,
            merged_at,
            (review(21, merged_at - timedelta(hours=1), ReviewState.APPROVED, "reviewer", comment_count=0),),
        ),
        # A comment on a review that never approved is not evidence of scrutinised sign-off.
        pull_request(
            3,
            starts_at,
            merged_at,
            (review(31, merged_at - timedelta(hours=1), ReviewState.COMMENTED, "reviewer", comment_count=3),),
        ),
    )

    summary = metric.summary(cohort(facts))
    classifications = tuple(metric.classification(fact) for fact in facts)

    assert (summary.numerator, summary.denominator) == (1, 2)
    assert classifications == ("commented", "uncommented-approval-only", "no-eligible-approval")


def test_review_depth_counts_an_approval_that_spoke_only_in_its_own_summary() -> None:
    """Count a review body as having said something, because GitHub records it apart from comments.

    An approval reading "this changes the retry semantics, I checked X" leaves no inline comment and
    is the opposite of the wordless click this metric exists to find. Counting only the `comments`
    connection scored it as one.
    """
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    metric = ReviewDepth()
    facts = (
        pull_request(
            1,
            starts_at,
            merged_at,
            (review(11, merged_at - timedelta(hours=1), ReviewState.APPROVED, "reviewer", has_body=True),),
        ),
        pull_request(
            2,
            starts_at,
            merged_at,
            (review(21, merged_at - timedelta(hours=1), ReviewState.APPROVED, "reviewer"),),
        ),
    )

    summary = metric.summary(cohort(facts))

    assert (summary.numerator, summary.denominator) == (1, 2)
    assert tuple(metric.classification(fact) for fact in facts) == ("commented", "uncommented-approval-only")


def test_waiting_times_do_not_charge_a_team_for_time_a_change_spent_in_draft() -> None:
    """Anchor both waiting times on the ready-for-review event, not on when the branch was opened.

    A change opened as a draft on day 1, marked ready on day 5 and merged on day 6 waited one day for
    the merge gate, not six. Anchoring on creation made these metrics report how long a branch
    existed.
    """
    created_at = datetime(2026, 7, 1, tzinfo=UTC)
    ready_at = created_at + timedelta(days=5)
    merged_at = created_at + timedelta(days=6)
    reviews = (review(11, ready_at + timedelta(hours=2), ReviewState.APPROVED, "reviewer"),)
    drafted = pull_request(1, created_at, merged_at, reviews).model_copy(update={"ready_for_review_at": ready_at})
    opened_ready = pull_request(2, created_at, merged_at, reviews)

    assert MergeCycleTime().value(drafted) == 24
    assert TimeToFirstReview().value(drafted) == 2
    # No event recorded — a pull request opened ready, or a fact cached before the event existed.
    assert MergeCycleTime().value(opened_ready) == 144
    assert TimeToFirstReview().value(opened_ready) == 122


def test_a_review_submitted_during_draft_starts_the_clock_before_the_ready_event() -> None:
    """Take the earlier of the two anchors, because reviewing a draft is still reviewing.

    GitHub allows it, and where someone did, review demonstrably began then. This is also what keeps
    a waiting time from coming out negative.
    """
    created_at = datetime(2026, 7, 1, tzinfo=UTC)
    reviewed_at = created_at + timedelta(days=1)
    ready_at = created_at + timedelta(days=5)
    merged_at = created_at + timedelta(days=6)
    fact = pull_request(
        1,
        created_at,
        merged_at,
        (review(11, reviewed_at, ReviewState.COMMENTED, "reviewer"),),
    ).model_copy(update={"ready_for_review_at": ready_at})

    assert MergeCycleTime().value(fact) == 120
    assert TimeToFirstReview().value(fact) == 0


def test_review_depth_credits_remarks_a_reviewer_left_before_approving() -> None:
    """Judge an approval against everything its reviewer said, not against the approval event alone.

    Leaving the remarks as `COMMENTED` reviews and coming back to approve once they are answered is
    the ordinary way to review. Reading only the approval scores that reviewer as a rubber stamp and
    is simply wrong about what happened.
    """
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    metric = ReviewDepth()
    facts = (
        pull_request(
            1,
            starts_at,
            merged_at,
            (
                review(11, starts_at + timedelta(hours=2), ReviewState.COMMENTED, "reviewer", comment_count=4),
                review(12, merged_at - timedelta(hours=1), ReviewState.APPROVED, "reviewer"),
            ),
        ),
        pull_request(
            2,
            starts_at,
            merged_at,
            (review(21, merged_at - timedelta(hours=1), ReviewState.APPROVED, "reviewer"),),
        ),
    )

    summary = metric.summary(cohort(facts))

    assert (summary.numerator, summary.denominator) == (1, 2)
    assert tuple(metric.classification(fact) for fact in facts) == ("commented", "uncommented-approval-only")


def test_review_depth_attributes_remarks_to_the_reviewer_who_made_them() -> None:
    """Keep a silent second approval visible where one reviewer did all of the arguing.

    Where two approvals are required and only one reviewer engages, crediting the silent approval
    with the other reviewer's comments would hide exactly the rubber stamp this metric exists to
    find.
    """
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    fact = pull_request(
        1,
        starts_at,
        merged_at,
        (
            review(11, starts_at + timedelta(hours=2), ReviewState.COMMENTED, "engaged", comment_count=4),
            review(12, merged_at - timedelta(hours=2), ReviewState.APPROVED, "engaged"),
            review(13, merged_at - timedelta(hours=1), ReviewState.APPROVED, "silent"),
        ),
    )

    summary = ReviewDepth().summary(cohort((fact,)))

    assert (summary.numerator, summary.denominator) == (1, 2)
    assert ReviewDepth().classification(fact) == "commented"


def test_review_depth_does_not_credit_an_approval_with_the_authors_own_remarks() -> None:
    """Leave a self-review out of the evidence: an author answering questions is not scrutiny."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    fact = pull_request(
        1,
        starts_at,
        merged_at,
        (
            review(11, starts_at + timedelta(hours=2), ReviewState.COMMENTED, "author", comment_count=9),
            review(12, merged_at - timedelta(hours=1), ReviewState.APPROVED, "reviewer"),
        ),
    )

    summary = ReviewDepth().summary(cohort((fact,)))

    assert (summary.numerator, summary.denominator) == (0, 1)
    assert ReviewDepth().classification(fact) == "uncommented-approval-only"


def test_review_depth_reports_no_denominator_when_nothing_was_approved() -> None:
    """State not_applicable rather than a manufactured zero when nothing was ever approved."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    facts = (pull_request(1, starts_at, merged_at, (review(11, merged_at, ReviewState.COMMENTED, "reviewer"),)),)

    summary = ReviewDepth().summary(cohort(facts))

    assert summary.status is ObservationStatus.NOT_APPLICABLE
    assert (summary.numerator, summary.denominator) == (0, 0)


def test_review_depth_ignores_direct_commits() -> None:
    """Leave a direct commit uncounted: it has no review to be shallow or deep."""
    pushed = commit("aaa", datetime(2026, 7, 10, 12, tzinfo=UTC))

    assert ReviewDepth().commit_classification(pushed) is None


def test_description_quality_rates_bodies_at_or_above_the_minimum_length() -> None:
    """Classify a body of exactly the minimum length as described, and one short of it as not."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    metric = DescriptionQuality(TraceabilityConfiguration(minimum_description=10))
    facts = (
        pull_request(1, starts_at, merged_at, body="x" * 10),
        pull_request(2, starts_at, merged_at, body="x" * 9),
        pull_request(3, starts_at, merged_at, body=None),
    )

    summary = metric.summary(cohort(facts))

    assert (summary.numerator, summary.denominator) == (1, 3)
    assert tuple(metric.classification(fact) for fact in facts) == (
        "described",
        "description-too-short",
        "description-too-short",
    )


def test_description_quality_strips_whitespace_before_measuring_length() -> None:
    """Do not let padding whitespace count towards the minimum length."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    metric = DescriptionQuality(TraceabilityConfiguration(minimum_description=10))

    assert not metric.described(pull_request(1, starts_at, merged_at, body=" " * 20))
    assert metric.described(pull_request(2, starts_at, merged_at, body=f"  {'x' * 10}  "))


def test_description_quality_ignores_direct_commits() -> None:
    """Leave a direct commit uncounted: there was no pull request to describe."""
    pushed = commit("aaa", datetime(2026, 7, 10, 12, tzinfo=UTC))

    assert DescriptionQuality(TraceabilityConfiguration()).commit_classification(pushed) is None


def test_traceability_reference_matches_in_title_or_body() -> None:
    """Search both title and body, because a ticket key in the title is traceability too."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    metric = TraceabilityReference(TraceabilityConfiguration())
    facts = (
        pull_request(1, starts_at, merged_at, title="Fix #42", body=None),
        pull_request(2, starts_at, merged_at, title="Fix the thing", body="closes JIRA-123"),
        pull_request(3, starts_at, merged_at, title="Fix #1", body="closes JIRA-1"),
        pull_request(4, starts_at, merged_at, title="Fix the thing", body="no reference here"),
    )

    summary = metric.summary(cohort(facts))

    assert (summary.numerator, summary.denominator) == (3, 4)
    assert tuple(metric.classification(fact) for fact in facts) == (
        "referenced",
        "referenced",
        "referenced",
        "reference-missing",
    )


def test_traceability_reference_honours_a_custom_pattern_list() -> None:
    """Match only the organisation's configured patterns, not the shipped defaults."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    merged_at = starts_at + timedelta(days=1)
    metric = TraceabilityReference(TraceabilityConfiguration(reference_patterns=(r"TICKET-\d+",)))
    matching = pull_request(1, starts_at, merged_at, title="Fix", body="see TICKET-7")
    default_pattern_only = pull_request(2, starts_at, merged_at, title="Fix #7", body=None)

    assert metric.references_ticket(matching)
    assert not metric.references_ticket(default_pattern_only)


def test_traceability_reference_ignores_direct_commits() -> None:
    """Leave a direct commit uncounted: there was no pull request to carry a reference."""
    pushed = commit("aaa", datetime(2026, 7, 10, 12, tzinfo=UTC))

    assert TraceabilityReference(TraceabilityConfiguration()).commit_classification(pushed) is None


def authored(identifier: int, login: str | None, author_type: str | None = "User") -> PullRequestFact:
    """Build one merged pull request attributed to the given account, for a contributor count."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    return pull_request(identifier, starts_at, starts_at + timedelta(days=1)).model_copy(
        update={"author_login": login, "author_type": author_type},
    )


def test_contributors_are_counted_once_per_person_across_both_routes() -> None:
    """Count the distinct people behind a window's merges, not the merges they made."""
    pushed = commit("aaa", datetime(2026, 7, 10, 12, tzinfo=UTC), author_login="carol")

    logins = contributor_logins((authored(1, "alice"), authored(2, "bob"), authored(3, "alice"), pushed))

    assert logins == {"alice", "bob", "carol"}


def test_one_login_spelled_two_ways_is_one_contributor() -> None:
    """Fold case, because a GitHub login is unique case-insensitively and a person is not two people."""
    assert contributor_logins((authored(1, "Alice"), authored(2, "alice"))) == {"alice"}


def test_bot_accounts_are_not_contributors_however_github_marks_them() -> None:
    """Leave out both the accounts GitHub types as bots and the user accounts named as ones.

    Both spellings are needed: GitHub types an account `Bot` only where it is a GitHub App, so an
    automation account running as an ordinary user is typed `User` and identifiable only by its
    login. An unattributable merge is nobody, and so is counted as nobody rather than as an unnamed
    contributor.
    """
    logins = contributor_logins(
        (
            authored(1, "alice"),
            authored(2, "copilot-swe-agent[bot]", "Bot"),
            authored(3, "some-service", "Bot"),
            authored(4, "legacy-ci[bot]"),
            authored(5, None, None),
        ),
    )

    assert logins == {"alice"}


def test_a_window_authored_entirely_by_bots_has_no_contributors() -> None:
    """Report nobody rather than falling back to a merge count: the merges happened, the people did not."""
    assert contributor_logins((authored(1, "renovate[bot]", "Bot"),)) == frozenset()
