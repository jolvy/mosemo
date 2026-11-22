"""Inputs of the label suggestion engine evaluation (Plan #309).

``snapshot`` fixes the account's closed detailed segments in a period as seen at
``--as-of``; ``cases`` applies persona rules to them for the gold label and
rebuilds the few-shot examples each case would have had at its time. It never
reads the current time. Both outputs hold personal activity, so they are not
written inside the repository.

Usage: ``python scripts/label_eval_cases.py {snapshot,cases} --help``.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from mosemo.labeling.application.proposals import MAX_EXAMPLES, summarize_context
from mosemo.observation.bootstrap import create_observation_timeline, map_tables
from mosemo.observation.public import (
    ActivityTimelineKind,
    BrowserWebContext,
    CapturedString,
    CapturedWindow,
    DetailedActivityContext,
    ObservedSegment,
)
from mosemo.platform.database.config import DatabaseSettings
from mosemo.platform.database.transactions import read_snapshot

type Row = dict[str, object]
type Fields = dict[str, str | None]

EXIT_USAGE = 2
JSON_WHITESPACE = " \t\r"
MAX_VALUE_LENGTH = 256
NULL_LABEL = "null"
RESERVED_LABELS = frozenset({NULL_LABEL, "none"})
MIN_LABELS = 2
MAX_LABELS = 254
OWNER = "owner"
FIELD_KEYS = ("app_bundle_id", "app_name", "window_title", "tab_title", "web_host")
ACTIVITY_KEYS = frozenset(
    {"activity_id", "account_id", "version", "ended_at", "activity", "fields"}
)
COLD_START_EXAMPLES = 3
CONDITION_KEYS = ("app_bundle_id", "app_name", "web_host_suffix", "title_contains_any")
_context_adapter = TypeAdapter(DetailedActivityContext)


class InputError(Exception):
    """An argument or input file the command rejects; nothing is written."""


# Snapshot ---------------------------------------------------------------------


def context_fields(context: dict[str, object]) -> Fields:
    """The values ``summarize_context`` shows, by key; ``None`` where it shows none."""
    detailed = _context_adapter.validate_python(context)
    web = detailed.web if isinstance(detailed.web, BrowserWebContext) else None
    window_title = (
        detailed.window.title.value[:MAX_VALUE_LENGTH]
        if isinstance(detailed.window, CapturedWindow)
        else None
    )
    return {
        "app_bundle_id": _captured(detailed.app.bundle_id),
        "app_name": _captured(detailed.app.name),
        "window_title": window_title,
        "tab_title": None if web is None else _captured(web.tab_title),
        "web_host": None if web is None else _host(web.url),
    }


def _captured(value: object) -> str | None:
    if isinstance(value, CapturedString):
        return value.value[:MAX_VALUE_LENGTH]
    return None


def _host(url: object) -> str | None:
    if not isinstance(url, CapturedString):
        return None
    try:
        host = urlsplit(url.value).hostname
    except ValueError:
        return None
    return host[:MAX_VALUE_LENGTH] if host else None


def snapshot_rows(
    segments: Iterable[ObservedSegment], *, start: datetime, end: datetime
) -> list[Row]:
    """Closed detailed segments ending in ``[start, end)``, by end then activity."""
    closed = [
        (segment.ended_at, segment)
        for segment in segments
        if segment.kind is ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
        and segment.ended_at is not None
        and start <= segment.ended_at < end
    ]
    closed.sort(key=lambda pair: (pair[0], pair[1].first_event_id))
    rows: list[Row] = []
    for ended_at, segment in closed:
        assert segment.context is not None
        rows.append(
            {
                "activity_id": str(segment.first_event_id),
                "account_id": str(segment.account_id),
                "version": segment.version,
                "ended_at": _timestamp(ended_at),
                "activity": summarize_context(segment.context),
                "fields": context_fields(segment.context),
            }
        )
    return rows


async def read_activities(
    db_session: AsyncSession,
    account_id: UUID,
    *,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> list[Row]:
    """The snapshot rows read in one read-only transaction at ``as_of``."""
    timeline = create_observation_timeline()
    async with read_snapshot(db_session):
        segments = await timeline.read_range(
            db_session, account_id, start=start, end=end, now=as_of
        )
    return snapshot_rows(segments, start=start, end=end)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# Personas ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    """Conditions that must all hold; each list matches when any value does."""

    label: str | None
    app_bundle_id: tuple[str, ...] | None = None
    app_name: tuple[str, ...] | None = None
    web_host_suffix: tuple[str, ...] | None = None
    title_contains_any: tuple[str, ...] | None = None

    def matches(self, fields: Fields) -> bool:
        return (
            _is_one_of(self.app_bundle_id, fields["app_bundle_id"])
            and _is_one_of(self.app_name, fields["app_name"])
            and _host_matches(self.web_host_suffix, fields["web_host"])
            and _title_matches(
                self.title_contains_any,
                (fields["window_title"], fields["tab_title"]),
            )
        )


def _is_one_of(values: tuple[str, ...] | None, value: str | None) -> bool:
    return values is None or (value is not None and value in values)


def _host_matches(suffixes: tuple[str, ...] | None, host: str | None) -> bool:
    """A suffix matches the host itself or a subdomain of it, ignoring case."""
    if suffixes is None:
        return True
    if host is None:
        return False
    host = host.casefold()
    return any(
        host == suffix.casefold() or host.endswith(f".{suffix.casefold()}")
        for suffix in suffixes
    )


def _title_matches(
    needles: tuple[str, ...] | None, titles: tuple[str | None, str | None]
) -> bool:
    if needles is None:
        return True
    texts = [title.casefold() for title in titles if title is not None]
    return any(needle.casefold() in text for needle in needles for text in texts)


@dataclass(frozen=True, slots=True)
class Persona:
    """One virtual user: labels and rules where the first match wins."""

    name: str
    labels: tuple[str, ...]
    rules: tuple[Rule, ...]

    def label_id(self, label: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"mosemo-label-eval/{self.name}/{label}"))

    def gold(self, fields: Fields) -> str | None:
        """The label name of the first matching rule, ``None`` when none matches."""
        for rule in self.rules:
            if rule.matches(fields):
                return rule.label
        return None


def parse_persona(data: dict[str, object], *, with_rules: bool = True) -> Persona:
    """A persona from TOML data; ``with_rules=False`` reads the owner label names.

    The owner label names file has no rules and its persona is always ``owner``.
    """
    allowed = {"name", "labels", "rules"} if with_rules else {"labels"}
    _reject_unknown(data, allowed, where="persona")
    name = data.get("name") if with_rules else OWNER
    if not isinstance(name, str) or not name or "/" in name:
        raise InputError("persona name must be a non-empty string without '/'")
    labels = _labels(data.get("labels"))
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise InputError("rules must be an array of tables")
    rules = tuple(
        _rule(raw, labels, index=index) for index, raw in enumerate(raw_rules)
    )
    return Persona(name=name, labels=labels, rules=rules)


def load_persona(path: Path, *, with_rules: bool = True) -> Persona:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return parse_persona(data, with_rules=with_rules)
    except (OSError, tomllib.TOMLDecodeError, RecursionError, InputError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def _reject_unknown(data: dict[str, object], allowed: set[str], *, where: str) -> None:
    if unknown := sorted(set(data) - allowed):
        raise InputError(f"unknown {where} keys: {', '.join(unknown)}")


def _labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InputError("labels must be an array of strings")
    labels = tuple(cast(list[str], value))
    if reserved := sorted(RESERVED_LABELS.intersection(labels)):
        raise InputError(f"reserved label names: {', '.join(reserved)}")
    if len(set(labels)) != len(labels):
        raise InputError("duplicate label names")
    if not MIN_LABELS <= len(labels) <= MAX_LABELS:
        raise InputError(f"labels must number {MIN_LABELS} to {MAX_LABELS}")
    return labels


def _rule(raw: object, labels: tuple[str, ...], *, index: int) -> Rule:
    if not isinstance(raw, dict):
        raise InputError(f"rule {index} must be a table")
    data = cast(dict[str, object], raw)
    _reject_unknown(data, {*CONDITION_KEYS, "label"}, where=f"rule {index}")
    label = data.get("label")
    if not isinstance(label, str) or (label != NULL_LABEL and label not in labels):
        raise InputError(f"rule {index} label is not in labels: {label!r}")
    conditions = {
        key: _strings(data[key], key) for key in CONDITION_KEYS if key in data
    }
    if not conditions:
        raise InputError(f"rule {index} has no condition")
    return Rule(label=None if label == NULL_LABEL else label, **conditions)


def _strings(value: object, key: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and v for v in value)
    ):
        raise InputError(f"{key} must be a non-empty array of non-empty strings")
    return tuple(cast(list[str], value))


# Cases ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Labeled:
    activity: Row
    label: str | None

    @property
    def activity_id(self) -> str:
        return cast(str, self.activity["activity_id"])

    @property
    def fields(self) -> Fields:
        return cast(Fields, self.activity["fields"])


def build_cases(
    activities: Sequence[Row],
    personas: Sequence[Persona],
    *,
    owner: tuple[Persona, dict[str, str | None]] | None = None,
) -> list[Row]:
    """Cases of every persona, then the owner's, each in snapshot order.

    ``owner`` pairs the owner label names with the owner labels by activity ID.
    """
    ordered = sorted(
        activities,
        key=lambda row: (
            datetime.fromisoformat(cast(str, row["ended_at"])),
            row["activity_id"],
        ),
    )
    labeled = {
        persona.name: [
            _Labeled(row, persona.gold(cast(Fields, row["fields"]))) for row in ordered
        ]
        for persona in personas
    }
    disagree = {
        row["activity_id"]
        for index, row in enumerate(ordered)
        if len({labeled[persona.name][index].label for persona in personas}) > 1
    }
    cases: list[Row] = []
    for persona in personas:
        cases.extend(_persona_cases(persona, labeled[persona.name], disagree=disagree))
    if owner is not None:
        persona, labels = owner
        owned = [
            _Labeled(row, labels[cast(str, row["activity_id"])])
            for row in ordered
            if row["activity_id"] in labels
        ]
        cases.extend(_persona_cases(persona, owned, disagree=set()))
    return cases


def _persona_cases(
    persona: Persona, labeled: Sequence[_Labeled], *, disagree: set[object]
) -> list[Row]:
    candidates = [
        {"label_id": persona.label_id(label), "name": label} for label in persona.labels
    ]
    cases: list[Row] = []
    for index, current in enumerate(labeled):
        # Strictly earlier cases only, latest first, like the production retriever.
        examples = labeled[max(0, index - MAX_EXAMPLES) : index][::-1]
        cases.append(
            {
                "case_id": f"{persona.name}/{current.activity_id}",
                "persona": persona.name,
                "activity_id": current.activity_id,
                "activity": current.activity["activity"],
                "fields": current.fields,
                "labels": candidates,
                "examples": [
                    {
                        "activity_id": example.activity_id,
                        "activity": example.activity["activity"],
                        "label_id": _label_id(persona, example.label),
                    }
                    for example in examples
                ],
                "gold": _label_id(persona, current.label),
                "tags": _tags(
                    current, examples, disagrees=current.activity_id in disagree
                ),
            }
        )
    return cases


def _tags(
    current: _Labeled, examples: Sequence[_Labeled], *, disagrees: bool
) -> list[str]:
    host = current.fields["web_host"]
    tags: list[str] = []
    if len(examples) < COLD_START_EXAMPLES:
        tags.append("cold_start")
    if current.label is None:
        tags.append("gold_null")
    if host is not None and host not in {e.fields["web_host"] for e in examples}:
        tags.append("new_host")
    if disagrees:
        tags.append("personas_disagree")
    return tags


def _label_id(persona: Persona, label: str | None) -> str | None:
    return None if label is None else persona.label_id(label)


def owner_labels(
    rows: Iterable[Row], persona: Persona, *, activity_ids: set[str]
) -> dict[str, str | None]:
    """Owner labels by activity ID; ``"null"`` marks an abstention."""
    labels: dict[str, str | None] = {}
    for row in rows:
        _reject_unknown(row, {"activity_id", "label"}, where="owner label")
        activity_id, label = row.get("activity_id"), row.get("label")
        if not isinstance(activity_id, str) or activity_id not in activity_ids:
            raise InputError(f"owner label for unknown activity: {activity_id!r}")
        if activity_id in labels:
            raise InputError(f"duplicate owner label for activity {activity_id}")
        if not isinstance(label, str) or (
            label != NULL_LABEL and label not in persona.labels
        ):
            raise InputError(f"owner label is not in labels: {label!r}")
        labels[activity_id] = None if label == NULL_LABEL else label
    return labels


# Files ------------------------------------------------------------------------


def encode_jsonl(rows: Iterable[Row]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        for row in rows
    )


def write_jsonl(path: Path, rows: Iterable[Row]) -> None:
    """Write ``path`` and ``{path}.sha256`` (``sha256sum`` format), owner-only.

    Both go to new temporary files first and then replace the targets, so a
    symlink at either name is replaced rather than followed. If the digest
    cannot take its place, the new output is removed: no output is left
    without its matching digest.
    """
    content = encode_jsonl(rows)
    digest = f"{hashlib.sha256(content).hexdigest()}  {path.name}\n".encode()
    digest_path = Path(f"{path}.sha256")
    temporary: list[Path] = []
    try:
        temporary.append(_private_temporary(path, content))
        temporary.append(_private_temporary(path, digest))
        os.replace(temporary[0], path)
        try:
            os.replace(temporary[1], digest_path)
        except OSError:
            path.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise InputError(f"{path}: {exc}") from exc
    finally:
        for leftover in temporary:
            leftover.unlink(missing_ok=True)


def _private_temporary(path: Path, content: bytes) -> Path:
    """A new 0600 file next to ``path`` holding ``content``."""
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
    except OSError:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def read_jsonl(path: Path) -> list[Row]:
    # Split on "\n" only: unescaped U+2028 and similar inside titles are data.
    # A line of JSON whitespace only, "\r" of CRLF included, is skipped.
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").split("\n")
            if line.strip(JSON_WHITESPACE)
        ]
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"{path}: {exc}") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise InputError(f"{path}: every line must be a JSON object")
    return rows


def read_activity_rows(path: Path) -> list[Row]:
    """Snapshot rows, each with the snapshot keys and a timezone-aware end."""
    rows = read_jsonl(path)
    for number, row in enumerate(rows, start=1):
        fields = row.get("fields")
        if (
            set(row) != ACTIVITY_KEYS
            or not isinstance(row["activity_id"], str)
            or not isinstance(row["activity"], str)
            or not isinstance(row["ended_at"], str)
            or not isinstance(fields, dict)
            or set(fields) != set(FIELD_KEYS)
            or not all(v is None or isinstance(v, str) for v in fields.values())
        ):
            raise InputError(f"{path}:{number}: not a snapshot row")
        try:
            _aware_time(row["ended_at"])
        except argparse.ArgumentTypeError as exc:
            raise InputError(f"{path}:{number}: ended_at {exc}") from exc
    return rows


def _output_path(value: str) -> Path:
    """Refuse a path in any git work tree, this repository's checkouts included."""
    path = Path(value)
    # The write replaces a symlink at the path, so check where the link sits.
    directory = path.parent.resolve()
    if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
        raise argparse.ArgumentTypeError(
            f"holds personal activity; write it outside any git work tree: {value}"
        )
    return path


def _aware_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"timezone required: {value}")
    return parsed


# CLI --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build label evaluation inputs.")
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser(
        "snapshot", help="fix closed detailed segments from the DB_* database"
    )
    snapshot.add_argument("--account-id", type=UUID, required=True)
    snapshot.add_argument("--from", dest="start", type=_aware_time, required=True)
    snapshot.add_argument("--to", dest="end", type=_aware_time, required=True)
    snapshot.add_argument("--as-of", type=_aware_time, required=True)
    snapshot.add_argument("--out", type=_output_path, required=True)
    cases = commands.add_parser("cases", help="build cases from a snapshot")
    cases.add_argument("--activities", type=Path, required=True)
    cases.add_argument("--persona", type=Path, action="append", required=True)
    cases.add_argument("--owner-labels", type=Path)
    cases.add_argument("--owner-label-names", type=Path)
    cases.add_argument("--out", type=_output_path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; rejected arguments or inputs exit with 2 and write nothing."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            return _snapshot(args)
        return _cases(args)
    except InputError as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return EXIT_USAGE


def _snapshot(args: argparse.Namespace) -> int:
    if not args.end <= args.as_of:
        raise InputError("--to must not be after --as-of")
    map_tables()
    rows = asyncio.run(
        _read(
            DatabaseSettings().database.dsn,
            args.account_id,
            start=args.start,
            end=args.end,
            as_of=args.as_of,
        )
    )
    write_jsonl(args.out, rows)
    print(f"wrote {len(rows)} activities to {args.out}")
    return 0


async def _read(
    dsn: str, account_id: UUID, *, start: datetime, end: datetime, as_of: datetime
) -> list[Row]:
    engine = create_async_engine(dsn)
    try:
        async with AsyncSession(engine) as db_session:
            return await read_activities(
                db_session, account_id, start=start, end=end, as_of=as_of
            )
    finally:
        await engine.dispose()


def _cases(args: argparse.Namespace) -> int:
    if (args.owner_labels is None) != (args.owner_label_names is None):
        raise InputError("--owner-labels and --owner-label-names go together")
    activities = read_activity_rows(args.activities)
    activity_ids = [cast(str, row["activity_id"]) for row in activities]
    if len(set(activity_ids)) != len(activity_ids):
        raise InputError("duplicate activity_id in --activities")
    personas = [load_persona(path) for path in args.persona]
    names = [persona.name for persona in personas]
    if OWNER in names or len(set(names)) != len(names):
        raise InputError(f"persona names must be unique and not {OWNER!r}")
    owner = None
    if args.owner_label_names is not None:
        owner_persona = load_persona(args.owner_label_names, with_rules=False)
        labels = owner_labels(
            read_jsonl(args.owner_labels), owner_persona, activity_ids=set(activity_ids)
        )
        owner = (owner_persona, labels)
    cases = build_cases(activities, personas, owner=owner)
    write_jsonl(args.out, cases)
    print(f"wrote {len(cases)} cases to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
