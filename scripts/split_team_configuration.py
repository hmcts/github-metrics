#!/usr/bin/env python3
"""Split one team configuration into parts small enough for a single collection run to finish.

A collection over 1863 repositories visits each one twice, and the first pass is not cached: it
re-reads every repository's current state on every run, which at HMCTS is 15,690 GitHub calls and
close to two hours before the windowed pass starts. Any run that ends early therefore spends almost
all of its budget on work it has already done, and the windowed pass gets whatever minutes are left.

Splitting the cohort turns one long run into several short ones. The caches a collection fills are
keyed by repository, so collecting a part fills the same rows the whole cohort reads back:

    uv run scripts/split_team_configuration.py hmcts.yml --size 400 --policy config.yml
    uv run metrics collect --config config.yml --config hmcts-part1.yml --from 2026-06-01 --to 2026-09-03
    uv run metrics collect --config config.yml --config hmcts-part2.yml --from 2026-06-01 --to 2026-09-03
    ...
    uv run metrics evidence --config config.yml --config hmcts.yml --from 2026-06-01 --to 2026-09-03

REPORT AGAINST THE UNSPLIT FILE, as the last line above does. A part is a work list for one
collection and not a cohort: reporting one would describe 400 repositories as though they were the
organisation, and every team total would be taken over whichever teams that part happens to hold.

RUN THE PARTS BACK TO BACK, IN ORDER, AND WITHOUT `--hold-anchor`. A part run that finishes confirms
coverage for its own repositories only, and the estate's reporting anchor is the edge most of it is
confirmed to — so the anchor steps forward part way through the sequence, once the parts already
collected are the majority, and the repositories in the parts still to run report as `unavailable` at
every span until theirs lands. The gap is the time between those two runs, which is why the sequence
should be tight; there is no option that avoids it, because nothing confirms an estate it did not
collect.

NOT `--hold-anchor` HERE, despite the temptation. It is for an ad-hoc run over a handful of
repositories. Holding a part leaves that part's repositories on the confirmations they already had,
so holding most of the estate freezes the anchor for good: the final part's 400 confirmations cannot
outvote the 1463 the held runs left behind, and the next cycle cannot either. `docs/architecture.md`
has the ruling.

WHAT A PART HOLDS. Whole teams, in the order the source file lists them, packed up to `--size`
repositories. A team larger than `--size` on its own is sliced across consecutive parts, keeping its
identifier and display name on each slice; nothing else is ever divided, so a part boundary falls
between teams and a reader can find a repository by reading the parts in order. Every other key in
the source file is copied into every part, except `enablement` and `sonar_projects`, which are
narrowed to the repositories that part owns because the configuration rejects an entry naming a
repository no team in the document holds.

PARTS ARE GENERATED AND OVERWRITTEN. Edit the source file and split it again. `--force` is required
to replace parts that already exist, so a hand-edited part cannot be lost to a habit.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from metrics.config import ConfigurationError, TeamConfiguration, load_configuration

REPOSITORY_KEYED_SECTIONS = ("enablement", "sonar_projects")
"""Keys the configuration cross-checks against the repositories the same document owns.

`Configuration.validate_enablement` and `validate_sonar_projects` reject an entry naming a
repository no team holds, so both are narrowed to each part instead of copied whole.
"""


@dataclass(frozen=True)
class Options:
    """What to split, how small, and where to put the result."""

    configuration: Path
    size: int
    output_directory: Path | None
    prefix: str | None
    policies: tuple[Path, ...]
    dry_run: bool
    force: bool


@dataclass(frozen=True)
class Naming:
    """Where the parts are written and what they are called."""

    directory: Path
    prefix: str
    suffix: str

    def path(self, position: int) -> Path:
        """Return the file the part at one position is written to."""
        return self.directory / f"{self.prefix}-part{position}{self.suffix}"

    def existing(self) -> tuple[Path, ...]:
        """Return every part file already on disk under this naming, including a wider split's."""
        return tuple(sorted(self.directory.glob(f"{self.prefix}-part*{self.suffix}")))


@dataclass(frozen=True)
class Part:
    """One part's teams and the file it is written to."""

    path: Path
    teams: tuple[Mapping[str, Any], ...]

    @property
    def repositories(self) -> tuple[str, ...]:
        """Return every repository this part owns, in the order the part lists them."""
        return tuple(repository for team in self.teams for repository in team["repositories"])


class ConfigurationDumper(yaml.SafeDumper):
    """Dump a nested sequence indented under its key, the layout the configuration files use."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:  # noqa: ARG002, FBT001, FBT002
        """Indent a nested sequence, which PyYAML leaves flush with its key by default."""
        super().increase_indent(flow=flow, indentless=False)


def progress(message: str) -> None:
    """Write a line for the human watching, never into a part file."""
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def load_teams(path: Path) -> tuple[dict[str, Any], tuple[Mapping[str, Any], ...]]:
    """Read the source document and its teams, refusing anything this script cannot split.

    The teams are checked here and not left to the parts, because a fault in the source is one
    message about one file: a source holding a team the configuration would reject produces parts
    that are each rejected in turn, and the reader has to work out that all five failures are one.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as failure:
        sys.exit(f"could not read {path}: {failure}")
    if not isinstance(document, dict):
        sys.exit(f"{path} does not hold a YAML mapping, so it is not a configuration file")
    teams = document.get("teams")
    if not isinstance(teams, list) or not teams:
        sys.exit(f"{path} has no teams to split")
    for entry in teams:
        try:
            TeamConfiguration.model_validate(entry)
        except ValidationError as failure:
            sys.exit(f"{path} holds a team this script cannot split: {failure}")
    return document, tuple(teams)


def pack(teams: Sequence[Mapping[str, Any]], size: int) -> Iterator[tuple[Mapping[str, Any], ...]]:
    """Group teams into consecutive parts of at most `size` repositories each.

    A team that fits is kept whole, so most part boundaries fall between teams. One larger than
    `size` is sliced, and its last slice stays open for the teams listed after it to pack into, so
    dividing a team costs at most one part boundary more than the repositories require.
    """
    current: list[Mapping[str, Any]] = []
    count = 0
    for team in teams:
        repositories = tuple(team["repositories"])
        if current and count + len(repositories) > size:
            yield tuple(current)
            current, count = [], 0
        for start in range(0, len(repositories), size):
            held = repositories[start : start + size]
            current.append({**team, "repositories": list(held)})
            count += len(held)
            if count == size:
                yield tuple(current)
                current, count = [], 0
    if current:
        yield tuple(current)


def plan(naming: Naming, teams: Sequence[Mapping[str, Any]], size: int) -> tuple[Part, ...]:
    """Decide what each part holds and what it is called."""
    return tuple(
        Part(path=naming.path(position), teams=grouped) for position, grouped in enumerate(pack(teams, size), start=1)
    )


def part_document(document: Mapping[str, Any], part: Part) -> dict[str, Any]:
    """Build one part's document: its own teams, and the source's other keys narrowed where needed."""
    owned = set(part.repositories)
    narrowed: dict[str, Any] = {}
    for key, value in document.items():
        if key == "teams":
            narrowed[key] = [dict(team) for team in part.teams]
        elif key in REPOSITORY_KEYED_SECTIONS and isinstance(value, Mapping):
            kept = {name: entry for name, entry in value.items() if name in owned}
            if kept:
                narrowed[key] = kept
        else:
            narrowed[key] = value
    return narrowed


def anchor_note(total: int) -> tuple[str, ...]:
    """Say to run the parts back to back and to keep `--hold-anchor` off them, or nothing for one part.

    The estate's reporting anchor is the edge most of it is confirmed to, and a part confirms its own
    repositories only — so the anchor steps forward once the parts already collected are the
    majority, and the parts still to run report as `unavailable` until theirs lands. `--hold-anchor`
    makes that worse rather than better: held repositories keep the confirmations they had, so
    holding most of the estate leaves the anchor where it is for good.

    A SINGLE PART GETS NO NOTE. It is the whole cohort, collected by one run, so the sequence the
    note is about does not exist.
    """
    if total == 1:
        return ()
    return (
        "# RUN THE PARTS BACK TO BACK AND WITHOUT --hold-anchor. A part confirms its own repositories",
        "# only, so the reporting anchor steps forward once the parts collected so far are the",
        "# majority, and the parts still to run report as unavailable at every span until theirs",
        "# lands. Holding a part instead freezes the anchor: held repositories keep the confirmations",
        "# they already had. docs/architecture.md has the ruling.",
        "#",
    )


def header(source: Path, part: Part, position: int, total: int) -> str:
    """Write the comment block at the top of one part, saying what it holds and how to run it."""
    teams = len(part.teams)
    held = f"{len(part.repositories)} repositories in {teams} team{'' if teams == 1 else 's'}"
    return "\n".join(
        (
            f"# Part {position} of {total} of {source.name}: {held}.",
            "#",
            "# Generated by scripts/split_team_configuration.py. Collect each part on its own run:",
            "#",
            f"#   uv run metrics collect --config config.yml --config {part.path.name} --from DATE --to DATE",
            "#",
            *anchor_note(total),
            f"# Report against {source.name} and not against a part: the caches these runs fill are keyed",
            "# by repository, so the whole cohort reads back from them once every part is collected.",
            "#",
            f"# EDITS HERE ARE LOST ON THE NEXT SPLIT. Edit {source.name} and split it again.",
            "",
        ),
    )


def render(document: Mapping[str, Any]) -> str:
    """Render one part's document in the layout the configuration files use."""
    return str(
        yaml.dump(
            dict(document),
            Dumper=ConfigurationDumper,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
            width=120,
        ),
    )


def check_coverage(teams: Sequence[Mapping[str, Any]], parts: Sequence[Part]) -> None:
    """Check the parts hold the source's repositories, each of them exactly once.

    This is what makes the operation a split. A repository dropped here is a repository no run
    collects, and the whole-cohort report would show it as never collected long after every run that
    was meant to cover it had succeeded.
    """
    source = sorted(repository for team in teams for repository in team["repositories"])
    split = sorted(repository for part in parts for repository in part.repositories)
    if source != split:
        missing = sorted(set(source) - set(split))
        extra = sorted(set(split) - set(source))
        sys.exit(f"the split does not cover the source: missing {missing[:5]}, unexpected {extra[:5]}")


def check_targets(naming: Naming, parts: Sequence[Part], options: Options) -> tuple[Path, ...]:
    """Refuse to replace existing parts unless asked to, and return any a wider split left behind.

    A leftover is returned to be reported and not deleted: this script cannot tell a stale part from
    a file somebody named to look like one, and a part nobody collects is a smaller problem than a
    file nobody meant to lose.
    """
    planned = {part.path for part in parts}
    existing = naming.existing()
    clashing = [path.name for path in existing if path in planned]
    if clashing and not options.force:
        sys.exit(f"{', '.join(clashing)} already exist; pass --force to replace them")
    return tuple(path for path in existing if path not in planned)


def write(source: Path, document: Mapping[str, Any], parts: Sequence[Part]) -> None:
    """Write every part."""
    for position, part in enumerate(parts, start=1):
        text = f"{header(source, part, position, len(parts))}\n{render(part_document(document, part))}"
        part.path.write_text(text, encoding="utf-8")


def check_parts_load(parts: Sequence[Part], policies: Sequence[Path]) -> None:
    """Load each written part beside the policy files, so a part is never a collection's surprise."""
    for part in parts:
        try:
            loaded = load_configuration(*policies, part.path)
        except ConfigurationError as failure:
            sys.exit(f"{part.path.name} is not valid configuration: {failure}")
        configured = sum(len(team.repositories) for team in loaded.teams)
        if configured != len(part.repositories):
            sys.exit(f"{part.path.name} loaded {configured} repositories, not the {len(part.repositories)} written")


def report(
    source: Path,
    teams: Sequence[Mapping[str, Any]],
    parts: Sequence[Part],
    leftovers: Sequence[Path],
    options: Options,
) -> None:
    """Say what each part holds, which teams were divided, and what still has to be run."""
    repositories = sum(len(team["repositories"]) for team in teams)
    progress(f"split {source.name}: {repositories} repositories in {len(teams)} teams, at most {options.size} a part")
    for part in parts:
        action = "would write" if options.dry_run else "wrote      "
        teams_held = len(part.teams)
        progress(
            f"  {action} {part.path.name:<24} {len(part.repositories):>5} repositories"
            f" in {teams_held:>3} team{'' if teams_held == 1 else 's'}",
        )
    slices = Counter(team["identifier"] for part in parts for team in part.teams)
    for identifier, count in slices.items():
        if count > 1:
            positions = [
                str(position)
                for position, part in enumerate(parts, start=1)
                if any(team["identifier"] == identifier for team in part.teams)
            ]
            progress(f"  divided    {identifier} across parts {', '.join(positions)}")
    for path in leftovers:
        progress(f"  left over  {path.name} is from an earlier split and is not one of the parts above; delete it")
    if options.dry_run:
        progress("nothing was written: --dry-run")
    elif options.policies:
        progress(f"every part loaded beside {', '.join(str(path) for path in options.policies)}")
    else:
        progress("parts were not loaded back: pass --policy config.yml to read each one through the loader")


def parse_arguments(argv: Sequence[str] | None) -> Options:
    """Read the command line."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("configuration", type=Path, help="the team configuration file to split, such as hmcts.yml")
    parser.add_argument("--size", type=int, default=400, help="most repositories in one part (default: 400)")
    parser.add_argument("--output-directory", type=Path, help="where to write the parts (default: beside the source)")
    parser.add_argument("--prefix", help="name the parts PREFIX-partN.yml (default: the source file's stem)")
    parser.add_argument(
        "--policy",
        dest="policies",
        type=Path,
        action="append",
        metavar="PATH",
        help="a policy file to load each part beside, such as config.yml; repeatable",
    )
    parser.add_argument("--dry-run", action="store_true", help="report the split without writing anything")
    parser.add_argument("--force", action="store_true", help="replace parts that already exist")
    parsed = parser.parse_args(argv)
    if parsed.size < 1:
        parser.error("--size must be at least 1")
    return Options(
        configuration=parsed.configuration,
        size=parsed.size,
        output_directory=parsed.output_directory,
        prefix=parsed.prefix,
        policies=tuple(parsed.policies or ()),
        dry_run=parsed.dry_run,
        force=parsed.force,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Split a team configuration into parts one collection run can finish."""
    options = parse_arguments(argv)
    document, teams = load_teams(options.configuration)
    naming = Naming(
        directory=options.output_directory or options.configuration.parent,
        prefix=options.prefix or options.configuration.stem,
        suffix=options.configuration.suffix,
    )
    parts = plan(naming, teams, options.size)
    check_coverage(teams, parts)
    leftovers: tuple[Path, ...] = ()
    if not options.dry_run:
        naming.directory.mkdir(parents=True, exist_ok=True)
        leftovers = check_targets(naming, parts, options)
        write(options.configuration, document, parts)
        if options.policies:
            check_parts_load(parts, options.policies)
    report(options.configuration, teams, parts, leftovers, options)


if __name__ == "__main__":
    main()
