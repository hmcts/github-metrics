"""Diagnose configuration and GitHub access."""

import logging

from requests import Session

from metrics.config import Configuration
from metrics.github import GitHubClient, GitHubError


def run_doctor(configuration: Configuration, token: str, session: Session) -> bool:
    """Verify access to every configured repository."""
    client = GitHubClient(token, session)
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
