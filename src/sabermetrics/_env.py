"""Load environment variables from .env once, at process startup.

The app reads credentials (ANTHROPIC_API_KEY, SABER_SECRET_KEY, ...) straight
from os.environ. Nothing in the code base loaded .env, so those values only took
effect if a caller had exported them by hand — which is why launching the server
without that manual step left ANTHROPIC_API_KEY unset. This centralizes the load
so every entry point (the CLI group and the Flask app factory) picks up .env
automatically.

Real environment variables always win over .env (load_dotenv defaults to
override=False), so an explicitly exported value is never clobbered by a stale
.env entry.
"""

from __future__ import annotations

from dotenv import load_dotenv

_loaded = False


def load_env() -> None:
    """Load .env into os.environ once. Idempotent; cheap to call repeatedly.

    Searches upward from the current working directory for a .env file. The app
    is always run from the project root (paths like ``data/...`` are relative),
    so this resolves the repo's .env in every supported launch mode.
    """
    global _loaded
    if _loaded:
        return
    load_dotenv()
    _loaded = True
