"""Pure schema-version migration primitives for HLoop state.

The legacy CLI owns file locking, backups, and atomic writes.  This module owns
only deterministic in-memory migration: it finds a contiguous revision path,
applies every step to a deep copy, and returns the resulting state.  Callers can
therefore use the same API for dry-run planning and for the mutation performed
after their external safety checks have passed.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


State = Mapping[str, Any]
MigrationTransform = Callable[[dict[str, Any]], Mapping[str, Any]]


class MigrationError(ValueError):
    """Base error for invalid schemas, migration definitions, or paths."""


class FutureSchemaError(MigrationError):
    """Raised when state is newer than the runtime target."""


class MissingMigrationError(MigrationError):
    """Raised when no contiguous migration path reaches the target."""


@dataclass(frozen=True, order=True, slots=True)
class SchemaVersion:
    """A state format and its monotonically increasing in-format revision."""

    format_version: int
    revision: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.format_version, bool)
            or not isinstance(self.format_version, int)
            or self.format_version < 1
        ):
            raise MigrationError("format_version must be a positive integer")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise MigrationError("revision must be a non-negative integer")

    def label(self) -> str:
        """Return a stable label suitable for diagnostics and audit output."""

        return f"format-{self.format_version}.revision-{self.revision}"


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """One declared edge in the schema revision graph.

    A step must advance exactly one revision within a format, or enter the next
    format at revision zero.  The transform changes content only; the migration
    engine owns ``state_format_version`` and ``schema_revision`` markers.
    """

    source: SchemaVersion
    target: SchemaVersion
    name: str
    transform: MigrationTransform = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, SchemaVersion) or not isinstance(
            self.target, SchemaVersion
        ):
            raise MigrationError("migration source and target must be SchemaVersion values")
        if not isinstance(self.name, str) or not self.name.strip():
            raise MigrationError("migration step name must not be empty")
        same_format_increment = (
            self.target.format_version == self.source.format_version
            and self.target.revision == self.source.revision + 1
        )
        next_format = (
            self.target.format_version == self.source.format_version + 1
            and self.target.revision == 0
        )
        if not (same_format_increment or next_format):
            raise MigrationError(
                "migration steps must advance one revision or enter the next "
                "format at revision zero"
            )
        if not callable(self.transform):
            raise MigrationError("migration transform must be callable")


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A validated, ordered migration path with no state mutation."""

    source: SchemaVersion
    target: SchemaVersion
    steps: tuple[MigrationStep, ...]

    @property
    def changed(self) -> bool:
        return self.source != self.target

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """The migrated copy and the exact plan used to produce it."""

    state: Mapping[str, Any]
    plan: MigrationPlan

    @property
    def changed(self) -> bool:
        return self.plan.changed

    @property
    def applied_steps(self) -> tuple[str, ...]:
        return self.plan.step_names


def _integer_marker(state: State, key: str, default: int) -> int:
    value = state.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MigrationError(f"{key} must be an integer")
    return value


def schema_version_of(state: State) -> SchemaVersion:
    """Read a schema version without modifying ``state``.

    Pre-revision states are revision zero.  This keeps format-2 fixtures and
    early format-3 development snapshots representable by the same chain.
    """

    if not isinstance(state, Mapping):
        raise MigrationError("state must be a mapping")
    format_version = _integer_marker(state, "state_format_version", 1)
    revision = _integer_marker(state, "schema_revision", 0)
    return SchemaVersion(format_version, revision)


def _step_index(steps: Sequence[MigrationStep]) -> dict[SchemaVersion, MigrationStep]:
    index: dict[SchemaVersion, MigrationStep] = {}
    names: set[str] = set()
    for step in steps:
        if step.source in index:
            raise MigrationError(f"duplicate migration source: {step.source.label()}")
        if step.name in names:
            raise MigrationError(f"duplicate migration step name: {step.name}")
        index[step.source] = step
        names.add(step.name)
    return index


def plan_schema_migration(
    state: State,
    *,
    target: SchemaVersion,
    steps: Sequence[MigrationStep],
) -> MigrationPlan:
    """Return the unique contiguous path from ``state`` to ``target``.

    Future formats/revisions are rejected rather than silently downgraded.
    Missing or overshooting edges are also rejected, so a caller cannot skip an
    intermediate development revision by accident.
    """

    source = schema_version_of(state)
    if source > target:
        raise FutureSchemaError(
            f"state {source.label()} is newer than runtime {target.label()}"
        )
    index = _step_index(steps)
    current = source
    path: list[MigrationStep] = []
    visited: set[SchemaVersion] = set()
    while current != target:
        if current in visited:
            raise MissingMigrationError(f"migration cycle at {current.label()}")
        visited.add(current)
        step = index.get(current)
        if step is None:
            raise MissingMigrationError(
                f"missing migration from {current.label()} to {target.label()}"
            )
        if step.target > target:
            raise MissingMigrationError(
                f"migration {step.name} overshoots target {target.label()}"
            )
        path.append(step)
        current = step.target
    return MigrationPlan(source=source, target=target, steps=tuple(path))


def migrate_schema(
    state: State,
    *,
    target: SchemaVersion,
    steps: Sequence[MigrationStep],
) -> MigrationResult:
    """Apply a validated revision chain to a deep copy of ``state``.

    The input remains byte-for-byte serializable to its original value even if
    a transform mutates its argument before returning it.  A transform may not
    forge schema markers; those markers are written after each successful step.
    """

    plan = plan_schema_migration(state, target=target, steps=steps)
    working: dict[str, Any] = deepcopy(dict(state))
    for step in plan.steps:
        candidate_input = deepcopy(working)
        candidate = step.transform(candidate_input)
        if not isinstance(candidate, Mapping):
            raise MigrationError(f"migration {step.name} did not return a mapping")
        migrated = deepcopy(dict(candidate))
        declared = schema_version_of(migrated)
        if declared not in {step.source, step.target}:
            raise MigrationError(
                f"migration {step.name} forged schema marker {declared.label()}"
            )
        migrated["state_format_version"] = step.target.format_version
        migrated["schema_revision"] = step.target.revision
        working = migrated
    return MigrationResult(state=working, plan=plan)
