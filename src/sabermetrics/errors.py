"""Exception hierarchy for Sabermetrics.

Three severity tiers:
- RecoverableError: transient failure, caller may retry with backoff
- DegradableError: source unavailable, system continues with reduced functionality
- FatalError: unrecoverable, requires user intervention
"""


class SabermetricsError(Exception):
    """Base for all custom exceptions."""


class RecoverableError(SabermetricsError):
    """Transient failure; caller may retry with backoff."""


class DegradableError(SabermetricsError):
    """Source unavailable but system can continue with degraded functionality."""


class FatalError(SabermetricsError):
    """Unrecoverable; requires user intervention."""


# Specific subclasses
class APIRateLimitError(RecoverableError):
    """API rate limit exceeded."""


class NetworkError(RecoverableError):
    """Network connectivity issue."""


class SourceUnavailableError(DegradableError):
    """A data source is temporarily unavailable."""


class LLMCostCeilingExceeded(FatalError):
    """Monthly LLM cost ceiling has been exceeded."""


class SchemaValidationError(FatalError):
    """Data failed schema validation."""


class CommanderNotFoundError(FatalError):
    """Requested commander does not exist in the database."""
