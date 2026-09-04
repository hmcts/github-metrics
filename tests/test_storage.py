"""Test SQLite collection snapshot persistence."""

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Error, connect
from unittest.mock import patch

import pytest

from metrics.domain import (
    AlertFamily,
    AlertSeverity,
    AvailabilityReason,
    BehaviourProvenance,
    CacheStatus,
    CodeownersEvidence,
    CodeownersFile,
    CollectionStatus,
    DirectCommitFact,
    EvidenceKind,
    EvidenceSource,
    MaintenanceEvidence,
    MergeGateEvidence,
    OpenAlertCount,
    PullRequestFact,
    PullRequestRule,
    ReportingWindow,
    RepositoryInventory,
    RepositoryInventoryIssue,
    RepositoryInventoryItem,
    RepositoryMetadata,
    ReviewFact,
    ReviewState,
    SecurityAlertEvidence,
    SonarProjectMapping,
    SonarResolution,
    SourceCoverage,
    StatusCheck,
    StatusChecksRule,
    StoredSonarMapping,
)
from metrics.storage import (
    StorageError,
    cache_direct_commit_facts,
    cache_pull_request_facts,
    find_missing_cached_coverage,
    find_missing_coverage,
    get_pull_request_facts,
    get_source_coverage,
    initialize,
    load_alert_observations,
    load_cached_direct_commit_facts,
    load_cached_pull_request_facts,
    load_repository_state,
    load_sonar_mapping,
    observation_database,
    pin_journal_mode,
    prepare,
    prevailing_cached_coverage,
    prune_cache,
    record_alert_observations,
    record_repository_state,
    record_sonar_mapping,
    record_source_coverage,
    replace_pull_request_facts,
    repository_project,
)


@pytest.fixture
def inventory() -> RepositoryInventory:
    """Create a partial repository inventory."""
    return RepositoryInventory(
        status=CollectionStatus.PARTIAL,
        organization="hmcts",
        collected_at=datetime(2026, 8, 3, 15, 50, tzinfo=UTC),
        starts_at=datetime(2026, 5, 5, 15, 50, tzinfo=UTC),
        ends_at=datetime(2026, 8, 3, 15, 50, tzinfo=UTC),
        repositories=(
            RepositoryInventoryItem(
                team_identifier="no-fault-divorce",
                repository=RepositoryMetadata(
                    name="nfdiv-case-api",
                    default_branch="master",
                    archived=False,
                    fork=False,
                    disabled=False,
                    created_at=datetime(2021, 2, 22, 15, 0, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 31, 19, 5, tzinfo=UTC),
                    pushed_at=datetime(2026, 8, 3, 15, 24, tzinfo=UTC),
                ),
                merge_gate=MergeGateEvidence(
                    branch="master",
                    protected=True,
                    pull_requests=(
                        PullRequestRule(
                            dismiss_stale_reviews_on_push=True,
                            require_code_owner_review=True,
                            require_last_push_approval=False,
                            required_approving_review_count=2,
                            required_review_thread_resolution=True,
                        ),
                    ),
                    status_checks=(
                        StatusChecksRule(
                            strict_required_status_checks_policy=True,
                            required_status_checks=(StatusCheck(context="build", integration_id=42),),
                        ),
                    ),
                    restricts_deletions=True,
                    blocks_force_pushes=True,
                ),
                codeowners=CodeownersEvidence(
                    files=(CodeownersFile(path=".github/CODEOWNERS", size_bytes=64, recognised_by_github=True),),
                ),
                maintenance=MaintenanceEvidence(
                    branch="master",
                    last_commit_at=datetime(2026, 8, 3, 15, 24, tzinfo=UTC),
                    last_human_commit_at=datetime(2026, 7, 30, 11, 0, tzinfo=UTC),
                    searched_back_to=None,
                ),
                collection=BehaviourProvenance(
                    stable_history=CacheStatus.FETCHED,
                    stable_intervals_fetched=1,
                    mutable_starts_at=datetime(2026, 8, 3, 9, 50, tzinfo=UTC),
                    pull_request_facts_loaded=4,
                    review_facts_loaded=9,
                ),
            ),
        ),
        failures=(
            RepositoryInventoryIssue(
                team_identifier="no-fault-divorce",
                repository="nfdiv-frontend",
                evidence=EvidenceKind.REPOSITORY,
                reason=AvailabilityReason.PERMISSION_DENIED,
                detail="GitHub permission denied",
            ),
        ),
    )


def journal_mode(path: Path) -> str:
    """Read one database file's journal mode without preparing it."""
    with closing(connect(path)) as connection:
        return str(connection.execute("PRAGMA journal_mode").fetchone()[0])


def test_pin_journal_mode_converts_a_database_left_in_write_ahead_log_mode(tmp_path: Path) -> None:
    """Take a cache file out of the mode whose mapped wal-index killed three collection runs."""
    path = tmp_path / "metrics.sqlite3"
    with closing(connect(path)) as connection:
        connection.execute("PRAGMA journal_mode = wal")
    assert journal_mode(path) == "wal"

    with closing(connect(path)) as connection:
        pin_journal_mode(connection)

    assert journal_mode(path) == "delete"
    assert not (tmp_path / "metrics.sqlite3-shm").exists()


def test_pin_journal_mode_leaves_a_database_already_in_the_pinned_mode_alone(tmp_path: Path) -> None:
    """Cost a file in the right mode one pragma read and no write."""
    path = tmp_path / "metrics.sqlite3"
    with closing(connect(path)) as connection, connection:
        prepare(connection)
    before = path.stat().st_mtime_ns

    with closing(connect(path)) as connection:
        pin_journal_mode(connection)

    assert journal_mode(path) == "delete"
    assert path.stat().st_mtime_ns == before


def test_record_repository_state_stores_current_state_without_run_provenance(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Persist irrecoverable current state and leave one run's collection provenance out of it."""
    path = tmp_path / "nested" / "metrics.sqlite3"

    record_repository_state(path, inventory)

    with closing(connect(path)) as connection:
        organization, repository, fetched_at, payload = connection.execute(
            "SELECT organization, repository, fetched_at, payload FROM repository_state",
        ).fetchone()

    assert (organization, repository) == ("hmcts", "nfdiv-case-api")
    assert fetched_at == "2026-08-03T15:50:00+00:00"
    state = json.loads(payload)
    assert state["team_identifier"] == "no-fault-divorce"
    assert state["merge_gate"]["pull_requests"][0]["required_approving_review_count"] == 2
    assert state["merge_gate"]["status_checks"][0]["required_status_checks"][0]["context"] == "build"
    assert "collection" not in state


def test_record_repository_state_translates_sqlite_failures(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Expose a failed state write through the storage boundary."""
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not store collection"),
    ):
        record_repository_state(tmp_path / "metrics.sqlite3", inventory)


def test_record_repository_state_replaces_the_previous_state(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Keep only the latest state, because an earlier merge gate is neither recoverable nor wanted."""
    path = tmp_path / "metrics.sqlite3"
    record_repository_state(path, inventory)

    record_repository_state(
        path,
        inventory.model_copy(update={"collected_at": datetime(2026, 8, 4, 9, 0, tzinfo=UTC)}),
    )

    with closing(connect(path)) as connection:
        rows = connection.execute("SELECT fetched_at FROM repository_state").fetchall()

    assert rows == [("2026-08-04T09:00:00+00:00",)]


def test_load_repository_state_returns_the_stored_gate_and_when_it_was_observed(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Read stored current state back with the instant it was collected."""
    path = tmp_path / "metrics.sqlite3"
    record_repository_state(path, inventory)

    stored = load_repository_state(path, "hmcts", "nfdiv-case-api")

    assert stored is not None
    assert stored.fetched_at == datetime(2026, 8, 3, 15, 50, tzinfo=UTC)
    assert stored.state.merge_gate == inventory.repositories[0].merge_gate
    assert stored.state.repository.default_branch == "master"
    # Provenance describes one run and is excluded from the payload, so it cannot be read back.
    assert stored.state.collection is None


def test_load_repository_state_round_trips_codeowners_and_maintenance(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Round-trip the CODEOWNERS and maintenance blocks through the stored JSON payload.

    The maintenance instants are timezone-aware datetimes and the CODEOWNERS flag a boolean, so a
    round trip that quietly stringified either would still typecheck while changing the report.
    """
    path = tmp_path / "metrics.sqlite3"
    record_repository_state(path, inventory)

    stored = load_repository_state(path, "hmcts", "nfdiv-case-api")

    assert stored is not None
    assert stored.state.codeowners == inventory.repositories[0].codeowners
    assert stored.state.maintenance == inventory.repositories[0].maintenance
    assert stored.state.maintenance is not None
    assert stored.state.maintenance.last_commit_at == datetime(2026, 8, 3, 15, 24, tzinfo=UTC)


def test_load_repository_state_reads_a_row_stored_before_the_standards_checks(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Read a row stored by a build predating the standards checks back with both blocks None.

    None means "not collected when repository state was stored" — never an absent file or an empty
    branch, which is why the projection layer turns it into a reason rather than an observation.
    """
    path = tmp_path / "metrics.sqlite3"
    record_repository_state(path, inventory)
    with closing(connect(path)) as connection, connection:
        (payload,) = connection.execute("SELECT payload FROM repository_state").fetchone()
        aged = {key: value for key, value in json.loads(payload).items() if key not in ("codeowners", "maintenance")}
        connection.execute("UPDATE repository_state SET payload = ?", (json.dumps(aged),))

    stored = load_repository_state(path, "hmcts", "nfdiv-case-api")

    assert stored is not None
    assert stored.state.codeowners is None
    assert stored.state.maintenance is None


def test_load_repository_state_reads_a_row_stored_before_the_hotspot_metrics_were_retired(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Load a row whose stored Sonar block still carries the two keys retired on 2026-09-04.

    `SonarMeasures` is validated `extra="forbid"` INSIDE this payload, which is the whole reason
    `security_hotspots` and `security_review_rating` were retained on the model rather than deleted:
    every row written before that date carries both. The guard belongs on this path and not only on
    the bare model, because this is the path a cached row actually takes — deleting the fields shows
    up here, as a `StorageError` on every repository on the estate, rather than as a tidy-up.
    """
    path = tmp_path / "metrics.sqlite3"
    record_repository_state(path, inventory)
    with closing(connect(path)) as connection, connection:
        (payload,) = connection.execute("SELECT payload FROM repository_state").fetchone()
        aged = {
            **json.loads(payload),
            "sonar": {
                "project_key": "hmcts_nfdiv-case-api",
                "security_hotspots": 4,
                "security_review_rating": {"value": 5.0},
            },
        }
        connection.execute("UPDATE repository_state SET payload = ?", (json.dumps(aged),))

    stored = load_repository_state(path, "hmcts", "nfdiv-case-api")

    assert stored is not None
    assert stored.state.sonar is not None
    assert stored.state.sonar.security_hotspots == 4
    assert stored.state.sonar.security_review_rating is not None
    assert stored.state.sonar.security_review_rating.letter == "E"


def test_load_repository_state_reports_a_repository_that_was_never_collected(tmp_path: Path) -> None:
    """Return nothing rather than inventing an unprotected gate for an uncollected repository."""
    assert load_repository_state(tmp_path / "metrics.sqlite3", "hmcts", "cath-service") is None


def test_load_repository_state_translates_an_unparseable_row(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Expose a row this build cannot parse through the storage boundary, like one it cannot read.

    The payload is validated `extra="forbid"`, so a row written by a newer build raises. Every caller
    degrades a `StorageError` to a reason on the one repository it is about, while an escaping
    `ValidationError` would fail the whole report that repository is a single row of.
    """
    path = tmp_path / "metrics.sqlite3"
    record_repository_state(path, inventory)
    with closing(connect(path)) as connection, connection:
        (payload,) = connection.execute("SELECT payload FROM repository_state").fetchone()
        newer = {**json.loads(payload), "a_field_this_build_has_never_heard_of": True}
        connection.execute("UPDATE repository_state SET payload = ?", (json.dumps(newer),))

    with pytest.raises(StorageError, match="could not read collection cache"):
        load_repository_state(path, "hmcts", "nfdiv-case-api")


def test_load_repository_state_translates_an_unreadable_instant(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Expose a stored instant that will not parse through the same boundary as the payload."""
    path = tmp_path / "metrics.sqlite3"
    record_repository_state(path, inventory)
    with closing(connect(path)) as connection, connection:
        connection.execute("UPDATE repository_state SET fetched_at = ?", ("not an instant",))

    with pytest.raises(StorageError, match="could not read collection cache"):
        load_repository_state(path, "hmcts", "nfdiv-case-api")


def test_load_repository_state_translates_sqlite_failures(tmp_path: Path) -> None:
    """Expose a failed state read through the storage boundary."""
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not read collection cache"),
    ):
        load_repository_state(tmp_path / "metrics.sqlite3", "hmcts", "cath-service")


AUGUST = ReportingWindow(starts_at=datetime(2026, 8, 1, tzinfo=UTC), ends_at=datetime(2026, 9, 1, tzinfo=UTC))
"""A range wide enough to hold every observation these fixtures record."""


def observed_alerts(dependabot: OpenAlertCount) -> SecurityAlertEvidence:
    """Build an alert block whose Dependabot family varies and whose other two always answer."""
    return SecurityAlertEvidence(
        dependabot=dependabot,
        code_scanning=OpenAlertCount(open=2, by_severity={AlertSeverity.HIGH: 2}),
        secret_scanning=OpenAlertCount(open=1),
    )


def collected(
    inventory: RepositoryInventory,
    security: SecurityAlertEvidence | None,
    day: int = 3,
) -> RepositoryInventory:
    """Return the inventory of one run that observed the given alerts on the given August day."""
    return inventory.model_copy(
        update={
            "collected_at": datetime(2026, 8, day, 9, 0, tzinfo=UTC),
            "repositories": (inventory.repositories[0].model_copy(update={"security": security}),),
        },
    )


def observed(path: Path) -> list[tuple[str, int, int]]:
    """Return each stored observation as its family, the day it was observed, and its open count."""
    return [
        (item.family.value, item.fetched_at.day, item.open)
        for item in load_alert_observations(path, "hmcts", "nfdiv-case-api", AUGUST)
    ]


def test_every_run_appends_an_alert_observation_rather_than_replacing_the_last(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Keep each run's open counts, because no past count can be recomputed from anything.

    The current-state row beside these is overwritten every run, which is what makes the appended
    history the only thing a series can be built from. Grouped by family and then by instant, so one
    family's history reads down consecutive rows.
    """
    path = observation_database(tmp_path / "metrics.sqlite3")
    severities = {AlertSeverity.CRITICAL: 1, AlertSeverity.LOW: 4}

    first = collected(inventory, observed_alerts(OpenAlertCount(open=5, by_severity=severities)))
    assert record_alert_observations(path, first) == 3
    assert record_alert_observations(path, collected(inventory, observed_alerts(OpenAlertCount(open=3)), day=10)) == 3

    assert observed(path) == [
        ("dependabot", 3, 5),
        ("dependabot", 10, 3),
        ("code-scanning", 3, 2),
        ("code-scanning", 10, 2),
        ("secret-scanning", 3, 1),
        ("secret-scanning", 10, 1),
    ]
    stored = load_alert_observations(path, "hmcts", "nfdiv-case-api", AUGUST)
    assert stored[0].by_severity == severities
    assert stored[1].by_severity == {}


def test_a_withheld_alert_family_appends_nothing_and_keeps_reporting_its_reason(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Record no observation for a family GitHub refused, and leave its reason on the current state.

    A row saying "unreadable" would be indistinguishable from a genuine fall to a low count once the
    reason is a year old, and no row at all is the honest shape: unavailable data never becomes zero.
    """
    database = tmp_path / "metrics.sqlite3"
    refused = collected(inventory, observed_alerts(OpenAlertCount(detail="dependabot/alerts: permission denied")))

    assert record_alert_observations(observation_database(database), refused) == 2
    record_repository_state(database, refused)

    families = {family for family, _, _ in observed(observation_database(database))}
    assert families == {AlertFamily.CODE_SCANNING.value, AlertFamily.CREDENTIAL_SCANNING.value}
    state = load_repository_state(database, "hmcts", "nfdiv-case-api")
    assert state is not None
    assert state.state.security is not None
    assert state.state.security.dependabot.detail == "dependabot/alerts: permission denied"


def test_a_repository_whose_alerts_were_never_read_appends_no_observation(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Append nothing at all for a repository carrying no alert block, rather than three zeroes."""
    path = observation_database(tmp_path / "metrics.sqlite3")

    assert record_alert_observations(path, collected(inventory, None)) == 0

    assert observed(path) == []


def test_stored_observations_are_selected_by_the_half_open_range_they_fall_in(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Select observations as every other interval selects facts, so none is reported twice."""
    path = observation_database(tmp_path / "metrics.sqlite3")
    for day in (3, 10, 17):
        record_alert_observations(path, collected(inventory, observed_alerts(OpenAlertCount(open=day)), day=day))

    selected = load_alert_observations(
        path,
        "hmcts",
        "nfdiv-case-api",
        ReportingWindow(starts_at=datetime(2026, 8, 10, tzinfo=UTC), ends_at=datetime(2026, 8, 17, tzinfo=UTC)),
    )

    assert {item.fetched_at.day for item in selected} == {10}
    assert load_alert_observations(path, "hmcts", "nfdiv-frontend", AUGUST) == ()


def test_observation_history_survives_deleting_the_disposable_cache(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Keep the irreplaceable history out of the file the cache design says may be blown away.

    "Delete the cache and refetch" is a load-bearing ruling, and it stays safe only while nothing
    irrecoverable lives there. A past open-alert count is exactly that, so it lives beside it.
    """
    database = tmp_path / "nested" / "metrics.sqlite3"
    run = collected(inventory, observed_alerts(OpenAlertCount(open=4)))
    record_repository_state(database, run)
    record_alert_observations(observation_database(database), run)

    assert observation_database(database) == tmp_path / "nested" / "metrics-observations.sqlite3"

    database.unlink()

    assert observed(observation_database(database)) == [
        ("dependabot", 3, 4),
        ("code-scanning", 3, 2),
        ("secret-scanning", 3, 1),
    ]


def test_alert_observation_storage_translates_sqlite_failures(tmp_path: Path, inventory: RepositoryInventory) -> None:
    """Expose failed observation reads and writes through the storage boundary."""
    path = observation_database(tmp_path / "metrics.sqlite3")

    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not store alert observations"),
    ):
        record_alert_observations(path, collected(inventory, observed_alerts(OpenAlertCount(open=1))))
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not read alert observations"),
    ):
        load_alert_observations(path, "hmcts", "nfdiv-case-api", AUGUST)


def resolved_mapping(
    project_key: str,
    repository: str,
    day: int,
    *,
    method: SonarResolution = SonarResolution.ANALYSIS_REVISION,
) -> StoredSonarMapping:
    """Resolve one project to one repository from an analysis made on the given August day."""
    return StoredSonarMapping(
        project_key=project_key,
        resolved_at=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
        mapping=SonarProjectMapping(
            project_key=project_key,
            repository=repository,
            method=method,
            analysis_at=datetime(2026, 8, day, 9, 30, tzinfo=UTC),
            revision=f"{project_key}-{day}",
        ),
    )


def test_a_resolved_project_round_trips_through_the_durable_map(tmp_path: Path) -> None:
    """Store one resolution with the evidence behind it, and read every column of it back."""
    path = observation_database(tmp_path / "metrics.sqlite3")

    assert record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service", 26)) is True

    stored = load_sonar_mapping(path, "hmcts", "hmcts.cath")
    assert stored is not None
    assert stored.detail is None
    assert stored.resolved_at == datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"
    assert stored.mapping.method is SonarResolution.ANALYSIS_REVISION
    assert stored.mapping.analysis_at == datetime(2026, 8, 26, 9, 30, tzinfo=UTC)
    assert stored.mapping.revision == "hmcts.cath-26"
    # A map built against one SonarCloud organisation says nothing about another's project keys.
    assert load_sonar_mapping(path, "another-org", "hmcts.cath") is None
    assert load_sonar_mapping(path, "hmcts", "hmcts.never-resolved") is None


def test_an_older_analysis_does_not_overwrite_what_a_newer_one_resolved(tmp_path: Path) -> None:
    """Refuse a re-run carrying stale analyses, so a project cannot be dragged back where it was.

    `map-sonar` is paced and interruptible, so two runs can overlap and the second can be reading an
    older analysis than the first stored. Only a strictly newer analysis is allowed to move a project.
    """
    path = observation_database(tmp_path / "metrics.sqlite3")
    record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service", 26))

    assert record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "old-repository", 20)) is False
    # The same analysis teaches nothing new, so it is not a write either.
    assert record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service", 26)) is False

    stored = load_sonar_mapping(path, "hmcts", "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"


def test_a_refused_write_still_records_that_the_project_was_asked_about(tmp_path: Path) -> None:
    """Move the skip watermark even when the stored answer stood, so the next run converges.

    `already_answered` skips a project when nothing has been analysed since its row was written. A
    project whose newest analysis names no findable commit resolves from an OLDER one, so the write
    is refused — and if `resolved_at` stayed where it was, that newer analysis would still stand
    above the watermark next run, and the search would be paid for again every run forever.
    """
    path = observation_database(tmp_path / "metrics.sqlite3")
    record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service", 26))
    asked_again = StoredSonarMapping(
        project_key="hmcts.cath",
        resolved_at=datetime(2026, 8, 28, 11, 0, tzinfo=UTC),
        mapping=None,
        detail="no analysis of hmcts.cath names a commit any hmcts repository holds",
    )

    assert record_sonar_mapping(path, "hmcts", asked_again) is False

    stored = load_sonar_mapping(path, "hmcts", "hmcts.cath")
    assert stored is not None
    assert stored.resolved_at == datetime(2026, 8, 28, 11, 0, tzinfo=UTC)
    # The answer itself is untouched: only the instant it was last asked about has moved.
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"
    assert stored.mapping.analysis_at == datetime(2026, 8, 26, 9, 30, tzinfo=UTC)
    assert stored.detail is None


def test_a_newer_analysis_follows_a_project_that_has_moved_repository(tmp_path: Path) -> None:
    """Let a newer analysis move a project, because the project is what the analysis describes."""
    path = observation_database(tmp_path / "metrics.sqlite3")
    record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service", 20))

    assert record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service-v2", 26)) is True

    stored = load_sonar_mapping(path, "hmcts", "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service-v2"
    assert repository_project(path, "hmcts", "cath-service") is None


def test_the_reverse_lookup_prefers_the_live_project_of_a_duplicate_pair(tmp_path: Path) -> None:
    """Answer a repository with its most recently analysed project, and say how many claimed it.

    SonarCloud has no rename, so a re-created project leaves its abandoned twin behind: on 2026-08-27
    `rpx-xui-webapp_2` had been analysed the day before while the unsuffixed `rpx-xui-webapp` went
    quiet in July. The exact name is the wrong answer, and analysis recency is the right one.
    """
    path = observation_database(tmp_path / "metrics.sqlite3")
    record_sonar_mapping(path, "hmcts", resolved_mapping("rpx-xui-webapp", "rpx-xui-webapp", 20))
    record_sonar_mapping(path, "hmcts", resolved_mapping("rpx-xui-webapp_2", "rpx-xui-webapp", 26))

    chosen = repository_project(path, "hmcts", "rpx-xui-webapp")

    assert chosen is not None
    assert chosen.mapping.project_key == "rpx-xui-webapp_2"
    assert chosen.candidates == 2
    # The name a human typed into the configuration file is compared as GitHub compares it.
    matched = repository_project(path, "hmcts", "RPX-XUI-WebApp")
    assert matched is not None
    assert matched.mapping.repository == "rpx-xui-webapp"
    assert repository_project(path, "hmcts", "nfdiv-case-api") is None


def test_an_undated_candidate_never_outranks_an_analysed_one(tmp_path: Path) -> None:
    """Order a configured override behind any analysed project rather than at random.

    A mapping resolved without an analysis — a configured override — carries no instant to compare,
    and SQLite would sort it either end depending on the direction. It must lose, so the answer is the
    project something has actually been measured about.
    """
    path = observation_database(tmp_path / "metrics.sqlite3")
    undated = StoredSonarMapping(
        project_key="em-icp-api",
        resolved_at=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
        mapping=SonarProjectMapping(
            project_key="em-icp-api",
            repository="rpx-xui-icp-api",
            method=SonarResolution.CONFIGURED,
        ),
    )
    record_sonar_mapping(path, "hmcts", undated)
    record_sonar_mapping(path, "hmcts", resolved_mapping("rpx-xui-icp-api", "rpx-xui-icp-api", 26))

    chosen = repository_project(path, "hmcts", "rpx-xui-icp-api")

    assert chosen is not None
    assert chosen.mapping.project_key == "rpx-xui-icp-api"
    assert chosen.candidates == 2


def test_an_unresolvable_project_is_stored_with_its_reason_and_then_replaced_by_an_answer(tmp_path: Path) -> None:
    """Remember that a project could not be resolved, so the next run does not pay to learn it again.

    A never-analysed project answers the same way every run, and the search quota is 10 requests a
    minute. Once it IS analysed, the resolution replaces the reason: a stored row with no analysis
    instant has nothing to defend.
    """
    path = observation_database(tmp_path / "metrics.sqlite3")
    unresolved = StoredSonarMapping(
        project_key="hmcts.never-analysed",
        resolved_at=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
        detail="SonarCloud records no analysis of this project, so there is no commit to resolve it by",
    )

    assert record_sonar_mapping(path, "hmcts", unresolved) is True

    stored = load_sonar_mapping(path, "hmcts", "hmcts.never-analysed")
    assert stored is not None
    assert stored.mapping is None
    assert stored.detail == "SonarCloud records no analysis of this project, so there is no commit to resolve it by"
    # An unresolved project is nobody's project, so it never answers a reverse lookup.
    assert repository_project(path, "hmcts", "hmcts.never-analysed") is None

    assert record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.never-analysed", "cath-service", 27)) is True

    resolved = load_sonar_mapping(path, "hmcts", "hmcts.never-analysed")
    assert resolved is not None
    assert resolved.detail is None
    assert resolved.mapping is not None
    assert resolved.mapping.repository == "cath-service"


def test_a_resolution_is_never_replaced_by_a_reason(tmp_path: Path) -> None:
    """Keep a mapping the search quota was spent on when a later run cannot resolve the project.

    Overwriting a resolved row with "no analysis" would spend the quota twice to end up worse off:
    the commit that resolved it is still in the repository whatever SonarCloud has retained since.
    """
    path = observation_database(tmp_path / "metrics.sqlite3")
    record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service", 26))

    forgetful = StoredSonarMapping(
        project_key="hmcts.cath",
        resolved_at=datetime(2026, 8, 28, 10, 0, tzinfo=UTC),
        detail="none of the 5 most recent analyses names the commit it ran against",
    )
    assert record_sonar_mapping(path, "hmcts", forgetful) is False

    stored = load_sonar_mapping(path, "hmcts", "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None


def test_the_sonar_project_map_lives_in_the_durable_file_and_not_the_disposable_cache(
    tmp_path: Path,
    inventory: RepositoryInventory,
) -> None:
    """Keep the map where deleting the cache cannot cost it, because rebuilding it is rate limited.

    289 projects against a 10-per-minute commit search is between ten minutes and half an hour of
    paced calls, so "delete the cache and refetch" must not silently shrink the next evidence run's
    coverage to whichever repositories happen to declare a properties file.
    """
    database = tmp_path / "nested" / "metrics.sqlite3"
    record_repository_state(database, inventory)
    record_sonar_mapping(observation_database(database), "hmcts", resolved_mapping("hmcts.cath", "cath-service", 26))

    with closing(connect(database)) as connection:
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "sonar_project_map" not in tables

    database.unlink()

    stored = load_sonar_mapping(observation_database(database), "hmcts", "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"


def test_a_row_naming_an_unreadable_resolution_method_fails_rather_than_reporting_one(tmp_path: Path) -> None:
    """Refuse a stored method this build cannot interpret, wherever the row came from.

    The report's whole use for the method is to make a wrong mapping diagnosable, so a row written by
    a build that knew a method this one does not — or edited by hand — must say so rather than be
    presented as a resolution nobody can trace.
    """
    path = observation_database(tmp_path / "metrics.sqlite3")
    record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service", 26))
    with closing(connect(path)) as connection, connection:
        connection.execute("UPDATE sonar_project_map SET method = 'guessed-from-the-name'")

    with pytest.raises(StorageError, match="unknown resolution method 'guessed-from-the-name'"):
        load_sonar_mapping(path, "hmcts", "hmcts.cath")
    with pytest.raises(StorageError, match="unknown resolution method"):
        repository_project(path, "hmcts", "cath-service")


def test_sonar_project_map_storage_translates_sqlite_failures(tmp_path: Path) -> None:
    """Expose failed map reads and writes through the storage boundary."""
    path = observation_database(tmp_path / "metrics.sqlite3")

    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not store the sonar project map"),
    ):
        record_sonar_mapping(path, "hmcts", resolved_mapping("hmcts.cath", "cath-service", 26))
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not read the sonar project map"),
    ):
        load_sonar_mapping(path, "hmcts", "hmcts.cath")
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not read the sonar project map"),
    ):
        repository_project(path, "hmcts", "cath-service")


def test_source_coverage_coalesces_intervals_and_reports_gaps() -> None:
    """Reuse complete history while retaining gaps left by partial collection."""
    with closing(connect(":memory:")) as connection:
        initialize(connection)
        august_first = datetime(2026, 8, 1, tzinfo=UTC)
        requested = SourceCoverage(
            organization="hmcts",
            repository="cath-service",
            source=EvidenceSource.PULL_REQUEST,
            query_hash="testhash",
            starts_at=august_first,
            ends_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        for starts_at, ends_at in ((1, 3), (5, 7), (7, 8)):
            record_source_coverage(
                connection,
                requested.model_copy(
                    update={
                        "starts_at": datetime(2026, 8, starts_at, tzinfo=UTC),
                        "ends_at": datetime(2026, 8, ends_at, tzinfo=UTC),
                    },
                ),
            )

        missing = find_missing_coverage(connection, requested)

        assert tuple((interval.starts_at.day, interval.ends_at.day) for interval in missing) == ((3, 5), (8, 10))
        assert tuple(
            (interval.starts_at.day, interval.ends_at.day) for interval in get_source_coverage(connection, requested)
        ) == ((1, 3), (5, 8))


def test_replace_pull_request_facts_reconstructs_interval_and_removes_stale_reviews() -> None:
    """Replace cached interval facts atomically and retain only current review evidence."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    coverage = SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.PULL_REQUEST,
        query_hash="testhash",
        starts_at=starts_at,
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    original = PullRequestFact(
        identifier=101,
        repository="cath-service",
        number=11,
        created_at=starts_at,
        merged_at=datetime(2026, 7, 3, tzinfo=UTC),
        draft=False,
        author_login="author",
        author_type="User",
        reviews=(
            ReviewFact(
                identifier=201,
                submitted_at=datetime(2026, 7, 2, tzinfo=UTC),
                state=ReviewState.COMMENTED,
                author_login="reviewer",
                author_type="User",
            ),
        ),
    )
    refreshed = original.model_copy(
        update={
            "reviews": (
                ReviewFact(
                    identifier=202,
                    submitted_at=datetime(2026, 7, 2, 12, tzinfo=UTC),
                    state=ReviewState.APPROVED,
                    author_login="approver",
                    author_type="User",
                ),
            ),
        },
    )

    with closing(connect(":memory:")) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        initialize(connection)
        replace_pull_request_facts(connection, coverage, (original,), complete=False)
        replace_pull_request_facts(connection, coverage, (refreshed,), complete=True)

        assert get_pull_request_facts(connection, coverage) == (refreshed,)
        assert get_source_coverage(connection, coverage) == (coverage,)


def test_fact_cache_operations_translate_sqlite_failures(tmp_path: Path) -> None:
    """Expose cache read and write failures through the storage boundary."""
    coverage = SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.PULL_REQUEST,
        query_hash="testhash",
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    path = tmp_path / "metrics.sqlite3"

    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not read collection cache"),
    ):
        find_missing_cached_coverage(path, coverage, record_use=True)
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not update collection cache"),
    ):
        cache_pull_request_facts(path, coverage, (), complete=True)
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not read collection cache"),
    ):
        load_cached_pull_request_facts(path, coverage)
    commits = coverage.model_copy(update={"source": EvidenceSource.COMMIT})
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not update collection cache"),
    ):
        cache_direct_commit_facts(path, commits, (), complete=True)
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not read collection cache"),
    ):
        load_cached_direct_commit_facts(path, commits)


def test_a_widened_query_refetches_rather_than_reading_facts_cached_under_the_old_one(tmp_path: Path) -> None:
    """Scope both coverage and facts to the query hash that produced them.

    This is the whole mechanism by which adding a field to a GraphQL document invalidates the cache:
    `query_signature()` hashes the document, so a widened query is a different hash. If the scoping
    regressed, facts collected before a field existed would load with that field at its default —
    every review reporting zero comments, say — and be indistinguishable from a real measurement.
    """
    path = tmp_path / "metrics.sqlite3"
    stored = SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.PULL_REQUEST,
        query_hash="before-the-field-was-added",
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    fact = PullRequestFact(
        identifier=101,
        repository="cath-service",
        number=11,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        merged_at=datetime(2026, 7, 3, tzinfo=UTC),
        draft=False,
        author_login="author",
        author_type="User",
        reviews=(),
    )
    cache_pull_request_facts(path, stored, (fact,), complete=True)
    widened = stored.model_copy(update={"query_hash": "after-the-field-was-added"})

    assert find_missing_cached_coverage(path, stored, record_use=True) == ()
    assert find_missing_cached_coverage(path, widened, record_use=True) == (widened,)
    assert load_cached_pull_request_facts(path, widened) == ()
    assert load_cached_pull_request_facts(path, stored) == (fact,)


def test_only_a_collecting_read_of_the_coverage_stamps_it_as_used(tmp_path: Path) -> None:
    """Write on the collecting read and not on the reporting one, and answer the same either way.

    The reporting read must leave the file alone: the service decides a held bundle still describes
    the caches by their modification time and size, so a report that stamped `accessed_at` would move
    the stamp its own build was compared against and rebuild the whole cohort on every request. The
    interval loses nothing by it — a collection records `accessed_at` on every interval it writes,
    which is what keeps a live signature out of `prune_cache`'s reach.
    """
    path = tmp_path / "metrics.sqlite3"
    coverage = SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.PULL_REQUEST,
        query_hash="testhash",
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    cache_pull_request_facts(path, coverage, (), complete=True)
    with closing(connect(path)) as connection:
        connection.execute(
            "UPDATE source_coverage SET accessed_at = ?", (datetime(2026, 1, 1, tzinfo=UTC).isoformat(),)
        )
        connection.commit()
    stamped = (path.stat().st_mtime, path.stat().st_size)

    assert find_missing_cached_coverage(path, coverage, record_use=False) == ()
    assert (path.stat().st_mtime, path.stat().st_size) == stamped
    assert prune_cache(path, datetime(2026, 6, 1, tzinfo=UTC)) == 1

    cache_pull_request_facts(path, coverage, (), complete=True)
    with closing(connect(path)) as connection:
        connection.execute(
            "UPDATE source_coverage SET accessed_at = ?", (datetime(2026, 1, 1, tzinfo=UTC).isoformat(),)
        )
        connection.commit()

    assert find_missing_cached_coverage(path, coverage, record_use=True) == ()
    assert prune_cache(path, datetime(2026, 6, 1, tzinfo=UTC)) == 0


def test_a_cache_holding_no_coverage_reports_nothing_collected(tmp_path: Path) -> None:
    """Report `None` rather than an invented instant when nothing has ever been collected.

    A cache that is not there is left where it was, unwritten: the service compares both cache files
    against the stamp it built a report from, and a read that created one would move that stamp.
    """
    path = tmp_path / "metrics.sqlite3"

    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "testhash") is None
    assert not path.exists()

    with closing(connect(path)) as connection:
        initialize(connection)

    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "testhash") is None


def collected_to(path: Path, repository: str, ends_at: datetime) -> None:
    """Record one repository's cached pull-request coverage as reaching an instant."""
    cache_pull_request_facts(
        path,
        SourceCoverage(
            organization="hmcts",
            repository=repository,
            source=EvidenceSource.PULL_REQUEST,
            query_hash="testhash",
            starts_at=datetime(2026, 6, 1, tzinfo=UTC),
            ends_at=ends_at,
        ),
        (),
        complete=True,
    )


def test_the_collected_edge_is_not_dragged_back_by_a_repository_the_last_run_missed(tmp_path: Path) -> None:
    """Answer with the edge most of the estate is at, not the edge every repository shares.

    A repository the last run missed reports as unavailable at the window; it must not drag every
    other repository's window back to whenever it was last collected. A repository's own edge is the
    latest of its intervals, which `cath-service` has two of.
    """
    path = tmp_path / "metrics.sqlite3"
    collected_to(path, "cath-service", datetime(2026, 7, 1, tzinfo=UTC))
    for repository in ("cath-service", "pcs-frontend", "sscs-api"):
        collected_to(path, repository, datetime(2026, 9, 1, tzinfo=UTC))
    collected_to(path, "missed-by-the-last-run", datetime(2026, 7, 1, tzinfo=UTC))

    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "testhash") == datetime(
        2026,
        9,
        1,
        tzinfo=UTC,
    )


def test_the_collected_edge_is_not_pushed_forward_by_one_repository_collected_alone(tmp_path: Path) -> None:
    """Ignore a repository that ran on its own and is covered further than the rest of the estate.

    `evidence --refresh --repository x`, a collecting `trend`, and a `collect` that died part-way all
    record coverage to today's midnight for the repositories they touched. Anchoring there would
    leave every repository the run did not reach short of coverage by that day alone — the
    whole-estate `unavailable` this anchor exists to stop.
    """
    path = tmp_path / "metrics.sqlite3"
    for repository in ("cath-service", "pcs-frontend", "sscs-api"):
        collected_to(path, repository, datetime(2026, 9, 1, tzinfo=UTC))
    collected_to(path, "refreshed-on-its-own", datetime(2026, 9, 2, tzinfo=UTC))

    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "testhash") == datetime(
        2026,
        9,
        1,
        tzinfo=UTC,
    )


def test_the_collected_edge_takes_the_later_of_two_equally_common_edges(tmp_path: Path) -> None:
    """With no edge in the majority, answer as the unweighted latest edge always did.

    Half the estate at one edge and half at another says nothing about which is the cohort and which
    is the exception, so the tie leaves the answer where it was before the mode was counted.
    """
    path = tmp_path / "metrics.sqlite3"
    collected_to(path, "cath-service", datetime(2026, 9, 1, tzinfo=UTC))
    collected_to(path, "pcs-frontend", datetime(2026, 8, 1, tzinfo=UTC))

    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "testhash") == datetime(
        2026,
        9,
        1,
        tzinfo=UTC,
    )


def test_reading_the_collected_edge_does_not_keep_an_interval_alive_against_the_prune(tmp_path: Path) -> None:
    """Leave `accessed_at` where it was: reading the edge is not a use of any interval.

    Stamping it here would make every superseded signature look freshly used and keep it in the cache
    for ever, which is exactly what `prune_cache` exists to stop.
    """
    path = tmp_path / "metrics.sqlite3"
    coverage = SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.PULL_REQUEST,
        query_hash="testhash",
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    cache_pull_request_facts(path, coverage, (), complete=True)
    with closing(connect(path)) as connection:
        connection.execute(
            "UPDATE source_coverage SET accessed_at = ?",
            (datetime(2026, 1, 1, tzinfo=UTC).isoformat(),),
        )
        connection.commit()

    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "testhash") is not None
    assert prune_cache(path, datetime(2026, 6, 1, tzinfo=UTC)) == 1


def test_the_collected_edge_ignores_another_organisation_source_or_signature(tmp_path: Path) -> None:
    """Answer for exactly one organisation, source and query signature.

    The signature matters most: `source_coverage` accumulates the signatures of older builds, whose
    rows are not coverage this build can report from. One of them reaching further ahead would
    otherwise anchor a report at an instant nothing current covers.
    """
    path = tmp_path / "metrics.sqlite3"
    asked = SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.PULL_REQUEST,
        query_hash="current-signature",
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    later = {"starts_at": datetime(2026, 8, 1, tzinfo=UTC), "ends_at": datetime(2026, 9, 1, tzinfo=UTC)}
    cache_pull_request_facts(path, asked, (), complete=True)
    cache_pull_request_facts(path, asked.model_copy(update={"organization": "moj", **later}), (), complete=True)
    cache_pull_request_facts(path, asked.model_copy(update={"query_hash": "superseded", **later}), (), complete=True)
    commits = asked.model_copy(update={"source": EvidenceSource.COMMIT, **later})
    cache_direct_commit_facts(path, commits, (), complete=True)

    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "current-signature") == datetime(
        2026,
        8,
        1,
        tzinfo=UTC,
    )
    # The commit row is not the pull-request answer above, and is the commit answer here.
    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.COMMIT, "current-signature") == datetime(
        2026,
        9,
        1,
        tzinfo=UTC,
    )
    assert prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "not-a-signature-here") is None


def test_reading_the_collected_edge_translates_sqlite_failures(tmp_path: Path) -> None:
    """Expose a cache that cannot be read through the storage boundary."""
    path = tmp_path / "metrics.sqlite3"
    path.write_bytes(b"not a database")
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not read collection cache"),
    ):
        prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "testhash")


def test_reading_the_collected_edge_translates_an_unparseable_instant(tmp_path: Path) -> None:
    """Treat an edge this build cannot parse as a cache it cannot read.

    Every caller degrades a `StorageError` to "nothing collected"; a bare `ValueError` escaping from
    here would fail the service's warm-up and every offline command instead.
    """
    path = tmp_path / "metrics.sqlite3"
    collected_to(path, "cath-service", datetime(2026, 9, 1, tzinfo=UTC))
    with closing(connect(path)) as connection:
        connection.execute("UPDATE source_coverage SET ends_at = ?", ("not-an-instant",))
        connection.commit()

    with pytest.raises(StorageError, match="could not read collection cache"):
        prevailing_cached_coverage(path, "hmcts", EvidenceSource.PULL_REQUEST, "testhash")


def test_prune_cache_removes_unused_intervals_and_orphaned_facts(tmp_path: Path) -> None:
    """Delete intervals unused since an instant together with the facts they leave behind."""
    path = tmp_path / "metrics.sqlite3"
    coverage = SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.PULL_REQUEST,
        query_hash="testhash",
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    fact = PullRequestFact(
        identifier=101,
        repository="cath-service",
        number=11,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        merged_at=datetime(2026, 7, 3, tzinfo=UTC),
        draft=False,
        author_login="author",
        author_type="User",
        reviews=(),
    )
    cache_pull_request_facts(path, coverage, (fact,), complete=True)

    assert prune_cache(path, datetime(2026, 7, 1, tzinfo=UTC)) == 0
    assert load_cached_pull_request_facts(path, coverage) == (fact,)

    assert prune_cache(path, datetime.now(UTC) + timedelta(days=1)) == 1

    with closing(connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM source_coverage").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM pull_request_facts").fetchone() == (0,)


def test_prune_cache_translates_sqlite_failures(tmp_path: Path) -> None:
    """Expose a failed prune through the storage boundary."""
    with (
        patch("metrics.storage.connect", side_effect=Error("unavailable")),
        pytest.raises(StorageError, match="could not update collection cache"),
    ):
        prune_cache(tmp_path / "metrics.sqlite3", datetime.now(UTC))


@pytest.mark.parametrize(
    ("covered_days", "missing_days"),
    [
        (((1, 2), (7, 8)), ((3, 6),)),
        (((1, 8),), ()),
    ],
)
def test_find_missing_coverage_respects_request_boundaries(
    covered_days: tuple[tuple[int, int], ...],
    missing_days: tuple[tuple[int, int], ...],
) -> None:
    """Ignore coverage outside a request and recognize enclosing coverage."""
    with closing(connect(":memory:")) as connection:
        initialize(connection)
        requested = SourceCoverage(
            organization="hmcts",
            repository="cath-service",
            source=EvidenceSource.PULL_REQUEST,
            query_hash="testhash",
            starts_at=datetime(2026, 8, 3, tzinfo=UTC),
            ends_at=datetime(2026, 8, 6, tzinfo=UTC),
        )
        for starts_at, ends_at in covered_days:
            record_source_coverage(
                connection,
                requested.model_copy(
                    update={
                        "starts_at": datetime(2026, 8, starts_at, tzinfo=UTC),
                        "ends_at": datetime(2026, 8, ends_at, tzinfo=UTC),
                    },
                ),
            )

        missing = find_missing_coverage(connection, requested)

        assert tuple((interval.starts_at.day, interval.ends_at.day) for interval in missing) == missing_days


def test_direct_commit_facts_are_cached_and_pruned_beside_pull_request_facts(tmp_path: Path) -> None:
    """Cache each source under its own coverage, and clear both when their intervals expire."""
    path = tmp_path / "metrics.sqlite3"
    coverage = SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.COMMIT,
        query_hash="commithash",
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    inside = DirectCommitFact(
        sha="aaa",
        committed_at=datetime(2026, 7, 3, tzinfo=UTC),
        author_login="pusher",
        author_type="User",
        additions=4,
        deletions=1,
        changed_files=1,
    )
    outside = inside.model_copy(update={"sha": "bbb", "committed_at": datetime(2026, 8, 2, tzinfo=UTC)})

    cache_direct_commit_facts(path, coverage, (inside, outside), complete=True)

    # The half-open interval decides what a window loads, exactly as it does for merged pull requests.
    assert load_cached_direct_commit_facts(path, coverage) == (inside,)

    assert prune_cache(path, datetime.now(UTC) + timedelta(days=1)) == 1

    with closing(connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM direct_commit_facts").fetchone() == (0,)
