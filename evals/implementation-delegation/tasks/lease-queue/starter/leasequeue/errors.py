class QueueError(ValueError):
    """Base class for public leasequeue errors."""


class InvalidGraph(QueueError):
    """Raised when job definitions do not form a valid dependency graph."""


class InvalidTransition(QueueError):
    """Raised when a queue state transition is not currently valid."""


class InvalidSnapshot(QueueError):
    """Raised when persisted queue state is malformed or inconsistent."""
