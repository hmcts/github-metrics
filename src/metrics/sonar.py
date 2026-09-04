"""Read SonarCloud project listings, analyses and quality measures, and resolve a project's repository.

Every SonarCloud call here is a plain GET against the public web API, and every one of them works
ANONYMOUSLY for a public project — which is what nearly all of an HMCTS organisation's projects are.
A token is sent only when the environment holds one, and it widens the listing to private projects
rather than being what makes the reads possible: a run with no token returns fewer projects, not an
error, and a run with a BAD token returns 401 where no token at all returns 200.

`sonar_to_github` is the one thing here that is not a read of SonarCloud. Neither side states the
pairing between a project and a repository, so it is established through the only identifier both
sides share: the commit SHA an analysis ran against. That call is GitHub's commit search, which is
limited to 30 requests a minute against every other quota's thousands, and it is why resolution is a
separate periodically-run command rather than part of a collection.

`github_to_sonar` is the reverse, and the one a collection asks: which project holds this
repository's quality state? It reads the map that command built rather than the search API, so it
spends nothing rate limited in the common case, and it prefers EVIDENCE to CONVENTION at every rung
of its ladder — a key a repository declares about itself is a hypothesis to be confirmed, never an
answer, because 123 of the organisation's 240 declarations name a project SonarCloud does not list
and 69 more collide with a template's key.
"""

import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from time import sleep, time
from typing import ClassVar, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from requests import RequestException, Response, Session
from requests.exceptions import JSONDecodeError

from metrics.domain import (
    AvailabilityReason,
    SonarGateLevel,
    SonarMeasures,
    SonarProjectMapping,
    SonarQualityGate,
    SonarQualityGateCondition,
    SonarRating,
    SonarRepositoryProject,
    SonarResolution,
    StoredSonarMapping,
)
from metrics.github import GitHubClient, GitHubError, RateLimitBudget
from metrics.storage import load_sonar_mapping, repository_project

SONAR_URL = "https://sonarcloud.io"
REQUEST_TIMEOUT = 30
# SonarCloud's maximum page size, and the size the project listing asks for: an organisation of 289
# projects is then one call rather than three.
MAXIMUM_PAGE_SIZE = 500
# Read in this order, so a repository that sets both is authenticated the way SonarCloud's own
# scanner documentation names first.
TOKEN_VARIABLES = ("SONAR_TOKEN", "SONARCLOUD_TOKEN")

# GitHub's DOCUMENTED commit-search limit for an authenticated caller: 30 requests a minute, against
# 10 unauthenticated. Every other quota this project spends is counted in thousands per hour, so this
# is the scarcest thing a run can consume and the only one paced by hand.
#
# THE DOCUMENTED NUMBER IS NOT ALWAYS THE ISSUED ONE, which is why it is a bootstrap value here and
# not the pacing rule. Measured over a full `map-sonar` run on 2026-08-27, the token in use was given
# a limit of TEN a minute: the run took a 403 after every tenth search, 37 times, and paid a 60-second
# backoff for each — slower than pacing correctly would have been, and it spent the retry budget a
# genuine failure then had none of. `CallPacer` therefore paces off the `x-ratelimit-*` headers GitHub
# actually returns and falls back to this interval only until the first response reports a budget.
SEARCH_CALLS_PER_MINUTE = 30
SEARCH_INTERVAL_SECONDS = 60 / SEARCH_CALLS_PER_MINUTE
# The resource name the commit search is logged and waited under. Deliberately NOT "search", which is
# what GitHub's own headers call it: `GitHubClient.wait_for_rate_limit` holds back an ABSOLUTE reserve
# of calls tuned to the 5,000-per-hour core quota, and 100 reserved calls out of a limit of 30 would
# pause for the window to reset before the second search of the run. Pacing for this quota is done
# here, by interval, and the name keeps the client's logs honest about which call is waiting.
COMMIT_SEARCH_RESOURCE = "commit-search"
# What GitHub's own headers call the quota the commit search spends, and so the key its budget is
# recorded under. The pair above and this one are deliberately different strings: one names the call
# for the client's waiting and logging, this one names the quota for reading what is left of it.
SEARCH_BUDGET_RESOURCE = "search"
# How many of a project's analyses may be searched before it is given up on. More than one, because a
# pull-request analysis can name a commit on a branch since force-pushed or deleted, and that commit
# is then in no repository at all; bounded, because every attempt spends the scarcest quota there is.
MAXIMUM_ANALYSES_TRIED = 5

SONAR_PROPERTIES_PATH = "sonar-project.properties"
"""The one file a repository's own declaration is read from: the root `sonar-project.properties`.

The root, and nothing else. A Gradle or Maven repository declares its key in a build file instead,
and reading those would mean parsing three build languages to obtain a hypothesis that still has to
be confirmed against evidence — while the map that confirms it answers those repositories anyway.
"""

PROJECT_KEY_PROPERTY = "sonar.projectKey"
ORGANIZATION_PROPERTY = "sonar.organization"

# `key=value`, `key:value` or `key value`, as the Java properties format allows. The name cannot
# contain any of the three separators, so splitting at whichever comes first is unambiguous — which
# matters here, because the VALUE routinely contains a colon: `uk.gov.hmcts.reform:darts-gateway`.
PROPERTY_PATTERN = re.compile(r"^([A-Za-z0-9_.\-]+)\s*[=:\s]\s*(.*)$")
# Recognised only at the start of a line, as the format specifies: a `#` inside a value is a `#`.
PROPERTY_COMMENT_MARKERS = ("#", "!")

SONAR_METRIC_KEYS = (
    "alert_status",
    "quality_gate_details",
    "coverage",
    "duplicated_lines_density",
    "ncloc",
    "violations",
    "software_quality_reliability_issues",
    "software_quality_maintainability_issues",
    "software_quality_security_issues",
    "reliability_rating",
    "sqale_rating",
    "security_rating",
)
"""The metric keys `component_measures` asks for, in one guarded constant.

THE WHOLE SET IS LOST TO ONE BAD KEY. `/api/measures/component` answers a request naming any metric
it does not know with `measures: null` for the WHOLE call — HTTP 200, no error text, every other
metric silently discarded. `maintainability_rating` is the trap: it reads like the sibling of
`reliability_rating` and `security_rating`, and it does not exist. The key is `sqale_rating`, after
the SQALE method the rating came from.

That is why the keys are a constant rather than a list assembled at the call site, and why
`tests/test_sonar.py` pins every one of them against an independently written list of the keys
verified against the live API on 2026-08-27. A typo here is not a missing column in a report; it is
an empty report that still says HTTP 200.
"""


class SonarError(RuntimeError):
    """Report a SonarCloud access failure, classified like a GitHub one."""

    def __init__(self, message: str, reason: AvailabilityReason) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SonarProject:
    """One project as the organisation's listing describes it.

    `analysis_at` is None for a project created and never analysed — 14 of the organisation's 289 on
    2026-08-27 — which is a fact about the project and not a gap in the listing.
    """

    key: str
    visibility: str | None
    analysis_at: datetime | None


@dataclass(frozen=True)
class SonarAnalysis:
    """One analysis of one project: when it ran, and the commit it ran against.

    `revision` is the SHA that makes a project resolvable to a repository, and it is optional
    because SonarCloud does not record one for every analysis.
    """

    analysis_at: datetime
    revision: str | None


class SonarResponse(BaseModel):
    """Parse the fields this module reads out of a SonarCloud response, ignoring the rest."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class ProjectPaging(SonarResponse):
    """State how many projects the listing holds in total, which is what ends the paging."""

    total: int = 0


class ProjectComponent(SonarResponse):
    """One entry of the project listing, read with `f=analysisDate`."""

    key: str
    visibility: str | None = None
    analysis_date: AwareDatetime | None = Field(default=None, alias="analysisDate")


class ProjectPage(SonarResponse):
    """One page of the project listing."""

    paging: ProjectPaging = ProjectPaging()
    components: tuple[ProjectComponent, ...] = ()


class AnalysisEntry(SonarResponse):
    """One analysis record, newest first as SonarCloud returns them."""

    date: AwareDatetime
    revision: str | None = None


class AnalysisPage(SonarResponse):
    """One page of a project's analysis history."""

    analyses: tuple[AnalysisEntry, ...] = ()


class ComponentMeasure(SonarResponse):
    """One measured metric, whose value SonarCloud reports as a string whatever its type."""

    metric: str
    value: str | None = None


class MeasuredComponent(SonarResponse):
    """The component a measures response describes.

    `measures` is nullable rather than defaulted to empty, because `null` is the shape SonarCloud
    answers with when a requested metric key does not exist. Every measure then reads as absent,
    which is exactly what it is: nothing was measured for this call.
    """

    measures: tuple[ComponentMeasure, ...] | None = None


class MeasuresResponse(SonarResponse):
    """One `/api/measures/component` response."""

    component: MeasuredComponent = MeasuredComponent()


class GateConditionDetail(SonarResponse):
    """One condition inside `quality_gate_details`, whose `error` field is the threshold."""

    metric: str
    op: str = ""
    error: str | None = None
    actual: str | None = None
    level: SonarGateLevel = SonarGateLevel.NONE


class GateDetail(SonarResponse):
    """The `quality_gate_details` document, which is JSON encoded inside a measure's value."""

    level: SonarGateLevel = SonarGateLevel.NONE
    conditions: tuple[GateConditionDetail, ...] = ()


class GitHubResponse(BaseModel):
    """Parse the fields this module reads out of a GitHub response, ignoring the rest."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class SearchedRepository(GitHubResponse):
    """The repository one commit-search result belongs to, as `owner/name`."""

    full_name: str


class SearchedCommit(GitHubResponse):
    """One commit-search result, read only for the repository that holds the commit."""

    repository: SearchedRepository


class CommitSearchPage(GitHubResponse):
    """One `/search/commits` response, read for its best match alone.

    The result count is deliberately not read: a SHA present in two repositories of one organisation
    is a fork, and the best match GitHub returns first is the answer either way.
    """

    items: tuple[SearchedCommit, ...] = ()


class SonarMappingOutcome(StrEnum):
    """Say how the attempt to name one project's repository ended.

    Five of the six are OBSERVATIONS ABOUT THE PROJECT and one is a failure of this run, and the
    distinction is the whole point of the enum: a project nobody has ever analysed, and a project
    whose analysis names a commit that no longer exists, have both been answered — there is nothing
    more to learn about them until they are analysed again. Only `FAILED` means the question was
    never put, and only `FAILED` may make a run partial.
    """

    RESOLVED = "resolved"
    NO_ANALYSIS = "no_analysis"
    NO_REVISION = "no_revision"
    UNKNOWN_COMMIT = "unknown_commit"
    OUTSIDE_ORGANIZATION = "outside_organization"
    FAILED = "failed"

    @property
    def is_observation(self) -> bool:
        """Report whether this outcome describes the project rather than this run's own failure."""
        return self is not SonarMappingOutcome.FAILED


@dataclass(frozen=True)
class SonarResolutionAttempt:
    """Record one attempt to name the repository behind one SonarCloud project.

    `mapping` is present exactly when the outcome is `RESOLVED`, and `detail` exactly when it is not:
    an unresolved project is stored WITH its reason so that the next run does not spend the scarcest
    quota there is asking the same hopeless question again.

    `analyses_tried` counts the commit searches this attempt issued, which is the cost it incurred.
    It is zero for a project that had no revision to search for, and it is what a summary needs to
    report what a run actually spent.
    """

    project_key: str
    outcome: SonarMappingOutcome
    mapping: SonarProjectMapping | None = None
    analyses_tried: int = 0
    detail: str | None = None


class CallPacer:
    """Space one kind of call out in time, so a per-minute quota is respected rather than hit.

    Pacing rather than reacting: GitHub answers a spent commit-search quota with a 403 whose window
    takes a full minute to clear, so a run that sprints into the limit is slower than one that never
    reaches it — and it burns a retry budget that a genuine failure then has none of. The clock and
    the pause are injected so the suite can assert the spacing without waiting for it.

    PACED OFF WHAT GITHUB REPORTS, NOT OFF WHAT GITHUB DOCUMENTS. `budget` reads the client's record
    of the live `x-ratelimit-*` headers for the quota being spent, and the spacing is recomputed
    before every call as "the calls left, spread over the time left in the window". A fixed interval
    cannot do this: `SEARCH_CALLS_PER_MINUTE` says 30 and the observed allowance for the token that
    ran `map-sonar` on 2026-08-27 was 10, so the run tripped a 403 every tenth search and waited a
    minute each time. `interval` survives as the FLOOR — the fastest this will ever go, and the
    spacing used for the first call of a run, before any header has been seen.
    """

    def __init__(
        self,
        interval: float,
        clock: Callable[[], float] = time,
        pause: Callable[[float], None] = sleep,
        budget: Callable[[], RateLimitBudget | None] | None = None,
    ) -> None:
        self.interval = interval
        self.clock = clock
        self.pause = pause
        self.budget = budget
        self.issued_at: float | None = None

    def spacing(self) -> float:
        """Return the gap to leave before the next call, from the live budget where there is one.

        Three cases, and the floor applies to all of them:

        NO BUDGET YET, or one whose window has already closed — the headers say nothing usable, so
        the configured interval stands. A closed window is not read as "0 remaining": GitHub refills
        at the reset instant and the record is simply stale.

        SOMETHING LEFT — spread it. `remaining` calls over the seconds until the window resets is the
        pace that arrives at the reset instant having spent exactly the allowance, and it self-corrects
        every call because the next response reports both numbers again.

        NOTHING LEFT — wait out the window. The alternative is issuing a call that is certain to be
        refused, and then sleeping through the same window anyway with a retry spent.
        """
        budget = self.budget() if self.budget is not None else None
        if budget is None:
            return self.interval
        window = budget.resets_at - self.clock()
        if window <= 0:
            return self.interval
        if budget.remaining <= 0:
            return max(window, self.interval)
        return max(window / budget.remaining, self.interval)

    def wait(self) -> None:
        """Pause until the interval since the previous call has passed, then claim this call's slot.

        No single wait ever exceeds the current spacing, because the elapsed time is floored at zero:
        a wall clock being corrected backwards would otherwise stall a run for as long as the
        correction was large, which is a hang and not a pause.
        """
        now = self.clock()
        if self.issued_at is None:
            self.issued_at = now
            return
        interval = self.spacing()
        delay = interval - max(now - self.issued_at, 0)
        if delay <= 0:
            self.issued_at = now
            return
        logging.debug("pacing the commit search: waiting %.1fs", delay)
        self.pause(delay)
        # Whichever is later: a real clock has advanced past the slot the pause bought, and a frozen
        # clock must still be seen to have consumed it or every later call would pause all over again.
        self.issued_at = max(self.clock(), self.issued_at + interval)


def search_pacer(client: GitHubClient, interval: float = SEARCH_INTERVAL_SECONDS) -> CallPacer:
    """Build the commit-search pacer, wired to the search budget that client has last been told."""
    return CallPacer(interval, budget=lambda: client.budget(SEARCH_BUDGET_RESOURCE))


def sonar_token(environment: Mapping[str, str]) -> str | None:
    """Return the first SonarCloud token the environment sets, or None for an anonymous read."""
    for name in TOKEN_VARIABLES:
        token = environment.get(name)
        if token:
            return token
    return None


def gate_level(value: str | None) -> SonarGateLevel | None:
    """Read one `alert_status` value, treating anything unrecognised as no level at all.

    A level this build does not know is reported as no gate rather than as a passing one, for the
    same reason an off-scale rating has no letter: an unread signal must never render as the good
    answer.
    """
    if value is None:
        return None
    try:
        return SonarGateLevel(value)
    except ValueError:
        return None


def measured_number(values: Mapping[str, str], key: str) -> float | None:
    """Read one numeric measure, treating an absent or unreadable value as absent.

    Never zero. SonarCloud reports every measure as a string, so a value it did not send and a value
    that did not parse are both "not measured" — and a rendered `0.0%` would be a claim about the
    code rather than about the measurement.
    """
    raw = values.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        logging.warning("SonarCloud reported an unreadable value for %s: %s", key, raw)
        return None


def measured_count(values: Mapping[str, str], key: str) -> int | None:
    """Read one counted measure, which SonarCloud sends as a string like every other."""
    number = measured_number(values, key)
    return None if number is None else int(number)


def measured_rating(values: Mapping[str, str], key: str) -> SonarRating | None:
    """Read one `1.0`-to-`5.0` rating, keeping the number the letter is derived from."""
    number = measured_number(values, key)
    return None if number is None else SonarRating(value=number)


def measured_gate(values: Mapping[str, str]) -> SonarQualityGate | None:
    """Build the gate from its conditions, falling back to the bare level when they do not parse.

    `quality_gate_details` carries the level AND every condition behind it, so it is preferred;
    `alert_status` says only whether the gate passed. A details document this build cannot read
    therefore degrades to the verdict rather than to nothing, and says so in the log.
    """
    level = gate_level(values.get("alert_status"))
    details = values.get("quality_gate_details")
    if details is None:
        return None if level is None else SonarQualityGate(level=level)
    try:
        parsed = GateDetail.model_validate_json(details)
    except ValidationError:
        logging.warning("SonarCloud returned quality gate details this build cannot read")
        return None if level is None else SonarQualityGate(level=level)
    return SonarQualityGate(
        level=parsed.level,
        conditions=tuple(
            SonarQualityGateCondition(
                metric=condition.metric,
                comparator=condition.op,
                threshold=condition.error,
                actual=condition.actual,
                level=condition.level,
            )
            for condition in parsed.conditions
        ),
    )


def parse_measures(project: str, component: MeasuredComponent, analysis_at: datetime | None) -> SonarMeasures:
    """Turn one measures response into the evidence model, keeping every absence an absence."""
    values = {measure.metric: measure.value for measure in (component.measures or ()) if measure.value is not None}
    return SonarMeasures(
        project_key=project,
        analysis_at=analysis_at,
        gate=measured_gate(values),
        coverage=measured_number(values, "coverage"),
        duplicated_lines_density=measured_number(values, "duplicated_lines_density"),
        lines_of_code=measured_count(values, "ncloc"),
        violations=measured_count(values, "violations"),
        reliability_issues=measured_count(values, "software_quality_reliability_issues"),
        maintainability_issues=measured_count(values, "software_quality_maintainability_issues"),
        security_issues=measured_count(values, "software_quality_security_issues"),
        reliability_rating=measured_rating(values, "reliability_rating"),
        maintainability_rating=measured_rating(values, "sqale_rating"),
        security_rating=measured_rating(values, "security_rating"),
    )


class SonarClient:
    """Read one SonarCloud organisation's projects, analyses and measures."""

    base_url: ClassVar[str] = SONAR_URL
    http_failures: ClassVar[Mapping[int, tuple[str, AvailabilityReason]]] = {
        HTTPStatus.UNAUTHORIZED: ("SonarCloud authentication failed", AvailabilityReason.AUTHENTICATION_FAILED),
        HTTPStatus.FORBIDDEN: ("SonarCloud permission denied", AvailabilityReason.PERMISSION_DENIED),
        HTTPStatus.NOT_FOUND: (
            "SonarCloud project not found or inaccessible",
            AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE,
        ),
        HTTPStatus.TOO_MANY_REQUESTS: ("SonarCloud rate limit exceeded", AvailabilityReason.RATE_LIMITED),
    }

    def __init__(self, session: Session, environment: Mapping[str, str] | None = None) -> None:
        token = sonar_token(os.environ if environment is None else environment)
        if token is not None:
            session.headers["Authorization"] = f"Bearer {token}"
        self.session = session
        self.issued = 0

    @property
    def requests_issued(self) -> int:
        """Count every SonarCloud call this client has issued since it was built."""
        return self.issued

    def list_projects(self, organization: str) -> tuple[SonarProject, ...]:
        """Return every project the organisation lists, with the instant each was last analysed."""
        projects: list[SonarProject] = []
        page = 1
        while True:
            parsed = self.read(
                ProjectPage,
                "/api/components/search_projects",
                {"organization": organization, "ps": MAXIMUM_PAGE_SIZE, "p": page, "f": "analysisDate"},
                "project listing",
            )
            projects.extend(
                SonarProject(key=component.key, visibility=component.visibility, analysis_at=component.analysis_date)
                for component in parsed.components
            )
            if not parsed.components or len(projects) >= parsed.paging.total:
                return tuple(projects)
            page += 1

    def project_analyses(self, project: str, limit: int) -> tuple[SonarAnalysis, ...]:
        """Return a project's most recent analyses, newest first, as SonarCloud orders them."""
        parsed = self.read(
            AnalysisPage,
            "/api/project_analyses/search",
            {"project": project, "ps": min(limit, MAXIMUM_PAGE_SIZE)},
            "project analyses",
        )
        return tuple(SonarAnalysis(analysis_at=entry.date, revision=entry.revision) for entry in parsed.analyses)

    def component_measures(self, project: str, analysis_at: datetime | None = None) -> SonarMeasures:
        """Measure one project with the single guarded key set, in one call.

        `analysis_at` is supplied by the caller rather than read here: this endpoint reports the
        measures and not the instant they were taken at, and the listing that named the project
        already carries it.
        """
        parsed = self.read(
            MeasuresResponse,
            "/api/measures/component",
            {"component": project, "metricKeys": ",".join(SONAR_METRIC_KEYS)},
            "measures",
        )
        try:
            return parse_measures(project, parsed.component, analysis_at)
        except ValidationError as exception:
            message = f"SonarCloud returned measures this build cannot accept for {project}"
            raise SonarError(message, AvailabilityReason.COLLECTION_FAILED) from exception

    def read[T: SonarResponse](
        self,
        model: type[T],
        path: str,
        parameters: dict[str, str | int],
        description: str,
    ) -> T:
        """Fetch one path and parse it, classifying a response this build cannot read as a failure."""
        try:
            return model.model_validate(self.get(path, parameters))
        except ValidationError as exception:
            message = f"SonarCloud returned an invalid {description} response"
            raise SonarError(message, AvailabilityReason.COLLECTION_FAILED) from exception

    def get(self, path: str, parameters: dict[str, str | int]) -> object:
        """Make one GET request and return its parsed JSON body."""
        self.issued += 1
        url = f"{self.base_url}{path}"
        logging.debug("SonarCloud request GET %s %s", url, parameters)
        try:
            response = self.session.get(url, params=parameters, timeout=REQUEST_TIMEOUT)
        except RequestException as exception:
            message = f"SonarCloud request failed: {exception}"
            raise SonarError(message, AvailabilityReason.COLLECTION_FAILED) from exception
        self.validate(response)
        try:
            return response.json()
        except JSONDecodeError as exception:
            message = f"SonarCloud returned invalid JSON at line {exception.lineno}, column {exception.colno}"
            raise SonarError(message, AvailabilityReason.COLLECTION_FAILED) from exception

    def validate(self, response: Response) -> None:
        """Classify a SonarCloud response that is not an answer.

        The body is deliberately left out of the message: it becomes a repository's `detail` in a
        report meant to be shared, and SonarCloud's error text can quote the request it refused.
        """
        if response.status_code == HTTPStatus.OK:
            return
        message, reason = self.http_failures.get(
            response.status_code,
            (f"SonarCloud returned HTTP {response.status_code}", AvailabilityReason.COLLECTION_FAILED),
        )
        raise SonarError(message, reason)


def search_commit(
    client: GitHubClient,
    organization: str,
    revision: str,
    pacer: CallPacer,
) -> CommitSearchPage:
    """Ask GitHub which repository in one organisation holds one commit.

    One paced call against the 30-per-minute commit-search quota. The `org:` qualifier is what makes
    the answer usable: an unqualified `hash:` search reaches every public repository on GitHub, and a
    commit found in a fork somewhere else says nothing about who owns the project.
    """
    pacer.wait()
    response = client.get(
        f"{client.api_url}/search/commits",
        {"q": f"org:{organization} hash:{revision}", "per_page": 1},
        COMMIT_SEARCH_RESOURCE,
    )
    try:
        return CommitSearchPage.model_validate(response.json())
    except (JSONDecodeError, ValidationError) as exception:
        message = "GitHub returned an invalid commit search response"
        raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED) from exception


def confirms_candidate(
    client: GitHubClient,
    organization: str,
    repository: str,
    revision: str,
) -> bool:
    """Ask whether one named repository holds one commit — the cheap half of resolution.

    `GET /repos/{org}/{repo}/commits/{sha}` answers 200 when the repository holds the commit and 422
    when it does not, and it spends the CORE quota of 5,000 an hour rather than the commit search's
    30 a minute. So confirming a candidate somebody has already proposed — a key declared in a
    repository's own `sonar-project.properties`, say — costs effectively nothing, and only
    DISCOVERING an unknown repository needs the search. A refusal this call cannot read as an answer
    is re-raised rather than reported as a refutation: "nobody may look" must never be recorded as
    "the repository does not hold it".

    THE 422 IS READ HERE RATHER THAN CLASSIFIED IN THE CLIENT, because it is this endpoint's answer
    and not GitHub's. The shared table would hand the same meaning to the branch-protection and
    alert reads, whose callers turn `NOT_FOUND_OR_INACCESSIBLE` into "unprotected" and "not enabled"
    — asserted facts that a malformed or throttled request would then manufacture.
    """
    try:
        client.get(f"{client.api_url}/repos/{organization}/{repository}/commits/{revision}")
    except GitHubError as exception:
        if (
            exception.reason is AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE
            or exception.status == HTTPStatus.UNPROCESSABLE_ENTITY
        ):
            return False
        raise
    return True


COMMIT_SHA = re.compile(r"\A[0-9a-fA-F]{7,64}\Z")
"""What a revision must look like to be worth asking GitHub about, and safe to ask with.

Worth: nothing but a hexadecimal object name can match a commit, so anything else is a wasted call.
Safe: the revision reaches GitHub both as a `hash:` search qualifier and as a path segment of
`/repos/{org}/{repo}/commits/{sha}`, and `requests` resolves `..` segments and honours a `?` in a
path before the request is sent — so an unconstrained value SonarCloud returned could address a
different endpoint entirely and have its `200` read as a confirmation.
"""


def searchable_revisions(analyses: tuple[SonarAnalysis, ...]) -> tuple[tuple[str, datetime], ...]:
    """List the distinct revisions worth searching for, newest first, with the analysis that named each.

    Distinct, because a project re-analysed on an unchanged main branch reports the same SHA for
    several analyses running, and paying the scarcest quota there is twice for one identical question
    buys nothing. Revision-less analyses drop out here: SonarCloud does not record a commit for every
    analysis, and one that names none cannot be searched for. So does anything that is not an object
    name — see `COMMIT_SHA`, which is both the filter that stops a wasted call and the one that keeps
    a value from another system out of a URL this code builds.
    """
    revisions: list[tuple[str, datetime]] = []
    seen: set[str] = set()
    for analysis in analyses:
        revision = analysis.revision
        if revision is not None and revision not in seen and COMMIT_SHA.match(revision):
            seen.add(revision)
            revisions.append((revision, analysis.analysis_at))
    return tuple(revisions)


def attributed_repository(organization: str, full_name: str) -> str | None:
    """Read the repository name out of an `owner/name` pair, or None when the owner is somebody else.

    Compared without regard to case, as GitHub compares owners. An owner that is not the configured
    organisation is not a near miss to be trimmed off: the project analyses somebody else's code, and
    attributing it here would put another organisation's quality gate on this one's report.
    """
    owner, _, repository = full_name.partition("/")
    return repository if repository and owner.casefold() == organization.casefold() else None


def resolve_revision(
    client: GitHubClient,
    organization: str,
    project: str,
    revision: str,
    analysis_at: datetime,
    pacer: CallPacer,
) -> SonarResolutionAttempt | None:
    """Search one analysed revision, returning the attempt it settles or None to walk to the next.

    None means only "this commit answered nothing", which is the one outcome worth another analysis:
    a force-pushed pull-request branch takes its commits with it, and the analysis under it may still
    name one that exists. Every other outcome — found, found elsewhere, refused — is final, because
    an older analysis cannot contradict it.
    """
    try:
        page = search_commit(client, organization, revision, pacer)
    except GitHubError as exception:
        if exception.reason is AvailabilityReason.RATE_LIMITED:
            raise
        return SonarResolutionAttempt(project, SonarMappingOutcome.FAILED, detail=str(exception))
    if not page.items:
        return None
    full_name = page.items[0].repository.full_name
    repository = attributed_repository(organization, full_name)
    if repository is None:
        detail = f"commit {revision} belongs to {full_name}, which is outside {organization}"
        return SonarResolutionAttempt(project, SonarMappingOutcome.OUTSIDE_ORGANIZATION, detail=detail)
    return SonarResolutionAttempt(
        project,
        SonarMappingOutcome.RESOLVED,
        mapping=SonarProjectMapping(
            project_key=project,
            repository=repository,
            method=SonarResolution.ANALYSIS_REVISION,
            analysis_at=analysis_at,
            revision=revision,
        ),
    )


def sonar_to_github(
    sonar_client: SonarClient,
    github_client: GitHubClient,
    organization: str,
    project: str,
    attempts: int = MAXIMUM_ANALYSES_TRIED,
    pacer: CallPacer | None = None,
) -> SonarResolutionAttempt:
    """Name the GitHub repository one SonarCloud project analyses, through its analysed commits.

    Neither side records the pairing, so the commit SHA is used as the identifier they share: read
    the project's most recent analyses, and search the organisation for each revision until a commit
    resolves. MORE THAN ONE ANALYSIS IS WALKED because the newest is often a pull-request analysis of
    a branch that has since been force-pushed or deleted, whose commit is then in no repository at
    all — while the analysis under it, on main, is permanent.

    Every outcome but `FAILED` is an answer about the project and is meant to be stored as one. A
    rate limit that survived the client's own retries is the exception that is RE-RAISED rather than
    classified: it will apply to every remaining project exactly as it applied to this one, so
    writing it into the map would record this run's exhaustion as the project's own dead end.
    """
    pacer = search_pacer(github_client) if pacer is None else pacer
    try:
        analyses = sonar_client.project_analyses(project, attempts)
    except SonarError as exception:
        return SonarResolutionAttempt(project, SonarMappingOutcome.FAILED, detail=str(exception))
    if not analyses:
        detail = "SonarCloud records no analysis of this project, so there is no commit to resolve it by"
        return SonarResolutionAttempt(project, SonarMappingOutcome.NO_ANALYSIS, detail=detail)
    revisions = searchable_revisions(analyses)
    if not revisions:
        detail = f"none of the {len(analyses)} most recent analyses names the commit it ran against"
        return SonarResolutionAttempt(project, SonarMappingOutcome.NO_REVISION, detail=detail)

    tried = 0
    for revision, analysis_at in revisions:
        tried += 1
        settled = resolve_revision(github_client, organization, project, revision, analysis_at, pacer)
        if settled is not None:
            # The cost is attached here rather than inside the search, so that the one place counting
            # searches is the one place issuing them.
            return replace(settled, analyses_tried=tried)
    detail = f"no commit in {organization} matches any of the {tried} most recently analysed revisions"
    return SonarResolutionAttempt(
        project,
        SonarMappingOutcome.UNKNOWN_COMMIT,
        analyses_tried=tried,
        detail=detail,
    )


@dataclass(frozen=True)
class SonarDeclaration:
    """What one repository's root `sonar-project.properties` says about its own SonarCloud project.

    A DECLARATION IS A HYPOTHESIS, NOT AN ANSWER. Measured over the organisation's 3,372
    repositories on 2026-08-27: 240 declare a key, 123 of those name a project SonarCloud does not
    list, and 69 sit in 26 collision groups where several repositories declare one key —
    `rpe-expressjs-template` is declared by 14 repositories scaffolded from the template that never
    changed it. Only 79 of the 240 are both visible and unique, so every declaration read here is
    put to a confirming check before it is believed.

    `organization` is carried because a key is only meaningful inside the organisation that holds
    it: a repository declaring a project in somebody else's SonarCloud organisation has said nothing
    about this one's.
    """

    project_key: str | None = None
    organization: str | None = None


def parse_properties(text: str) -> dict[str, str]:
    """Read a Java properties file well enough to find the two properties this module asks about.

    Later definitions win, as the format specifies, and comments are recognised only at the start of
    a line, again as the format specifies. Deliberately not a full properties parser: line
    continuations and escapes exist in the format and appear in none of the 240 declarations
    measured, and a parser that silently mis-read one would produce a key rather than an absence.
    """
    properties: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(PROPERTY_COMMENT_MARKERS):
            continue
        matched = PROPERTY_PATTERN.match(stripped)
        if matched is not None:
            properties[matched.group(1)] = matched.group(2).strip()
    return properties


def declared_project(text: str | None) -> SonarDeclaration | None:
    """Read one repository's declaration, or None where it has no `sonar-project.properties`.

    None means the file is not there; a declaration whose `project_key` is None means the file is
    there and leaves the key to the build — a real case, since a properties file can configure
    sources and exclusions and nothing else. The two are kept apart so the report can say which.
    """
    if text is None:
        return None
    properties = parse_properties(text)
    return SonarDeclaration(
        project_key=properties.get(PROJECT_KEY_PROPERTY) or None,
        organization=properties.get(ORGANIZATION_PROPERTY) or None,
    )


class SonarProjectMap(Protocol):
    """Read the stored `sonar → github` map in both directions, for one SonarCloud organisation.

    A protocol rather than the storage functions themselves, because the resolution ladder is the
    thing worth testing and it should be testable against a map somebody wrote by hand rather than
    against a database somebody had to populate first.
    """

    @property
    def sonar_organization(self) -> str:
        """Name the SonarCloud organisation this map holds, which is not always the GitHub one."""

    def project(self, project_key: str) -> StoredSonarMapping | None:
        """Return what the map knows about one project, or None when it has never been resolved."""

    def repository(self, repository: str) -> SonarRepositoryProject | None:
        """Return the project the map attributes to one repository, or None when none does."""


@dataclass(frozen=True)
class StoredProjectMap:
    """Serve the map out of the observations database, which is where `map-sonar` writes it."""

    path: Path
    sonar_organization: str

    def project(self, project_key: str) -> StoredSonarMapping | None:
        """Return what the map knows about one project, or None when it has never been resolved."""
        return load_sonar_mapping(self.path, self.sonar_organization, project_key)

    def repository(self, repository: str) -> SonarRepositoryProject | None:
        """Return the project the map attributes to one repository, or None when none does."""
        return repository_project(self.path, self.sonar_organization, repository)


@dataclass(frozen=True)
class SonarAttribution:
    """Name the SonarCloud project one repository's quality state should be read from, or say why not.

    Either `mapping` or `detail`, never both, exactly as the report it becomes requires. `reason` is
    set only where a CALL FAILED while resolving, which is the one outcome that makes a collection
    partial: a repository with no SonarCloud project is an observation, and most of the
    organisation's repositories are that.
    """

    mapping: SonarProjectMapping | None = None
    detail: str | None = None
    reason: AvailabilityReason | None = None


@dataclass(frozen=True)
class DeclarationCheck:
    """Say what testing one declared project key established.

    `mapping` is set where the declaration was CONFIRMED, and `note` where it was refuted or could
    not be tested — the note being what the report says instead, since "the repository declares a
    key" is not a fact worth reporting on its own. `reason` marks the failed-call case, which is
    carried out of here rather than raised so that the map's own answer is still consulted: a
    confirmation this run could not make does not discard a mapping an earlier run already paid for.
    """

    mapping: SonarProjectMapping | None = None
    note: str | None = None
    reason: AvailabilityReason | None = None


def names_repository(candidate: str, repository: str) -> bool:
    """Compare two repository names the way GitHub does, which is without regard to case."""
    return candidate.casefold() == repository.casefold()


def usable_declaration(declaration: SonarDeclaration | None, sonar_organization: str) -> tuple[str | None, str | None]:
    """Return the declared key worth testing, or None with the reason it is not worth testing.

    A key declared for another SonarCloud organisation is discarded here rather than tested: the
    project it names is not in the organisation this run reads, so confirming it would attribute one
    organisation's quality gate to another's repository.
    """
    if declaration is None:
        return None, None
    key = declaration.project_key
    if key is None:
        return None, f"{SONAR_PROPERTIES_PATH} declares no {PROJECT_KEY_PROPERTY}"
    declared_organization = declaration.organization
    if declared_organization is not None and not names_repository(declared_organization, sonar_organization):
        note = f"{SONAR_PROPERTIES_PATH} declares {key} in the {declared_organization} organisation, not {sonar_organization}"
        return None, note
    return key, None


def check_declaration(
    sonar_client: SonarClient,
    github_client: GitHubClient,
    organization: str,
    repository: str,
    key: str,
    project_map: SonarProjectMap,
) -> DeclarationCheck:
    """Test whether the repository that declares one project key is the repository that project analyses.

    The map answers first and for nothing, and it REFUTES as readily as it confirms: a declared key
    the map attributes to another repository is the `rpe-expressjs-template` case, where 14
    repositories declare one template's key and at most one of them owns it. Only where the map has
    never resolved the declared project is a call spent, and then it is the cheap one — the declared
    project's latest analysed commit, asked of this repository through the core quota rather than
    through the 30-a-minute search.
    """
    stored = project_map.project(key)
    if stored is not None and stored.mapping is not None:
        if not names_repository(stored.mapping.repository, repository):
            return DeclarationCheck(note=f"the declared project {key} is mapped to {stored.mapping.repository}")
        return DeclarationCheck(
            mapping=SonarProjectMapping(
                project_key=key,
                repository=stored.mapping.repository,
                method=SonarResolution.DECLARED_CONFIRMED_BY_MAP,
                analysis_at=stored.mapping.analysis_at,
                revision=stored.mapping.revision,
            ),
        )
    return confirm_by_commit(sonar_client, github_client, organization, repository, key)


def confirm_by_commit(
    sonar_client: SonarClient,
    github_client: GitHubClient,
    organization: str,
    repository: str,
    key: str,
) -> DeclarationCheck:
    """Ask whether the repository holds the commit the declared project was last analysed against.

    The cheap confirmation, and the only one available for a project `map-sonar` has never resolved:
    one SonarCloud read and one core-quota commit read, neither of them touching the 30-a-minute
    search. A project with no analysed commit cannot be confirmed this way and is left unconfirmed
    rather than refuted — nothing was learned about it, which is not the same as learning it is
    wrong.

    A DECLARED KEY SONARCLOUD DOES NOT LIST IS REFUTED, NOT A FAILED CALL. SonarCloud answers `404`
    for a project that does not exist, and 123 of the organisation's 240 declarations name one —
    they are stale keys left in properties files, exactly as GitHub's `404`/`422` on the commit read
    below means "this repository does not hold it". Carrying that out as a `reason` would record one
    repository's stale properties file as a collection failure and exit `3` for half the
    organisation. Only a read that was REFUSED — unauthenticated, forbidden, rate limited, or
    unparseable — keeps its reason.
    """
    try:
        revisions = searchable_revisions(sonar_client.project_analyses(key, 1))
    except SonarError as exception:
        refuted = exception.reason is AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE
        note = f"SonarCloud lists no project {key}" if refuted else str(exception)
        return DeclarationCheck(note=note, reason=None if refuted else exception.reason)
    if not revisions:
        return DeclarationCheck(note=f"the declared project {key} has no analysed commit to confirm it by")
    revision, analysis_at = revisions[0]
    try:
        confirmed = confirms_candidate(github_client, organization, repository, revision)
    except GitHubError as exception:
        return DeclarationCheck(note=str(exception), reason=exception.reason)
    if not confirmed:
        return DeclarationCheck(note=f"{repository} does not hold commit {revision}, the latest analysis of {key}")
    return DeclarationCheck(
        mapping=SonarProjectMapping(
            project_key=key,
            repository=repository,
            method=SonarResolution.DECLARED_CONFIRMED_BY_COMMIT,
            analysis_at=analysis_at,
            revision=revision,
        ),
    )


def mapped_project(claimed: SonarRepositoryProject, repository: str) -> SonarProjectMapping:
    """Restate the map's answer as this direction's resolution, keeping the evidence behind it.

    The method is rewritten to `STORED_MAP` because that is how THIS resolution was arrived at,
    while `analysis_at` and `revision` are the map's own — the commit that attributed the project to
    the repository in the first place, which is the evidence a doubted mapping is re-checked from.
    """
    if claimed.candidates > 1:
        logging.info(
            "%s is claimed by %s SonarCloud projects; reading the most recently analysed, %s",
            repository,
            claimed.candidates,
            claimed.mapping.project_key,
        )
    return SonarProjectMapping(
        project_key=claimed.mapping.project_key,
        repository=claimed.mapping.repository,
        method=SonarResolution.STORED_MAP,
        analysis_at=claimed.mapping.analysis_at,
        revision=claimed.mapping.revision,
    )


def github_to_sonar(
    sonar_client: SonarClient,
    github_client: GitHubClient,
    organization: str,
    repository: str,
    project_map: SonarProjectMap,
    override: str | None = None,
    declaration: SonarDeclaration | None = None,
) -> SonarAttribution:
    """Name the SonarCloud project one repository's quality state is read from, and say how.

    The ladder, strongest rung first:

    1. the CONFIGURED override, which is a human's instruction and beats every observation;
    2. the repository's own declared key, CONFIRMED by the map or by the declared project's latest
       analysed commit — and dropped where either REFUTES it;
    3. the map's own answer for this repository, built by `map-sonar` from analysed commits;
    4. otherwise nothing, with every reason collected on the way down.

    A NAME RULE IS NOT A RUNG. Matching a repository to a project by their names was measured over
    the whole organisation and rejected: it was wrong for 6 of the 70 repositories where it answered
    at all, and three of those six preferred an ABANDONED project to the live one — SonarCloud has
    no rename, so `rpx-xui-webapp_2` is the project still being analysed and `rpx-xui-webapp` is the
    one that went quiet in July. architecture.md records the measurement.

    The resolution method travels on the mapping rather than being inferred later, because a wrong
    mapping is diagnosable only if the block says which rung answered.
    """
    if override is not None:
        mapping = SonarProjectMapping(
            project_key=override,
            repository=repository,
            method=SonarResolution.CONFIGURED,
        )
        return SonarAttribution(mapping=mapping)
    key, unusable = usable_declaration(declaration, project_map.sonar_organization)
    notes = [] if unusable is None else [unusable]
    reason: AvailabilityReason | None = None
    refusal: str | None = None
    if key is not None:
        checked = check_declaration(sonar_client, github_client, organization, repository, key, project_map)
        if checked.mapping is not None:
            return SonarAttribution(mapping=checked.mapping)
        reason = checked.reason
        if checked.note is not None:
            notes.append(checked.note)
        if reason is not None:
            # Carried rather than read back off `notes`, which a reason is not required to have added
            # to: the two fields of a `DeclarationCheck` are independent.
            refusal = checked.note or reason.value
    claimed = project_map.repository(repository)
    if claimed is not None:
        if refusal is not None:
            logging.warning("%s: reading the stored map after a failed confirmation: %s", repository, refusal)
        return SonarAttribution(mapping=mapped_project(claimed, repository))
    notes.append(f"no SonarCloud project in {project_map.sonar_organization} is mapped to this repository")
    return SonarAttribution(detail="; ".join(notes), reason=reason)
