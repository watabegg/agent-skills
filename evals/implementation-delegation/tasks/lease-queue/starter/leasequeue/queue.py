class LeaseQueue:
    """Deterministic dependency-aware job queue driven by caller-supplied time."""

    def __init__(self, specs):
        raise NotImplementedError

    @classmethod
    def from_raw(cls, raw_specs):
        raise NotImplementedError

    def acquire(self, worker_id, now, lease_seconds, limit=1):
        """Return a tuple of newly issued Lease values."""
        raise NotImplementedError

    def heartbeat(self, job_id, token, now, lease_seconds):
        """Extend one current lease from caller-supplied `now`."""
        raise NotImplementedError

    def complete(self, job_id, token, now):
        raise NotImplementedError

    def fail(self, job_id, token, now):
        raise NotImplementedError

    def cancel(self, job_id, now):
        """Cancel a runnable job and return all jobs whose state changed."""
        raise NotImplementedError

    def view(self, job_id, now):
        raise NotImplementedError

    def snapshot(self, now):
        raise NotImplementedError

    @classmethod
    def restore(cls, payload):
        raise NotImplementedError
