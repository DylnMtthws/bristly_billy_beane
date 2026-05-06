"""Rate limiter for API requests.

Simple synchronous rate limiter using time.sleep().
Default: 1 request per second (self-imposed for all external APIs).
"""

import time
from functools import wraps
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)


class RateLimiter:
    """Simple token-bucket rate limiter.

    Args:
        requests_per_second: Maximum requests per second. Defaults to 1.
    """

    def __init__(self, requests_per_second: float = 1.0) -> None:
        self.min_interval = 1.0 / requests_per_second
        self._last_request_time: float = 0.0

    def wait(self) -> None:
        """Block until the next request is allowed."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def __call__(self, func: F) -> F:
        """Use as a decorator to rate-limit a function."""

        @wraps(func)
        def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.wait()
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]
