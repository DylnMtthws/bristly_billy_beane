"""Tailnet identity: who Tailscale says is making this request.

The app sits behind ``tailscale serve``, which terminates TLS on the tailnet and
proxies plain HTTP to 127.0.0.1. On every proxied request it sets three headers
describing the authenticated tailnet user, and — this is the part that makes
them usable — it **strips any client-supplied ``Tailscale-*`` headers first**,
so a remote caller cannot inject its own identity.

That gives us authentication without passwords, invite links, password resets,
or a login form. Tailscale already knows who everyone is; the ``users`` table
only has to say what they are allowed to do.

Threat model, stated plainly because header-based auth is exactly the thing that
goes wrong when it is left implicit:

* **A remote tailnet user cannot forge an identity.** Tailscale overwrites the
  headers at the proxy.
* **A non-tailnet caller cannot reach the app at all**, because it binds
  127.0.0.1 and the only listener on the tailnet is the Tailscale proxy.
* **A process already running on the host can forge one**, by connecting to
  127.0.0.1 directly and setting the headers itself. That is not a hole this
  module can close — a local process can also just read the SQLite file. The
  host is the trust boundary, and it is the admin's own machine.
* **Funnel traffic is anonymous.** ``tailscale funnel`` exposes the same port to
  the public internet and does *not* set identity headers. Requests with no
  identity are rejected rather than treated as some default user, so turning
  Funnel on by accident locks strangers out instead of letting them in.

The source check below is defence in depth against the realistic misconfiguration
rather than against a determined attacker: if the app is ever bound to a LAN
interface, a request arriving from 192.168.x.x with hand-set headers fails the
CGNAT check and is refused.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Tailscale assigns every node an address in the CGNAT range (RFC 6598).
#: A proxied request's forwarded client address is always inside it.
TAILNET_RANGE = ipaddress.ip_network("100.64.0.0/10")

LOGIN_HEADER = "Tailscale-User-Login"
NAME_HEADER = "Tailscale-User-Name"
PROFILE_PIC_HEADER = "Tailscale-User-Profile-Pic"


@dataclass(frozen=True, slots=True)
class TailscaleIdentity:
    """A tailnet user, as reported by the Tailscale proxy.

    ``login`` is the stable key. It is not necessarily an email: Tailscale
    renders GitHub SSO logins as ``someone@github``, Google as
    ``someone@example.com``. Storing it in the ``users.email`` column would be
    wrong for the first form, which is why it has its own column.
    """

    login: str
    display_name: str = ""
    profile_pic: str = ""

    @property
    def suggested_name(self) -> str:
        """Display name to seed a new account with."""
        return self.display_name or self.login.split("@", 1)[0]


def is_tailnet_address(addr: str | None) -> bool:
    """Return True if ``addr`` is a Tailscale CGNAT address."""
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr) in TAILNET_RANGE
    except ValueError:
        return False


def identity_from_headers(
    headers, remote_addr: str | None, *, require_tailnet_source: bool = True
) -> TailscaleIdentity | None:
    """Extract the tailnet identity from one request, or None.

    Args:
        headers: The request headers (any case-insensitive mapping).
        remote_addr: The client address *after* proxy headers have been
            applied, i.e. the tailnet address of the calling node.
        require_tailnet_source: Verify ``remote_addr`` is inside the Tailscale
            CGNAT range before believing the headers. Disable only in tests.

    Returns:
        The identity, or None when the request carries no usable one. None is
        always "not authenticated" — never a default or anonymous user.
    """
    login = (headers.get(LOGIN_HEADER) or "").strip()
    if not login:
        return None

    if require_tailnet_source and not is_tailnet_address(remote_addr):
        # Headers present but the request did not come from the tailnet proxy.
        # Refuse rather than downgrade: this is either a misconfiguration or
        # someone probing, and both deserve the same answer.
        logger.warning(
            "Rejected %s=%r from non-tailnet address %r. The app should be "
            "reachable only via `tailscale serve`.",
            LOGIN_HEADER,
            login,
            remote_addr,
        )
        return None

    return TailscaleIdentity(
        login=login,
        display_name=(headers.get(NAME_HEADER) or "").strip(),
        profile_pic=(headers.get(PROFILE_PIC_HEADER) or "").strip(),
    )
