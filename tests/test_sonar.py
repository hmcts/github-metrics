"""Test SonarCloud project listing, analysis and measure reads."""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from requests import ConnectionError as RequestsConnectionError
from requests import HTTPError, Session
from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError

from metrics.domain import (
    AvailabilityReason,
    SonarGateLevel,
    SonarProjectMapping,
    SonarRepositoryProject,
    SonarResolution,
    StoredSonarMapping,
)
from metrics.github import GitHubClient, GitHubError
from metrics.sonar import (
    SEARCH_INTERVAL_SECONDS,
    SONAR_METRIC_KEYS,
    CallPacer,
    SonarAnalysis,
    SonarClient,
    SonarDeclaration,
    SonarError,
    SonarMappingOutcome,
    SonarProject,
    StoredProjectMap,
    confirms_candidate,
    declared_project,
    github_to_sonar,
    parse_properties,
    searchable_revisions,
    sonar_to_github,
)
from metrics.storage import record_sonar_mapping

# Written out independently of the constant under test, from the keys verified against the live
# SonarCloud API on 2026-08-27. The point of the guard is that these two lists are maintained by
# different hands: copying the constant here would assert only that it equals itself.
VERIFIED_METRIC_KEYS = frozenset(
    {
        "alert_status",
        "quality_gate_details",
        "coverage",
        "duplicated_lines_density",
        "ncloc",
        "violations",
        "software_quality_reliability_issues",
        "software_quality_maintainability_issues",
        "software_quality_security_issues",
        "security_hotspots",
        "reliability_rating",
        "sqale_rating",
        "security_rating",
        "security_review_rating",
    },
)

GATE_DETAILS = json.dumps(
    {
        "level": "ERROR",
        "conditions": [
            {"metric": "new_coverage", "op": "LT", "period": 1, "error": "80", "actual": "62.1", "level": "ERROR"},
            {"metric": "new_reliability_rating", "op": "GT", "period": 1, "error": "1", "actual": "1", "level": "OK"},
        ],
        "ignoredConditions": False,
    },
)


def answer(payload: object, status_code: int = 200) -> MagicMock:
    """Build one faked SonarCloud response carrying a JSON body."""
    response = MagicMock(status_code=status_code)
    response.json.return_value = payload
    return response


def measures_payload(measures: list[dict[str, str]]) -> dict[str, object]:
    """Build one `/api/measures/component` body around the measures a test cares about."""
    return {"component": {"key": "hmcts.cath", "name": "CaTH", "qualifier": "TRK", "measures": measures}}


def project_payload(components: list[dict[str, object]], total: int) -> dict[str, object]:
    """Build one page of the project listing."""
    return {"paging": {"pageIndex": 1, "pageSize": 500, "total": total}, "components": components}


def test_metric_keys_are_all_verified_keys() -> None:
    """Guard the one constant a single wrong key silently empties the whole response for."""
    assert set(SONAR_METRIC_KEYS) == VERIFIED_METRIC_KEYS
    assert "maintainability_rating" not in SONAR_METRIC_KEYS
    assert "sqale_rating" in SONAR_METRIC_KEYS
    assert len(SONAR_METRIC_KEYS) == len(set(SONAR_METRIC_KEYS))


def test_client_sends_a_token_when_the_environment_sets_one() -> None:
    """Authenticate the session when a token is available, widening the listing to private projects."""
    session = Session()
    SonarClient(session, {"SONAR_TOKEN": "secret"})

    assert session.headers["Authorization"] == "Bearer secret"


def test_client_prefers_the_first_named_token_variable() -> None:
    """Read SONAR_TOKEN ahead of SONARCLOUD_TOKEN when both are set."""
    session = Session()
    SonarClient(session, {"SONAR_TOKEN": "first", "SONARCLOUD_TOKEN": "second"})

    assert session.headers["Authorization"] == "Bearer first"


def test_client_falls_back_to_the_second_token_variable() -> None:
    """Read SONARCLOUD_TOKEN when it is the only one set."""
    session = Session()
    SonarClient(session, {"SONARCLOUD_TOKEN": "second"})

    assert session.headers["Authorization"] == "Bearer second"


@pytest.mark.parametrize("environment", [{}, {"SONAR_TOKEN": ""}])
def test_client_reads_anonymously_without_a_token(environment: dict[str, str]) -> None:
    """Send no Authorization header at all when no token is set, because anonymous reads work."""
    session = Session()
    SonarClient(session, environment)

    assert "Authorization" not in session.headers


def test_client_reads_the_token_from_the_process_environment() -> None:
    """Default to the real environment, which is where a scanner token is normally set."""
    session = Session()
    with patch.dict("os.environ", {"SONAR_TOKEN": "ambient"}, clear=True):
        SonarClient(session)

    assert session.headers["Authorization"] == "Bearer ambient"


def test_list_projects_pages_the_listing() -> None:
    """Follow the listing's own total across pages, at the maximum page size."""
    session = Session()
    first = answer(
        project_payload(
            [{"key": "cmc-citizen-frontend", "visibility": "public", "analysisDate": "2026-08-24T14:49:07+0000"}],
            2,
        ),
    )
    second = answer(project_payload([{"key": "never-analysed", "visibility": "private"}], 2))
    client = SonarClient(session, {})
    with patch.object(session, "get", side_effect=[first, second]) as get:
        projects = client.list_projects("hmcts")

    assert projects == (
        SonarProject(
            key="cmc-citizen-frontend",
            visibility="public",
            analysis_at=datetime(2026, 8, 24, 14, 49, 7, tzinfo=UTC),
        ),
        SonarProject(key="never-analysed", visibility="private", analysis_at=None),
    )
    assert get.call_args_list[0].kwargs["params"] == {
        "organization": "hmcts",
        "ps": 500,
        "p": 1,
        "f": "analysisDate",
    }
    assert get.call_args_list[1].kwargs["params"]["p"] == 2
    assert client.requests_issued == 2


def test_list_projects_stops_on_an_empty_page() -> None:
    """Stop paging when a page holds nothing, whatever total the listing claimed."""
    session = Session()
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer(project_payload([], 900))) as get:
        assert client.list_projects("hmcts") == ()

    get.assert_called_once()


def test_list_projects_reports_an_unreadable_listing() -> None:
    """Classify a listing this build cannot parse as a failure rather than as an empty organisation."""
    session = Session()
    client = SonarClient(session, {})
    with (
        patch.object(session, "get", return_value=answer({"components": [{"visibility": "public"}]})),
        pytest.raises(SonarError, match="invalid project listing response"),
    ):
        client.list_projects("hmcts")


def test_project_analyses_returns_dates_and_revisions() -> None:
    """Return the analyses newest first, with the commit each one ran against."""
    session = Session()
    payload = {
        "analyses": [
            {"key": "one", "date": "2026-08-27T09:49:35+0000", "revision": "ce34e614"},
            {"key": "two", "date": "2026-08-24T09:24:56+0000"},
        ],
    }
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer(payload)) as get:
        analyses = client.project_analyses("hmcts.cath", 5)

    assert analyses == (
        SonarAnalysis(analysis_at=datetime(2026, 8, 27, 9, 49, 35, tzinfo=UTC), revision="ce34e614"),
        SonarAnalysis(analysis_at=datetime(2026, 8, 24, 9, 24, 56, tzinfo=UTC), revision=None),
    )
    assert get.call_args.kwargs["params"] == {"project": "hmcts.cath", "ps": 5}


def test_project_analyses_never_asks_beyond_the_maximum_page_size() -> None:
    """Clamp the requested count to the page size SonarCloud will serve."""
    session = Session()
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer({"analyses": []})) as get:
        assert client.project_analyses("hmcts.cath", 5000) == ()

    assert get.call_args.kwargs["params"]["ps"] == 500


def test_component_measures_parses_a_full_response() -> None:
    """Read every measure, the gate and its conditions out of one call."""
    session = Session()
    payload = measures_payload(
        [
            {"metric": "coverage", "value": "89.2"},
            {"metric": "duplicated_lines_density", "value": "2.3"},
            {"metric": "ncloc", "value": "47327"},
            {"metric": "violations", "value": "393"},
            {"metric": "software_quality_reliability_issues", "value": "28"},
            {"metric": "software_quality_maintainability_issues", "value": "365"},
            {"metric": "software_quality_security_issues", "value": "18"},
            {"metric": "security_hotspots", "value": "0"},
            {"metric": "reliability_rating", "value": "4.0"},
            {"metric": "sqale_rating", "value": "1.0"},
            {"metric": "security_rating", "value": "3.0"},
            {"metric": "security_review_rating", "value": "1.0"},
            {"metric": "alert_status", "value": "ERROR"},
            {"metric": "quality_gate_details", "value": GATE_DETAILS},
        ],
    )
    analysed_at = datetime(2026, 8, 27, 9, 49, 35, tzinfo=UTC)
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer(payload)) as get:
        measures = client.component_measures("hmcts.cath", analysed_at)

    assert get.call_args.kwargs["params"] == {
        "component": "hmcts.cath",
        "metricKeys": ",".join(SONAR_METRIC_KEYS),
    }
    assert measures.project_key == "hmcts.cath"
    assert measures.analysis_at == analysed_at
    assert measures.coverage == 89.2
    assert measures.duplicated_lines_density == 2.3
    assert measures.lines_of_code == 47327
    assert measures.violations == 393
    assert measures.reliability_issues == 28
    assert measures.maintainability_issues == 365
    assert measures.security_issues == 18
    assert measures.security_hotspots == 0
    assert measures.reliability_rating is not None
    assert measures.reliability_rating.letter == "D"
    assert measures.maintainability_rating is not None
    assert measures.maintainability_rating.letter == "A"
    assert measures.security_rating is not None
    assert measures.security_rating.letter == "C"
    assert measures.security_review_rating is not None
    assert measures.security_review_rating.letter == "A"
    assert measures.gate is not None
    assert measures.gate.level is SonarGateLevel.ERROR
    assert [
        (condition.metric, condition.comparator, condition.threshold, condition.actual, condition.level)
        for condition in measures.gate.conditions
    ] == [
        ("new_coverage", "LT", "80", "62.1", SonarGateLevel.ERROR),
        ("new_reliability_rating", "GT", "1", "1", SonarGateLevel.OK),
    ]


def test_component_measures_leaves_absent_metrics_absent() -> None:
    """Report a metric SonarCloud did not measure as absent, never as zero."""
    session = Session()
    payload = measures_payload([{"metric": "ncloc", "value": "12"}, {"metric": "alert_status", "value": "OK"}])
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer(payload)):
        measures = client.component_measures("hmcts.cath")

    assert measures.lines_of_code == 12
    assert measures.analysis_at is None
    assert measures.coverage is None
    assert measures.violations is None
    assert measures.security_hotspots is None
    assert measures.reliability_rating is None
    assert measures.maintainability_rating is None
    assert measures.gate is not None
    assert measures.gate.level is SonarGateLevel.OK
    assert measures.gate.conditions == ()


def test_component_measures_accepts_a_project_that_was_never_analysed() -> None:
    """Return a bare project key when SonarCloud measured nothing at all."""
    session = Session()
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer({"component": {"key": "never-analysed"}})):
        measures = client.component_measures("never-analysed")

    assert measures.project_key == "never-analysed"
    assert measures.gate is None
    assert measures.coverage is None


def test_component_measures_survives_a_null_measure_list() -> None:
    """Read every measure as absent when one bad metric key emptied the whole response."""
    session = Session()
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer({"component": {"key": "hmcts.cath", "measures": None}})):
        measures = client.component_measures("hmcts.cath")

    assert measures.gate is None
    assert measures.lines_of_code is None


def test_component_measures_ignores_an_unrecognised_gate_level(caplog: pytest.LogCaptureFixture) -> None:
    """Report a gate level this build does not know as no gate, never as a passing one."""
    session = Session()
    payload = measures_payload([{"metric": "alert_status", "value": "WARN"}])
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer(payload)):
        measures = client.component_measures("hmcts.cath")

    assert measures.gate is None
    assert caplog.records == []


def test_component_measures_falls_back_to_the_verdict_when_details_do_not_parse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep the gate level when its conditions cannot be read, and say so in the log."""
    session = Session()
    payload = measures_payload(
        [{"metric": "alert_status", "value": "ERROR"}, {"metric": "quality_gate_details", "value": "not json"}],
    )
    client = SonarClient(session, {})
    with caplog.at_level(logging.WARNING), patch.object(session, "get", return_value=answer(payload)):
        measures = client.component_measures("hmcts.cath")

    assert measures.gate is not None
    assert measures.gate.level is SonarGateLevel.ERROR
    assert measures.gate.conditions == ()
    assert "quality gate details" in caplog.text


def test_component_measures_drops_a_gate_whose_details_and_status_are_both_unreadable() -> None:
    """Report no gate when neither the details nor the status can be read."""
    session = Session()
    payload = measures_payload([{"metric": "quality_gate_details", "value": "not json"}])
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer(payload)):
        measures = client.component_measures("hmcts.cath")

    assert measures.gate is None


def test_component_measures_ignores_an_unreadable_number(caplog: pytest.LogCaptureFixture) -> None:
    """Treat a value that is not a number as unmeasured, and record that it was seen."""
    session = Session()
    payload = measures_payload([{"metric": "coverage", "value": "unknown"}])
    client = SonarClient(session, {})
    with caplog.at_level(logging.WARNING), patch.object(session, "get", return_value=answer(payload)):
        measures = client.component_measures("hmcts.cath")

    assert measures.coverage is None
    assert "unreadable value for coverage" in caplog.text


def test_component_measures_rejects_a_measure_the_evidence_model_refuses() -> None:
    """Classify a measure no evidence model can hold as a failure rather than storing a nonsense one."""
    session = Session()
    payload = measures_payload([{"metric": "coverage", "value": "-4"}])
    client = SonarClient(session, {})
    with (
        patch.object(session, "get", return_value=answer(payload)),
        pytest.raises(SonarError, match=re.escape("measures this build cannot accept for hmcts.cath")),
    ):
        client.component_measures("hmcts.cath")


@pytest.mark.parametrize(
    ("status_code", "message", "reason"),
    [
        (401, "SonarCloud authentication failed", AvailabilityReason.AUTHENTICATION_FAILED),
        (403, "SonarCloud permission denied", AvailabilityReason.PERMISSION_DENIED),
        (404, "SonarCloud project not found or inaccessible", AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE),
        (429, "SonarCloud rate limit exceeded", AvailabilityReason.RATE_LIMITED),
        (503, "SonarCloud returned HTTP 503", AvailabilityReason.COLLECTION_FAILED),
    ],
)
def test_get_classifies_http_failures(status_code: int, message: str, reason: AvailabilityReason) -> None:
    """Translate a refusal into a classified failure, without quoting the body back into a report."""
    session = Session()
    client = SonarClient(session, {})
    with (
        patch.object(session, "get", return_value=answer({"errors": [{"msg": "no"}]}, status_code)),
        pytest.raises(SonarError, match=message) as captured,
    ):
        client.component_measures("hmcts.cath")

    assert captured.value.reason is reason


def test_get_reports_a_network_failure() -> None:
    """Preserve the cause of a call that never answered."""
    session = Session()
    client = SonarClient(session, {})
    with (
        patch.object(session, "get", side_effect=RequestsConnectionError("offline")),
        pytest.raises(SonarError, match="SonarCloud request failed: offline") as captured,
    ):
        client.list_projects("hmcts")

    assert captured.value.reason is AvailabilityReason.COLLECTION_FAILED


def test_get_reports_an_unparseable_body() -> None:
    """Classify a body that is not JSON as a failure rather than as an absent measurement."""
    session = Session()
    response = MagicMock(status_code=200)
    response.json.side_effect = RequestsJSONDecodeError("invalid", "{", 1)
    client = SonarClient(session, {})
    with (
        patch.object(session, "get", return_value=response),
        pytest.raises(SonarError, match="invalid JSON at line 1, column 2") as captured,
    ):
        client.component_measures("hmcts.cath")

    assert captured.value.reason is AvailabilityReason.COLLECTION_FAILED


def github_answer(payload: object = None, status_code: int = 200, text: str = "") -> MagicMock:
    """Build one faked GitHub response, raising for a status the client is meant to classify."""
    response = MagicMock(status_code=status_code, headers={}, links={}, content=b"", text=text)
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = HTTPError(response=response)
    else:
        response.raise_for_status.return_value = None
    return response


def commit_search_payload(full_name: str | None) -> dict[str, object]:
    """Build one `/search/commits` body, either naming a repository or matching nothing."""
    if full_name is None:
        return {"total_count": 0, "incomplete_results": False, "items": []}
    return {
        "total_count": 1,
        "incomplete_results": False,
        "items": [{"sha": "ce34e614", "repository": {"id": 1, "name": "x", "full_name": full_name}}],
    }


def sonar_reading(payload: object) -> tuple[SonarClient, MagicMock]:
    """Build a SonarClient over a faked session, returning both so calls can be asserted on."""
    session = Session()
    get = MagicMock(return_value=answer(payload))
    session.get = get  # type: ignore[method-assign]
    return SonarClient(session, {}), get


def github_reading(*responses: MagicMock) -> tuple[GitHubClient, MagicMock]:
    """Build a GitHubClient over a faked session that answers with the given responses in order."""
    session = MagicMock()
    session.headers = {}
    session.get.side_effect = responses
    return GitHubClient("secret", session, pause=MagicMock()), session.get


def analyses_payload(*entries: tuple[str, str | None]) -> dict[str, object]:
    """Build one `/api/project_analyses/search` body from date and revision pairs."""
    return {
        "analyses": [
            {"key": f"analysis-{position}", "date": date} | ({} if revision is None else {"revision": revision})
            for position, (date, revision) in enumerate(entries)
        ],
    }


def resolving() -> tuple[CallPacer, MagicMock]:
    """Build a pacer over a clock that never advances, so spacing is asserted and never waited for."""
    pause = MagicMock()
    return CallPacer(SEARCH_INTERVAL_SECONDS, clock=lambda: 1000.0, pause=pause), pause


def test_call_pacer_does_not_delay_the_first_call() -> None:
    """Spend nothing on the call that cannot yet have exceeded any per-minute limit."""
    pacer, pause = resolving()
    pacer.wait()

    pause.assert_not_called()


def test_call_pacer_spaces_later_calls_by_the_interval() -> None:
    """Wait out the interval before each later call, so the limit is never reached."""
    pacer, pause = resolving()
    pacer.wait()
    pacer.wait()
    pacer.wait()

    assert [call.args for call in pause.call_args_list] == [(SEARCH_INTERVAL_SECONDS,), (SEARCH_INTERVAL_SECONDS,)]


def test_call_pacer_does_not_delay_a_call_the_interval_has_already_passed_for() -> None:
    """Charge nothing for spacing that real work has already provided."""
    pause = MagicMock()
    elapsing = iter([1000.0, 1000.0 + SEARCH_INTERVAL_SECONDS * 3])
    pacer = CallPacer(SEARCH_INTERVAL_SECONDS, clock=lambda: next(elapsing), pause=pause)
    pacer.wait()
    pacer.wait()

    pause.assert_not_called()


def test_call_pacer_does_not_stall_on_a_clock_that_went_backwards() -> None:
    """Keep a wall-clock correction a pause and never a hang, which an unfloored delay would be."""
    pause = MagicMock()
    corrected = iter([1000.0, 400.0, 400.0])
    pacer = CallPacer(SEARCH_INTERVAL_SECONDS, clock=lambda: next(corrected), pause=pause)
    pacer.wait()
    pacer.wait()

    assert [call.args for call in pause.call_args_list] == [(SEARCH_INTERVAL_SECONDS,)]


def test_searchable_revisions_keeps_one_entry_per_distinct_revision() -> None:
    """Ask the scarcest quota there is one question per commit, however often it was analysed."""
    first = datetime(2026, 8, 27, 9, 49, 35, tzinfo=UTC)
    second = datetime(2026, 8, 26, 9, 49, 35, tzinfo=UTC)
    third = datetime(2026, 8, 25, 9, 49, 35, tzinfo=UTC)
    analyses = (
        SonarAnalysis(analysis_at=first, revision="aaa1111"),
        SonarAnalysis(analysis_at=second, revision="aaa1111"),
        SonarAnalysis(analysis_at=third, revision=None),
        SonarAnalysis(analysis_at=third, revision="bbb2222"),
    )

    assert searchable_revisions(analyses) == (("aaa1111", first), ("bbb2222", third))


def test_searchable_revisions_drops_anything_that_is_not_an_object_name() -> None:
    """Never put a value another system chose into a URL this code builds, or spend a search on it.

    `requests` resolves `..` in a path and honours a `?` in it, so an unconstrained revision would
    let `/repos/{org}/{repo}/commits/{sha}` address a different endpoint entirely — whose `200` the
    declaration check would read as a confirmation.
    """
    analysed = datetime(2026, 8, 27, 9, 49, 35, tzinfo=UTC)
    analyses = (
        SonarAnalysis(analysis_at=analysed, revision="../../../rate_limit"),
        SonarAnalysis(analysis_at=analysed, revision="ce34e614?per_page=1"),
        SonarAnalysis(analysis_at=analysed, revision="ce34e6 hash:other"),
        SonarAnalysis(analysis_at=analysed, revision="short"),
        SonarAnalysis(analysis_at=analysed, revision="ce34e614"),
    )

    assert searchable_revisions(analyses) == (("ce34e614", analysed),)


def test_sonar_to_github_resolves_a_project_from_its_newest_analysis() -> None:
    """Name the repository the newest analysed commit belongs to, and say how it was found."""
    sonar, sonar_get = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, github_get = github_reading(github_answer(commit_search_payload("hmcts/cath-service")))
    pacer, pause = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.RESOLVED
    assert attempt.analyses_tried == 1
    assert attempt.detail is None
    assert attempt.mapping is not None
    assert attempt.mapping.project_key == "hmcts.cath"
    assert attempt.mapping.repository == "cath-service"
    assert attempt.mapping.method is SonarResolution.ANALYSIS_REVISION
    assert attempt.mapping.revision == "ce34e614"
    assert attempt.mapping.analysis_at == datetime(2026, 8, 27, 9, 49, 35, tzinfo=UTC)
    assert sonar_get.call_args.kwargs["params"] == {"project": "hmcts.cath", "ps": 5}
    assert github_get.call_args.args[0] == "https://api.github.com/search/commits"
    assert github_get.call_args.kwargs["params"] == {"q": "org:hmcts hash:ce34e614", "per_page": 1}
    pause.assert_not_called()


def test_sonar_to_github_falls_back_to_an_older_analysis() -> None:
    """Walk past a pull-request commit that no longer exists to the main-branch analysis under it."""
    sonar, _ = sonar_reading(
        analyses_payload(("2026-08-27T09:49:35+0000", "f04cef11"), ("2026-08-24T09:24:56+0000", "0a3a1e11")),
    )
    github, github_get = github_reading(
        github_answer(commit_search_payload(None)),
        github_answer(commit_search_payload("hmcts/sscs-submit-your-appeal")),
    )
    pacer, pause = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "SSCSSYAF", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.RESOLVED
    assert attempt.analyses_tried == 2
    assert attempt.mapping is not None
    assert attempt.mapping.repository == "sscs-submit-your-appeal"
    assert attempt.mapping.revision == "0a3a1e11"
    assert attempt.mapping.analysis_at == datetime(2026, 8, 24, 9, 24, 56, tzinfo=UTC)
    assert [call.kwargs["params"]["q"] for call in github_get.call_args_list] == [
        "org:hmcts hash:f04cef11",
        "org:hmcts hash:0a3a1e11",
    ]
    # The second search waited out the interval rather than reaching for the limit.
    assert [call.args for call in pause.call_args_list] == [(SEARCH_INTERVAL_SECONDS,)]


def test_sonar_to_github_searches_a_repeated_revision_once() -> None:
    """Spend one search on a project re-analysed several times on an unchanged branch."""
    sonar, _ = sonar_reading(
        analyses_payload(("2026-08-27T09:49:35+0000", "5a4e1111"), ("2026-08-26T09:49:35+0000", "5a4e1111")),
    )
    github, github_get = github_reading(github_answer(commit_search_payload(None)))
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.UNKNOWN_COMMIT
    assert attempt.analyses_tried == 1
    github_get.assert_called_once()


def test_sonar_to_github_reports_a_project_that_was_never_analysed() -> None:
    """Answer a never-analysed project without spending a search, because there is nothing to search."""
    sonar, _ = sonar_reading({"analyses": []})
    github, github_get = github_reading()
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "never-analysed", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.NO_ANALYSIS
    assert attempt.outcome.is_observation
    assert attempt.mapping is None
    assert attempt.analyses_tried == 0
    assert attempt.detail is not None
    assert "no analysis" in attempt.detail
    github_get.assert_not_called()


def test_sonar_to_github_reports_analyses_that_name_no_commit() -> None:
    """Distinguish a project whose analyses record no revision from one never analysed at all."""
    sonar, _ = sonar_reading(
        analyses_payload(("2026-08-27T09:49:35+0000", None), ("2026-08-24T09:24:56+0000", None)),
    )
    github, github_get = github_reading()
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.NO_REVISION
    assert attempt.outcome.is_observation
    assert attempt.analyses_tried == 0
    assert attempt.detail == "none of the 2 most recent analyses names the commit it ran against"
    github_get.assert_not_called()


def test_sonar_to_github_reports_a_revision_no_commit_matches() -> None:
    """Record that the organisation holds none of the analysed commits, having tried each of them."""
    sonar, _ = sonar_reading(
        analyses_payload(("2026-08-27T09:49:35+0000", "9017e11"), ("2026-08-24T09:24:56+0000", "a150907")),
    )
    github, _ = github_reading(
        github_answer(commit_search_payload(None)),
        github_answer(commit_search_payload(None)),
    )
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.UNKNOWN_COMMIT
    assert attempt.outcome.is_observation
    assert attempt.mapping is None
    assert attempt.analyses_tried == 2
    assert attempt.detail == "no commit in hmcts matches any of the 2 most recently analysed revisions"


def test_sonar_to_github_refuses_a_repository_outside_the_organisation() -> None:
    """Never attribute a project to a repository the configured organisation does not own."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, _ = github_reading(github_answer(commit_search_payload("ministryofjustice/other-service")))
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.OUTSIDE_ORGANIZATION
    assert attempt.outcome.is_observation
    assert attempt.mapping is None
    assert attempt.detail is not None
    assert "ministryofjustice/other-service" in attempt.detail
    assert "outside hmcts" in attempt.detail


def test_sonar_to_github_accepts_an_organisation_named_in_another_case() -> None:
    """Match the owner the way GitHub matches it, which is without regard to case."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, _ = github_reading(github_answer(commit_search_payload("HMCTS/cath-service")))
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.RESOLVED
    assert attempt.mapping is not None
    assert attempt.mapping.repository == "cath-service"


def test_sonar_to_github_reports_a_failed_search_as_a_failure() -> None:
    """Classify a refused search as this run's failure, never as a project with no repository."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, _ = github_reading(github_answer({"message": "Bad credentials"}, 401))
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.FAILED
    assert not attempt.outcome.is_observation
    assert attempt.mapping is None
    assert attempt.analyses_tried == 1
    assert attempt.detail == "GitHub authentication failed"


def test_sonar_to_github_reports_an_unreadable_search_response() -> None:
    """Classify a search body this build cannot read as a failure rather than as no match."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, _ = github_reading(github_answer({"items": [{"sha": "ce34e614", "repository": {}}]}))
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.FAILED
    assert attempt.detail == "GitHub returned an invalid commit search response"


def test_sonar_to_github_reports_a_failed_sonarcloud_read() -> None:
    """Fail the project when its analyses could not be read, without spending a search on it."""
    session = Session()
    session.get = MagicMock(return_value=answer({}, 503))  # type: ignore[method-assign]
    sonar = SonarClient(session, {})
    github, github_get = github_reading()
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.FAILED
    assert attempt.analyses_tried == 0
    assert attempt.detail == "SonarCloud returned HTTP 503"
    github_get.assert_not_called()


def test_sonar_to_github_pauses_a_rate_limited_search_before_giving_up() -> None:
    """Wait out a 403 that names a rate limit rather than reading it as a refusal."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    limited = github_answer({"message": "API rate limit exceeded"}, 403, "You have exceeded a secondary rate limit")
    session = MagicMock()
    session.headers = {}
    session.get.side_effect = [limited, limited, github_answer(commit_search_payload("hmcts/cath-service"))]
    retry = MagicMock()
    github = GitHubClient("secret", session, pause=retry)
    pacer, _ = resolving()

    attempt = sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert attempt.outcome is SonarMappingOutcome.RESOLVED
    assert [call.args for call in retry.call_args_list] == [(60,), (60,)]


def test_sonar_to_github_reraises_a_rate_limit_that_never_cleared() -> None:
    """Stop the run rather than writing this run's exhaustion into the project's own record."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    limited = github_answer({"message": "API rate limit exceeded"}, 403, "You have exceeded a secondary rate limit")
    session = MagicMock()
    session.headers = {}
    session.get.side_effect = [limited, limited, limited]
    github = GitHubClient("secret", session, pause=MagicMock())
    pacer, _ = resolving()

    with pytest.raises(GitHubError, match="rate limit exceeded") as captured:
        sonar_to_github(sonar, github, "hmcts", "hmcts.cath", pacer=pacer)

    assert captured.value.reason is AvailabilityReason.RATE_LIMITED


def test_sonar_to_github_stops_at_the_attempt_cap() -> None:
    """Read only as many analyses as may be searched for, because every one of them costs the limit."""
    sonar, sonar_get = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "one")))
    github, _ = github_reading(github_answer(commit_search_payload(None)))
    pacer, _ = resolving()

    sonar_to_github(sonar, github, "hmcts", "hmcts.cath", attempts=2, pacer=pacer)

    assert sonar_get.call_args.kwargs["params"]["ps"] == 2


def test_confirms_candidate_accepts_a_repository_holding_the_commit() -> None:
    """Confirm a proposed mapping with one core-quota call rather than a rate-limited search."""
    github, github_get = github_reading(github_answer({"sha": "ce34e614"}))

    assert confirms_candidate(github, "hmcts", "cath-service", "ce34e614") is True
    assert github_get.call_args.args[0] == "https://api.github.com/repos/hmcts/cath-service/commits/ce34e614"


@pytest.mark.parametrize("status_code", [404, 422])
def test_confirms_candidate_refutes_a_repository_without_the_commit(status_code: int) -> None:
    """Read GitHub's answer that the repository does not hold the commit as the refutation it is."""
    github, _ = github_reading(github_answer({"message": "No commit found for SHA"}, status_code))

    assert confirms_candidate(github, "hmcts", "em-icp-api", "ce34e614") is False


def test_confirms_candidate_reraises_a_refusal_it_cannot_read_as_an_answer() -> None:
    """Never record "nobody may look" as "the repository does not hold it"."""
    github, _ = github_reading(github_answer({"message": "Bad credentials"}, 401))

    with pytest.raises(GitHubError, match="GitHub authentication failed"):
        confirms_candidate(github, "hmcts", "cath-service", "ce34e614")


def test_client_reads_the_public_sonarcloud_host() -> None:
    """Address SonarCloud itself, as `GitHubClient` addresses GitHub: one host, stated once."""
    session = Session()
    client = SonarClient(session, {})
    with patch.object(session, "get", return_value=answer({"analyses": []})) as get:
        client.project_analyses("hmcts.cath", 1)

    assert get.call_args.args[0] == "https://sonarcloud.io/api/project_analyses/search"


ANALYSED_AT = datetime(2026, 8, 27, 9, 49, 35, tzinfo=UTC)
RESOLVED_AT = datetime(2026, 8, 27, 10, 15, 0, tzinfo=UTC)

DECLARATION = """
# Analysed by SonarCloud
sonar.projectKey=hmcts.cath
sonar.organization: hmcts
sonar.sources src
"""


@dataclass(frozen=True)
class FakeMap:
    """Answer both directions of the stored map from rows a test wrote by hand.

    The ladder is what these tests are about, so the map is stated rather than built: a database
    populated by `map-sonar` would put a run of the resolver between the test and its own fixture.
    """

    sonar_organization: str = "hmcts"
    projects: dict[str, StoredSonarMapping] = field(default_factory=dict)
    repositories: dict[str, SonarRepositoryProject] = field(default_factory=dict)

    def project(self, project_key: str) -> StoredSonarMapping | None:
        """Return the row the test filed under one project key."""
        return self.projects.get(project_key)

    def repository(self, repository: str) -> SonarRepositoryProject | None:
        """Return the claim the test filed under one repository name."""
        return self.repositories.get(repository)


def resolved_row(
    project_key: str,
    repository: str,
    revision: str = "ce34e614",
    analysis_at: datetime = ANALYSED_AT,
) -> StoredSonarMapping:
    """Build one resolved row of the stored `sonar → github` map."""
    return StoredSonarMapping(
        project_key=project_key,
        resolved_at=RESOLVED_AT,
        mapping=SonarProjectMapping(
            project_key=project_key,
            repository=repository,
            method=SonarResolution.ANALYSIS_REVISION,
            analysis_at=analysis_at,
            revision=revision,
        ),
    )


def claimed_by(project_key: str, repository: str, candidates: int = 1) -> SonarRepositoryProject:
    """Build the map's answer for one repository, with the number of projects that claimed it."""
    return SonarRepositoryProject(
        mapping=SonarProjectMapping(
            project_key=project_key,
            repository=repository,
            method=SonarResolution.ANALYSIS_REVISION,
            analysis_at=ANALYSED_AT,
            revision="ce34e614",
        ),
        candidates=candidates,
    )


def test_parse_properties_reads_the_three_separators_the_format_allows() -> None:
    """Split at `=`, `:` or whitespace, whichever comes first, so a value may hold the others."""
    properties = parse_properties(
        "sonar.projectKey=uk.gov.hmcts.reform:darts-gateway\nsonar.organization: hmcts\nsonar.sources src\n",
    )

    assert properties == {
        "sonar.projectKey": "uk.gov.hmcts.reform:darts-gateway",
        "sonar.organization": "hmcts",
        "sonar.sources": "src",
    }


def test_parse_properties_ignores_comments_and_blank_lines() -> None:
    """Recognise a comment only at the start of a line, as the properties format specifies."""
    properties = parse_properties("# sonar.projectKey=commented\n\n! also a comment\nsonar.projectKey=real#1\n")

    assert properties == {"sonar.projectKey": "real#1"}


def test_parse_properties_lets_the_last_definition_win() -> None:
    """Take the later of two definitions of one key, as the format specifies."""
    assert parse_properties("sonar.projectKey=first\nsonar.projectKey=second\n") == {"sonar.projectKey": "second"}


def test_declared_project_reads_a_declaration() -> None:
    """Read the two properties the resolution ladder asks about out of a real declaration."""
    assert declared_project(DECLARATION) == SonarDeclaration(project_key="hmcts.cath", organization="hmcts")


def test_declared_project_distinguishes_no_file_from_no_key() -> None:
    """Keep a repository with no properties file apart from one that leaves the key to the build."""
    assert declared_project(None) is None
    assert declared_project("sonar.sources=src\n") == SonarDeclaration(project_key=None, organization=None)


def test_github_to_sonar_prefers_the_configured_override() -> None:
    """Let a human's instruction beat every observation, and spend nothing confirming it."""
    sonar, sonar_get = sonar_reading({"analyses": []})
    github, github_get = github_reading()
    project_map = FakeMap(
        projects={"em-icp-api": resolved_row("em-icp-api", "em-icp-api")},
        repositories={"rpx-xui-icp-api": claimed_by("rpx-xui-icp-api", "rpx-xui-icp-api")},
    )

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "rpx-xui-icp-api",
        project_map,
        override="hmcts.icp",
        declaration=SonarDeclaration(project_key="em-icp-api"),
    )

    assert attribution.mapping is not None
    assert attribution.mapping.project_key == "hmcts.icp"
    assert attribution.mapping.repository == "rpx-xui-icp-api"
    assert attribution.mapping.method is SonarResolution.CONFIGURED
    assert attribution.detail is None
    assert attribution.reason is None
    sonar_get.assert_not_called()
    github_get.assert_not_called()


def test_github_to_sonar_confirms_a_declaration_against_the_map() -> None:
    """Believe a declared key the map already attributes to this repository, for no calls at all."""
    sonar, sonar_get = sonar_reading({"analyses": []})
    github, github_get = github_reading()
    project_map = FakeMap(projects={"hmcts.cath": resolved_row("hmcts.cath", "CaTH-Service")})

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        project_map,
        declaration=declared_project(DECLARATION),
    )

    assert attribution.mapping is not None
    assert attribution.mapping.project_key == "hmcts.cath"
    # The stored spelling wins, because it is the name GitHub itself reported.
    assert attribution.mapping.repository == "CaTH-Service"
    assert attribution.mapping.method is SonarResolution.DECLARED_CONFIRMED_BY_MAP
    assert attribution.mapping.revision == "ce34e614"
    assert attribution.mapping.analysis_at == ANALYSED_AT
    sonar_get.assert_not_called()
    github_get.assert_not_called()


def test_github_to_sonar_falls_through_a_declaration_the_map_refutes() -> None:
    """Drop the template key 14 repositories declare, and read the map's own answer instead."""
    sonar, sonar_get = sonar_reading({"analyses": []})
    github, github_get = github_reading()
    project_map = FakeMap(
        projects={"rpe-expressjs-template": resolved_row("rpe-expressjs-template", "rpe-expressjs-template")},
        repositories={"pcs-frontend": claimed_by("pcs-frontend", "pcs-frontend")},
    )

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "pcs-frontend",
        project_map,
        declaration=SonarDeclaration(project_key="rpe-expressjs-template"),
    )

    assert attribution.mapping is not None
    assert attribution.mapping.project_key == "pcs-frontend"
    assert attribution.mapping.method is SonarResolution.STORED_MAP
    assert attribution.mapping.revision == "ce34e614"
    assert attribution.reason is None
    # A refutation the map could make costs nothing to make.
    sonar_get.assert_not_called()
    github_get.assert_not_called()


def test_github_to_sonar_confirms_an_unmapped_declaration_by_its_analysed_commit() -> None:
    """Confirm a project nobody has mapped with the cheap call, never with the rate-limited search."""
    sonar, sonar_get = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, github_get = github_reading(github_answer({"sha": "ce34e614"}))

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        FakeMap(),
        declaration=declared_project(DECLARATION),
    )

    assert attribution.mapping is not None
    assert attribution.mapping.project_key == "hmcts.cath"
    assert attribution.mapping.repository == "cath-service"
    assert attribution.mapping.method is SonarResolution.DECLARED_CONFIRMED_BY_COMMIT
    assert attribution.mapping.revision == "ce34e614"
    assert attribution.mapping.analysis_at == ANALYSED_AT
    assert sonar_get.call_args.kwargs["params"] == {"project": "hmcts.cath", "ps": 1}
    assert github_get.call_args.args[0] == "https://api.github.com/repos/hmcts/cath-service/commits/ce34e614"


def test_github_to_sonar_confirms_a_declaration_the_map_left_unresolved() -> None:
    """Test a declared key the map stored a reason for, because a reason is not an attribution."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, github_get = github_reading(github_answer({"sha": "ce34e614"}))
    unresolved = StoredSonarMapping(project_key="hmcts.cath", resolved_at=RESOLVED_AT, detail="no analysis")
    project_map = FakeMap(projects={"hmcts.cath": unresolved})

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        project_map,
        declaration=declared_project(DECLARATION),
    )

    assert attribution.mapping is not None
    assert attribution.mapping.method is SonarResolution.DECLARED_CONFIRMED_BY_COMMIT
    github_get.assert_called_once()


def test_github_to_sonar_reports_a_declaration_the_commit_check_refutes() -> None:
    """Refuse a declared key whose project analyses a commit this repository does not hold."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, _ = github_reading(github_answer({"message": "No commit found for SHA"}, 422))

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "rpx-xui-icp-api",
        FakeMap(),
        declaration=SonarDeclaration(project_key="em-icp-api"),
    )

    assert attribution.mapping is None
    assert attribution.detail is not None
    assert "rpx-xui-icp-api does not hold commit ce34e614, the latest analysis of em-icp-api" in attribution.detail
    assert "no SonarCloud project in hmcts is mapped to this repository" in attribution.detail
    assert attribution.reason is None


def test_github_to_sonar_reports_a_declared_project_with_no_analysed_commit() -> None:
    """Leave a never-analysed declared project unconfirmed, which is not the same as refuted."""
    sonar, _ = sonar_reading({"analyses": []})
    github, github_get = github_reading()

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        FakeMap(),
        declaration=declared_project(DECLARATION),
    )

    assert attribution.mapping is None
    assert attribution.detail is not None
    assert "the declared project hmcts.cath has no analysed commit to confirm it by" in attribution.detail
    assert attribution.reason is None
    github_get.assert_not_called()


def test_github_to_sonar_ignores_a_declaration_for_another_organisation() -> None:
    """Never attribute a project from somebody else's SonarCloud organisation to this repository."""
    sonar, sonar_get = sonar_reading({"analyses": []})
    github, _ = github_reading()
    project_map = FakeMap(repositories={"cath-service": claimed_by("hmcts.cath", "cath-service")})

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        project_map,
        declaration=SonarDeclaration(project_key="cath", organization="ministryofjustice"),
    )

    assert attribution.mapping is not None
    assert attribution.mapping.project_key == "hmcts.cath"
    assert attribution.mapping.method is SonarResolution.STORED_MAP
    sonar_get.assert_not_called()


def test_github_to_sonar_reports_a_properties_file_declaring_no_key() -> None:
    """Say that the file is there and names no project, which is a real and different case."""
    sonar, _ = sonar_reading({"analyses": []})
    github, _ = github_reading()

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        FakeMap(),
        declaration=declared_project("sonar.sources=src\n"),
    )

    assert attribution.mapping is None
    assert attribution.detail == (
        "sonar-project.properties declares no sonar.projectKey; "
        "no SonarCloud project in hmcts is mapped to this repository"
    )


def test_github_to_sonar_reads_the_map_for_a_repository_that_declares_nothing() -> None:
    """Answer from the map alone, which is how every Gradle and Maven repository is answered."""
    sonar, sonar_get = sonar_reading({"analyses": []})
    github, github_get = github_reading()
    project_map = FakeMap(repositories={"cath-service": claimed_by("hmcts.cath", "cath-service")})

    attribution = github_to_sonar(sonar, github, "hmcts", "cath-service", project_map)

    assert attribution.mapping is not None
    assert attribution.mapping.project_key == "hmcts.cath"
    assert attribution.mapping.method is SonarResolution.STORED_MAP
    assert attribution.mapping.analysis_at == ANALYSED_AT
    assert attribution.mapping.revision == "ce34e614"
    sonar_get.assert_not_called()
    github_get.assert_not_called()


def test_github_to_sonar_reports_a_repository_no_project_analyses() -> None:
    """Report the reason rather than an empty block, because most repositories have no project."""
    sonar, _ = sonar_reading({"analyses": []})
    github, _ = github_reading()

    attribution = github_to_sonar(sonar, github, "hmcts", "unwatched-service", FakeMap())

    assert attribution.mapping is None
    assert attribution.detail == "no SonarCloud project in hmcts is mapped to this repository"
    assert attribution.reason is None


def test_github_to_sonar_names_the_project_a_duplicate_pair_resolved_to(caplog: pytest.LogCaptureFixture) -> None:
    """Read the live project of a duplicate pair, and say a choice was made between two."""
    sonar, _ = sonar_reading({"analyses": []})
    github, _ = github_reading()
    project_map = FakeMap(repositories={"rpx-xui-webapp": claimed_by("rpx-xui-webapp_2", "rpx-xui-webapp", 2)})

    with caplog.at_level(logging.INFO):
        attribution = github_to_sonar(sonar, github, "hmcts", "rpx-xui-webapp", project_map)

    assert attribution.mapping is not None
    assert attribution.mapping.project_key == "rpx-xui-webapp_2"
    assert "claimed by 2 SonarCloud projects" in caplog.text


def test_github_to_sonar_reports_a_failed_sonarcloud_confirmation() -> None:
    """Classify a SonarCloud call that failed as a failure, never as a repository with no project."""
    session = Session()
    session.get = MagicMock(return_value=answer({}, 503))  # type: ignore[method-assign]
    sonar = SonarClient(session, {})
    github, github_get = github_reading()

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        FakeMap(),
        declaration=declared_project(DECLARATION),
    )

    assert attribution.mapping is None
    assert attribution.reason is AvailabilityReason.COLLECTION_FAILED
    assert attribution.detail is not None
    assert "SonarCloud returned HTTP 503" in attribution.detail
    github_get.assert_not_called()


def test_github_to_sonar_refutes_a_declared_key_sonarcloud_does_not_list() -> None:
    """Read a `404` on a declared project as a stale properties file, never as a collection failure.

    123 of the organisation's 240 declarations name a project SonarCloud does not list. Carrying that
    out as a reason would record half the organisation's properties files as failed calls and exit
    `3` for them, which is the exact signal the three-valued status exists to carry.
    """
    session = Session()
    session.get = MagicMock(return_value=answer({}, 404))  # type: ignore[method-assign]
    sonar = SonarClient(session, {})
    github, github_get = github_reading()

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        FakeMap(),
        declaration=declared_project(DECLARATION),
    )

    assert attribution.mapping is None
    assert attribution.reason is None
    assert attribution.detail is not None
    assert "SonarCloud lists no project hmcts.cath" in attribution.detail
    assert "no SonarCloud project in hmcts is mapped to this repository" in attribution.detail
    github_get.assert_not_called()


def test_github_to_sonar_reports_a_failed_commit_confirmation() -> None:
    """Never read a refusal to look as the repository not holding the commit."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, _ = github_reading(github_answer({"message": "Bad credentials"}, 401))

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        FakeMap(),
        declaration=declared_project(DECLARATION),
    )

    assert attribution.mapping is None
    assert attribution.reason is AvailabilityReason.AUTHENTICATION_FAILED
    assert attribution.detail is not None
    assert "GitHub authentication failed" in attribution.detail


def test_github_to_sonar_keeps_the_maps_answer_after_a_failed_confirmation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Never discard a mapping an earlier run paid for because this run could not confirm a declaration."""
    sonar, _ = sonar_reading(analyses_payload(("2026-08-27T09:49:35+0000", "ce34e614")))
    github, _ = github_reading(github_answer({"message": "Bad credentials"}, 401))
    project_map = FakeMap(repositories={"cath-service": claimed_by("hmcts.cath", "cath-service")})

    with caplog.at_level(logging.WARNING):
        attribution = github_to_sonar(
            sonar,
            github,
            "hmcts",
            "cath-service",
            project_map,
            declaration=SonarDeclaration(project_key="hmcts.cath-declared"),
        )

    assert attribution.mapping is not None
    assert attribution.mapping.method is SonarResolution.STORED_MAP
    assert attribution.reason is None
    assert "reading the stored map after a failed confirmation" in caplog.text


def test_stored_project_map_answers_both_directions_from_the_database(tmp_path: Path) -> None:
    """Read the map `map-sonar` wrote, in the file it wrote it to, in both directions."""
    database = tmp_path / "metrics-observations.sqlite3"
    record_sonar_mapping(database, "hmcts", resolved_row("hmcts.cath", "cath-service"))
    project_map = StoredProjectMap(database, "hmcts")

    stored = project_map.project("hmcts.cath")
    claimed = project_map.repository("cath-service")

    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"
    assert claimed is not None
    assert claimed.mapping.project_key == "hmcts.cath"
    assert project_map.project("never-resolved") is None
    assert project_map.repository("unwatched-service") is None


def test_github_to_sonar_resolves_through_the_stored_map(tmp_path: Path) -> None:
    """Resolve one repository end to end over the real map, as a collection will."""
    database = tmp_path / "metrics-observations.sqlite3"
    record_sonar_mapping(database, "hmcts", resolved_row("hmcts.cath", "cath-service"))
    sonar, sonar_get = sonar_reading({"analyses": []})
    github, github_get = github_reading()

    attribution = github_to_sonar(
        sonar,
        github,
        "hmcts",
        "cath-service",
        StoredProjectMap(database, "hmcts"),
        declaration=declared_project(DECLARATION),
    )

    assert attribution.mapping is not None
    assert attribution.mapping.project_key == "hmcts.cath"
    assert attribution.mapping.method is SonarResolution.DECLARED_CONFIRMED_BY_MAP
    sonar_get.assert_not_called()
    github_get.assert_not_called()
