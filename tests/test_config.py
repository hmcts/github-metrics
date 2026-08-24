"""Test YAML configuration loading and validation."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from metrics.config import (
    ConfigurationError,
    configured_repositories,
    enablement_instants,
    load_configuration,
    owned_repositories,
    repository_owners,
)
from metrics.domain import FindingSeverity


@pytest.fixture
def configuration_path(tmp_path: Path) -> Path:
    """Create a valid configuration file."""
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: data/metrics.sqlite3
lookback:
  operational_days: 60
  maximum_days: 180
  stale_open_days: 21
excluded_repositories:
  - retired-service
teams:
  - identifier: civil
    display_name: Civil
    github_team_slugs:
      - civil-developers
    repositories:
      - civil-service
""",
        encoding="utf-8",
    )
    return path


def test_load_configuration(configuration_path: Path) -> None:
    """Load all supported configuration fields."""
    configuration = load_configuration(configuration_path)

    assert configuration.organization == "hmcts"
    assert configuration.database == configuration_path.parent / "data/metrics.sqlite3"
    assert configuration.lookback.operational_days == 60
    assert configuration.lookback.maximum_days == 180
    assert configuration.lookback.stale_open_days == 21
    assert configuration.excluded_repositories == ("retired-service",)
    assert configuration.teams[0].identifier == "civil"
    assert configuration.teams[0].github_team_slugs == ("civil-developers",)
    assert configuration.teams[0].repositories == ("civil-service",)


def test_load_configuration_uses_default_lookbacks(configuration_path: Path) -> None:
    """Use stable collection windows when lookback is omitted."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(
        content.replace("lookback:\n  operational_days: 60\n  maximum_days: 180\n  stale_open_days: 21\n", ""),
        encoding="utf-8",
    )

    configuration = load_configuration(configuration_path)

    assert configuration.lookback.operational_days == 90
    assert configuration.lookback.maximum_days == 365
    assert configuration.lookback.mutable_hours == 6
    assert configuration.lookback.stale_open_days == 14
    assert configuration.cohort.excluded_authors == ("renovate", "dependabot")


def test_load_configuration_uses_default_traceability(configuration_path: Path) -> None:
    """Match the reference tool's minimum description length and both HMCTS reference patterns."""
    configuration = load_configuration(configuration_path)

    assert configuration.traceability.minimum_description == 30
    assert configuration.traceability.reference_patterns == (r"#\d+", r"[A-Z][A-Z0-9]+-\d+")


def test_load_configuration_customizes_traceability(configuration_path: Path) -> None:
    """Load a configured minimum description length and a replaced pattern list."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(
        content.replace(
            "excluded_repositories:\n",
            "traceability:\n"
            "  minimum_description: 15\n"
            "  reference_patterns:\n"
            "    - TICKET-\\d+\n"
            "excluded_repositories:\n",
        ),
        encoding="utf-8",
    )

    traceability = load_configuration(configuration_path).traceability

    assert traceability.minimum_description == 15
    assert traceability.reference_patterns == ("TICKET-\\d+",)


def test_load_configuration_rejects_an_unusable_reference_pattern(configuration_path: Path) -> None:
    """Fail at load time rather than at every pull request an invalid pattern would scan."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(
        content.replace(
            "excluded_repositories:\n",
            "traceability:\n  reference_patterns:\n    - '[unclosed'\nexcluded_repositories:\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="invalid reference pattern"):
        load_configuration(configuration_path)


def test_load_configuration_uses_default_practice_rule(configuration_path: Path) -> None:
    """Enable high-severity unreviewed-merge findings by default."""
    configuration = load_configuration(configuration_path)

    assert configuration.practices.unreviewed_merge.enabled
    assert configuration.practices.unreviewed_merge.severity == "high"
    assert configuration.practices.unreviewed_merge.minimum_occurrences == 1
    assert configuration.practices.unreviewed_merge.excluded_logins == ()


def test_load_configuration_customizes_practice_rule(configuration_path: Path) -> None:
    """Load independent rule enablement, severity, threshold, and identity exclusions."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(
        content.replace(
            "excluded_repositories:\n",
            "practices:\n"
            "  unreviewed-merge:\n"
            "    enabled: false\n"
            "    severity: medium\n"
            "    minimum_occurrences: 3\n"
            "    excluded_logins:\n"
            "      - service-account\n"
            "excluded_repositories:\n",
        ),
        encoding="utf-8",
    )

    rule = load_configuration(configuration_path).practices.unreviewed_merge

    assert not rule.enabled
    assert rule.severity == "medium"
    assert rule.minimum_occurrences == 3
    assert rule.excluded_logins == ("service-account",)


def test_load_configuration_allows_missing_github_teams(configuration_path: Path) -> None:
    """Allow ownership evidence when GitHub team data is unavailable."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(
        content.replace("    github_team_slugs:\n      - civil-developers\n", ""),
        encoding="utf-8",
    )

    assert load_configuration(configuration_path).teams[0].github_team_slugs == ()


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (("version: 1", "version: 2"), "Input should be 1"),
        (("  operational_days: 60", "  operational_days: 0"), "Input should be greater than 0"),
        (("organization: hmcts", "organisation: hmcts"), "Extra inputs are not permitted"),
    ],
)
def test_load_configuration_rejects_invalid_values(
    configuration_path: Path,
    replacement: tuple[str, str],
    message: str,
) -> None:
    """Reject unsupported, invalid, and misspelled values."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(content.replace(*replacement), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_configuration(configuration_path)


def test_load_configuration_rejects_duplicate_ownership(configuration_path: Path) -> None:
    """Prevent one repository contributing to multiple team aggregates."""
    with configuration_path.open("a", encoding="utf-8") as configuration_file:
        configuration_file.write(
            """\
  - identifier: family
    display_name: Family
    github_team_slugs:
      - family-developers
    repositories:
      - civil-service
""",
        )

    with pytest.raises(ConfigurationError, match="repositories may belong to only one team: civil-service"):
        load_configuration(configuration_path)


def test_load_configuration_rejects_duplicate_team_identifiers(configuration_path: Path) -> None:
    """Require stable unique business team identifiers."""
    with configuration_path.open("a", encoding="utf-8") as configuration_file:
        configuration_file.write(
            """\
  - identifier: civil
    display_name: Another Civil Team
    repositories:
      - civil-frontend
""",
        )

    with pytest.raises(ConfigurationError, match="team identifiers must be unique"):
        load_configuration(configuration_path)


def test_load_configuration_rejects_owned_exclusion(configuration_path: Path) -> None:
    """Prevent an owned repository also being excluded."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(content.replace("retired-service", "civil-service"), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="excluded repositories may not have an owner: civil-service"):
        load_configuration(configuration_path)


def test_load_configuration_has_no_enablement_dates_by_default(configuration_path: Path) -> None:
    """Anchor nothing until someone records when the tooling was turned on."""
    configuration = load_configuration(configuration_path)

    assert configuration.enablement == {}
    assert enablement_instants(configuration) == {"civil-service": None}


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("2026-01-05", datetime(2026, 1, 5, tzinfo=UTC)),
        ("'2026-01-05'", datetime(2026, 1, 5, tzinfo=UTC)),
        ("2026-01-05T09:30:00Z", datetime(2026, 1, 5, 9, 30, tzinfo=UTC)),
        ("2026-01-05T09:30:00", datetime(2026, 1, 5, 9, 30, tzinfo=UTC)),
        ("2026-01-05T09:30:00+02:00", datetime(2026, 1, 5, 9, 30, tzinfo=timezone(timedelta(hours=2)))),
    ],
)
def test_load_configuration_parses_an_enablement_date_as_a_window_edge_does(
    configuration_path: Path,
    written: str,
    expected: datetime,
) -> None:
    """Apply one instant rule everywhere: bare date is UTC midnight, naive is UTC, offsets are kept.

    The quoted and unquoted date forms must agree, because YAML resolves the unquoted one to a
    `date` and the quoted one to a string before pydantic sees either.
    """
    with configuration_path.open("a", encoding="utf-8") as configuration_file:
        configuration_file.write(f"enablement:\n  civil-service: {written}\n")

    configuration = load_configuration(configuration_path)

    assert configuration.enablement["civil-service"] == expected
    assert enablement_instants(configuration) == {"civil-service": expected}


def test_load_configuration_rejects_an_enablement_date_for_an_unconfigured_repository(
    configuration_path: Path,
) -> None:
    """Name the offending repository rather than reporting it as a date nobody set."""
    with configuration_path.open("a", encoding="utf-8") as configuration_file:
        configuration_file.write("enablement:\n  civil-servcie: 2026-01-05\n  retired-service: 2026-01-05\n")

    with pytest.raises(
        ConfigurationError,
        match="enablement dates must name a configured repository: civil-servcie, retired-service",
    ):
        load_configuration(configuration_path)


@pytest.mark.parametrize(
    ("written", "message"),
    [
        ("not-a-date", "expected a date or datetime"),
        # PyYAML's own timestamp constructor rejects an impossible date before pydantic sees it, and
        # its bare `ValueError` must still surface as a configuration error rather than a crash.
        ("2026-13-05", "month must be in 1..12"),
        ("42", "expected a date or datetime"),
    ],
)
def test_load_configuration_rejects_an_unparseable_enablement_date(
    configuration_path: Path,
    written: str,
    message: str,
) -> None:
    """Fail at load time rather than anchoring a series to something that is not an instant.

    The bare number matters: pydantic would otherwise read `42` as a Unix timestamp and anchor the
    series to 1970, which is a second instant rule arriving by accident.
    """
    with configuration_path.open("a", encoding="utf-8") as configuration_file:
        configuration_file.write(f"enablement:\n  civil-service: {written}\n")

    with pytest.raises(ConfigurationError, match=message):
        load_configuration(configuration_path)


def test_enablement_instants_reports_every_repository_in_the_reporting_order(population_path: Path) -> None:
    """Keep a repository without a date in the mapping, so `trend` can report the reason it has none."""
    with population_path.open("a", encoding="utf-8") as configuration_file:
        configuration_file.write("enablement:\n  opal-logging-service: 2026-02-01\n")

    assert list(enablement_instants(load_configuration(population_path)).items()) == [
        ("nfdiv-case-api", None),
        ("opal-common-lib", None),
        ("opal-logging-service", datetime(2026, 2, 1, tzinfo=UTC)),
    ]


def test_load_configuration_preserves_absolute_database_path(configuration_path: Path, tmp_path: Path) -> None:
    """Keep explicitly absolute database paths unchanged."""
    database = tmp_path / "absolute.sqlite3"
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(
        content.replace("data/metrics.sqlite3", str(database)),
        encoding="utf-8",
    )

    assert load_configuration(configuration_path).database == database


def test_load_configuration_uses_safe_yaml_loader(tmp_path: Path) -> None:
    """Reject YAML constructors that could instantiate arbitrary Python objects."""
    path = tmp_path / "unsafe.yaml"
    path.write_text("!!python/object/apply:builtins.str [unsafe]", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="could not determine a constructor"):
        load_configuration(path)


@pytest.fixture
def population_path(tmp_path: Path) -> Path:
    """Create a configuration whose teams and repositories are deliberately out of order."""
    path = tmp_path / "population.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: opal
    display_name: Opal
    repositories:
      - opal-logging-service
      - opal-common-lib
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
""",
        encoding="utf-8",
    )
    return path


def test_owned_repositories_orders_by_team_and_then_repository(population_path: Path) -> None:
    """Fix one reporting order, so editing the configuration file cannot reorder a report.

    The file lists `opal` before `divorce` and its second repository before its first; both are
    corrected here. At fourteen repositories an unordered report makes two runs undiffable, which
    hides the change a reader opened the diff to find.
    """
    configuration = load_configuration(population_path)

    assert owned_repositories(configuration) == (
        ("divorce", "nfdiv-case-api"),
        ("opal", "opal-common-lib"),
        ("opal", "opal-logging-service"),
    )
    assert configured_repositories(configuration) == (
        "nfdiv-case-api",
        "opal-common-lib",
        "opal-logging-service",
    )


def test_repository_owners_names_the_team_that_owns_each_repository(population_path: Path) -> None:
    """Say who owns a repository, which is all `teams` means — never how several reduce to one."""
    assert repository_owners(load_configuration(population_path)) == {
        "nfdiv-case-api": "divorce",
        "opal-common-lib": "opal",
        "opal-logging-service": "opal",
    }


def test_the_shipped_example_configuration_loads_and_carries_every_documented_key() -> None:
    """Keep `metrics.example.yaml` loadable and current: the README tells users to start from it.

    Asserted against the parsed values rather than the file's text, so a key renamed in `config.py`
    fails here rather than leaving the example silently falling back to a default the reader believes
    they set. This file had already drifted behind four assessment keys and the whole `traceability`
    block before this test existed.
    """
    # Anchored to this file rather than the working directory, so the test does not depend on pytest
    # having been invoked from the repository root.
    configuration = load_configuration(Path(__file__).resolve().parent.parent / "metrics.example.yaml")

    assert configuration.assessment.pull_request_size.maximum == 400
    assert configuration.assessment.merge_cycle_time.maximum == 24
    assert configuration.assessment.time_to_first_review.maximum == 8
    assert configuration.assessment.review_depth_minimum_percentage == 50
    assert configuration.traceability.minimum_description == 30
    assert configuration.traceability.reference_patterns == (r"#\d+", r"[A-Z][A-Z0-9]+-\d+")
    assert configuration.enablement == {"example-service": datetime(2026, 6, 1, tzinfo=UTC)}


@pytest.fixture
def policy_path(tmp_path: Path) -> Path:
    """Create a shared policy file that names no teams, as a reusable rules file would not."""
    path = tmp_path / "policy.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: data/metrics.sqlite3
assessment:
  minimum_merges: 25
  independent-review-coverage:
    green_percentage: 95
    amber_percentage: 80
practices:
  unreviewed-merge:
    severity: medium
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def teams_path(tmp_path: Path) -> Path:
    """Create a team file that carries no assessment rules, as a per-team file would not."""
    path = tmp_path / "teams.yaml"
    path.write_text(
        """\
teams:
  - identifier: civil
    display_name: Civil
    repositories:
      - civil-service
""",
        encoding="utf-8",
    )
    return path


def test_load_configuration_reads_several_files_as_one_document(policy_path: Path, teams_path: Path) -> None:
    """Validate the combined text, so neither file has to be a configuration in its own right.

    This is the point of the split: the assessment rules are written once and paired with whichever
    team file a run is about, rather than each team file restating the policy and drifting from it.
    """
    configuration = load_configuration(policy_path, teams_path)

    assert configuration.organization == "hmcts"
    assert configuration.assessment.minimum_merges == 25
    assert configuration.assessment.independent_review_coverage.green_percentage == 95
    assert configuration.practices.unreviewed_merge.severity is FindingSeverity.MEDIUM
    assert configuration.teams[0].repositories == ("civil-service",)


def test_load_configuration_resolves_a_key_given_twice_as_yaml_does(policy_path: Path, teams_path: Path) -> None:
    """Let the last occurrence win, exactly as it would within a single file."""
    replacement_path = teams_path.with_name("replacement.yaml")
    replacement_path.write_text(
        """\
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
""",
        encoding="utf-8",
    )

    configuration = load_configuration(policy_path, teams_path, replacement_path)

    assert configured_repositories(configuration) == ("nfdiv-case-api",)


def test_load_configuration_joins_files_that_do_not_end_in_a_newline(tmp_path: Path, teams_path: Path) -> None:
    """Keep the last line of one file off the first line of the next."""
    unterminated_path = tmp_path / "unterminated.yaml"
    unterminated_path.write_text("version: 1\norganization: hmcts\ndatabase: metrics.sqlite3", encoding="utf-8")

    assert load_configuration(unterminated_path, teams_path).organization == "hmcts"


def test_load_configuration_anchors_a_relative_database_to_the_first_file(
    policy_path: Path,
    teams_path: Path,
) -> None:
    """Resolve the database beside the file the configuration starts in, wherever the rest lives."""
    assert load_configuration(policy_path, teams_path).database == policy_path.parent / "data/metrics.sqlite3"
