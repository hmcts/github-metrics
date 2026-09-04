"""Read the published list of repositories approved to deploy to production.

HMCTS states that approval in ONE organisation-wide document, `environment-approvals.yml` in
`cnp-jenkins-config`, which the deployment pipeline reads to decide whether a repository may be
promoted to an environment at all. The `prod:` sequence in it is therefore the only public statement
of which repositories are production services, and it is what puts a `Production` badge on a row.

THE FETCH CARRIES NO CREDENTIAL AND MUST NOT BE GIVEN ONE. The file is served by a public raw
content host, so a token would buy nothing and sending one to a host outside the GitHub API is a
leak with no benefit. Nothing in this module takes a session, a token or an environment.

Every failure here is reported as `None` — NOT as an empty set. "The list could not be read" and "no
repository is approved for production" are different answers, and only one of them is ever true of
this organisation; collapsing the first into the second would quietly clear 250 badges on the day the
content host returned a 502. The same rule runs through the parsing: a document that is not a mapping
or that holds no `prod:` key raises, because the file changing shape must not read as an empty list,
while ONE entry nobody can parse costs that entry alone and leaves its neighbours standing. A
sequence whose EVERY entry is unreadable raises too — that is the shape change again, one level
further down, and it must not read as an estate that deploys nothing.
"""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from urllib.parse import urlparse

import requests
import yaml
from requests import RequestException

# The raw URL of the document, pinned to `master` because that is the branch the pipeline itself
# reads: a tag or a SHA would freeze the answer at the day this was written.
PRODUCTION_LIST_URL = (
    "https://raw.githubusercontent.com/hmcts/cnp-jenkins-config/refs/heads/master/environment-approvals.yml"
)
# The one environment this project asks about. The document names others, and every one of them is
# ignored: a repository approved for `demo` or `ithc` is not a production service.
PRODUCTION_ENVIRONMENT = "prod"
# Matching `github.py` and `sonar.py`, so one host being slow is bounded the same way everywhere.
REQUEST_TIMEOUT = 30
# The key each entry in the sequence states its repository under.
REPOSITORY_KEY = "repo"


class ProductionListError(ValueError):
    """Report a production list this build cannot read as a list of repositories."""


def parse_repository_url(url: str) -> tuple[str, str] | None:
    """Read one repository URL as a casefolded `(organization, repository)` pair.

    CASEFOLDING IS NOT OPTIONAL. The live document holds
    `https://github.com/HMCTS/adoption-shared-infrastructure.git` among 200-odd otherwise lowercase
    `hmcts` entries, so a case-sensitive comparison against a configured organisation would silently
    drop that repository's badge — and GitHub owner and repository names are case-insensitive
    anyway, which makes the fold a correction rather than a convenience.

    Returns `None` for anything that does not read as exactly an owner and a name, so a URL nobody
    can interpret is skipped rather than becoming a pair that matches nothing.
    """
    parsed = urlparse(url)
    # A HOST IS REQUIRED, because without one the whole text is the path: the `scp`-style
    # `git@github.com:hmcts/bar-api.git` that a Git remote is often written as would otherwise split
    # into two parts and read as the organisation `git@github.com:hmcts` — a pair that matches
    # nothing, badges nothing, and logs nothing, because a pair was returned. Refusing it here is
    # what makes it one of the entries the caller counts as unreadable.
    if not parsed.netloc:
        return None
    # Matched rather than counted: an owner and a name, and nothing else, is the whole rule, and a
    # pattern says that without a length literal to explain.
    match [part for part in parsed.path.split("/") if part]:
        case [organization, repository]:
            name = repository.removesuffix(".git")
            return (organization.casefold(), name.casefold()) if name else None
        case _:
            return None


def entry_repository(entry: object) -> tuple[str, str] | None:
    """Read one entry of the `prod:` sequence, returning `None` for one this build cannot read."""
    if not isinstance(entry, Mapping):
        return None
    url = entry.get(REPOSITORY_KEY)
    if not isinstance(url, str):
        return None
    return parse_repository_url(url)


def parse_production_repositories(document: str) -> frozenset[tuple[str, str]]:
    """Read the `prod:` sequence of one approvals document as casefolded repository pairs.

    Every other top-level key is ignored: this asks one question of the document, and an environment
    that is not production is not an answer to it.

    Raises:
        ProductionListError: if the document does not parse, is not a mapping, holds no `prod:`
            sequence, or holds one whose every entry is unreadable. Deliberately NOT an empty set:
            those are the shapes that say the file was reorganised, and a reorganised file must not
            be reported as an organisation that deploys nothing to production.
    """
    try:
        parsed = yaml.safe_load(document)
    except yaml.YAMLError as exception:
        message = f"the production list is not a YAML document: {exception}"
        raise ProductionListError(message) from exception
    if not isinstance(parsed, Mapping):
        message = f"the production list is not a mapping but a {type(parsed).__name__}"
        raise ProductionListError(message)
    entries = parsed.get(PRODUCTION_ENVIRONMENT)
    if not isinstance(entries, list):
        message = f"the production list holds no {PRODUCTION_ENVIRONMENT!r} sequence"
        raise ProductionListError(message)

    repositories = set()
    for entry in entries:
        pair = entry_repository(entry)
        if pair is None:
            # Debug rather than warning: the document is maintained by another team for another
            # purpose, and one entry this build cannot read is not a fault in the report.
            logging.debug("Ignoring an unreadable production list entry: %r", entry)
            continue
        repositories.add(pair)
    # ONE unreadable entry costs that entry; EVERY entry unreadable is the file having changed shape,
    # and is the same fault as a missing `prod:` key read one level down. The day the other team
    # renames `repo:`, each of 250 entries fails on its own and the parse would otherwise "succeed"
    # with nothing in it — serving a confident `false` to the whole estate, which is the one answer
    # this module exists to refuse. An explicitly EMPTY sequence is not this: it is a document
    # stating that nothing is approved, and it is returned as the empty set it says.
    if entries and not repositories:
        message = f"no entry of the {PRODUCTION_ENVIRONMENT!r} sequence names a repository this build can read"
        raise ProductionListError(message)
    return frozenset(repositories)


def fetch_production_repositories(url: str) -> frozenset[tuple[str, str]] | None:
    """Fetch and parse the production list, returning `None` for any failure to read it.

    One unauthenticated GET. A network failure, a response that is not a 200, and a body that does
    not parse are all the same answer to the caller — nobody could say which repositories are
    production services — and each is logged at warning level, because a list that has been failing
    all day should be visible in the service's log rather than only in the missing badges.
    """
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
    except RequestException as exception:
        logging.warning("Could not fetch the production list from %s: %s", url, exception)
        return None
    if response.status_code != HTTPStatus.OK:
        logging.warning("The production list at %s returned HTTP %s", url, response.status_code)
        return None
    try:
        return parse_production_repositories(response.text)
    except ProductionListError as exception:
        logging.warning("Could not read the production list from %s: %s", url, exception)
        return None
