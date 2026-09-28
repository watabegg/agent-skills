from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobSpec:
    id: str
    deps: tuple[str, ...] = ()
    priority: int = 0
    max_attempts: int = 3


@dataclass(frozen=True, slots=True)
class Lease:
    job_id: str
    token: str
    worker_id: str
    deadline: float
    attempt: int


@dataclass(frozen=True, slots=True)
class JobView:
    id: str
    status: str
    attempts: int
    lease: Lease | None = None
    blocked_by: tuple[str, ...] = ()


def normalize_specs(raw_specs):
    """Return a deterministic mapping of canonical job IDs to JobSpec values."""
    raise NotImplementedError
