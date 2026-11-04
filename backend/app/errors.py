"""Application-level errors shared by service and API layers."""


class StateConflict(Exception):
    """The requested operation conflicts with the record's current state."""


class NotFound(Exception):
    """The requested application record does not exist."""
