"""Exceptions for the cEDH Deck Lab.

Each subclasses one of the three severity tiers in :mod:`sabermetrics.errors`,
so existing handlers (retry / degrade / halt) keep working unchanged.
"""

from sabermetrics.errors import DegradableError, FatalError, RecoverableError


class CedhError(Exception):
    """Marker mixin so callers can catch every cEDH-layer failure at once."""


class ModelProviderError(CedhError, RecoverableError):
    """The model provider failed transiently (timeout, 5xx, rate limit)."""


class ModelValidationError(CedhError, FatalError):
    """The model returned output that never validated against its schema.

    Raised only after the configured repair retries are exhausted. A malformed
    machine-used response is never silently repaired or partially accepted.
    """


class ModelConfigurationError(CedhError, FatalError):
    """The model gateway is misconfigured (missing credential, unpinned model)."""


class RepositoryUnavailable(CedhError, DegradableError):
    """A backing repository cannot be reached, or the view it needs is absent.

    Degradable on purpose: missing tournament evidence must surface as absent
    evidence in the UI, never as a silently empty result that reads like "no
    cards matched".
    """


class SchemaBoundaryViolation(CedhError, FatalError):
    """A query named a schema outside the ``mtg_v1`` public contract."""


class SimulatorUnavailable(CedhError, DegradableError):
    """The simulator binary or its fixture is not present."""


class SimulatorContractError(CedhError, FatalError):
    """The simulator returned output that does not satisfy its versioned schema."""


class UnsupportedCommander(CedhError, DegradableError):
    """No strategy pack exists for the requested commander identity."""


class CandidateConstraintViolation(CedhError, FatalError):
    """A deterministic constraint on the candidate was violated."""
