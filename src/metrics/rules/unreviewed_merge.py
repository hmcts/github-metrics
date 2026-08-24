"""Find merges into the default branch made without independent human review."""

from collections import Counter
from dataclasses import dataclass
from typing import ClassVar

from metrics.analysis import Merge, change_size, eligible_reviews, size_class
from metrics.domain import (
    CachedBehaviourFacts,
    DirectCommitFact,
    Merges,
    PracticeCommitReference,
    PracticeFinding,
    PracticePullRequestReference,
    PullRequestFact,
)
from metrics.rules.base import PracticeRule


@dataclass(frozen=True)
class UnreviewedMerge(PracticeRule):
    """Find authors whose merges had no independent human review.

    A direct commit counts exactly as a pull request merged without review does. Pushing past the
    process and merging without review are the same failure, so an actor cannot show a better
    finding by taking the more egregious route. The two routes stay in separate evidence arrays
    because a reader has to be able to see which one a change took.
    """

    identifier: ClassVar[str] = "unreviewed-merge"

    def size_class(self, change: Merge) -> str:
        """Classify one merge against the configured triviality thresholds."""
        return size_class(
            change,
            self.triviality.maximum_lines,
            self.triviality.maximum_files,
        )

    def reference(self, cached: CachedBehaviourFacts, pull_request: PullRequestFact) -> PracticePullRequestReference:
        """Build one sized pull-request reference supporting a finding."""
        measured = change_size(pull_request)
        return PracticePullRequestReference(
            number=pull_request.number,
            url=f"https://github.com/{cached.organization}/{cached.repository}/pull/{pull_request.number}",
            merged_at=pull_request.merged_at,
            size_class=self.size_class(pull_request),
            changed_lines=measured[0] if measured else None,
            changed_files=measured[1] if measured else None,
        )

    def commit_reference(self, cached: CachedBehaviourFacts, commit: DirectCommitFact) -> PracticeCommitReference:
        """Build one sized direct-commit reference supporting a finding."""
        measured = change_size(commit)
        return PracticeCommitReference(
            sha=commit.sha,
            url=f"https://github.com/{cached.organization}/{cached.repository}/commit/{commit.sha}",
            committed_at=commit.committed_at,
            size_class=self.size_class(commit),
            changed_lines=measured[0] if measured else None,
            changed_files=measured[1] if measured else None,
        )

    def actor_finding(
        self,
        cached: CachedBehaviourFacts,
        actor_login: str,
        unreviewed: Merges,
        authored: int,
    ) -> PracticeFinding:
        """Build one actor-level finding against every merge that actor made."""
        commits = unreviewed.direct_commits
        occurrences = len(unreviewed.pull_requests) + len(commits)
        percentage = round(occurrences / authored * 100, 1)
        change_label = "merge" if authored == 1 else "merges"
        sizes = Counter(self.size_class(pull_request) for pull_request in unreviewed.pull_requests)
        sizes.update(self.size_class(commit) for commit in commits)
        breakdown = ", ".join(f"{count} {name}" for name, count in sorted(sizes.items()))
        bypassed = f"; {len(commits)} pushed directly to the default branch" if commits else ""
        return PracticeFinding(
            rule=self.identifier,
            severity=self.configuration.severity,
            actor_login=actor_login,
            occurrences=occurrences,
            authored_merges=authored,
            percentage=percentage,
            message=(
                f"{actor_login}: {occurrences} of {authored} {change_label} "
                f"had no independent human review ({percentage:g}%) — {breakdown}{bypassed}"
            ),
            occurrences_by_size=dict(sorted(sizes.items())),
            pull_requests=tuple(self.reference(cached, pull_request) for pull_request in unreviewed.pull_requests),
            direct_commits=tuple(self.commit_reference(cached, commit) for commit in commits) or None,
        )

    def attributable(self, cached: CachedBehaviourFacts) -> Merges:
        """Return the in-window changes this rule may attribute to a named actor."""
        excluded = {login.casefold() for login in self.configuration.excluded_logins}
        return Merges(
            pull_requests=tuple(
                pull_request
                for pull_request in cached.pull_requests
                if cached.starts_at <= pull_request.merged_at < cached.ends_at
                and pull_request.author_login is not None
                and pull_request.author_login.casefold() not in excluded
            ),
            direct_commits=tuple(
                commit
                for commit in cached.direct_commits
                if cached.starts_at <= commit.committed_at < cached.ends_at
                and commit.author_login is not None
                and commit.author_login.casefold() not in excluded
            ),
        )

    def authored_by(self, changes: Merges, actor: str) -> Merges:
        """Return the merges one comparable actor login made."""
        return Merges(
            pull_requests=tuple(
                pull_request
                for pull_request in changes.pull_requests
                if pull_request.author_login is not None and pull_request.author_login.casefold() == actor
            ),
            direct_commits=tuple(
                commit
                for commit in changes.direct_commits
                if commit.author_login is not None and commit.author_login.casefold() == actor
            ),
        )

    def findings(self, cached: CachedBehaviourFacts) -> tuple[PracticeFinding, ...]:
        """Return actor-level findings for one repository window.

        Every direct commit is unreviewed by definition, so only the pull requests are filtered:
        there was no pull request to carry a review.
        """
        attributable = self.attributable(cached)
        unreviewed = attributable.model_copy(
            update={
                "pull_requests": tuple(
                    pull_request for pull_request in attributable.pull_requests if not eligible_reviews(pull_request)
                ),
            },
        )
        actors = {
            login.casefold(): login
            for login in (
                *(pull_request.author_login for pull_request in unreviewed.pull_requests),
                *(commit.author_login for commit in unreviewed.direct_commits),
            )
            if login is not None
        }
        findings = (
            (actor, self.authored_by(unreviewed, actor), self.authored_by(attributable, actor))
            for actor in sorted(actors)
        )
        return tuple(
            self.actor_finding(
                cached,
                actors[actor],
                unreviewed_merges,
                len(authored.pull_requests) + len(authored.direct_commits),
            )
            for actor, unreviewed_merges, authored in findings
            if len(unreviewed_merges.pull_requests) + len(unreviewed_merges.direct_commits)
            >= self.configuration.minimum_occurrences
        )
