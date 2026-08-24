"""Persist immutable collection snapshots in SQLite.

TWO FILES, NOT ONE. The collection cache is disposable — deleting it and refetching is expected —
which is why it carries no migrations and why nothing irreplaceable may live in it. Alert
observations ARE irreplaceable: GitHub cannot say what was open last month, so a row lost is a hole
in the series for ever. They therefore live in a second SQLite file beside the cache, so that
"delete the cache" stays a safe operation. See architecture.md, "Storage rule".
"""

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection, Error, connect

from metrics.domain import (
    AlertFamily,
    AlertObservation,
    DirectCommitFact,
    PullRequestFact,
    ReportingWindow,
    RepositoryInventory,
    RepositoryInventoryItem,
    SourceCoverage,
    StoredRepositoryState,
)


class StorageError(RuntimeError):
    """Report a metrics storage failure."""


def observation_database(database: Path) -> Path:
    """Return the observation history file that sits beside one configured cache.

    Derived from the cache path rather than configured separately: one file is an implementation
    detail of the other's disposability, and a second configuration key would let the two be pointed
    at different directories by accident and the history be silently started again from empty.
    """
    return database.with_name(f"{database.stem}-observations{database.suffix}")


def initialize(connection: Connection) -> None:
    """Create the current database schema when needed."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS repository_state (
            organization TEXT NOT NULL,
            repository TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (organization, repository)
        );
        CREATE TABLE IF NOT EXISTS source_coverage (
            organization TEXT NOT NULL,
            repository TEXT NOT NULL,
            source TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            ends_at TEXT NOT NULL,
            accessed_at TEXT NOT NULL,
            PRIMARY KEY (organization, repository, source, query_hash, starts_at),
            CHECK (ends_at > starts_at)
        );
        CREATE TABLE IF NOT EXISTS pull_request_facts (
            organization TEXT NOT NULL,
            repository TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            identifier INTEGER NOT NULL,
            merged_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (organization, repository, query_hash, identifier)
        );
        CREATE TABLE IF NOT EXISTS direct_commit_facts (
            organization TEXT NOT NULL,
            repository TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            sha TEXT NOT NULL,
            committed_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (organization, repository, query_hash, sha)
        );
        """,
    )


def initialize_observations(connection: Connection) -> None:
    """Create the observation history schema when needed.

    Its own database and its own initializer, so the cache's "no migrations, delete it and refetch"
    rule keeps applying to the cache alone. Keyed on the observed instant as well as the family, so
    one run's rows replace only themselves if it is stored twice — the instant carries microseconds,
    so two runs never collide — and every earlier run's rows are untouched: appending, not replacing,
    is the whole point of this file.
    """
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS alert_observations (
            organization TEXT NOT NULL,
            repository TEXT NOT NULL,
            family TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (organization, repository, family, fetched_at)
        );
        """,
    )


def prepare(connection: Connection) -> None:
    """Enable relational integrity and initialize the current schema."""
    connection.execute("PRAGMA foreign_keys = ON")
    initialize(connection)


def get_pull_request_facts(connection: Connection, coverage: SourceCoverage) -> tuple[PullRequestFact, ...]:
    """Return cached pull requests merged in one half-open interval with their reviews."""
    rows = connection.execute(
        """
        SELECT payload
        FROM pull_request_facts
        WHERE organization = ? AND repository = ? AND query_hash = ? AND merged_at >= ? AND merged_at < ?
        ORDER BY merged_at, identifier
        """,
        (
            coverage.organization,
            coverage.repository,
            coverage.query_hash,
            coverage.starts_at.astimezone(UTC).isoformat(),
            coverage.ends_at.astimezone(UTC).isoformat(),
        ),
    ).fetchall()
    return tuple(PullRequestFact.model_validate_json(payload) for (payload,) in rows)


def replace_pull_request_facts(
    connection: Connection,
    coverage: SourceCoverage,
    facts: tuple[PullRequestFact, ...],
    *,
    complete: bool,
) -> None:
    """Atomically replace one pull-request interval and optionally mark it complete."""
    connection.execute(
        """
        DELETE FROM pull_request_facts
        WHERE organization = ? AND repository = ? AND query_hash = ? AND merged_at >= ? AND merged_at < ?
        """,
        (
            coverage.organization,
            coverage.repository,
            coverage.query_hash,
            coverage.starts_at.astimezone(UTC).isoformat(),
            coverage.ends_at.astimezone(UTC).isoformat(),
        ),
    )
    connection.executemany(
        "INSERT INTO pull_request_facts VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                coverage.organization,
                coverage.repository,
                coverage.query_hash,
                fact.identifier,
                fact.merged_at.astimezone(UTC).isoformat(),
                fact.model_dump_json(),
            )
            for fact in facts
        ),
    )
    if complete:
        record_source_coverage(connection, coverage)


def get_direct_commit_facts(connection: Connection, coverage: SourceCoverage) -> tuple[DirectCommitFact, ...]:
    """Return cached direct commits made in one half-open interval."""
    rows = connection.execute(
        """
        SELECT payload
        FROM direct_commit_facts
        WHERE organization = ? AND repository = ? AND query_hash = ? AND committed_at >= ? AND committed_at < ?
        ORDER BY committed_at, sha
        """,
        (
            coverage.organization,
            coverage.repository,
            coverage.query_hash,
            coverage.starts_at.astimezone(UTC).isoformat(),
            coverage.ends_at.astimezone(UTC).isoformat(),
        ),
    ).fetchall()
    return tuple(DirectCommitFact.model_validate_json(payload) for (payload,) in rows)


def replace_direct_commit_facts(
    connection: Connection,
    coverage: SourceCoverage,
    facts: tuple[DirectCommitFact, ...],
    *,
    complete: bool,
) -> None:
    """Atomically replace one direct-commit interval and optionally mark it complete."""
    connection.execute(
        """
        DELETE FROM direct_commit_facts
        WHERE organization = ? AND repository = ? AND query_hash = ? AND committed_at >= ? AND committed_at < ?
        """,
        (
            coverage.organization,
            coverage.repository,
            coverage.query_hash,
            coverage.starts_at.astimezone(UTC).isoformat(),
            coverage.ends_at.astimezone(UTC).isoformat(),
        ),
    )
    connection.executemany(
        "INSERT INTO direct_commit_facts VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                coverage.organization,
                coverage.repository,
                coverage.query_hash,
                fact.sha,
                fact.committed_at.astimezone(UTC).isoformat(),
                fact.model_dump_json(),
            )
            for fact in facts
        ),
    )
    if complete:
        record_source_coverage(connection, coverage)


def find_missing_cached_coverage(path: Path, requested: SourceCoverage) -> tuple[SourceCoverage, ...]:
    """Return missing source coverage from the configured SQLite cache."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            connection.execute(
                """
                UPDATE source_coverage SET accessed_at = ?
                WHERE organization = ? AND repository = ? AND source = ? AND query_hash = ?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    requested.organization,
                    requested.repository,
                    requested.source.value,
                    requested.query_hash,
                ),
            )
            return find_missing_coverage(connection, requested)
    except (Error, OSError) as exception:
        message = f"could not read collection cache: {exception}"
        raise StorageError(message) from exception


def cache_pull_request_facts(
    path: Path,
    coverage: SourceCoverage,
    facts: tuple[PullRequestFact, ...],
    *,
    complete: bool,
) -> None:
    """Replace one cached pull-request interval in a transaction."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            replace_pull_request_facts(connection, coverage, facts, complete=complete)
    except (Error, OSError) as exception:
        message = f"could not update collection cache: {exception}"
        raise StorageError(message) from exception


def load_cached_pull_request_facts(path: Path, coverage: SourceCoverage) -> tuple[PullRequestFact, ...]:
    """Load pull-request facts for one reporting interval from SQLite."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            return get_pull_request_facts(connection, coverage)
    except (Error, OSError) as exception:
        message = f"could not read collection cache: {exception}"
        raise StorageError(message) from exception


def cache_direct_commit_facts(
    path: Path,
    coverage: SourceCoverage,
    facts: tuple[DirectCommitFact, ...],
    *,
    complete: bool,
) -> None:
    """Replace one cached direct-commit interval in a transaction."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            replace_direct_commit_facts(connection, coverage, facts, complete=complete)
    except (Error, OSError) as exception:
        message = f"could not update collection cache: {exception}"
        raise StorageError(message) from exception


def load_cached_direct_commit_facts(path: Path, coverage: SourceCoverage) -> tuple[DirectCommitFact, ...]:
    """Load direct-commit facts for one reporting interval from SQLite."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            return get_direct_commit_facts(connection, coverage)
    except (Error, OSError) as exception:
        message = f"could not read collection cache: {exception}"
        raise StorageError(message) from exception


def prune_cache(path: Path, unused_since: datetime) -> int:
    """Delete cached intervals unused since an instant, and any facts they left behind."""
    try:
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            deleted = connection.execute(
                "DELETE FROM source_coverage WHERE accessed_at < ?",
                (unused_since.astimezone(UTC).isoformat(),),
            ).rowcount
            for table in ("pull_request_facts", "direct_commit_facts"):
                connection.execute(
                    # The table name is not user input: it comes from this literal tuple of fact tables.
                    f"""
                    DELETE FROM {table}
                    WHERE NOT EXISTS (
                        SELECT 1 FROM source_coverage
                        WHERE source_coverage.organization = {table}.organization
                          AND source_coverage.repository = {table}.repository
                          AND source_coverage.query_hash = {table}.query_hash
                    )
                    """,  # noqa: S608
                )
    except (Error, OSError) as exception:
        message = f"could not update collection cache: {exception}"
        raise StorageError(message) from exception
    return deleted


def get_source_coverage(connection: Connection, coverage: SourceCoverage) -> tuple[SourceCoverage, ...]:
    """Return cached intervals for one repository source in chronological order."""
    rows = connection.execute(
        """
        SELECT starts_at, ends_at
        FROM source_coverage
        WHERE organization = ? AND repository = ? AND source = ? AND query_hash = ?
        ORDER BY starts_at
        """,
        (coverage.organization, coverage.repository, coverage.source.value, coverage.query_hash),
    ).fetchall()
    return tuple(
        coverage.model_copy(
            update={"starts_at": datetime.fromisoformat(starts_at), "ends_at": datetime.fromisoformat(ends_at)},
        )
        for starts_at, ends_at in rows
    )


def find_missing_coverage(connection: Connection, requested: SourceCoverage) -> tuple[SourceCoverage, ...]:
    """Return the uncovered portions of one requested half-open interval."""
    cursor = requested.starts_at
    missing: list[SourceCoverage] = []
    for covered in get_source_coverage(connection, requested):
        if covered.ends_at <= cursor:
            continue
        if covered.starts_at >= requested.ends_at:
            break
        if covered.starts_at > cursor:
            missing.append(
                requested.model_copy(
                    update={"starts_at": cursor, "ends_at": min(covered.starts_at, requested.ends_at)}
                ),
            )
        cursor = max(cursor, covered.ends_at)
        if cursor >= requested.ends_at:
            break
    if cursor < requested.ends_at:
        missing.append(requested.model_copy(update={"starts_at": cursor}))
    return tuple(missing)


def record_source_coverage(connection: Connection, coverage: SourceCoverage) -> None:
    """Store successful coverage and coalesce overlapping or adjacent intervals."""
    normalised = coverage.model_copy(
        update={
            "starts_at": coverage.starts_at.astimezone(UTC),
            "ends_at": coverage.ends_at.astimezone(UTC),
        },
    )
    intervals = sorted(
        (*get_source_coverage(connection, coverage), normalised),
        key=lambda interval: interval.starts_at,
    )
    merged: list[SourceCoverage] = []
    for interval in intervals:
        if not merged or interval.starts_at > merged[-1].ends_at:
            merged.append(interval)
            continue
        merged[-1] = merged[-1].model_copy(update={"ends_at": max(merged[-1].ends_at, interval.ends_at)})

    connection.execute(
        "DELETE FROM source_coverage WHERE organization = ? AND repository = ? AND source = ? AND query_hash = ?",
        (coverage.organization, coverage.repository, coverage.source.value, coverage.query_hash),
    )
    connection.executemany(
        "INSERT INTO source_coverage VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            (
                interval.organization,
                interval.repository,
                interval.source.value,
                interval.query_hash,
                interval.starts_at.astimezone(UTC).isoformat(),
                interval.ends_at.astimezone(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
            )
            for interval in merged
        ),
    )


def record_repository_state(path: Path, inventory: RepositoryInventory) -> None:
    """Replace the stored current state of every repository one collection observed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            connection.executemany(
                "INSERT OR REPLACE INTO repository_state VALUES (?, ?, ?, ?)",
                (
                    (
                        inventory.organization,
                        item.repository.name,
                        inventory.collected_at.astimezone(UTC).isoformat(),
                        # Collection provenance describes one run, not the repository's durable state.
                        item.model_dump_json(exclude={"collection"}),
                    )
                    for item in inventory.repositories
                ),
            )
    except (Error, OSError) as exception:
        message = f"could not store collection: {exception}"
        raise StorageError(message) from exception


def repository_alert_observations(
    item: RepositoryInventoryItem,
    fetched_at: datetime,
) -> tuple[AlertObservation, ...]:
    """Project one repository's collected alert block into the rows an observation series appends.

    A family that could not be read contributes NOTHING: it keeps reporting its reason on the
    current-state block, and a row recording its absence would be indistinguishable from a genuine
    fall to a low count once the reason is a year old. Unavailable data never becomes zero, applied
    to a series rather than to a single number.
    """
    if item.security is None:
        return ()
    return tuple(
        AlertObservation(family=family, fetched_at=fetched_at, open=count.open, by_severity=count.by_severity)
        for family, count in item.security.families()
        if count.open is not None
    )


def record_alert_observations(path: Path, inventory: RepositoryInventory) -> int:
    """Append each repository's readable alert families to the observation history, and count them.

    Appended beside the latest-only state the same run writes, not instead of it: the current-state
    row answers "what is unfixed now" and is overwritten every run, while these rows are what makes
    a past count reportable at all. Written to the observation database, so deleting the cache costs
    no history.
    """
    rows = tuple(
        (
            inventory.organization,
            item.repository.name,
            observation.family.value,
            observation.fetched_at.astimezone(UTC).isoformat(),
            observation.model_dump_json(),
        )
        for item in inventory.repositories
        for observation in repository_alert_observations(item, inventory.collected_at)
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            initialize_observations(connection)
            connection.executemany("INSERT OR REPLACE INTO alert_observations VALUES (?, ?, ?, ?, ?)", rows)
    except (Error, OSError) as exception:
        message = f"could not store alert observations: {exception}"
        raise StorageError(message) from exception
    return len(rows)


def load_alert_observations(
    path: Path,
    organization: str,
    repository: str,
    window: ReportingWindow,
) -> tuple[AlertObservation, ...]:
    """Load one repository's alert observations made inside one half-open interval.

    Half-open `[starts_at, ends_at)` like every other interval here, so an observation is reported by
    exactly one range and none is double-counted at a boundary.

    Grouped by family and then ordered by the instant observed, so one family's history reads down
    consecutive rows: the question asked of this series is what a family did over time, not what the
    three families held on one afternoon. Families keep their declared order rather than SQLite's
    alphabetical one, so this block lists them exactly as the current-state block does.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            initialize_observations(connection)
            rows = connection.execute(
                """
                SELECT payload
                FROM alert_observations
                WHERE organization = ? AND repository = ? AND fetched_at >= ? AND fetched_at < ?
                ORDER BY fetched_at, family
                """,
                (
                    organization,
                    repository,
                    window.starts_at.astimezone(UTC).isoformat(),
                    window.ends_at.astimezone(UTC).isoformat(),
                ),
            ).fetchall()
    except (Error, OSError) as exception:
        message = f"could not read alert observations: {exception}"
        raise StorageError(message) from exception
    families = tuple(AlertFamily)
    observed = (AlertObservation.model_validate_json(payload) for (payload,) in rows)
    return tuple(sorted(observed, key=lambda item: (families.index(item.family), item.fetched_at)))


def load_repository_state(path: Path, organization: str, repository: str) -> StoredRepositoryState | None:
    """Load one repository's latest stored current state, or None when it was never collected."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            row = connection.execute(
                "SELECT fetched_at, payload FROM repository_state WHERE organization = ? AND repository = ?",
                (organization, repository),
            ).fetchone()
    except (Error, OSError) as exception:
        message = f"could not read collection cache: {exception}"
        raise StorageError(message) from exception
    if row is None:
        return None
    fetched_at, payload = row
    return StoredRepositoryState(
        fetched_at=datetime.fromisoformat(fetched_at),
        state=RepositoryInventoryItem.model_validate_json(payload),
    )
