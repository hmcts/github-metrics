"""Diagnose configuration and GitHub access."""

import logging

from requests import Session

from metrics.config import Configuration
from metrics.credentials import GitHubCredentials
from metrics.github import GitHubClient, GitHubError


def run_doctor(configuration: Configuration, credentials: GitHubCredentials, session: Session) -> bool:
    """Verify access to every configured repository.

    Takes the credential rather than a token string: what a run may read depends on WHICH KIND of
    credential it holds — an App installation is granted its permissions by the organisation, a
    personal access token has them intersected with a user's — and a `str` cannot express that.
    """
    client = GitHubClient(credentials, session)
    succeeded = True
    for team in configuration.teams:
        for repository in team.repositories:
            try:
                client.get_repository(configuration.organization, repository)
            except GitHubError as exception:
                message = str(exception)
            else:
                logging.info("OK     Repository accessible: %s/%s", configuration.organization, repository)
                continue
            logging.error(
                "ERROR  Repository inaccessible: %s/%s (%s)",
                configuration.organization,
                repository,
                message,
            )
            succeeded = False
    return succeeded
