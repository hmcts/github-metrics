#!/usr/bin/env python3
"""Report who used GitHub Copilot over a window, through which modes, globally and per grouping.

    uv run scripts/report_copilot_usage.py [--config config.yml] [--org hmcts] [--days 90]
                                           [--until 2026-08-27] [--format report|json]
                                           [--cache .metrics/copilot] [--no-repositories]

Writes the report to stdout and progress to stderr, so stdout can be redirected into a file.

Authenticates the way `metrics collect` does, through `metrics.credentials`: a GitHub App
installation when GH_APP_ID, GH_APP_INSTALLATION_ID and GH_APP_PRIVATE_KEY_PATH (or
GH_APP_PRIVATE_KEY) are all set, and GH_TOKEN otherwise. An App is worth configuring here for the
same reason it is there — an installation's permissions are granted by the organisation rather than
intersected with a user's — and it is what makes the seat list readable without an owner's personal
token. This script keeps two fallbacks the main collection has no use for, GITHUB_TOKEN and
whatever `gh` is logged in as, because it is run from a developer's shell where one of those is
usually already present. This is user-run only: the loop that maintains plan.md never holds a
GitHub credential (architecture.md, "Access"), so neither this report nor its numbers can be
produced inside it.

`scripts/probe-copilot-usage.sh` probes `GET /orgs/{org}/copilot/metrics`, which is aggregate-only
and short-retention. GitHub has since replaced it with a reports API
(`X-GitHub-Api-Version: 2026-03-10`) that answers all three questions the probe could not:

    granularity  there is a per-user report, one record per user per day, naming the login.
    reach        reports exist from 2025-10-10 and are served for up to a year back, so a 90-day
                 window is readable today and nothing has to be snapshotted to obtain it.
    modes        every mode is a field, including the one this report exists to answer: `used_agent`
                 is IDE chat agent mode, and `chat_panel_agent_mode` is its interaction count.

The endpoints used, all of them read-only:

    /{scope}/copilot/metrics/reports/{aggregate}-1-day?day=  GitHub's org-wide counts per day
    /{scope}/copilot/metrics/reports/users-1-day?day=        one record per user per day
    /{scope}/copilot/metrics/reports/user-teams-1-day?day=   one record per user-team pair
    /{scope}/copilot/metrics/reports/repos-1-day?day=        one record per repository per day
    /orgs/{org}/copilot/billing                              seat total, for a denominator
    /{scope}/copilot/billing/seats                           who this scope pays for, and on what plan

where `{scope}` is `orgs/{org}` or `enterprises/{enterprise}` and `{aggregate}` is `organization`
or `enterprise` to match. Each answers a small object holding `download_links`, signed URLs to
NDJSON files. The download session carries no Authorization header: a signed URL is already
authorised, and some object stores reject a request that also authenticates.

The run costs one API call plus one file download per report per day: with `--days 90` that is about
270 calls against a 5,000/hour limit, and `--no-repositories` drops it to about 180. The seat list
adds one call per hundred seats, once, whatever the window. The run stays sequential because that is
well inside the quota and concurrency would only risk the secondary limits. `--cache DIR` keeps
every downloaded file, so a second run over the same window costs nothing and the report can be
re-cut without re-fetching.

Groupings cost one call, not one per team. There is a team-level metrics endpoint, but it would cost
a call per team per day. The `user-teams-1-day` report gives every user-team pair in a single file,
and this script rolls the per-user records up into teams itself, so team numbers and global numbers
are the same numbers added up two ways. `--team-days` asks for the most recent few days and unions
them, which costs those few calls and survives the newest day not being ready yet. Two things to
know about the team figures: GitHub omits teams with fewer than five seated Copilot users, so an
active user may have no team row at all and is reported under `(no team reported)`; and a user in
several teams is counted in each, so team columns sum to more than the global figure.

The data holds two kinds of "mode". They are different numbers and both are reported:

    surfaces are the per-day `used_*` booleans: did this user touch this surface that day. They
             count users and user-days. `used_agent` is IDE chat agent mode;
             `used_copilot_cloud_agent` (formerly `used_copilot_coding_agent`, and both names are
             still emitted during the rename, so both are reported) is the cloud agent, which
             happens on GitHub and not in the editor.
    features are the `totals_by_feature` breakdown, whose values include `chat_panel_ask_mode`,
             `chat_panel_edit_mode`, `chat_panel_agent_mode`, `chat_panel_plan_mode` and
             `code_completion`. These carry interaction and line counts.

So "how many used agent mode" is a surface question and "how much agent mode was used" is a feature
question, and a user can appear in one without the other if GitHub's telemetry saw only one of them.

Every bucket is discovered from the records. Nothing here hardcodes the set of surfaces, features,
IDEs, models, languages or agent apps. Any key beginning `used_` is a surface and any key beginning
`totals_by_` is a breakdown, keyed on whatever string fields its records carry and summing whatever
numeric fields they carry. A mode GitHub adds after this was written therefore appears in the report
by itself, labelled with GitHub's name for it. `SURFACE_LABELS` only supplies English for the ones
known today.

Usage and billing are different populations, which is why the seat list is read. The metrics reports
cover everyone active in the scope, because activity is attributed to the organisation whose
repositories it happened in. Billing is attributed to whoever bought the seat. Contractors and
supplier staff working in these repositories on their employer's subscription therefore appear in
full in every usage section and in none of the billing ones. `/copilot/billing/seats` is the only
call that draws the line, and without it a user on somebody else's subscription looks exactly like
one on ours who spent nothing. Two consequences run through the report: `ai_credits_used` reads
`n/a` for a seat billed elsewhere, since it measures what this scope was charged; and the models a
user reached are a property of their subscription's Copilot policy, so a model that appears only
under an external subscription is one this scope has not enabled, reaching these repositories
through a seat somebody else pays for.

The adoption phase is the one column that describes a different period from the window. GitHub
assigns `ai_adoption_phase` over a trailing 28-day window and recalculates it daily, so over a short
window every other per-user column describes the days asked for and this one describes the preceding
month. A Phase 3 user can therefore show almost no activity on the day reported. Phases measure
breadth of surfaces, not volume: Phase 1 is code completion and/or IDE agent mode, Phase 2 is one
GitHub-based agent surface (cloud agent, code review or CLI), Phase 3 is two or more of those or the
Copilot app, and No Cohort is below the threshold of two active days in the 28. The record also
carries GitHub's English name for the phase and the version of the model that assigned it, and the
report prints both, so a reader does not have to guess the criteria from a bare identifier.

A table cell says one of three things: `0` is a measurement GitHub
reported, `-` is a field GitHub did not report, and `n/a` is a quantity that belongs to a
subscription this scope does not pay for. Rendering an absence as `0` invents a measurement out of a
silence, and a column where both appear as zero says "nobody did this" and "we were never told" in
the same glyph. Every measure therefore reaches `number` as `mapping.get(name)` and never as
`mapping.get(name, 0.0)`.

Users who never used Copilot are absent by construction: the per-user report has no record for them,
so an idle user can only be found by subtracting who was active from who holds a seat, which is what
the seat list makes possible and why idle seats are named in the JSON and counted in the report.
Nobody outside this scope's billing can be named that way: a supplier's
idle staff hold no seat here to subtract from. Distinct-user counts are counts of distinct logins
over the whole window; summing daily counts would count the same person once per day. GitHub's
`monthly_active_users` is a trailing 28-day count, so it is reported unadjusted, beside the window
count.
"""

import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any

import requests
import yaml

from metrics.credentials import (
    ACCESS_TOKEN_VARIABLE,
    APP_IDENTIFIER_VARIABLE,
    INSTALLATION_IDENTIFIER_VARIABLE,
    AppInstallation,
    CredentialsError,
    GitHubCredentials,
    key_configured,
    resolve_credentials,
)

DEFAULT_API_URL = "https://api.github.com"
# config.yml, not hmcts.yml: the team files carry only `teams`, and naming one here would report a
# missing organisation as though the file were malformed. `probe-copilot-usage.sh` still defaults to
# hmcts.yml and no longer resolves an organisation from it.
DEFAULT_CONFIG = "config.yml"
API_VERSION = "2026-03-10"
JSON_ACCEPT = "application/vnd.github+json"
REQUEST_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 300
SUBPROCESS_TIMEOUT = 10

# Reports begin here; GitHub published nothing before it, so a window reaching further is clamped.
EARLIEST_REPORT_DAY = date(2025, 10, 10)
DEFAULT_DAYS = 90
DEFAULT_TEAM_DAYS = 3

MAXIMUM_NETWORK_ATTEMPTS = 3
SEAT_PAGE_SIZE = 100
# A backstop against an endpoint that never says it has finished: a hundred pages of a hundred seats
# is far more than any organisation this reports on holds.
MAXIMUM_SEAT_PAGES = 100
NETWORK_RETRY_SECONDS = 2
MAXIMUM_RATE_PAUSES = 3
RATE_PAUSE_SECONDS = 60
NETWORK_FAILURE_STATUS = -1
UNREACHABLE = "unreachable"
RATE_LIMIT_MARKERS = ("rate limit", "secondary rate", "abuse")

GZIP_MAGIC = b"\x1f\x8b"

SURFACE_PREFIX = "used_"
BREAKDOWN_PREFIX = "totals_by_"
# Nested version stamps describe the client, so they are excluded from every sum.
VERSION_PREFIX = "last_known"
# Identifiers happen to be numbers, so they are excluded from every sum.
IDENTITY_KEYS = frozenset({"user_id", "team_id", "organization_id", "enterprise_id"})
# Carried for display only: GitHub warns that agent display names change, so `agent_id` is the key.
LABEL_KEYS = frozenset({"agent_name"})
# Averages cannot be added together, so they are dropped. See `flatten_measures`.
AVERAGE_MARKERS = ("avg_", "_per_", "median_")
# Fields that NAME a bucket while arriving as a number. An adoption-phase record carries `phase` as a
# small integer beside its English name, and summing it across ninety days would produce a figure
# that looks like a measurement and is the sum of a label. Listed by name, because nothing about the
# value distinguishes a phase number from a count.
NUMERIC_DIMENSIONS = frozenset({"phase", "phase_id"})
# Several spellings, because these fields are newer than this script and GitHub has renamed fields in
# this API before: `used_copilot_coding_agent` became `used_copilot_cloud_agent` within its lifetime.
PHASE_NAME_KEYS = ("phase_name", "name", "display_name", "label", "title")
PHASE_VERSION_KEYS = ("version", "model_version", "phase_version", "ai_adoption_phase_version")
# GitHub's published criteria, for phases whose record carries no name of its own. A phase absent
# from here still appears, under GitHub's identifier and without a gloss, exactly as a new surface
# does: this table supplies English only.
PHASE_CRITERIA = {
    "No Cohort": "below the engagement threshold for any phase (needs 2+ active days in the 28)",
    "Phase 1": "code completion and/or IDE agent mode",
    "Phase 2": "one GitHub-based agent surface: cloud agent, code review or CLI",
    "Phase 3": "two or more of those surfaces on 2+ days, or the Copilot app",
}
PHASE_BREAKDOWN = "totals_by_ai_adoption_phase"
NO_TEAM = "(no team reported)"

# The three things a table cell can say. `0` is a measurement: GitHub reported this quantity and it
# was zero. ABSENT is the lack of one: GitHub reported no such field, and rendering that as `0` would
# invent a measurement out of a silence. NOT_APPLICABLE is narrower still: the quantity is real, but
# it belongs to a subscription this scope does not pay for, so it is not ours to report either way.
# See `number` and `credits_cell`.
ABSENT = "-"
NOT_APPLICABLE = "n/a"
PLACEHOLDER_CELLS = frozenset({ABSENT, NOT_APPLICABLE})

# An active user whose login holds no seat in THIS scope's billing. Their seat is paid for somewhere
# this token cannot see, typically their employer's subscription.
EXTERNAL_SUBSCRIPTION = "(billed outside this scope)"
# The seat list could not be read at all. Reporting that as everyone being external would state
# something specific and wrong. See `Seats.subscription`.
UNKNOWN_SUBSCRIPTION = "(subscription unknown)"
SPECIAL_SUBSCRIPTIONS = (EXTERNAL_SUBSCRIPTION, UNKNOWN_SUBSCRIPTION)

# English for the surfaces known when this was written. Anything absent is reported under GitHub's
# field name, and the columns still appear.
SURFACE_LABELS = {
    "used_agent": "IDE chat, agent mode",
    "used_chat": "IDE chat, any mode",
    "used_cli": "Copilot CLI",
    "used_copilot_app": "Copilot app",
    "used_copilot_cloud_agent": "Copilot cloud agent",
    "used_copilot_coding_agent": "Copilot cloud agent (former field name)",
    "used_copilot_code_review_active": "Copilot code review, engaged with",
    "used_copilot_code_review_passive": "Copilot code review, auto-assigned",
}
SURFACE_COLUMNS = {
    "used_agent": "agent",
    "used_chat": "chat",
    "used_cli": "cli",
    "used_copilot_app": "app",
    "used_copilot_cloud_agent": "cloud",
    "used_copilot_coding_agent": "cloud~",
    "used_copilot_code_review_active": "review",
    "used_copilot_code_review_passive": "review~",
}
AGENT_SURFACE = "used_agent"
# Matches `chat_panel_agent_mode` and anything GitHub names in the same family later.
AGENT_FEATURE = re.compile(r"agent_mode")
FEATURE_BREAKDOWN = "totals_by_feature"
# The breakdowns that have a section of their own below. Anything GitHub adds later is absent from
# this set, which is how it earns a section of its own.
KNOWN_BREAKDOWNS = frozenset(
    {
        FEATURE_BREAKDOWN,
        "totals_by_ide",
        "totals_by_model_feature",
        "totals_by_language_feature",
        "totals_by_language_model",
        "totals_by_3rd_party_agent",
        "totals_by_cli",
        "totals_by_copilot_app",
    }
)
INTERACTIONS = "user_initiated_interaction_count"
GENERATIONS = "code_generation_activity_count"
ACCEPTANCES = "code_acceptance_activity_count"
CREDITS = "ai_credits_used"

# A cell holding only a formatted number is right-aligned; anything else is left-aligned.
NUMERIC_CELL = re.compile(r"[-+]?[\d,]+(?:\.\d+)?%?")
HEADLINE_MEASURES = (INTERACTIONS, GENERATIONS, ACCEPTANCES, "loc_added_sum", "loc_deleted_sum")

Row = dict[str, Any]
Dimensions = tuple[tuple[str, str], ...]


class AccessError(RuntimeError):
    """The token may not read these reports, which is true of every day in the window."""


class RateLimitError(RuntimeError):
    """The quota did not clear within the pauses this run is willing to take."""


@dataclass(frozen=True)
class Options:
    """Everything the command line settled."""

    scope_kind: str
    scope_name: str
    aggregate_report: str
    days: int
    until: date
    team_days: int
    repositories: bool
    output_format: str
    cache: Path | None


@dataclass(frozen=True)
class Response:
    """One HTTP response: the status that classifies it, the body it belongs to, and its request.

    The request URL is carried so that every failure can name the call that failed. 403 on the
    metrics report and 403 on the billing endpoint are different problems with different fixes, and
    a message saying only "403" leaves the reader guessing which one it met.
    """

    status: int
    text: str
    method: str = "GET"
    url: str = ""

    def mapping(self) -> Mapping[str, Any]:
        """Return the body as a JSON object, or an empty one when it is anything else."""
        try:
            body = json.loads(self.text)
        except ValueError:
            return {}
        return body if isinstance(body, Mapping) else {}

    def message(self) -> str:
        """Return GitHub's error message, or an empty string when it gave none."""
        return str(self.mapping().get("message", ""))

    def failure(self) -> str:
        """Describe a call that did not answer: the status, the call made, and GitHub's words.

        Used for every outcome other than success, so no failure is ever reported as a bare status.
        """
        described = f"{describe_status(self.status)} on {self.method} {self.url or 'an unnamed URL'}"
        message = self.message() or (self.text.strip()[:200] if self.status == NETWORK_FAILURE_STATUS else "")
        return f"{described}: {message}" if message else described


@dataclass(frozen=True)
class Fetched:
    """How one report request went, and whatever records came back from it.

    `outcome` separates the four answers it must keep apart: `ok` (records), `empty` (GitHub
    says there was no activity that day), `absent` (no report for that day) and `failed` (a status
    that answered neither way). An `absent` day is a gap in GitHub's publishing; an `empty` day is
    evidence that nobody used Copilot.
    """

    outcome: str
    rows: tuple[Row, ...]
    detail: str


@dataclass(frozen=True)
class Phase:
    """One user's AI adoption cohort as GitHub classified it, with GitHub's English for it.

    GitHub assigns this over a trailing 28-day window and recalculates it daily, so it is the one
    per-user field in this report that describes a period other than the window asked for. In a
    one-day run every other column is that day and this one is the preceding month.
    """

    identifier: str
    name: str
    version: str

    def label(self) -> str:
        """Name the phase as GitHub names it, adding its English only when that says something new."""
        return f"{self.identifier} ({self.name})" if self.name and self.name != self.identifier else self.identifier


@dataclass
class Bucket:
    """Distinct users, user-days and summed measures for one value of one breakdown."""

    label: str = ""
    users: set[str] = field(default_factory=set)
    user_days: int = 0
    measures: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))

    def absorb(self, login: str, measures: Iterable[tuple[str, float]]) -> None:
        """Add one user's one day to this bucket."""
        self.users.add(login)
        self.user_days += 1
        for name, value in measures:
            self.measures[name] += value


@dataclass
class Rollup:
    """One population's usage, added up. The same type serves the whole org, a team and one user."""

    users: set[str] = field(default_factory=set)
    days: set[str] = field(default_factory=set)
    totals: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    surfaces: dict[str, Bucket] = field(default_factory=dict)
    breakdowns: dict[str, dict[Dimensions, Bucket]] = field(default_factory=dict)
    # The most recent phase seen for each user, because a phase is a state and not a quantity.
    phases: dict[str, tuple[str, Phase]] = field(default_factory=dict)

    def surface_users(self, flag: str) -> int:
        """Count the distinct users who touched one surface at least once."""
        bucket = self.surfaces.get(flag)
        return 0 if bucket is None else len(bucket.users)

    def surface_days(self, flag: str) -> int:
        """Count the user-days on which one surface was touched."""
        bucket = self.surfaces.get(flag)
        return 0 if bucket is None else bucket.user_days


@dataclass(frozen=True)
class Person:
    """One user who used Copilot, with the teams they were reported in."""

    login: str
    user_id: str
    teams: tuple[str, ...]
    rollup: Rollup
    subscription: str = UNKNOWN_SUBSCRIPTION
    seat: Seat | None = None

    def agent_interactions(self) -> float:
        """Sum the interactions this user sent in any chat mode GitHub calls an agent mode."""
        buckets = self.rollup.breakdowns.get(FEATURE_BREAKDOWN, {})
        return sum(
            bucket.measures.get(INTERACTIONS, 0.0)
            for dimensions, bucket in buckets.items()
            if any(AGENT_FEATURE.search(value) for _, value in dimensions)
        )


@dataclass(frozen=True)
class Seat:
    """One seat THIS scope pays for, and what its billing record says about it."""

    login: str
    plan_type: str
    assigning_team: str
    last_activity: str


@dataclass(frozen=True)
class Seats:
    """Who this scope bills for Copilot, or why that could not be established.

    `available` carries the difference between "this user is on somebody else's subscription" and
    "we could not read the seat list", which the report must keep apart. Without the list, no active
    user can be seen to hold a seat, so treating a failure as an answer would report the whole
    organisation as externally billed.
    """

    available: bool
    total: int | None
    by_login: Mapping[str, Seat]
    detail: str

    def subscription(self, scope_name: str, login: str) -> str:
        """Name the subscription one active user's seat is billed to, as far as this scope can see."""
        if not self.available:
            return UNKNOWN_SUBSCRIPTION
        seat = self.by_login.get(login)
        if seat is None:
            return EXTERNAL_SUBSCRIPTION
        return f"{scope_name} ({seat.plan_type})" if seat.plan_type else scope_name

    def idle(self, active: Iterable[str]) -> list[str]:
        """Name the seats this scope pays for that nobody used in the window.

        The one population the per-user report cannot produce, because it holds no record for a user
        who did nothing. It exists only by subtracting who was active from who is paid for.
        """
        if not self.available:
            return []
        used = set(active)
        return sorted(login for login in self.by_login if login not in used)


@dataclass(frozen=True)
class Collection:
    """Everything fetched, before any of it is added up.

    `day_outcomes` is kept per report so the report can say how many days it actually saw, which is
    the difference between "nobody used agent mode" and "GitHub published nothing that day".
    """

    aggregate_rows: tuple[Row, ...]
    user_rows: tuple[Row, ...]
    repository_rows: tuple[Row, ...]
    teams: Mapping[str, tuple[str, ...]]
    team_day: str
    day_outcomes: Mapping[str, Counter[str]]
    seats: Mapping[str, Any] | None
    seat_assignments: Seats


def progress(message: str) -> None:
    """Write a line to stderr for the human watching, keeping stdout for the report."""
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def describe_status(status: int) -> str:
    """Name a status for reporting, including the pseudo-status for a call that never answered."""
    return UNREACHABLE if status == NETWORK_FAILURE_STATUS else f"HTTP {status}"


def is_rate_limited(response: Response) -> bool:
    """Tell a rate limit apart from a refusal: both answer 403, and only the body says which."""
    if response.status not in {HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS}:
        return False
    lowered = response.text.lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


class Reader:
    """Reads the GitHub API, turning a dropped connection into a status and a rate limit into a stop.

    A call that never answers becomes a pseudo-status, so one unreadable day leaves one gap in the
    window and the run carries on. A rate limit raises instead, because it applies to every
    remaining day just as it applied to this one, and days written under it would record this run's
    exhaustion as the organisation's idleness.

    The credential is asked for a token per request rather than pinned into the session's headers,
    exactly as `GitHubClient` does it. A GitHub App installation token lives an hour and is replaced
    inside a margin before that, and a ninety-day window with a per-repository report runs long
    enough to cross the boundary, so a token frozen at startup would stop working mid-window and
    every remaining day would be recorded as refused.
    """

    def __init__(self, credentials: GitHubCredentials) -> None:
        """Prepare a session carrying the pinned API version, taking the token per call instead."""
        self.base_url = os.environ.get("GITHUB_API_URL", DEFAULT_API_URL).rstrip("/")
        self.credentials = credentials
        self.calls = 0
        self.rate_pauses = 0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": JSON_ACCEPT,
                "X-GitHub-Api-Version": API_VERSION,
            }
        )

    def authorization(self) -> dict[str, str]:
        """Return the Authorization header for ONE request, minting or renewing a token if needed."""
        return {"Authorization": f"Bearer {self.credentials.token()}"}

    def get(self, path: str, parameters: Mapping[str, Any] | None = None) -> Response:
        """Fetch one path, retrying a transient network failure and briefly pausing a rate limit.

        A 401 is retried ONCE, and only when the credential can produce a different token: an App
        installation mints a replacement, and a personal access token says it cannot, in which case
        the 401 is returned and stops the run. Without this, a token revoked or expired mid-window
        would be reported as the token never having had the role.
        """
        pauses = 0
        failures = 0
        refreshed = False
        requested = f"{self.base_url}{path}"
        while True:
            try:
                self.calls += 1
                raw = self.session.get(
                    requested, headers=self.authorization(), params=parameters, timeout=REQUEST_TIMEOUT
                )
            except requests.RequestException as failure:
                failures += 1
                if failures >= MAXIMUM_NETWORK_ATTEMPTS:
                    # No response to read the URL back from, so the one asked for is carried instead.
                    return Response(NETWORK_FAILURE_STATUS, str(failure), url=requested)
                time.sleep(NETWORK_RETRY_SECONDS * failures)
                continue
            # raw.url rather than the path, so the query GitHub actually received is what gets
            # reported: the `day` a report was refused for is half of what makes a 404 legible.
            response = Response(raw.status_code, raw.text, url=raw.url)
            if response.status == HTTPStatus.UNAUTHORIZED and not refreshed and self.credentials.refresh():
                refreshed = True
                continue
            if not is_rate_limited(response):
                return response
            if pauses >= MAXIMUM_RATE_PAUSES:
                raise RateLimitError(response.failure())
            pauses += 1
            self.rate_pauses += 1
            progress(f"  rate limited: waiting {RATE_PAUSE_SECONDS}s (pause {pauses} of {MAXIMUM_RATE_PAUSES})")
            time.sleep(RATE_PAUSE_SECONDS)


def decode_rows(payload: bytes) -> list[Row]:
    """Read one downloaded report file into records, whatever of three shapes it arrives in.

    The files are documented as NDJSON, but a gzipped body and a plain JSON array are both cheap to
    accept and impossible to confuse with it, so all three are read. A line that will not parse is
    skipped, so one malformed record does not cost the other several thousand.
    """
    if payload.startswith(GZIP_MAGIC):
        payload = gzip.decompress(payload)
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    try:
        whole = json.loads(text)
    except ValueError:
        pass
    else:
        if isinstance(whole, list):
            return [record for record in whole if isinstance(record, dict)]
        return [whole] if isinstance(whole, dict) else []
    rows: list[Row] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


def download_session() -> requests.Session:
    """Prepare a session for the signed URLs, deliberately carrying no Authorization header.

    A signed URL is already authorised by its signature, and some object stores reject a request
    that authenticates two ways at once, which looks exactly like a report that could not be read.
    """
    return requests.Session()


def cache_path(options: Options, report: str, day: date) -> Path | None:
    """Name the file one day's report is cached in, or None when no cache was asked for."""
    if options.cache is None:
        return None
    return options.cache / f"{options.scope_kind}-{options.scope_name}-{report}-{day.isoformat()}.ndjson"


def report_path(options: Options, report: str) -> str:
    """Build the endpoint path for one report under whichever scope was asked for."""
    return f"/{options.scope_kind}/{options.scope_name}/copilot/metrics/reports/{report}-1-day"


def request_links(reader: Reader, options: Options, report: str, day: date) -> Fetched | list[str]:
    """Ask for one day's download links, or classify why there are none.

    A 403 raises, because permission is a property of the token and not of the day: carrying on
    would spend a call per remaining day to learn the same thing ninety times.
    """
    response = reader.get(report_path(options, report), {"day": day.isoformat()})
    if response.status == HTTPStatus.NO_CONTENT:
        return Fetched("empty", (), "GitHub reported no activity")
    if response.status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        # 401 and 403 are both properties of the token rather than of the day, so both stop the run.
        # A rate-limited 403 never reaches here: `Reader.get` pauses on it and only raises once the
        # pauses are spent, which is a different failure with a different exit.
        raise AccessError(response.failure())
    if response.status == HTTPStatus.NOT_FOUND:
        return Fetched("absent", (), response.failure())
    if response.status != HTTPStatus.OK:
        return Fetched("failed", (), response.failure())
    links = response.mapping().get("download_links")
    if not isinstance(links, list) or not links:
        return Fetched("empty", (), f"HTTP 200 on GET {response.url} named no download links")
    return [str(link) for link in links]


def unsigned(url: str) -> str:
    """Strip the query from a signed URL so it can be named in a failure without leaking its key.

    A download link carries credentials as query parameters. The path identifies the file
    well enough to debug with, and printing the signature into a log or a report would publish a
    working, if short-lived, credential.
    """
    return url.split("?", 1)[0] + ("?<signature omitted>" if "?" in url else "")


def download_rows(downloads: requests.Session, links: Sequence[str]) -> Fetched | list[Row]:
    """Read every file behind one day's links into records, or classify the download that failed."""
    rows: list[Row] = []
    for link in links:
        try:
            file = downloads.get(link, timeout=DOWNLOAD_TIMEOUT)
        except requests.RequestException as failure:
            return Fetched("failed", (), f"{UNREACHABLE} on GET {unsigned(link)}: {failure}")
        if file.status_code != HTTPStatus.OK:
            return Fetched("failed", (), f"{describe_status(file.status_code)} on GET {unsigned(link)}")
        rows.extend(decode_rows(file.content))
    return rows


def fetch_report(reader: Reader, downloads: requests.Session, options: Options, report: str, day: date) -> Fetched:
    """Fetch one report for one day, from the cache when it is there and from GitHub when it is not."""
    cached = cache_path(options, report, day)
    if cached is not None and cached.exists():
        # Classified from the records, exactly as a fresh fetch is, so a cached day and a fetched day
        # are counted the same way in the outcome tally.
        reused = tuple(decode_rows(cached.read_bytes()))
        return Fetched("ok" if reused else "empty", reused, "cached")

    links = request_links(reader, options, report, day)
    if isinstance(links, Fetched):
        return links
    rows = download_rows(downloads, links)
    if isinstance(rows, Fetched):
        return rows

    # Only a day that produced records is cached. A day GitHub has not published yet must be asked
    # for again next run; caching it would remember it as a day on which nobody used Copilot.
    if cached is not None and rows:
        cached.parent.mkdir(parents=True, exist_ok=True)
        # Cached as the records this run read, not as the bytes GitHub sent: several links become one
        # file, and NDJSON is what a rerun expects to find whatever shape the download arrived in.
        cached.write_text("".join(f"{json.dumps(record)}\n" for record in rows), encoding="utf-8")

    return Fetched("ok" if rows else "empty", tuple(rows), f"{len(rows)} records")


def read_seats(reader: Reader, options: Options) -> Mapping[str, Any] | None:
    """Read the seat total, the only denominator for "how many of our people used it".

    One call, and a failure is survivable: without it the report gives counts without shares, which
    is a smaller answer than the one asked for.
    """
    if options.scope_kind == "orgs":
        response = reader.get(f"/orgs/{options.scope_name}/copilot/billing")
        breakdown = response.mapping().get("seat_breakdown")
        if response.status == HTTPStatus.OK and isinstance(breakdown, Mapping):
            return dict(breakdown)
    else:
        response = reader.get(f"/enterprises/{options.scope_name}/copilot/billing/seats", {"per_page": 1})
        total = response.mapping().get("total_seats")
        if response.status == HTTPStatus.OK and isinstance(total, int):
            return {"total": total}
    progress(f"  seats unavailable, shares of seats will be omitted: {response.failure()}")
    return None


def describe_seat(record: Mapping[str, Any]) -> Seat | None:
    """Read one seat record, which names its holder in a nested assignee rather than at the top."""
    assignee = record.get("assignee")
    login = str(assignee.get("login") or "") if isinstance(assignee, Mapping) else ""
    if not login:
        return None
    team = record.get("assigning_team")
    return Seat(
        login=login,
        plan_type=str(record.get("plan_type") or ""),
        # The team a seat was assigned through, which is not the same as the teams its holder is in:
        # membership of a team named after Copilot does not mean the seat came from it.
        assigning_team=str(team.get("slug") or "") if isinstance(team, Mapping) else "",
        last_activity=str(record.get("last_activity_at") or ""),
    )


def read_seat_assignments(reader: Reader, options: Options) -> Seats:
    """List every seat this scope pays for, so a user we bill can be told from a user we merely see.

    This is the only call that answers whose subscription a user is on. The metrics reports cover
    everyone active in the scope regardless of who pays for them, because activity is attributed to
    the organisation it happened in; billing is attributed to the entity that bought the seat. A
    supplier's staff working in these repositories therefore appear in every usage section and in
    none of the billing ones, and only this endpoint can draw the line between the two.

    A failure is carried, not raised: the usage half of the report does not depend on knowing who
    pays, so the report says the subscriptions are unknown and reports the usage anyway.
    """
    path = f"/{options.scope_kind}/{options.scope_name}/copilot/billing/seats"
    seats: dict[str, Seat] = {}
    total: int | None = None
    for page in range(1, MAXIMUM_SEAT_PAGES + 1):
        response = reader.get(path, {"per_page": SEAT_PAGE_SIZE, "page": page})
        if response.status != HTTPStatus.OK:
            progress(f"  seats not listable, subscriptions will be reported as unknown: {response.failure()}")
            return Seats(available=False, total=None, by_login={}, detail=response.failure())
        body = response.mapping()
        if total is None and isinstance(body.get("total_seats"), int):
            total = int(body["total_seats"])
        listed = body.get("seats")
        if not isinstance(listed, list) or not listed:
            break
        for record in listed:
            if isinstance(record, Mapping):
                seat = describe_seat(record)
                if seat is not None:
                    seats[seat.login] = seat
        if len(listed) < SEAT_PAGE_SIZE:
            break
    progress(f"  read {len(seats):,} seat assignments")
    return Seats(
        available=True,
        total=total if total is not None else len(seats),
        by_login=seats,
        detail=f"{len(seats):,} seats listed",
    )


def collect_teams(
    reader: Reader, downloads: requests.Session, options: Options, days: Sequence[date]
) -> tuple[dict[str, tuple[str, ...]], str]:
    """Read the user-team pairs from the most recent days that have them, unioning what they say.

    Team membership is a state, not a series, so the newest reading wins; the union exists only
    because the newest day or two may not be published yet. The day actually used is returned so the
    report can date its groupings.
    """
    memberships: dict[str, set[str]] = defaultdict(set)
    used: list[str] = []
    for day in days:
        fetched = fetch_report(reader, downloads, options, "user-teams", day)
        if fetched.outcome != "ok":
            continue
        for row in fetched.rows:
            login = str(row.get("user_login") or "")
            slug = str(row.get("slug") or "")
            if login and slug:
                memberships[login].add(slug)
        used.append(day.isoformat())
        if memberships:
            break
    return ({login: tuple(sorted(slugs)) for login, slugs in memberships.items()}, ", ".join(used) or "none")


def window_days(options: Options) -> list[date]:
    """List the days of the window, oldest first, clamped to the first day GitHub has reports for."""
    start = max(options.until - timedelta(days=options.days - 1), EARLIEST_REPORT_DAY)
    span = (options.until - start).days + 1
    return [start + timedelta(days=offset) for offset in range(span)]


def wanted_reports(options: Options) -> tuple[str, ...]:
    """Name the per-day reports this run will ask for, in the order they are fetched."""
    reports = (options.aggregate_report, "users")
    return (*reports, "repos") if options.repositories else reports


def collect(reader: Reader, downloads: requests.Session, options: Options) -> Collection:
    """Fetch every day's reports, one day at a time, reporting each day's outcome as it goes."""
    days = window_days(options)
    reports = wanted_reports(options)
    gathered: dict[str, list[Row]] = {report: [] for report in reports}
    outcomes: dict[str, Counter[str]] = {report: Counter() for report in reports}

    progress(f"fetching {len(reports)} report(s) for each of {len(days)} days")
    for position, day in enumerate(days, start=1):
        notes: list[str] = []
        for report in reports:
            fetched = fetch_report(reader, downloads, options, report, day)
            outcomes[report][fetched.outcome] += 1
            gathered[report].extend(fetched.rows)
            notes.append(f"{report} {fetched.outcome}" + (f" ({fetched.detail})" if fetched.detail else ""))
        progress(f"  [{position}/{len(days)}] {day.isoformat()}  " + "; ".join(notes))

    teams, team_day = collect_teams(reader, downloads, options, list(reversed(days))[: options.team_days])
    return Collection(
        aggregate_rows=tuple(gathered[options.aggregate_report]),
        user_rows=tuple(gathered["users"]),
        repository_rows=tuple(gathered.get("repos", [])),
        teams=teams,
        team_day=team_day,
        day_outcomes=outcomes,
        seats=read_seats(reader, options),
        seat_assignments=read_seat_assignments(reader, options),
    )


def flatten_measures(entry: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, float]]:
    """Yield every numeric addable measure in one record, descending into nested totals.

    Three kinds of number are refused. Booleans, because `True` is an int in Python and adding it up
    would invent a measure out of a flag. Version stamps and identifiers, because they describe the
    client or name the row. And averages, anything GitHub named `avg_...` or `..._per_...`, because
    adding averages together produces a number that is an average of nothing: `avg_tokens_per_request`
    over five days is not five times one day's figure. Recomputing them would need denominators
    GitHub does not publish per bucket, so they are dropped and the report says they are dropped.
    """
    for name, value in entry.items():
        if name.startswith(VERSION_PREFIX) or name in IDENTITY_KEYS or name in NUMERIC_DIMENSIONS:
            continue
        if any(marker in name for marker in AVERAGE_MARKERS):
            continue
        full = f"{prefix}{name}"
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            yield full, float(value)
        elif isinstance(value, Mapping):
            yield from flatten_measures(value, f"{full}.")


def entry_dimensions(entry: Mapping[str, Any]) -> Dimensions:
    """Key one breakdown record on the fields that name it, ignoring the ones that measure it.

    Every string field, so a record naming `language` and `feature` keys a different bucket from one
    naming `feature` alone, and a breakdown invented after this was written keys itself correctly
    without being described here first. Plus the handful of fields that name a bucket while arriving
    as a number, which cannot be told from a measurement by looking at them: see NUMERIC_DIMENSIONS.
    """
    return tuple(
        sorted(
            (name, str(value))
            for name, value in entry.items()
            if name not in LABEL_KEYS and (isinstance(value, str) or (name in NUMERIC_DIMENSIONS and value is not None))
        )
    )


def absorb_entry(rollup: Rollup, breakdown: str, entry: object, login: str) -> None:
    """Add one record of one breakdown to the rollup, keyed on whatever names it carries."""
    if not isinstance(entry, Mapping):
        return
    dimensions = entry_dimensions(entry)
    label = str(entry.get("agent_name") or "")
    bucket = rollup.breakdowns.setdefault(breakdown, {}).setdefault(dimensions, Bucket(label=label))
    if label and not bucket.label:
        bucket.label = label
    bucket.absorb(login, flatten_measures(entry))


def describe_phase(entry: Mapping[str, Any]) -> Phase | None:
    """Read one adoption-phase record, keeping GitHub's English for it alongside the identifier.

    The record carries an identifier, a human-readable name and the version of the model that
    assigned it. Reading only the identifier leaves the report saying `Phase 1` where GitHub said
    `Phase 1 (Code first)`, and leaves a reader to guess criteria that were shipped alongside the
    number. The name and version keys are looked for under several spellings, because they are newer
    than this script and GitHub has renamed fields in this API before.
    """
    identifier = entry.get("phase")
    # `or ""` would discard a legitimate phase 0, which is the No Cohort classification.
    named = "" if identifier is None or identifier == "" else str(identifier)
    if not named:
        return None
    return Phase(
        identifier=named,
        name=first_string(entry, PHASE_NAME_KEYS),
        version=first_string(entry, PHASE_VERSION_KEYS),
    )


def first_string(entry: Mapping[str, Any], keys: Sequence[str]) -> str:
    """Return the first of several spellings of one field that the record actually carries."""
    return next((str(entry[key]) for key in keys if isinstance(entry.get(key), str) and entry[key]), "")


def absorb_phase(rollup: Rollup, row: Row, login: str, day: str) -> None:
    """Record the user's adoption phase, keeping the most recent day's reading.

    A phase is a state, not a quantity, and GitHub recalculates it daily over a trailing 28-day
    window, so the newest reading replaces the older one.
    """
    entry = row.get("ai_adoption_phase")
    if not isinstance(entry, Mapping):
        return
    phase = describe_phase(entry)
    if phase is None:
        return
    known = rollup.phases.get(login)
    if known is None or day >= known[0]:
        rollup.phases[login] = (day, phase)


def absorb_row(rollup: Rollup, row: Row) -> None:
    """Add one user's one day to a rollup, discovering its surfaces and breakdowns as it goes."""
    login = str(row.get("user_login") or "")
    if not login:
        return
    day = str(row.get("day") or "")
    rollup.users.add(login)
    if day:
        rollup.days.add(day)
    for name, value in row.items():
        if isinstance(value, bool):
            if value and name.startswith(SURFACE_PREFIX):
                rollup.surfaces.setdefault(name, Bucket()).absorb(login, ())
        elif isinstance(value, int | float):
            if name not in IDENTITY_KEYS:
                rollup.totals[name] += float(value)
        elif name.startswith(BREAKDOWN_PREFIX):
            entries = value if isinstance(value, list) else [value]
            for entry in entries:
                absorb_entry(rollup, name, entry, login)
    absorb_phase(rollup, row, login, day)


@dataclass(frozen=True)
class Rollups:
    """The same records added up three ways, so the three always agree."""

    everyone: Rollup
    people: tuple[Person, ...]
    teams: Mapping[str, Rollup]
    subscriptions: Mapping[str, Rollup]


def build_rollups(
    rows: Sequence[Row], memberships: Mapping[str, tuple[str, ...]], seats: Seats, scope_name: str
) -> Rollups:
    """Add the per-user records up globally, per user, per team and per subscription in one pass.

    Every grouping is built from the same records as the global one, so a group column and the global
    column are one measurement added up two ways. A user in several teams is absorbed into each of
    them, which is why teams do not partition the population. Subscriptions do partition it: a login
    holds a seat in this scope's billing or it does not, so those columns sum to the global figure
    exactly and any drift is a bug.
    """
    everyone = Rollup()
    per_user: dict[str, Rollup] = {}
    per_team: dict[str, Rollup] = {}
    per_subscription: dict[str, Rollup] = {}
    identifiers: dict[str, str] = {}

    for row in rows:
        login = str(row.get("user_login") or "")
        if not login:
            continue
        identifiers.setdefault(login, str(row.get("user_id") or ""))
        absorb_row(everyone, row)
        absorb_row(per_user.setdefault(login, Rollup()), row)
        for slug in memberships.get(login) or (NO_TEAM,):
            absorb_row(per_team.setdefault(slug, Rollup()), row)
        absorb_row(per_subscription.setdefault(seats.subscription(scope_name, login), Rollup()), row)

    people = tuple(
        Person(
            login=login,
            user_id=identifiers.get(login, ""),
            teams=memberships.get(login, ()),
            rollup=rollup,
            subscription=seats.subscription(scope_name, login),
            seat=seats.by_login.get(login),
        )
        for login, rollup in per_user.items()
    )
    return Rollups(everyone=everyone, people=people, teams=per_team, subscriptions=per_subscription)


def rank_people(people: Sequence[Person]) -> list[Person]:
    """Order users by the thing this report was asked for first: agent-mode use, then volume."""
    return sorted(
        people,
        key=lambda person: (
            -person.rollup.surface_days(AGENT_SURFACE),
            -person.agent_interactions(),
            -person.rollup.totals.get(INTERACTIONS, 0.0),
            person.login,
        ),
    )


def is_user_count(name: str) -> bool:
    """Tell GitHub's distinct-user counts apart from its activity totals.

    Summing them double-counts: `monthly_active_users` is a trailing 28-day count of distinct
    people, so adding ninety days of it would count one person up to ninety times.
    """
    return "active_" in name or "passive_" in name


@dataclass(frozen=True)
class AggregateSummary:
    """GitHub's org-wide numbers: user counts at the latest day, activity totals added up."""

    days: int
    latest_day: str
    latest_counts: Mapping[str, float]
    peak_counts: Mapping[str, float]
    sums: Mapping[str, float]
    breakdowns: Mapping[str, Mapping[Dimensions, Mapping[str, float]]]


def aggregate_breakdowns(rows: Sequence[Row]) -> dict[str, dict[Dimensions, defaultdict[str, float]]]:
    """Add up the breakdown arrays the org-wide records carry, which `flatten_measures` cannot.

    `flatten_measures` descends into numbers and nested objects and falls through a list in silence.
    That worked while every aggregate breakdown was an object (`totals_by_cli` still is), and broke
    when GitHub added `totals_by_ai_adoption_phase` as an array: the per-phase pull request,
    review-cycle and line counts were dropped without trace, and those are the figures that say
    whether the agent cohorts ship more than the completion cohort.

    Summed here, and not by teaching `flatten_measures` about lists, because flattening an array
    would add every entry together and report the sum of all four phases as one number. A breakdown
    has to keep its dimensions to mean anything, so it is keyed like every other breakdown in this
    file, and one GitHub adds tomorrow is picked up without being named here.
    """
    breakdowns: dict[str, dict[Dimensions, defaultdict[str, float]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        for name, value in row.items():
            if not name.startswith(BREAKDOWN_PREFIX) or not isinstance(value, list):
                continue
            for entry in value:
                if not isinstance(entry, Mapping):
                    continue
                measures = breakdowns.setdefault(name, {}).setdefault(entry_dimensions(entry), defaultdict(float))
                for measure, amount in flatten_measures(entry):
                    measures[measure] += amount
    return breakdowns


def summarise_aggregate(rows: Sequence[Row]) -> AggregateSummary:
    """Reduce the daily org-wide records to counts as-at the latest day and totals over the window."""
    flattened = sorted(
        (str(row.get("day") or ""), dict(flatten_measures(row))) for row in rows if isinstance(row, Mapping)
    )
    latest: Mapping[str, float] = {}
    peak: defaultdict[str, float] = defaultdict(float)
    sums: defaultdict[str, float] = defaultdict(float)
    for _, measures in flattened:
        for name, value in measures.items():
            if is_user_count(name):
                peak[name] = max(peak[name], value)
            else:
                sums[name] += value
    if flattened:
        latest = {name: value for name, value in flattened[-1][1].items() if is_user_count(name)}
    return AggregateSummary(
        days=len(flattened),
        latest_day=flattened[-1][0] if flattened else "",
        latest_counts=latest,
        peak_counts=dict(peak),
        sums=dict(sums),
        breakdowns={
            name: {dimensions: dict(measures) for dimensions, measures in buckets.items()}
            for name, buckets in aggregate_breakdowns(rows).items()
        },
    )


def measures_json(measures: Mapping[str, float]) -> dict[str, float]:
    """Render summed measures for JSON, keeping whole numbers whole."""
    return {name: int(value) if float(value).is_integer() else value for name, value in sorted(measures.items())}


def bucket_json(dimensions: Dimensions, bucket: Bucket) -> dict[str, Any]:
    """Describe one breakdown bucket: what it is, how many people, and how much."""
    described: dict[str, Any] = {"dimensions": dict(dimensions)}
    if bucket.label:
        described["label"] = bucket.label
    described["users"] = len(bucket.users)
    described["user_days"] = bucket.user_days
    described["measures"] = measures_json(bucket.measures)
    return described


def surfaces_json(rollup: Rollup) -> list[dict[str, Any]]:
    """List every surface seen, most-used first, with GitHub's field name kept alongside English."""
    return [
        {
            "flag": flag,
            "label": SURFACE_LABELS.get(flag, flag.removeprefix(SURFACE_PREFIX).replace("_", " ")),
            "users": len(bucket.users),
            "user_days": bucket.user_days,
        }
        for flag, bucket in sorted(rollup.surfaces.items(), key=lambda item: (-len(item[1].users), item[0]))
    ]


def attributed_totals(measures: Mapping[str, float], subscription: str | None) -> dict[str, Any]:
    """Render summed measures for JSON, nulling a credit figure that is not this scope's to report.

    The rule `credits_cell` applies to the table, applied to the data, so the two outputs agree
    about whose spend a zero belongs to. A measure GitHub never sent stays absent, because
    omission is how JSON already says "not reported"; only a zero that belongs to somebody else's
    subscription becomes an explicit null. A non-zero figure survives untouched, for the same reason
    it does in the table: a charge against a seat we do not bill is worth seeing.
    """
    rendered: dict[str, Any] = dict(measures_json(measures))
    if subscription in SPECIAL_SUBSCRIPTIONS and CREDITS in rendered and not rendered[CREDITS]:
        rendered[CREDITS] = None
    return rendered


def rollup_json(rollup: Rollup, subscription: str | None = None) -> dict[str, Any]:
    """Describe one population's usage in full."""
    return {
        "distinct_users": len(rollup.users),
        "active_days": len(rollup.days),
        "first_active_day": min(rollup.days) if rollup.days else None,
        "last_active_day": max(rollup.days) if rollup.days else None,
        "totals": attributed_totals(rollup.totals, subscription),
        "surfaces": surfaces_json(rollup),
        # Counted on the identifier, which is the stable key: the English name beside it is display
        # text GitHub may reword, and a Phase is not orderable so counting the object cannot sort.
        "adoption_phases": dict(sorted(Counter(phase.identifier for _, phase in rollup.phases.values()).items())),
        "breakdowns": {
            breakdown: [
                bucket_json(dimensions, bucket)
                for dimensions, bucket in sorted(buckets.items(), key=lambda item: (-len(item[1].users), item[0]))
            ]
            for breakdown, buckets in sorted(rollup.breakdowns.items())
        },
    }


def person_json(person: Person) -> dict[str, Any]:
    """Describe one user, including the agent-mode figures this report exists to answer."""
    phase = person.rollup.phases.get(person.login)
    return {
        "login": person.login,
        "user_id": person.user_id,
        "teams": list(person.teams),
        "subscription": person.subscription,
        "seat": {
            "plan_type": person.seat.plan_type,
            "assigning_team": person.seat.assigning_team,
            "last_activity_at": person.seat.last_activity,
        }
        if person.seat
        else None,
        "adoption_phase": {
            "phase": phase[1].identifier,
            "name": phase[1].name,
            "criteria": phase_criteria(phase[1]),
            "version": phase[1].version,
            "assessed_on": phase[0],
            "window": "trailing 28 days, recalculated daily",
        }
        if phase
        else None,
        "agent_mode_days": person.rollup.surface_days(AGENT_SURFACE),
        "agent_mode_interactions": int(person.agent_interactions()),
        **rollup_json(person.rollup, person.subscription),
    }


def number(value: float | None) -> str:
    """Format one measure for a table, keeping whole numbers whole and absence absent.

    None renders as `-`. A measure GitHub did not report is a thing this report does not
    know; a measure GitHub reported as `0` is a thing it does know. Collapsing the two makes a column
    of zeroes that says "nobody did this" and "we were never told" at once, with no way to tell which
    cell is which. Every call site therefore passes `mapping.get(name)`, never `mapping.get(name, 0.0)`.
    """
    if value is None:
        return ABSENT
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def share(count: float, total: float | None) -> str:
    """Express a count as a share of a total, or as nothing when there is no total to share of."""
    if not total:
        return ABSENT
    return f"{count / total:.1%}"


def plural(count: int, noun: str) -> str:
    """Count a noun, agreeing its plural."""
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def heading(title: str) -> str:
    """Rule off one section so the report can be skimmed and grepped."""
    return f"== {title} " + "=" * max(0, 100 - len(title))


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Lay out one table, right-aligning the columns that hold only numbers."""
    if not rows:
        return ["  (none)"]
    columns = list(zip(headers, *rows, strict=True))
    widths = [max(len(cell) for cell in column) for column in columns]
    # A placeholder does not stop a column being a column of numbers, so `-` and `n/a` sit in the
    # numeric alignment and the column stays right-aligned.
    numeric = [
        all(cell in PLACEHOLDER_CELLS or NUMERIC_CELL.fullmatch(cell) for cell in column[1:]) for column in columns
    ]
    lines = []
    for index, cells in enumerate([headers, *rows]):
        rendered = [
            cell.rjust(width) if right else cell.ljust(width)
            for cell, width, right in zip(cells, widths, numeric, strict=True)
        ]
        lines.append("  " + "  ".join(rendered).rstrip())
        if index == 0:
            lines.append("  " + "  ".join("-" * width for width in widths))
    return lines


def ordered_surfaces(rollup: Rollup) -> list[str]:
    """Order surfaces so the known ones read in a deliberate order and new ones still appear."""
    known = [flag for flag in SURFACE_COLUMNS if flag in rollup.surfaces]
    return known + sorted(flag for flag in rollup.surfaces if flag not in SURFACE_COLUMNS)


def surface_column(flag: str) -> str:
    """Abbreviate one surface for a per-user table column."""
    return SURFACE_COLUMNS.get(flag, flag.removeprefix(SURFACE_PREFIX))


def render_window(options: Options, collection: Collection, rollups: Rollups, days: Sequence[date]) -> list[str]:
    """State what was asked for, what came back, and how many people used Copilot at all."""
    seats = collection.seats or {}
    # The seat listing is a second, independent route to the same denominator, so a summary endpoint
    # this token may not read still leaves every percentage computable.
    total_seats = seats.get("total") or collection.seat_assignments.total
    outcomes = collection.day_outcomes.get("users", Counter())
    lines = [
        f"GitHub Copilot usage - {options.scope_kind.rstrip('s')} {options.scope_name}",
        "",
        f"window          {days[0].isoformat()} .. {days[-1].isoformat()}  ({len(days)} days)",
        (
            f"days with data  {outcomes['ok']} reported activity, {outcomes['empty']} reported none, "
            f"{outcomes['absent']} had no report, {outcomes['failed']} could not be read"
        ),
        f"active users    {len(rollups.everyone.users):,} distinct logins used Copilot at least once",
    ]
    assignments = collection.seat_assignments
    if assignments.available:
        # Only users this scope actually bills can be a share of this scope's seats. Dividing every
        # active login by the seat total would count supplier staff against seats nobody bought for
        # them, and can exceed 100%.
        ours = {login for login in rollups.everyone.users if login in assignments.by_login}
        external = len(rollups.everyone.users) - len(ours)
        lines.append(
            f"subscriptions   {len(ours):,} of those hold a seat {options.scope_name} bills; "
            f"{external:,} are billed outside this scope"
        )
        if total_seats:
            lines.append(
                f"seats           {int(total_seats):,} billed here  "
                f"({share(len(ours), float(total_seats))} of them used Copilot, "
                f"{len(assignments.idle(rollups.everyone.users)):,} idle)"
            )
    elif total_seats:
        lines.append(f"seats           {int(total_seats):,} billed here  (holders unknown: {assignments.detail})")
    # Guarded per name rather than on the seat total, which may now have come from the seat listing
    # while this breakdown comes from the billing summary: two sources, one of which can be absent.
    lines.extend(
        f"                {int(seats[name]):,} {name.replace('_', ' ')}"
        for name in ("active_this_cycle", "inactive_this_cycle", "pending_invitation", "pending_cancellation")
        if isinstance(seats.get(name), int)
    )
    if options.days > len(days):
        lines.append(
            f"NOTE            {options.days} days were asked for; reports begin "
            f"{EARLIEST_REPORT_DAY.isoformat()}, so the window was clamped"
        )
    return lines


def render_surfaces(rollup: Rollup) -> list[str]:
    """Report each surface by distinct users and user-days, agent mode included.

    Shares are of the active population. Active users include people this scope does not buy a seat
    for, so a seat total is not their denominator: dividing by it counts supplier staff against seats
    nobody bought for them and can exceed 100%. The meaningful seat share, our seated users as a
    share of our seats, is stated in the header.
    """
    active = float(len(rollup.users)) or None
    rows = [
        [
            SURFACE_LABELS.get(flag, flag.removeprefix(SURFACE_PREFIX).replace("_", " ")),
            flag,
            number(len(rollup.surfaces[flag].users)),
            share(len(rollup.surfaces[flag].users), active),
            number(rollup.surfaces[flag].user_days),
        ]
        for flag in ordered_surfaces(rollup)
    ]
    return [
        heading("USERS AND USER-DAYS PER SURFACE"),
        "  A user-day is one person on one day.",
        "",
        *render_table(("surface", "field", "users", "of active", "user-days"), rows),
    ]


def render_breakdown(title: str, note: str, rollup: Rollup, breakdown: str) -> list[str]:
    """Report one discovered breakdown, with its dimension values named as GitHub names them."""
    buckets = rollup.breakdowns.get(breakdown)
    if not buckets:
        return [heading(title), f"  (GitHub reported no {breakdown} records in this window)"]
    dimension_names = sorted({name for dimensions in buckets for name, _ in dimensions}) or ["total"]
    measure_names = breakdown_measures(buckets)
    headers = (*dimension_names, "users", "user-days", *measure_names)
    rows = []
    for dimensions, bucket in sorted(buckets.items(), key=lambda item: (-len(item[1].users), item[0])):
        values = dict(dimensions)
        named = [values.get(name, "all") for name in dimension_names]
        if bucket.label:
            named[0] = f"{named[0]} ({bucket.label})"
        rows.append(
            [
                *named,
                number(len(bucket.users)),
                number(bucket.user_days),
                *(number(bucket.measures.get(measure)) for measure in measure_names),
            ]
        )
    return [heading(title), *([f"  {note}", ""] if note else []), *render_table(headers, rows)]


def breakdown_measures(buckets: Mapping[Dimensions, Bucket]) -> list[str]:
    """Name the measures one breakdown actually carries, headline ones first.

    Read from the records, because `totals_by_cli` counts sessions and tokens while
    `totals_by_feature` counts interactions and lines: a fixed column set would print a column of
    zeroes for one of them and silently omit what the other measures.
    """
    present = {name for bucket in buckets.values() for name in bucket.measures}
    headline = [name for name in HEADLINE_MEASURES if name in present]
    return headline + sorted(present - set(headline))


def render_aggregate(summary: AggregateSummary) -> list[str]:
    """Report GitHub's org-wide numbers beside the ones this script added up itself."""
    if not summary.days:
        return [heading("GITHUB'S ORGANISATION-WIDE COUNTS"), "  (no aggregate report was readable)"]
    counts = [
        [name, number(summary.latest_counts.get(name)), number(summary.peak_counts.get(name))]
        for name in sorted(summary.peak_counts)
    ]
    sums = [[name, number(value)] for name, value in sorted(summary.sums.items())]
    return [
        heading("GITHUB'S ORGANISATION-WIDE COUNTS"),
        f"  Distinct-user counts as GitHub published them on {summary.latest_day}, and the highest each",
        "  reached in the window. The monthly figures are trailing 28-day counts, not window counts, so",
        "  they are shown unadjusted beside the window count above.",
        "",
        *render_table(("count", f"on {summary.latest_day}", "peak in window"), counts),
        "",
        heading("GITHUB'S ACTIVITY TOTALS, SUMMED OVER THE WINDOW"),
        *render_table(("total", "window"), sums),
        *aggregate_breakdown_lines(summary),
    ]


def aggregate_breakdown_lines(summary: AggregateSummary) -> list[str]:
    """Report each breakdown array the org-wide records carry, one section apiece.

    `totals_by_ai_adoption_phase` is the one that prompted this: it answers whether the agent cohorts
    merge more pull requests than the completion cohort, which no per-user section can reach.
    """
    lines: list[str] = []
    for name, buckets in sorted(summary.breakdowns.items()):
        dimension_names = sorted({key for dimensions in buckets for key, _ in dimensions}) or ["total"]
        measure_names = sorted({measure for measures in buckets.values() for measure in measures})
        rows = [
            [
                *(dict(dimensions).get(key, "all") for key in dimension_names),
                *(number(measures.get(measure)) for measure in measure_names),
            ]
            for dimensions, measures in sorted(buckets.items())
        ]
        title = f"GITHUB'S TOTALS BY {name.removeprefix(BREAKDOWN_PREFIX).upper().replace('_', ' ')}"
        lines.extend(["", heading(title)])
        if name == PHASE_BREAKDOWN:
            lines.extend(
                [
                    "  Summed over the window, for the users GitHub placed in each phase on each day. A user who",
                    "  changed phase mid-window contributes to both, because the phase is recalculated daily.",
                    "",
                ]
            )
        lines.extend(render_table((*dimension_names, *measure_names), rows))
    return lines


def dimension_values(rollup: Rollup, breakdown: str, name: str) -> tuple[str, ...]:
    """Name every distinct value one dimension took, for a compact per-user column."""
    buckets = rollup.breakdowns.get(breakdown, {})
    return tuple(sorted({value for dimensions in buckets for key, value in dimensions if key == name}))


def render_teams(rollups: Rollups, collection: Collection, flags: Sequence[str]) -> list[str]:
    """Report each team as distinct users per surface, from the same records as the global figures."""
    headers = ("team", "users", *(surface_column(flag) for flag in flags), "prompts")
    rows = [
        [
            slug,
            number(len(rollup.users)),
            *(number(rollup.surface_users(flag)) for flag in flags),
            number(rollup.totals.get(INTERACTIONS)),
        ]
        for slug, rollup in sorted(rollups.teams.items(), key=lambda item: (-len(item[1].users), item[0]))
    ]
    unattributed = len(rollups.teams.get(NO_TEAM, Rollup()).users)
    return [
        heading("DISTINCT USERS PER SURFACE, BY TEAM"),
        f"  Team membership as reported on {collection.team_day}. Columns count distinct users, and a user in",
        "  several teams is counted in each, so these columns sum to more than the global figure.",
        "  GitHub omits teams with fewer than five seated Copilot users from the report, which is why",
        f"  {unattributed:,} active users appear under {NO_TEAM}.",
        "",
        *render_table(headers, rows),
    ]


def subscription_order(groups: Mapping[str, Rollup]) -> list[str]:
    """Order subscription groups so the ones this scope actually pays for read first."""
    ours = sorted(label for label in groups if label not in SPECIAL_SUBSCRIPTIONS)
    return [*ours, *(label for label in SPECIAL_SUBSCRIPTIONS if label in groups)]


def subscription_models(rollup: Rollup) -> dict[str, set[str]]:
    """Name every model one population used and who used it, from whichever breakdown carries one.

    Discovered from the records, because `model` appears as a dimension of
    `totals_by_model_feature` and `totals_by_language_model` alike, and GitHub may key it into
    another tomorrow. A user is counted once per model however many breakdowns mention it.
    """
    users: dict[str, set[str]] = {}
    for buckets in rollup.breakdowns.values():
        for dimensions, bucket in buckets.items():
            for name, value in dimensions:
                if name == "model":
                    users.setdefault(value, set()).update(bucket.users)
    return users


def render_subscriptions(options: Options, collection: Collection, rollups: Rollups, flags: Sequence[str]) -> list[str]:
    """Report who pays for each active user, which only the seat list can answer.

    The metrics reports cover everyone active in the scope, because activity is attributed to the
    organisation whose repositories it happened in. Billing is attributed to whoever bought the seat.
    Contractors and suppliers working here on their employer's subscription therefore appear in full
    in every usage section of this report and in none of its billing figures, and without this
    section their zero credits read as thrift.
    """
    seats = collection.seat_assignments
    groups = subscription_order(rollups.subscriptions)
    if not seats.available:
        return [
            heading("WHO PAYS FOR EACH ACTIVE USER"),
            f"  The seat list could not be read, so no user's subscription is known: {seats.detail}",
            "  Every user is therefore reported as `?`. Reading the seat list needs organisation owner, or",
            "  the 'GitHub Copilot Business' / billing permission — granted to a GitHub App installation",
            "  by the organisation, or held by the user behind a fine-grained token — or",
            "  manage_billing:copilot on a classic one. Until it is readable, no credit figure in this",
            "  report can be attributed.",
        ]
    idle = seats.idle(rollups.everyone.users)
    headers = ("subscription", "users", *(surface_column(flag) for flag in flags), "prompts", "credits", "models")
    rows = [
        [
            label,
            number(len(rollups.subscriptions[label].users)),
            *(number(rollups.subscriptions[label].surface_users(flag)) for flag in flags),
            number(rollups.subscriptions[label].totals.get(INTERACTIONS)),
            credits_cell(label, rollups.subscriptions[label].totals.get(CREDITS)),
            number(len(subscription_models(rollups.subscriptions[label]))),
        ]
        for label in groups
    ]
    external = len(rollups.subscriptions.get(EXTERNAL_SUBSCRIPTION, Rollup()).users)
    return [
        heading("WHO PAYS FOR EACH ACTIVE USER"),
        "  Subscriptions partition the active population: a login either holds a seat in this scope's",
        "  billing or it does not, so these columns, unlike the team columns, sum to the global figure.",
        f"  {external:,} of {len(rollups.everyone.users):,} active users hold no seat {options.scope_name} pays for. They are not unlicensed:",
        "  their seat is bought somewhere this token cannot see, typically their employer's. Their usage",
        f"  is fully visible here and their spend is not visible at all, so their credits read `{NOT_APPLICABLE}`.",
        f"  {plural(len(seats.by_login), 'seat')} billed by {options.scope_name}, of which {len(idle):,} saw no activity in this window",
        "  (--format json names them; the per-user report holds no record for a user who did nothing).",
        "",
        *render_table(headers, rows),
    ]


def render_models_by_subscription(rollups: Rollups, seats: Seats) -> list[str]:
    """Report which models each subscription's users reached, which is set by policy.

    Model availability is set by the Copilot policy of the subscription that owns the seat, not by
    the organisation the work happens in. A model with users only under an external subscription is
    therefore one this scope has not enabled, being used in this scope's repositories by someone
    whose employer has enabled it. That is the question this table answers and no other section can.
    """
    if not seats.available:
        return [
            heading("MODELS BY SUBSCRIPTION"),
            "  (the seat list could not be read, so models cannot be attributed)",
        ]
    groups = subscription_order(rollups.subscriptions)
    per_group = {label: subscription_models(rollups.subscriptions[label]) for label in groups}
    models = sorted({model for models in per_group.values() for model in models})
    if not models:
        return [heading("MODELS BY SUBSCRIPTION"), "  (GitHub reported no model records in this window)"]
    headers = ("model", *(subscription_column(label) for label in groups), "users")
    rows = [
        [
            model,
            *(number(len(per_group[label].get(model, set()))) for label in groups),
            number(len({login for label in groups for login in per_group[label].get(model, set())})),
        ]
        for model in models
    ]
    return [
        heading("MODELS BY SUBSCRIPTION"),
        "  Counts are distinct users of each model. A `0` here is a measurement: the model was reported",
        "  for this window and nobody on that subscription used it. A model with users only under an",
        "  external subscription is one this scope has not enabled: it is reaching these repositories",
        "  through a seat somebody else pays for and sets the policy on.",
        "",
        *render_table(headers, rows),
    ]


def credits_cell(subscription: str, value: float | None) -> str:
    """Render one credit figure, refusing to report another subscription's spend as our zero.

    `ai_credits_used` measures what this scope was charged for a user. For a seat billed elsewhere
    that quantity does not exist here, so printing the zero GitHub sends would assert that an
    externally-billed user cost nothing, a claim this report cannot support and which reads as
    evidence of frugality. A non-zero figure is still printed: a charge against a seat we do not bill
    is worth seeing.
    """
    if value:
        return number(value)
    return NOT_APPLICABLE if subscription in SPECIAL_SUBSCRIPTIONS else number(value)


def subscription_column(subscription: str) -> str:
    """Abbreviate one subscription for a per-user column."""
    if subscription == EXTERNAL_SUBSCRIPTION:
        return "external"
    if subscription == UNKNOWN_SUBSCRIPTION:
        return "?"
    return subscription.replace(" (", "/").rstrip(")")


def person_row(person: Person, flags: Sequence[str]) -> list[str]:
    """Lay out one user's line: active days, then a day count per surface, then volume."""
    rollup = person.rollup
    phase = rollup.phases.get(person.login)
    return [
        person.login,
        number(len(rollup.days)),
        min(rollup.days) if rollup.days else ABSENT,
        max(rollup.days) if rollup.days else ABSENT,
        *(number(rollup.surface_days(flag)) for flag in flags),
        number(rollup.totals.get(INTERACTIONS)),
        number(rollup.totals.get(GENERATIONS)),
        number(rollup.totals.get(ACCEPTANCES)),
        number(rollup.totals.get("loc_added_sum")),
        credits_cell(person.subscription, person.rollup.totals.get(CREDITS)),
        subscription_column(person.subscription),
        person.seat.assigning_team if person.seat and person.seat.assigning_team else ABSENT,
        # The identifier alone, not the full label: this table already carries fifteen columns, and
        # the English and the criteria are stated once in the AI ADOPTION PHASE section.
        phase[1].identifier if phase else ABSENT,
        ",".join(dimension_values(rollup, "totals_by_ide", "ide")) or ABSENT,
        ",".join(dimension_values(rollup, "totals_by_model_feature", "model")) or ABSENT,
        ",".join(person.teams) or ABSENT,
    ]


def person_headers(flags: Sequence[str]) -> tuple[str, ...]:
    """Name the per-user columns, one per discovered surface."""
    return (
        "login",
        "days",
        "first",
        "last",
        *(surface_column(flag) for flag in flags),
        "prompts",
        "generated",
        "accepted",
        "loc+",
        "credits",
        "billed by",
        "seat from",
        "phase",
        "ides",
        "models",
        "teams",
    )


def render_agent_users(people: Sequence[Person], flags: Sequence[str]) -> list[str]:
    """Report the population this was asked for first: everyone who used IDE chat agent mode."""
    users = [person for person in people if person.rollup.surface_days(AGENT_SURFACE) or person.agent_interactions()]
    headers = ("login", "agent days", "agent prompts", *person_headers(flags)[1:])
    rows = [
        [
            person.login,
            number(person.rollup.surface_days(AGENT_SURFACE)),
            number(person.agent_interactions()),
            *person_row(person, flags)[1:],
        ]
        for person in users
    ]
    return [
        heading(f"USERS WHO USED IDE CHAT AGENT MODE ({len(users):,})"),
        f"  `agent days` counts days on which {AGENT_SURFACE} was true; `agent prompts` counts interactions in",
        "  every chat feature GitHub names an agent mode. A user can have one without the other, because the",
        "  two come from different parts of the record; both are shown.",
        "",
        *render_table(headers, rows),
    ]


def render_people(people: Sequence[Person], flags: Sequence[str]) -> list[str]:
    """Report every user with any activity. Users who never used Copilot have no record to report."""
    return [
        heading(f"EVERY USER WITH ANY COPILOT ACTIVITY ({len(people):,})"),
        "  Surface columns count days on which that surface was used, ordered by agent-mode use first.",
        "  Users who never used Copilot are absent: the per-user report holds no record for them.",
        "",
        *render_table(person_headers(flags), [person_row(person, flags) for person in people]),
    ]


def render_limits(options: Options, collection: Collection, rollups: Rollups) -> list[str]:
    """State what these numbers cannot be asked to mean."""
    failures = sum(counts["failed"] + counts["absent"] for counts in collection.day_outcomes.values())
    seats = collection.seat_assignments
    return [
        heading("LIMITS"),
        f"  - `{ABSENT}` means GitHub reported no value and `0` means it reported zero.",
        f"  - `{NOT_APPLICABLE}` credits mean the seat is billed outside this scope, so its spend is not ours to see.",
        "  - Distinct-user counts are distinct logins over the whole window.",
        "  - A surface count is days touched, not volume; a feature count is volume, not days.",
        "  - Usage covers everyone active here; billing covers only seats this scope bought.",
        (
            f"  - {plural(len(seats.idle(rollups.everyone.users)), 'seat')} billed here saw no activity; --format json names them."
            if seats.available
            else f"  - The seat list was unreadable, so no subscription and no credit figure can be attributed: {seats.detail}"
        ),
        "  - A user in several teams counts in each, and GitHub omits teams under five seated users entirely.",
        "  - Averages GitHub publishes per day are omitted: adding averages averages nothing.",
        f"  - {failures} report-days were absent or unreadable; days with no report are not days with no use.",
        f"  - Reports exist only from {EARLIEST_REPORT_DAY.isoformat()}, and GitHub serves about a year back.",
        f"  - Read from {report_days(collection)} report-days under scope {options.scope_kind}/{options.scope_name}.",
    ]


def report_days(collection: Collection) -> str:
    """Count the report-days this run asked for, from the outcomes themselves."""
    return str(sum(sum(counts.values()) for counts in collection.day_outcomes.values()))


def render(options: Options, collection: Collection, rollups: Rollups, days: Sequence[date]) -> str:
    """Assemble the whole human-readable report."""
    flags = ordered_surfaces(rollups.everyone)
    people = rank_people(rollups.people)
    sections = [
        render_window(options, collection, rollups, days),
        render_surfaces(rollups.everyone),
        render_breakdown(
            "CHAT MODES AND OTHER FEATURES",
            "GitHub's feature names. `chat_panel_agent_mode` is IDE chat agent mode.",
            rollups.everyone,
            FEATURE_BREAKDOWN,
        ),
        render_breakdown("BY IDE", "", rollups.everyone, "totals_by_ide"),
        render_breakdown("BY MODEL AND FEATURE", "", rollups.everyone, "totals_by_model_feature"),
        render_breakdown("BY LANGUAGE AND FEATURE", "", rollups.everyone, "totals_by_language_feature"),
        render_breakdown("BY LANGUAGE AND MODEL", "", rollups.everyone, "totals_by_language_model"),
        render_breakdown(
            "THIRD-PARTY AGENT APPS",
            "Grouped on agent_id; display names change.",
            rollups.everyone,
            "totals_by_3rd_party_agent",
        ),
        render_breakdown("COPILOT CLI", "", rollups.everyone, "totals_by_cli"),
        render_breakdown("COPILOT APP", "", rollups.everyone, "totals_by_copilot_app"),
        *[
            render_breakdown(
                f"BY {breakdown.removeprefix(BREAKDOWN_PREFIX).upper().replace('_', ' ')}",
                "",
                rollups.everyone,
                breakdown,
            )
            for breakdown in sorted(rollups.everyone.breakdowns)
            if breakdown not in KNOWN_BREAKDOWNS
        ],
        render_phases(rollups.everyone),
        render_aggregate(summarise_aggregate(collection.aggregate_rows)),
        render_repositories(collection.repository_rows),
        render_subscriptions(options, collection, rollups, flags),
        render_models_by_subscription(rollups, collection.seat_assignments),
        render_teams(rollups, collection, flags),
        render_agent_users(people, flags),
        render_people(people, flags),
        render_limits(options, collection, rollups),
    ]
    return "\n".join("\n".join(section) + "\n" for section in sections)


def phase_criteria(phase: Phase) -> str:
    """Gloss one phase with GitHub's published criteria, under either spelling it may arrive as."""
    return PHASE_CRITERIA.get(phase.identifier) or PHASE_CRITERIA.get(phase.name, ABSENT)


def render_phases(rollup: Rollup) -> list[str]:
    """Report how many users sat in each adoption phase on the last day each was seen.

    GitHub assigns a phase over a trailing 28-day window and recalculates it daily, so a one-day run
    reports a preceding month's classification beside a single day's activity, and a Phase 3 user can
    show almost no use on the day asked for. The section says so, so that a reader meeting the
    mismatch in the table has the explanation to hand.
    """
    seen = [phase for _, phase in rollup.phases.values()]
    counted = Counter(phase.identifier for phase in seen)
    named = {phase.identifier: phase for phase in seen}
    versions = sorted({phase.version for phase in seen if phase.version})
    rows = [
        [
            named[identifier].label(),
            number(count),
            share(count, len(rollup.users) or None),
            # The criteria only: `label` has already said the name if the record carried
            # one. Looked up under the identifier and under the name, because GitHub sends the phase
            # as a bare integer in some reports and as `Phase 1` in others, and the gloss fits both.
            phase_criteria(named[identifier]),
        ]
        for identifier, count in sorted(counted.items())
    ]
    lines = [
        heading("AI ADOPTION PHASE"),
        "  GitHub's classification, taken from each user's most recent day in the window. It is assigned",
        "  over a trailing 28-day window and recalculated daily, so it describes the month up to that day;",
        "  over a short window it is the only column here describing anything but the window this report",
        "  covers. Phases measure breadth of surfaces, not volume: heavy completion use with no agent",
        "  surface stays Phase 1, and light use across two agent surfaces reaches Phase 3.",
    ]
    if versions:
        lines.append(f"  Assigned by GitHub's adoption-phase model {', '.join(versions)}.")
    return [*lines, "", *render_table(("phase", "users", "of active users", "criteria, as GitHub defines them"), rows)]


REPOSITORY_KEYS = ("repository_name", "repository_full_name", "repository", "repo", "full_name", "name")
REPOSITORY_ROWS_SHOWN = 100


def summarise_repositories(rows: Sequence[Row]) -> list[tuple[str, dict[str, float]]]:
    """Add the per-repository records up per repository, whichever field names the repository.

    The naming field is discovered from the record: this report is a later addition to the API than
    the rest, and one wrong guess at its key would silently produce an empty section.
    """
    totals: dict[str, defaultdict[str, float]] = {}
    for row in rows:
        name = next((str(row[key]) for key in REPOSITORY_KEYS if isinstance(row.get(key), str)), "")
        if not name:
            continue
        measures = totals.setdefault(name, defaultdict(float))
        for measure, value in flatten_measures(row):
            measures[measure] += value
    ranked = sorted(totals.items(), key=lambda item: (-item[1].get(INTERACTIONS, 0.0), item[0]))
    return [(name, dict(measures)) for name, measures in ranked]


def render_repositories(rows: Sequence[Row]) -> list[str]:
    """Report the per-repository breakdown, which carries activity and no user login."""
    ranked = summarise_repositories(rows)
    if not ranked:
        return [heading("BY REPOSITORY"), "  (no per-repository report was readable; --no-repositories skips it)"]
    measure_names = [name for name in HEADLINE_MEASURES if any(name in measures for _, measures in ranked)]
    shown = ranked[:REPOSITORY_ROWS_SHOWN]
    lines = [
        heading(f"BY REPOSITORY ({len(ranked):,})"),
        "  GitHub's per-repository report attributes activity to repositories but names no users, so this",
        "  section has no user counts and cannot be joined to the per-user sections above.",
    ]
    if len(shown) < len(ranked):
        lines.append(f"  Showing the {len(shown)} busiest by prompts; --format json holds all {len(ranked):,}.")
    lines.extend(
        [
            "",
            *render_table(
                ("repository", *measure_names),
                [[name, *(number(measures.get(measure)) for measure in measure_names)] for name, measures in shown],
            ),
        ]
    )
    return lines


def build_json(options: Options, collection: Collection, rollups: Rollups, days: Sequence[date]) -> dict[str, Any]:
    """Describe the whole report as data, with every discovered bucket kept."""
    summary = summarise_aggregate(collection.aggregate_rows)
    return {
        "scope": {"kind": options.scope_kind, "name": options.scope_name},
        "window": {
            "since": days[0].isoformat(),
            "until": days[-1].isoformat(),
            "days": len(days),
            "days_requested": options.days,
            "earliest_available": EARLIEST_REPORT_DAY.isoformat(),
            "day_outcomes": {report: dict(counts) for report, counts in collection.day_outcomes.items()},
        },
        "seats": dict(collection.seats) if collection.seats else None,
        "subscriptions": {
            "seats_listed": collection.seat_assignments.available,
            "detail": collection.seat_assignments.detail,
            "seats_billed_here": len(collection.seat_assignments.by_login),
            "note": "a partition: a login either holds a seat this scope bills or it does not. Credits "
            "measure what this scope was charged, so a seat billed elsewhere carries null: its usage "
            "is fully visible here and its spend is not visible at all.",
            "idle_seats": collection.seat_assignments.idle(rollups.everyone.users),
            "groups": {
                label: {
                    **rollup_json(rollup, label),
                    "models": {model: len(users) for model, users in sorted(subscription_models(rollup).items())},
                }
                for label, rollup in sorted(rollups.subscriptions.items())
            },
        },
        "github_aggregate": {
            "days": summary.days,
            "latest_day": summary.latest_day,
            "latest_counts": measures_json(summary.latest_counts),
            "peak_counts": measures_json(summary.peak_counts),
            "window_totals": measures_json(summary.sums),
            "breakdowns": {
                name: [
                    {"dimensions": dict(dimensions), "measures": measures_json(measures)}
                    for dimensions, measures in sorted(buckets.items())
                ]
                for name, buckets in sorted(summary.breakdowns.items())
            },
        },
        "global": rollup_json(rollups.everyone),
        "teams": {
            "membership_day": collection.team_day,
            "note": "a user in several teams is counted in each, so these groups sum to more than "
            "the global figure; GitHub omits teams with fewer than five seated Copilot users",
            "groups": {slug: rollup_json(rollup) for slug, rollup in sorted(rollups.teams.items())},
        },
        "repositories": [
            {"repository": name, "measures": measures_json(measures)}
            for name, measures in summarise_repositories(collection.repository_rows)
        ],
        "users": [person_json(person) for person in rank_people(rollups.people)],
    }


def fallback_token() -> str | None:
    """Find a token in the two places this script looks and the main collection does not.

    GH_TOKEN is deliberately absent: `resolve_credentials` reads it itself, and reading it here as
    well would put this script's precedence rules in two places. What is left is a shell's
    conveniences — GITHUB_TOKEN, which is what CI and `gh` itself export, and whatever `gh` is
    logged in as — which exist because this report is run by hand from a developer's terminal.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    executable = shutil.which("gh")
    if executable is not None:
        # Not a shell, and the path comes from `which`, not from anything user-supplied.
        completed = subprocess.run(  # noqa: S603
            [executable, "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
            timeout=SUBPROCESS_TIMEOUT,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    return None


def app_configured(environment: Mapping[str, str]) -> bool:
    """Report whether the whole App set is configured, which is what `resolve_credentials` selects on.

    Asked here for one reason only: so that a fully configured App does not send this script off to
    run `gh auth token` for a fallback it will never use. The rule itself is not reimplemented — the
    key half is `credentials.key_configured`, and if the answer here is wrong the only cost is a
    fallback that is computed and then ignored.
    """
    return bool(
        environment.get(APP_IDENTIFIER_VARIABLE)
        and environment.get(INSTALLATION_IDENTIFIER_VARIABLE)
        and key_configured(environment)
    )


def authentication_mode(credentials: GitHubCredentials) -> str:
    """Name the credential this run authenticates with, so a refusal is read against the right one.

    The App and installation ids are identifiers rather than secrets — anyone who can read the App's
    page can see them — so they are named. Nothing about the key or the token is.
    """
    if isinstance(credentials, AppInstallation):
        return f"GitHub App {credentials.app_identifier}, installation {credentials.installation_identifier}"
    return "a personal access token"


def resolve_run_credentials() -> GitHubCredentials:
    """Build the credential every call in this run is made with, and prove it before the window starts.

    `resolve_credentials` decides, so this report and `metrics collect` select the same way from the
    same variables: the App installation when all three of its variables are set, GH_TOKEN
    otherwise. The two fallbacks this script adds are offered to it as GH_TOKEN would have been,
    which keeps the whole precedence order in one expression and means a half-configured App still
    falls back to a token exactly as it does in the main collection.

    Minted here, before any day is fetched, for the reason `cli.github_credentials` does it: in App
    mode this forces the exchange the first request would have made anyway, so a wrong key or a
    stale installation id stops the run in one line while a human is still watching, rather than
    ninety days of refusals in.
    """
    environment = dict(os.environ)
    if not app_configured(environment) and not environment.get(ACCESS_TOKEN_VARIABLE):
        fallback = fallback_token()
        if fallback:
            environment[ACCESS_TOKEN_VARIABLE] = fallback
    # Its own session, and not the reader's: the reader pins the reports API version on every call it
    # makes, and the token exchange is not one of those calls.
    try:
        credentials = resolve_credentials(requests.Session(), environment)
        credentials.token()
    except CredentialsError as failure:
        sys.exit(
            f"cannot authenticate to GitHub: {failure}\nThis script also accepts GITHUB_TOKEN, or 'gh auth login'."
        )
    progress(f"authenticating as {authentication_mode(credentials)}")
    return credentials


def read_organization(path: Path) -> str:
    """Read the organisation from the policy file, as the other measurement scripts do."""
    try:
        configuration = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as failure:
        sys.exit(f"could not read {path}: {failure}")
    if not isinstance(configuration, Mapping) or not configuration.get("organization"):
        sys.exit(f"{path} names no organization; pass --org or --enterprise instead")
    return str(configuration["organization"])


def positive_integer(value: str) -> int:
    """Parse a count that must be a whole number above zero."""
    if not value.isdigit() or int(value) == 0:
        message = f"must be a whole number greater than zero, got: {value}"
        raise argparse.ArgumentTypeError(message)
    return int(value)


def calendar_day(value: str) -> date:
    """Parse a YYYY-MM-DD day, which is how every report is addressed."""
    try:
        return date.fromisoformat(value)
    except ValueError as failure:
        message = f"must be a YYYY-MM-DD day, got: {value}"
        raise argparse.ArgumentTypeError(message) from failure


def parse_arguments(argv: Sequence[str] | None) -> Options:
    """Settle the scope, the window and the output format."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG), help="policy file naming the organisation")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--org", help="organisation to report on, overriding the config file")
    scope.add_argument("--enterprise", help="report at enterprise scope instead of organisation scope")
    parser.add_argument("--days", type=positive_integer, default=DEFAULT_DAYS, help="days to report, ending at --until")
    parser.add_argument("--until", type=calendar_day, default=None, help="last day of the window; default today UTC")
    parser.add_argument(
        "--team-days",
        type=positive_integer,
        default=DEFAULT_TEAM_DAYS,
        help="most recent days to try for team membership, unioned",
    )
    parser.add_argument(
        "--repositories",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="also fetch the per-repository report, one extra call per day",
    )
    parser.add_argument("--format", dest="output_format", choices=("report", "json"), default="report")
    parser.add_argument("--cache", type=Path, default=None, help="directory to keep downloaded reports in")
    parsed = parser.parse_args(argv)

    if parsed.enterprise:
        kind, name, aggregate = "enterprises", str(parsed.enterprise), "enterprise"
    else:
        kind, aggregate = "orgs", "organization"
        name = str(parsed.org) if parsed.org else read_organization(parsed.config)
    return Options(
        scope_kind=kind,
        scope_name=name,
        aggregate_report=aggregate,
        days=parsed.days,
        until=parsed.until or datetime.now(UTC).date(),
        team_days=parsed.team_days,
        repositories=bool(parsed.repositories),
        output_format=str(parsed.output_format),
        cache=parsed.cache,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Report Copilot usage for the window, globally, by team and per user, onto stdout."""
    options = parse_arguments(argv)
    days = window_days(options)
    if not days:
        sys.exit(f"empty window: --until {options.until.isoformat()} is before {EARLIEST_REPORT_DAY.isoformat()}")

    credentials = resolve_run_credentials()
    reader = Reader(credentials)
    downloads = download_session()
    progress(f"scope {options.scope_kind}/{options.scope_name}, {days[0].isoformat()} .. {days[-1].isoformat()}")
    try:
        collection = collect(reader, downloads, options)
    except AccessError as refused:
        sys.exit(
            f"refused: {refused}\n"
            "This is the credential's access and not a property of the day asked for, so the run\n"
            "stopped instead of spending a call per remaining day to be told the same thing again.\n"
            "  401  the credential was not accepted at all: check it is set, unexpired, and — for a\n"
            "       token — SSO-authorised for this organisation.\n"
            "  403  the credential is accepted but lacks the role: reading these reports needs\n"
            "       organisation owner or the 'View Organization Copilot Metrics' permission, which a\n"
            "       GitHub App installation is granted by the organisation and a fine-grained token\n"
            "       only inherits from its user, plus read:org on a classic token. The Copilot metrics\n"
            "       policy must also be enabled."
        )
    except RateLimitError as limit:
        sys.exit(f"rate limit did not clear: {limit}")
    except CredentialsError as failure:
        # Only an App installation reaches here, and only once its held token has genuinely expired:
        # `AppInstallation` carries an early renewal that failed and re-attempts on the next call.
        sys.exit(f"credentials failed mid-run after {reader.calls} calls: {failure}")

    rollups = build_rollups(collection.user_rows, collection.teams, collection.seat_assignments, options.scope_name)
    progress(
        f"read {len(collection.user_rows):,} per-user records for {len(rollups.everyone.users):,} users "
        f"in {reader.calls} API calls"
    )

    if options.output_format == "json":
        sys.stdout.write(json.dumps(build_json(options, collection, rollups, days), indent=2) + "\n")
    else:
        sys.stdout.write(render(options, collection, rollups, days))


if __name__ == "__main__":
    main()
