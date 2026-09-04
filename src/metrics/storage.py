"""Persist immutable collection snapshots in SQLite.

TWO FILES, NOT ONE. The collection cache is disposable — deleting it and refetching is expected —
which is why it carries no migrations and why nothing irreplaceable may live in it. Alert
observations ARE irreplaceable: GitHub cannot say what was open last month, so a row lost is a hole
in the series for ever. They therefore live in a second SQLite file beside the cache, so that
"delete the cache" stays a safe operation. See architecture.md, "Storage rule".
"""

from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection, Error, connect

from pydantic import ValidationError

from metrics.domain import (
    AlertFamily,
    AlertObservation,
    DirectCommitFact,
    EvidenceSource,
    PullRequestFact,
    ReportingWindow,
    RepositoryInventory,
    RepositoryInventoryItem,
    SonarProjectMapping,
    SonarRepositoryProject,
    SonarResolution,
    SourceCoverage,
    StoredRepositoryState,
    StoredSonarMapping,
)


class StorageError(RuntimeError):
    """Report a metrics storage failure."""


JOURNAL_MODE = "delete"
"""The journal mode both files are held in, set on every connection that opens either of them.

Set here because the mode belongs to the FILE and not to the code that opens it. SQLite records it
in the header, so whatever sets it once — a database browser, an older build, a library compiled
with a different default — is what every later run inherits, however long ago that was and whoever
did it. Leaving the mode to the default therefore leaves it to history.

WAL is the mode this excludes. A WAL database keeps its wal-index in a `-shm` file mapped into
memory, and that mapping is only as sound as the filesystem under it: where the kernel cannot fault
one of its pages in, the process takes SIGBUS inside `walIndexAppend` part-way through a commit and
dies without a Python traceback. Three collection runs over the HMCTS estate died exactly that way,
on a cache file that had been left in WAL mode, on a directory shared out to a VM. A rollback
journal maps nothing and cannot fail that way.

The cost is concurrency, and it is small here. A collection is a single process, and a
`metrics-serve` read taken while one is writing waits on the five-second busy timeout instead of
proceeding beside the writer as it would under WAL.
"""


def pin_journal_mode(connection: Connection) -> None:
    """Hold one connection's database in `JOURNAL_MODE`, converting a file that arrived in another.

    Read before writing, because this runs on every connection a collection opens and a collection
    opens thousands: a file already in the right mode costs one pragma and no write. A conversion
    another connection holds the file against raises `sqlite3.Error`, which every caller in this
    module already turns into a `StorageError` naming the file.
    """
    if connection.execute("PRAGMA journal_mode").fetchone()[0] != JOURNAL_MODE:
        # Interpolated because PRAGMA takes no bound parameter. The value is this module's constant
        # and no caller can reach it.
        connection.execute(f"PRAGMA journal_mode = {JOURNAL_MODE}")


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
        CREATE TABLE IF NOT EXISTS confirmed_coverage (
            organization TEXT NOT NULL,
            repository TEXT NOT NULL,
            source TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            ends_at TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY (organization, repository, source, query_hash)
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
    """Hold the journal mode and create the observation history schema when needed.

    The mode is held here for the reason `JOURNAL_MODE` gives, and it matters more on this file than
    on the cache: a commit lost to a SIGBUS takes a month of alert history with it that GitHub cannot
    be asked for again. `prepare` is the cache's equivalent, and this file does not use it — foreign
    keys buy nothing across two tables that reference neither.

    Its own database and its own initializer, so the cache's "no migrations, delete it and refetch"
    rule keeps applying to the cache alone. `alert_observations` is keyed on the observed instant as
    well as the family, so one run's rows replace only themselves if it is stored twice — the instant
    carries microseconds, so two runs never collide — and every earlier run's rows are untouched:
    appending, not replacing, is the whole point of this file.

    `sonar_project_map` IS HERE RATHER THAN IN THE CACHE BECAUSE IT IS NOT CHEAPLY REBUILDABLE.
    Nothing on either side records which repository a SonarCloud project analyses, so each row is
    resolved by searching GitHub for the commit an analysis ran against — and that search is limited
    to 10 requests a minute unauthenticated, 30 authenticated. Rebuilding the organisation's 289
    projects therefore costs something between ten minutes and half an hour of paced calls, so
    `rm metrics.sqlite3` must not silently shrink the next evidence run's coverage to whichever
    repositories happen to declare a properties file.

    THIS STRETCHES THIS FILE PAST ITS ORIGINAL NAME: it holds an alert history, which cannot be
    refetched at any price, and now a mapping which can be refetched but only slowly. The alternative
    was a third SQLite file for expensive-but-recoverable state, which would have meant a third
    lifecycle rule for a reader to learn and a third path for a backup to miss. One durable file
    beside one disposable one is the distinction that actually matters — "deleting the cache is
    always safe" holds either way — so the map lives with the observations. Both are keyed by
    `sonar_organization` rather than the GitHub organisation because the two names can differ, and a
    map built against one SonarCloud organisation says nothing about another's project keys.

    A row whose `repository` is NULL is a project that could not be resolved, and its `detail` says
    why. It is stored deliberately: a never-analysed project answers the same way every run, and
    remembering the answer is what stops the next run paying the search quota to learn it again.
    """
    pin_journal_mode(connection)
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
        CREATE TABLE IF NOT EXISTS sonar_project_map (
            sonar_organization TEXT NOT NULL,
            project_key TEXT NOT NULL,
            repository TEXT,
            analysis_at TEXT,
            revision TEXT,
            method TEXT,
            resolved_at TEXT NOT NULL,
            detail TEXT,
            PRIMARY KEY (sonar_organization, project_key),
            CHECK ((repository IS NULL) <> (detail IS NULL))
        );
        """,
    )


def prepare(connection: Connection) -> None:
    """Enable relational integrity, hold the journal mode, and initialize the current schema."""
    connection.execute("PRAGMA foreign_keys = ON")
    pin_journal_mode(connection)
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


def find_missing_cached_coverage(
    path: Path,
    requested: SourceCoverage,
    *,
    record_use: bool,
) -> tuple[SourceCoverage, ...]:
    """Return missing source coverage from the configured SQLite cache.

    `record_use` STAMPS `accessed_at`, AND ONLY A COLLECTING RUN MAY. The stamp is what keeps an
    interval the current queries still ask for out of `prune_cache`'s reach, and a collection sets it
    on every interval it records anyway — so the collecting path asks for it and the reporting path
    does not. A report that stamped it would write to the cache file on every read, moving the
    modification time the service compares a built bundle against, and every request would rebuild
    the whole cohort's report because its own last build had touched the caches it was built from.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            if record_use:
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


def confirm_collection_coverage(
    path: Path,
    organization: str,
    repositories: Iterable[str],
    signatures: Mapping[EvidenceSource, str],
    confirmed_at: datetime,
) -> None:
    """Snapshot the coverage edge each named repository holds, as one finished run's confirmation.

    CALLED AT THE END OF A RUN, NEVER AT THE START. `source_coverage` moves as a run works, so a row
    count over it cannot tell a run that finished from one still going or one that died; a row here
    exists only because a run reached its end. `prevailing_cached_coverage` takes the estate's anchor
    over this table for that reason, so starting a collection moves nothing.

    IT COPIES EACH REPOSITORY'S ACTUAL CACHED EDGE, NOT THE WINDOW THE RUN ASKED FOR. A run whose
    repository failed — rate-limited, permission denied, or never reached before an ad-hoc run was
    scoped away from it — leaves that repository's `source_coverage` where it was, so this
    re-confirms the OLD edge and the repository stays behind the anchor rather than being vouched
    for at an instant nothing was collected to. A run therefore cannot carry the estate's anchor
    forward on behalf of a repository it did not actually collect.

    A named repository with no coverage rows at all contributes nothing: `MAX(ends_at)` is grouped,
    so a repository the cache has never held is absent from the result rather than present with a
    null edge. Repositories are filtered in Python, not through an `IN (...)` list, because the
    estate runs to thousands of names and the cache moves to PostgreSQL later.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        named = set(repositories)
        stamp = confirmed_at.astimezone(UTC).isoformat()
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            rows = [
                (organization, repository, source.value, query_hash, edge, stamp)
                for source, query_hash in signatures.items()
                for repository, edge in connection.execute(
                    """
                    SELECT repository, MAX(ends_at)
                    FROM source_coverage
                    WHERE organization = ? AND source = ? AND query_hash = ?
                    GROUP BY repository
                    """,
                    (organization, source.value, query_hash),
                ).fetchall()
                if repository in named
            ]
            # `ON CONFLICT ... DO UPDATE` rather than `INSERT OR REPLACE`, which is SQLite's own
            # spelling and not standard SQL — the cache moves to PostgreSQL later. One transaction,
            # so a reporting read sees one run's confirmation whole or not at all.
            connection.executemany(
                """
                INSERT INTO confirmed_coverage (organization, repository, source, query_hash, ends_at, confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (organization, repository, source, query_hash)
                DO UPDATE SET ends_at = excluded.ends_at, confirmed_at = excluded.confirmed_at
                """,
                rows,
            )
    except (Error, OSError) as exception:
        message = f"could not update collection cache: {exception}"
        raise StorageError(message) from exception


PREVAILING_EDGE = """
    SELECT COALESCE(confirmed.ends_at, live.edge)
    FROM (
        SELECT repository, MAX(ends_at) AS edge
        FROM source_coverage
        WHERE organization = ? AND source = ? AND query_hash = ?
        GROUP BY repository
    ) AS live
    LEFT JOIN confirmed_coverage AS confirmed
        ON confirmed.organization = ?
       AND confirmed.source = ?
       AND confirmed.query_hash = ?
       AND confirmed.repository = live.repository
    GROUP BY COALESCE(confirmed.ends_at, live.edge)
    ORDER BY COUNT(*) DESC, COALESCE(confirmed.ends_at, live.edge) DESC
    LIMIT 1
"""
"""The modal edge over one row per covered repository: its confirmed edge, or its live one if none.

EVERY COVERED REPOSITORY GETS A VOTE, AND THE FALLBACK IS PER REPOSITORY RATHER THAN PER SIGNATURE.
The confirmation is preferred wherever there is one, so a run in flight moves nothing; a repository
with no confirmation votes with the edge its own coverage reaches, so the estate still has an anchor
before any run has finished. Reading the whole table for the fallback instead — preferring
confirmations only once the signature has any at all — would let the FIRST run to finish be the
entire electorate: one repository of a thousand confirmed at a new edge would be 100% of the
confirmed rows and would carry the anchor there, taking the other 999 out of every span. That is the
whole-estate blanking this anchor exists to stop, and the cache in service when this shipped holds
exactly that state.

The fallback covers two cases and self-heals out of both, repository by repository, at the first
completed run that reaches each one. The first is a cache written before confirmations existed. The
second is a signature whose first run has not finished, where the alternative is reporting nothing
at all from coverage that is already there. Under a FRESH signature a repository the in-progress run
has not reached yet has no coverage row, so it has nothing to report at any anchor and no vote.

`live` is the driving side, so a confirmation whose coverage has gone stops voting. `prune_cache`
deletes those together anyway; this makes the surviving-coverage rule the query's own.

The inner `GROUP BY` is what `confirmed_coverage` does not need: `source_coverage` holds an interval
per repository per run, so a repository's own edge is the latest of them. Stored text rather than
parsed instants throughout, because `record_source_coverage` normalises every instant to a UTC
`isoformat()`, so the text is fixed-width through the seconds and both `MAX` and the `ORDER BY` sort
chronologically.
"""


def prevailing_cached_coverage(
    path: Path,
    organization: str,
    source: EvidenceSource,
    query_hash: str,
) -> datetime | None:
    """Return the instant most of one organisation's confirmed coverage reaches, or `None` for none.

    The anchor an offline report ends its windows at. THE EDGE THE LAST COMPLETED RUN CONFIRMED, over
    `confirmed_coverage` rather than over live `source_coverage`, so that only a run which reached its
    end moves it. ONCE A REPOSITORY IS CONFIRMED, an in-progress `collect` moves its vote nothing, and
    neither does a `collect` that dies part way, `evidence --refresh --repository x`, or a collecting
    `trend`: all four write `source_coverage` as they work, and none of them writes a confirmation.
    Where the confirmation is missing the fallback below votes live instead, and those four do move
    the anchor once the rows they re-stamp are the majority — the residual is stated there. `None`
    means the cache holds no coverage at all under this signature.

    THE EDGE MOST REPOSITORIES ARE AT, NOT THE GREATEST ONE ANY OF THEM REACHED, and not the edge
    every one of them shares. Both extremes blank the estate from one repository:
      - The edge every repository shares hands the window to the worst straggler — one repository
        missed for a month would drag a thousand others' window back a month to hide one gap.
      - The greatest edge hands it to whichever repository was collected last on its own, leaving
        every repository that run did not reach short of coverage by that day alone — the
        whole-estate `unavailable` this anchor exists to stop.
    `confirm_collection_coverage` writes one row per repository, holding the edge that repository is
    ACTUALLY covered to, so a run that failed on a repository re-confirms that repository's old edge
    and the mode stays where the estate is. A repository BEHIND the edge reports as unavailable at
    the window, which is the existing meaning of that field and the honest answer; one AHEAD of it
    still reports, because `record_source_coverage` coalesces its newer interval into the stored one,
    so its coverage spans the anchor and no gap is found there. A run over a SUBSET of the estate
    that finishes still confirms only what it collected, so it carries the mode forward only when its
    subset is the majority of the covered estate — which `--hold-anchor` is there to prevent.

    ONE VOTE PER COVERED REPOSITORY, whether or not a run has confirmed it: `PREVAILING_EDGE` falls
    back to a repository's own live edge only for a repository with no confirmation, so that the
    first run to finish cannot be the whole electorate — see there for why that matters. A repository
    with no coverage at all has nothing to report at any anchor and no vote. THE RESIDUAL IS THAT AN
    UNCONFIRMED MAJORITY VOTES LIVE: while the repositories a run covers hold no confirmation nothing
    records where they were, so re-stamping the majority of the covered estate carries the anchor
    forward on the run's own live rows — as the run works, on a run that dies past halfway, and under
    `--hold-anchor` alike. That is the state of the cache in service when this shipped, and it
    self-heals repository by repository at the first completed run that reaches each one.

    The count is over whatever rows the cache holds, INCLUDING repositories no longer configured,
    whose coverage and confirmations survive until `prune_cache` removes them. A de-configured cohort
    larger than the configured one would therefore win the mode with its older edge.

    TIES GO TO THE LATER EDGE: with no edge in the majority there is nothing to tell a cohort moving
    forward from one lagging behind, so the answer stays what it was before the mode was counted.

    `query_hash` IS PART OF THE FILTER because both tables accumulate superseded signatures — the
    working cache holds seven pull-request signatures and two commit ones from older builds. Their
    rows are not coverage this build can report from, and one of them reaching further ahead would
    set an anchor nothing current covers.

    `accessed_at` is left alone: reading the edge is not a use of any interval, and stamping it here
    would keep dead signatures alive against `prune_cache` for ever. A cache that is not there yet is
    not created either, for a sharper reason: the service stamps both cache files to decide whether a
    built report still describes them, and a read that brought one into being would move the stamp it
    was just compared against and rebuild the whole cohort's report on the next request.
    """
    if not path.exists():
        return None
    try:
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            parameters = (organization, source.value, query_hash)
            row = connection.execute(PREVAILING_EDGE, parameters + parameters).fetchone()
            # Grouped, so no matching row is no row at all rather than one row holding NULL. The
            # parse sits inside the guard with the query: a row this build cannot READ and a row it
            # cannot PARSE are the same failure to every caller, each of which degrades a
            # `StorageError` to "nothing collected", while a bare `ValueError` escaping from here
            # would take down the service's warm-up and every offline command with it.
            return None if row is None else datetime.fromisoformat(str(row[0]))
    except (Error, OSError, ValueError) as exception:
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
    """Delete cached intervals unused since an instant, and any facts and confirmations they leave.

    A confirmation outlives the coverage it vouched for unless it is cleared here, and
    `prevailing_cached_coverage` counts every row it finds — so a retired query signature or a
    de-configured repository would go on voting for its old edge, and a cohort of them larger than
    the live estate would win the mode. Confirmations go with their coverage for that reason.

    The COUNT IS OF INTERVALS ONLY, which is what `prune --days N` reports to the operator: facts and
    confirmations are consequences of an interval going, not separate things pruned.
    """
    try:
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            deleted = connection.execute(
                "DELETE FROM source_coverage WHERE accessed_at < ?",
                (unused_since.astimezone(UTC).isoformat(),),
            ).rowcount
            connection.execute(
                """
                DELETE FROM confirmed_coverage
                WHERE NOT EXISTS (
                    SELECT 1 FROM source_coverage
                    WHERE source_coverage.organization = confirmed_coverage.organization
                      AND source_coverage.repository = confirmed_coverage.repository
                      AND source_coverage.source = confirmed_coverage.source
                      AND source_coverage.query_hash = confirmed_coverage.query_hash
                )
                """,
            )
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


def sonar_resolution(method: str | None) -> SonarResolution:
    """Read one stored resolution method back as the enum it was written as.

    A method this build does not know fails loudly rather than being carried through as free text: the
    report's whole use for it is to say how a mapping was arrived at, and a value nobody can interpret
    would make a wrong mapping less diagnosable than no mapping at all.
    """
    resolutions = {resolution.value: resolution for resolution in SonarResolution}
    resolution = resolutions.get(method or "")
    if resolution is None:
        message = f"could not read the sonar project map: unknown resolution method {method!r}"
        raise StorageError(message)
    return resolution


def sonar_mapping(
    project_key: str,
    repository: str,
    analysis_at: str | None,
    revision: str | None,
    method: str | None,
) -> SonarProjectMapping:
    """Rebuild one resolved mapping from its stored columns."""
    return SonarProjectMapping(
        project_key=project_key,
        repository=repository,
        method=sonar_resolution(method),
        analysis_at=None if analysis_at is None else datetime.fromisoformat(analysis_at),
        revision=revision,
    )


StoredMappingRow = tuple[str, str | None, str | None, str | None, str | None, str, str | None]
"""One `sonar_project_map` row, in the column order both of this module's queries select."""


def sonar_mapping_row(row: StoredMappingRow) -> StoredSonarMapping:
    """Project one `sonar_project_map` row into the mapping it resolved, or the reason it did not."""
    project_key, repository, analysis_at, revision, method, resolved_at, detail = row
    return StoredSonarMapping(
        project_key=project_key,
        resolved_at=datetime.fromisoformat(resolved_at),
        mapping=None if repository is None else sonar_mapping(project_key, repository, analysis_at, revision, method),
        detail=detail,
    )


def resolved_columns(mapping: SonarProjectMapping | None) -> tuple[str | None, str | None, str | None, str | None]:
    """Flatten one resolution into its four stored columns, all NULL when nothing was resolved.

    The columns of an unresolved project are empty rather than defaulted: a repository name invented
    here would be read back as an answer by every later lookup.
    """
    if mapping is None:
        return (None, None, None, None)
    analysis_at = None if mapping.analysis_at is None else mapping.analysis_at.astimezone(UTC).isoformat()
    return (mapping.repository, analysis_at, mapping.revision, mapping.method.value)


def supersedes(stored: datetime | None, incoming: datetime | None) -> bool:
    """Decide whether an incoming resolution's analysis instant beats the stored one's.

    A stored row with no analysis instant has nothing to defend — it is either absent or a project
    that could not be resolved — so anything replaces it, including a fresher reason. Otherwise only
    a STRICTLY NEWER analysis wins: re-running the mapping over stale data must not be able to drag a
    project that has moved between repositories back to the repository it left, and re-resolving from
    the same analysis buys nothing to justify a write.
    """
    if stored is None:
        return True
    return incoming is not None and incoming > stored


def record_sonar_mapping(path: Path, sonar_organization: str, resolution: StoredSonarMapping) -> bool:
    """Upsert one project's mapping into the durable map, reporting whether it was written.

    Written per project as each one resolves rather than once at the end of a run, because the run is
    paced against a per-minute search quota and may be stopped by a rate limit or by a human at any
    point: every row already paid for is kept.

    A REFUSED WRITE STILL MOVES `resolved_at`, WHICH IS WHY THE REFUSAL IS NOT SILENT. The skip
    watermark `already_answered` reads is when the row was last WRITTEN, so leaving it untouched here
    would make the run that just asked the question look like it never asked: a project whose newest
    analysis names no findable commit resolves from an older one, the write is refused as not
    superseding, and the next run sees that same newer analysis still standing above the watermark
    and re-runs the whole search — every run, forever, against the scarcest quota there is. Touching
    the instant records "asked, and the stored answer stood", which converges. The answer itself is
    left exactly as it was.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            initialize_observations(connection)
            row = connection.execute(
                "SELECT analysis_at FROM sonar_project_map WHERE sonar_organization = ? AND project_key = ?",
                (sonar_organization, resolution.project_key),
            ).fetchone()
            stored_at = None if row is None or row[0] is None else datetime.fromisoformat(row[0])
            if not supersedes(stored_at, resolution.analysis_at):
                connection.execute(
                    """
                    UPDATE sonar_project_map
                    SET resolved_at = ?
                    WHERE sonar_organization = ? AND project_key = ?
                    """,
                    (
                        resolution.resolved_at.astimezone(UTC).isoformat(),
                        sonar_organization,
                        resolution.project_key,
                    ),
                )
                return False
            connection.execute(
                "INSERT OR REPLACE INTO sonar_project_map VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sonar_organization,
                    resolution.project_key,
                    *resolved_columns(resolution.mapping),
                    resolution.resolved_at.astimezone(UTC).isoformat(),
                    resolution.detail,
                ),
            )
    except (Error, OSError) as exception:
        message = f"could not store the sonar project map: {exception}"
        raise StorageError(message) from exception
    return True


def load_sonar_mapping(path: Path, sonar_organization: str, project_key: str) -> StoredSonarMapping | None:
    """Load what the map knows about one SonarCloud project, or None when it has never been resolved."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            initialize_observations(connection)
            row = connection.execute(
                """
                SELECT project_key, repository, analysis_at, revision, method, resolved_at, detail
                FROM sonar_project_map
                WHERE sonar_organization = ? AND project_key = ?
                """,
                (sonar_organization, project_key),
            ).fetchone()
    except (Error, OSError) as exception:
        message = f"could not read the sonar project map: {exception}"
        raise StorageError(message) from exception
    return None if row is None else sonar_mapping_row(row)


def repository_project(path: Path, sonar_organization: str, repository: str) -> SonarRepositoryProject | None:
    """Name the SonarCloud project the map attributes to one repository, with how many claimed it.

    The reverse of `load_sonar_mapping`, and MANY-TO-ONE: the most recently analysed candidate wins,
    which is what picks the live project out of a duplicate pair whose abandoned twin holds the
    unsuffixed key. An undated candidate loses to every dated one and never wins on a tie, so the
    order is total and a repeated call answers the same way.

    The repository name is compared without regard to case, because one side of the comparison is a
    name GitHub returned and the other is a name a human typed into the configuration file.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            initialize_observations(connection)
            rows = connection.execute(
                """
                SELECT project_key, repository, analysis_at, revision, method, resolved_at, detail
                FROM sonar_project_map
                WHERE sonar_organization = ? AND repository = ? COLLATE NOCASE
                ORDER BY analysis_at DESC, project_key
                """,
                (sonar_organization, repository),
            ).fetchall()
    except (Error, OSError) as exception:
        message = f"could not read the sonar project map: {exception}"
        raise StorageError(message) from exception
    if not rows:
        return None
    project_key, stored_repository, analysis_at, revision, method, _, _ = rows[0]
    return SonarRepositoryProject(
        # The stored spelling, not the queried one: it is the name GitHub itself reported.
        mapping=sonar_mapping(project_key, stored_repository, analysis_at, revision, method),
        candidates=len(rows),
    )


def load_repository_state(path: Path, organization: str, repository: str) -> StoredRepositoryState | None:
    """Load one repository's latest stored current state, or None when it was never collected.

    A row this build cannot PARSE is a `StorageError` like a row it cannot READ, which is why the
    parses sit inside the same guard as the query. The stored payload is validated `extra="forbid"`,
    so a row written by a newer build — or a corrupt one — raises rather than returning, and letting
    that escape would cost far more than the one repository it is about: every caller degrades a
    `StorageError` to a reason on that repository alone, while an unhandled `ValidationError` fails
    the whole report the repository is one row of, and takes the service's warm-up down with it.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection, connection:
            prepare(connection)
            row = connection.execute(
                "SELECT fetched_at, payload FROM repository_state WHERE organization = ? AND repository = ?",
                (organization, repository),
            ).fetchone()
        if row is None:
            return None
        fetched_at, payload = row
        return StoredRepositoryState(
            fetched_at=datetime.fromisoformat(fetched_at),
            state=RepositoryInventoryItem.model_validate_json(payload),
        )
    except (Error, OSError, ValidationError, ValueError) as exception:
        message = f"could not read collection cache: {exception}"
        raise StorageError(message) from exception
