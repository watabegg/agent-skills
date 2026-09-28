def dumps_snapshot(data):
    """Encode an already validated snapshot data structure canonically."""
    raise NotImplementedError


def loads_snapshot(payload):
    """Decode snapshot JSON into primitive Python data or raise InvalidSnapshot."""
    raise NotImplementedError
