#!/usr/bin/env python3
"""Turn a merge-gate survey into the `teams:` section of a configuration file.

Reads the JSON that `survey_merge_gate_access.py` writes, selects the repositories worth
configuring, groups them by owning team, and prints YAML on stdout. Everything a human reads goes
to stderr, so stdout can be redirected or pasted straight into a configuration file.

    uv run scripts/survey_to_teams.py survey.json > teams.yml

WHAT IT SELECTS. By default the repositories that answered `ruleset` and are not archived: rulesets
are readable with ordinary repository access, which is the whole reason those repositories can be
assessed at all (architecture.md, "Access"), and an archived repository cannot receive a merge.
`--gate` takes any of the five answers, so `--gate ruleset classic-full` is the population this
token can read in full, and `--gate classic-flag` is the blind spot — configurable, but every
governance question about it assesses as `cannot_assess`.

HOW IT GROUPS, AND WHAT A HUMAN STILL HAS TO DECIDE. One team per distinct FIRST owning team, which
is the alphabetically-first handle in that repository's CODEOWNERS. This is a starting point and not
an answer, because the configuration requires each repository to belong to exactly one team and
CODEOWNERS does not describe teams that neatly:

  * two unrelated services that happen to share a lead handle are grouped into one team, and one
    service whose repositories name different leads is split across two;
  * `display_name` is derived from the slug, so it is only as good as the slug — a programme name
    like "Financial Remedy" cannot be recovered from `developer-enablement`;
  * `github_team_slugs` carries the grouping handle alone unless `--all-slugs` is given, though
    every owner the survey saw is reported on stderr.

The generated file is therefore a draft to be edited, and the notes in hmcts-small.yml are what
that editing looks like. What is NOT left to judgement: each repository appears under exactly one
team and every identifier is unique, both of which `metrics.config` rejects a file for, and the
output is read back and validated against `TeamConfiguration` before this script exits.

REPOSITORIES WITH NO OWNER are grouped under `unowned` rather than dropped: an unowned repository on
rulesets is still assessable, and which team it belongs to is a question for a human and not for
this script. `--drop-unowned` leaves them out instead. A repository whose CODEOWNERS could not be
READ is a different case and is never treated as unowned — it is reported and left out, because
this script cannot tell whose it is and must not guess.
"""

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from metrics.config import TeamConfiguration

GATES = ("ruleset", "classic-full", "classic-flag", "unprotected", "error")
UNOWNED_IDENTIFIER = "unowned"
UNOWNED_DISPLAY_NAME = "Unowned (no CODEOWNERS)"

# Slug words that are names rather than words, and read wrongly title-cased. Only the ones actually
# seen in hmcts team handles: a longer list would be guessing at other organisations' initialisms.
ACRONYMS = frozenset(
    {
        "ai",
        "amp",
        "api",
        "ccd",
        "cft",
        "cnp",
        "dtsse",
        "hmcts",
        "idam",
        "moj",
        "opal",
        "pcs",
        "rse",
        "sds",
        "sscs",
        "ui",
        "xui",
    },
)

# A plain YAML scalar: starts with a letter, single-spaced, and nothing needing an escape. Anything
# else is quoted, because a repository named `no` or `123` is a boolean or an integer unquoted.
PLAIN_SCALAR_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._()-]*(?: [A-Za-z0-9._()-]+)*")
YAML_KEYWORDS = frozenset({"true", "false", "yes", "no", "on", "off", "null", "none", "y", "n", "~"})


@dataclass(frozen=True)
class Options:
    """What to select, and how to render it."""

    survey: Path
    gates: tuple[str, ...]
    include_archived: bool
    all_slugs: bool
    items_only: bool
    drop_unowned: bool


@dataclass(frozen=True)
class Team:
    """One team as the configuration file describes it."""

    identifier: str
    display_name: str
    github_team_slugs: tuple[str, ...]
    repositories: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    """The survey rows worth configuring, and a count of every row that was left out."""

    rows: tuple[Mapping[str, Any], ...]
    excluded: Counter[str]


def progress(message: str) -> None:
    """Write a line for the human watching, never onto the YAML on stdout."""
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def load_survey(path: Path) -> list[Mapping[str, Any]]:
    """Read a survey, rejecting anything that is not the JSON array the survey script writes."""
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as failure:
        sys.exit(f"could not read {path}: {failure}")
    except ValueError as failure:
        sys.exit(f"{path} is not valid JSON ({failure}) — an interrupted survey may need its closing ']'")
    if not isinstance(body, list):
        sys.exit(f"{path} must hold a JSON array of survey records, as survey_merge_gate_access.py writes")
    rows = [row for row in body if isinstance(row, Mapping) and isinstance(row.get("repository"), str)]
    if not rows:
        sys.exit(f"{path} holds no survey records")
    return rows


def owners_of(row: Mapping[str, Any]) -> tuple[str, ...]:
    """List the teams a survey row names as owners, in the order the survey sorted them."""
    owners = row.get("owning_teams")
    return tuple(str(owner) for owner in owners) if isinstance(owners, list) else ()


def select(rows: Iterable[Mapping[str, Any]], options: Options) -> Selection:
    """Keep the rows worth configuring, counting what each rule left out and why."""
    kept: list[Mapping[str, Any]] = []
    excluded: Counter[str] = Counter()
    for row in rows:
        if str(row.get("gate")) not in options.gates:
            excluded[f"gate is not {'/'.join(options.gates)}"] += 1
        elif row.get("archived") is True and not options.include_archived:
            excluded["archived"] += 1
        elif not owners_of(row) and row.get("codeowners") != "absent":
            # No owners AND no file is an unowned repository. No owners because the file could not
            # be read is a repository whose team is unknown, and guessing it is not this script's
            # business — see the module docstring.
            excluded[f"owner unknown (codeowners: {row.get('codeowners')})"] += 1
        elif not owners_of(row) and options.drop_unowned:
            excluded["unowned"] += 1
        else:
            kept.append(row)
    return Selection(tuple(kept), excluded)


def display_name_for(slug: str) -> str:
    """Make a readable name out of a team slug, upper-casing the words that are initialisms."""
    words = [word for word in re.split(r"[-_./]+", slug) if word]
    return " ".join(word.upper() if word.casefold() in ACRONYMS else word.capitalize() for word in words)


def identifier_for(slug: str, taken: Iterable[str]) -> str:
    """Make a unique identifier out of a team slug, since the configuration requires uniqueness."""
    base = slug.replace("/", "-")
    identifier = base
    suffix = 2
    while identifier in set(taken):
        identifier = f"{base}-{suffix}"
        suffix += 1
    return identifier


def group(selection: Selection, options: Options) -> tuple[tuple[Team, ...], Counter[str]]:
    """Group the selected rows into teams by their first owner, and count the owners not emitted."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in selection.rows:
        owners = owners_of(row)
        grouped.setdefault(owners[0] if owners else UNOWNED_IDENTIFIER, []).append(row)

    teams: list[Team] = []
    unemitted: Counter[str] = Counter()
    for slug in sorted(grouped):
        rows = grouped[slug]
        unowned = slug == UNOWNED_IDENTIFIER
        observed = sorted({owner for row in rows for owner in owners_of(row)})
        if not options.all_slugs and len(observed) > 1:
            unemitted[slug] = len(observed) - 1
        slugs = () if unowned else tuple(observed) if options.all_slugs else (slug,)
        teams.append(
            Team(
                identifier=identifier_for(UNOWNED_IDENTIFIER if unowned else slug, [team.identifier for team in teams]),
                display_name=UNOWNED_DISPLAY_NAME if unowned else display_name_for(slug),
                github_team_slugs=slugs,
                repositories=tuple(sorted(str(row["repository"]) for row in rows)),
            ),
        )
    # Sorted by identifier because `owned_repositories` reports in that order anyway, so a file
    # written this way diffs against the report instead of against the survey's push order.
    return tuple(sorted(teams, key=lambda team: team.identifier)), unemitted


def scalar(value: str) -> str:
    """Render a YAML scalar, quoting only where a plain one would not read back as this string."""
    plain = PLAIN_SCALAR_PATTERN.fullmatch(value) is not None and value.casefold() not in YAML_KEYWORDS
    # A JSON string is a valid double-quoted YAML scalar, and escapes exactly what needs escaping.
    return value if plain else json.dumps(value)


def render(teams: Sequence[Team]) -> str:
    """Render the teams as the list under a `teams:` key, in the layout the config files use."""
    blocks = []
    for team in teams:
        lines = [f"  - identifier: {scalar(team.identifier)}", f"    display_name: {scalar(team.display_name)}"]
        if team.github_team_slugs:
            lines.append("    github_team_slugs:")
            lines.extend(f"      - {scalar(slug)}" for slug in team.github_team_slugs)
        lines.append("    repositories:")
        lines.extend(f"      - {scalar(repository)}" for repository in team.repositories)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def validate(document: str, teams: Sequence[Team]) -> None:
    """Read the generated YAML back and check it against the model that will have to load it.

    A generator whose output is a configuration file should not be the reason a configuration file
    is wrong. Reading it back catches both halves of that: a quoting slip that turns a repository
    named `no` into `False`, and a team the tool's own model would reject.
    """
    parsed = yaml.safe_load(document)
    entries = parsed.get("teams") if isinstance(parsed, Mapping) else None
    if not isinstance(entries, list) or len(entries) != len(teams):
        sys.exit("generated YAML did not read back as the teams that went into it")
    for entry, team in zip(entries, teams, strict=True):
        try:
            model = TeamConfiguration.model_validate(entry)
        except ValidationError as failure:
            sys.exit(f"generated team {team.identifier} is not valid configuration: {failure}")
        if model != TeamConfiguration.model_validate(vars(team)):
            sys.exit(f"generated YAML for {team.identifier} did not read back unchanged")


def report(selection: Selection, teams: Sequence[Team], unemitted: Counter[str], options: Options) -> None:
    """Say what was selected, what was left out, and what a human still has to decide."""
    configured = sum(len(team.repositories) for team in teams)
    progress(f"selected      {configured} repositories in {len(teams)} teams, gate {'/'.join(options.gates)}")
    for reason, count in sorted(selection.excluded.items(), key=lambda item: (-item[1], item[0])):
        progress(f"  left out    {count:>5}  {reason}")
    for slug, count in sorted(unemitted.items()):
        progress(f"  {slug}: {count} further owner(s) observed and not emitted; --all-slugs emits them")
    progress(
        "\nGrouping is by first CODEOWNERS handle, and display names are derived from slugs: both are"
        "\na draft for a human to correct. Every repository appears under exactly one team, and the"
        "\noutput has been validated against metrics.config.TeamConfiguration.",
    )


def parse_arguments(argv: Sequence[str] | None) -> Options:
    """Read the command line."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("survey", type=Path, help="JSON written by survey_merge_gate_access.py")
    parser.add_argument(
        "--gate",
        dest="gates",
        nargs="+",
        choices=GATES,
        default=["ruleset"],
        help="which merge-gate answers to configure (default: ruleset)",
    )
    parser.add_argument("--include-archived", action="store_true", help="keep archived repositories")
    parser.add_argument("--all-slugs", action="store_true", help="emit every owner observed, not just the grouping one")
    parser.add_argument("--items-only", action="store_true", help="omit the `teams:` key, for pasting into a file")
    parser.add_argument("--drop-unowned", action="store_true", help="leave out repositories that name no owner")
    parsed = parser.parse_args(argv)
    return Options(
        survey=parsed.survey,
        gates=tuple(parsed.gates),
        include_archived=parsed.include_archived,
        all_slugs=parsed.all_slugs,
        items_only=parsed.items_only,
        drop_unowned=parsed.drop_unowned,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Turn a survey into team configuration on stdout."""
    options = parse_arguments(argv)
    selection = select(load_survey(options.survey), options)
    if not selection.rows:
        sys.exit(f"no repositories in {options.survey} match gate {'/'.join(options.gates)}")
    teams, unemitted = group(selection, options)
    items = render(teams)
    # Validated as the whole document even when only the items are printed: the `teams:` key is what
    # the items will be pasted under, so that is the thing that has to load.
    validate(f"teams:\n{items}\n", teams)
    sys.stdout.write(f"{items}\n" if options.items_only else f"teams:\n{items}\n")
    report(selection, teams, unemitted, options)


if __name__ == "__main__":
    main()
