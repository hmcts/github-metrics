"""Test reading the published list of repositories approved to deploy to production."""

import logging
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from requests import RequestException

from metrics.production import (
    PRODUCTION_ENVIRONMENT,
    PRODUCTION_LIST_URL,
    REQUEST_TIMEOUT,
    ProductionListError,
    entry_repository,
    fetch_production_repositories,
    parse_production_repositories,
    parse_repository_url,
)

# The shape of the live document, read on 2026-09-04: one `prod:` key over a sequence of mappings,
# each naming a repository by its clone URL. Kept short, but every awkwardness the real 250-entry
# file holds is represented — the mixed-case `HMCTS` owner among lowercase siblings, and an entry
# written without the `.git` suffix.
APPROVALS_DOCUMENT = """
prod:
  - repo: https://github.com/hmcts/am-shared-infrastructure.git
  - repo: https://github.com/hmcts/bar-api.git
  - repo: https://github.com/HMCTS/adoption-shared-infrastructure.git
  - repo: https://github.com/hmcts/ccd-data-store-api
demo:
  - repo: https://github.com/hmcts/not-a-production-service.git
"""

FETCH_URL = "https://raw.example.invalid/environment-approvals.yml"


def answer(body: str, status_code: int = HTTPStatus.OK) -> MagicMock:
    """Build one faked raw-content response carrying a document body."""
    return MagicMock(status_code=status_code, text=body)


def test_the_list_url_names_the_pipeline_document() -> None:
    """Guard the one constant a wrong path silently empties every badge for."""
    assert PRODUCTION_LIST_URL.startswith("https://raw.githubusercontent.com/hmcts/cnp-jenkins-config/")
    assert PRODUCTION_LIST_URL.endswith("/environment-approvals.yml")
    assert PRODUCTION_ENVIRONMENT == "prod"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/hmcts/bar-api.git", ("hmcts", "bar-api")),
        ("https://github.com/hmcts/bar-api", ("hmcts", "bar-api")),
        ("https://github.com/HMCTS/Adoption-Shared-Infrastructure.git", ("hmcts", "adoption-shared-infrastructure")),
        ("https://github.com/hmcts/bar-api/", ("hmcts", "bar-api")),
    ],
)
def test_parse_repository_url_reads_a_casefolded_pair(url: str, expected: tuple[str, str]) -> None:
    """Read an owner and a name, folded, with any `.git` suffix dropped."""
    assert parse_repository_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/hmcts",
        "https://github.com/hmcts/bar-api/tree/master",
        "https://github.com/hmcts/.git",
        "",
        "not a url at all",
        # No host, so the whole text is the path: an `scp`-style remote splits into two parts and
        # would read as the organisation `git@github.com:hmcts` — a pair matching nothing, badging
        # nothing, and logging nothing, because a pair came back.
        "git@github.com:hmcts/bar-api.git",
        "hmcts/bar-api",
    ],
)
def test_parse_repository_url_refuses_what_it_cannot_read(url: str) -> None:
    """Return `None` for anything that is not exactly a host, an owner and a name."""
    assert parse_repository_url(url) is None


def test_parse_production_repositories_reads_the_live_document_shape() -> None:
    """Read the `prod:` sequence as pairs, ignoring every other environment."""
    repositories = parse_production_repositories(APPROVALS_DOCUMENT)

    assert repositories == frozenset(
        {
            ("hmcts", "am-shared-infrastructure"),
            ("hmcts", "bar-api"),
            ("hmcts", "adoption-shared-infrastructure"),
            ("hmcts", "ccd-data-store-api"),
        },
    )
    assert ("hmcts", "not-a-production-service") not in repositories


def test_parse_production_repositories_folds_a_mixed_case_owner_onto_its_siblings() -> None:
    """Fold `HMCTS` onto `hmcts`, which is the one entry a case-sensitive read would drop."""
    repositories = parse_production_repositories(APPROVALS_DOCUMENT)
    organizations = {organization for organization, _ in repositories}

    assert organizations == {"hmcts"}


def test_parse_production_repositories_keeps_the_neighbours_of_an_unreadable_entry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cost one unreadable entry that entry alone: a malformed line must not discard the good ones."""
    document = """
prod:
  - repo: https://github.com/hmcts/bar-api.git
  - a bare string, not a mapping
  - name: https://github.com/hmcts/wrong-key.git
  - repo: 42
  - repo: https://github.com/hmcts
  - repo: https://github.com/hmcts/blob-router-service.git
"""

    with caplog.at_level(logging.DEBUG):
        repositories = parse_production_repositories(document)

    assert repositories == frozenset({("hmcts", "bar-api"), ("hmcts", "blob-router-service")})
    assert caplog.text.count("Ignoring an unreadable production list entry") == 4


@pytest.mark.parametrize(
    "entry",
    [
        "a bare string, not a mapping",
        {"name": "https://github.com/hmcts/bar-api.git"},
        {"repo": 42},
        {"repo": "https://github.com/hmcts"},
    ],
)
def test_entry_repository_refuses_an_entry_it_cannot_read(entry: object) -> None:
    """Return `None` for an entry that is not a mapping naming a repository URL."""
    assert entry_repository(entry) is None


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("demo:\n  - repo: https://github.com/hmcts/bar-api.git\n", id="no-prod-key"),
        pytest.param("prod: not-a-sequence\n", id="prod-is-not-a-sequence"),
        pytest.param("- prod\n- demo\n", id="not-a-mapping"),
        pytest.param("", id="empty"),
        pytest.param("prod: [unclosed\n", id="unparseable"),
        # The shape change one level down, and the one that arrives without warning: the day the
        # other team renames `repo:`, every entry fails on its own and a parse that answered with
        # the empty set would tell the whole estate it deploys nothing to production.
        pytest.param(
            "prod:\n  - repository: https://github.com/hmcts/bar-api.git\n"
            "  - repository: https://github.com/hmcts/ccd-data-store-api.git\n",
            id="no-entry-readable",
        ),
    ],
)
def test_parse_production_repositories_raises_when_the_document_changed_shape(document: str) -> None:
    """Raise rather than return an empty set: a reorganised file is not an empty approvals list."""
    with pytest.raises(ProductionListError):
        parse_production_repositories(document)


def test_parse_production_repositories_reads_an_empty_sequence_as_an_empty_list() -> None:
    """Read `prod: []` as the empty set it states, which is NOT the same as a document nobody can read.

    The one case where nothing comes back and nothing is wrong: the key is there, the sequence is
    there, and it says no repository is approved. Raising here would refuse a document for saying
    something, rather than for having stopped saying anything.
    """
    assert parse_production_repositories("prod: []\ndemo:\n  - repo: https://github.com/hmcts/bar-api.git\n") == (
        frozenset()
    )


def test_fetch_production_repositories_reads_the_document_without_a_credential(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fetch and parse the list with a timeout, sending no `Authorization` header at all."""
    with (
        caplog.at_level(logging.WARNING),
        patch("metrics.production.requests.get", return_value=answer(APPROVALS_DOCUMENT)) as get,
    ):
        repositories = fetch_production_repositories(FETCH_URL)

    assert repositories is not None
    assert ("hmcts", "bar-api") in repositories
    assert caplog.records == []
    get.assert_called_once_with(FETCH_URL, timeout=REQUEST_TIMEOUT)
    headers = get.call_args.kwargs.get("headers") or {}
    assert "Authorization" not in headers
    assert "authorization" not in {key.casefold() for key in headers}


def test_fetch_production_repositories_reports_a_network_failure_as_unread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return `None`, not an empty set, when the host cannot be reached."""
    with (
        caplog.at_level(logging.WARNING),
        patch("metrics.production.requests.get", side_effect=RequestException("connection reset")),
    ):
        assert fetch_production_repositories(FETCH_URL) is None

    assert "Could not fetch the production list" in caplog.text
    assert "connection reset" in caplog.text


def test_fetch_production_repositories_reports_a_non_200_as_unread(caplog: pytest.LogCaptureFixture) -> None:
    """Return `None` for a response that is not an answer, naming the status in the log."""
    response = answer("<html>bad gateway</html>", HTTPStatus.BAD_GATEWAY)

    with caplog.at_level(logging.WARNING), patch("metrics.production.requests.get", return_value=response):
        assert fetch_production_repositories(FETCH_URL) is None

    assert "returned HTTP 502" in caplog.text


def test_fetch_production_repositories_reports_an_unreadable_body_as_unread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return `None` when the document fetched is not an approvals list."""
    with (
        caplog.at_level(logging.WARNING),
        patch("metrics.production.requests.get", return_value=answer("prod: [unclosed\n")),
    ):
        assert fetch_production_repositories(FETCH_URL) is None

    assert "Could not read the production list" in caplog.text
