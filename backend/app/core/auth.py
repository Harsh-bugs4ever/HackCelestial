"""Who is asking, and are they allowed to.

The product claim is human-in-the-loop accountability: the AI recommends, a
*named* manager decides, and Layer 4 learns from that decision. That claim only
holds if the name is verified. Previously ``decided_by`` was free text in the
request body, so any caller could approve a six-figure rate change signed
"manager" - the audit trail recorded authorship it could not prove.

Principals come from ``AUTH_USERS`` as ``token:Display Name:role`` triples,
comma-separated. No user table: a resort's real identity provider is its HRMS
or an SSO tenant, and inventing a half-finished one here would be worse than
deferring to a token list that ops can rotate with one env var.

With no tokens configured the API stays open for local demos, but says so
loudly at boot and in ``/health`` - a silent open deployment is the failure
mode worth engineering against.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from hmac import compare_digest

from fastapi import Depends, Header, HTTPException

from app.core.config import settings

log = logging.getLogger(__name__)

# Every decision surface a role can be granted. Engine-scoped so that the
# person who may re-price a suite is not automatically the person who may
# approve capital maintenance.
PERMISSIONS = (
    "decide:demand",
    "decide:maintenance",
    "decide:workforce",
    "decide:guest",
    "engines:run",
    "data:import",
    "action:undo",
)

# What each permission means in a sentence a manager can read. An error message
# built by string-munging the permission id ("may not engines run") is worse
# than no message.
PERMISSION_LABEL = {
    "decide:demand": "approve pricing recommendations",
    "decide:maintenance": "approve maintenance recommendations",
    "decide:workforce": "approve staffing recommendations",
    "decide:guest": "approve guest recommendations",
    "engines:run": "run the engines",
    "data:import": "import data",
    "action:undo": "undo an executed action",
}

ROLES: dict[str, tuple[str, ...]] = {
    "gm": PERMISSIONS,
    "revenue_manager": ("decide:demand", "engines:run", "action:undo"),
    "ops_manager": (
        "decide:maintenance",
        "decide:workforce",
        "engines:run",
        "action:undo",
    ),
    "duty_manager": ("decide:guest", "decide:workforce", "engines:run"),
    "analyst": ("engines:run",),
    "viewer": (),
}

# Which role-permission a card needs, by engine.
ENGINE_PERMISSION = {
    "demand": "decide:demand",
    "maintenance": "decide:maintenance",
    "workforce": "decide:workforce",
    "guest": "decide:guest",
}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    permissions: frozenset[str] = field(default_factory=frozenset)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def is_anonymous(self) -> bool:
        return self.role == "demo"


# The principal used when no tokens are configured. Full rights, obvious name -
# an audit row reading "Demo Manager (unauthenticated)" cannot be mistaken for
# a real approval when someone reads the decision log later.
DEMO = Principal(
    name="Demo Manager (unauthenticated)",
    role="demo",
    permissions=frozenset(PERMISSIONS),
)


def _parse_users(raw: str) -> dict[str, Principal]:
    users: dict[str, Principal] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) != 3 or not all(parts):
            log.warning("ignoring malformed AUTH_USERS entry %r", entry)
            continue
        token, name, role = parts
        if role not in ROLES:
            log.warning("ignoring user %r with unknown role %r", name, role)
            continue
        users[token] = Principal(name=name, role=role, permissions=frozenset(ROLES[role]))
    return users


_USERS = _parse_users(settings.auth_users)

if _USERS:
    log.info("auth enabled - %d principal(s) configured", len(_USERS))
elif settings.render_external_url:
    log.error(
        "AUTH DISABLED on a public deployment (%s). Anyone who can reach this "
        "URL can approve actions. Set AUTH_USERS to enable role checks.",
        settings.render_external_url,
    )
else:
    log.warning("auth disabled - local demo mode, every caller is %s", DEMO.name)


def auth_enabled() -> bool:
    return bool(_USERS)


def principal_for(token: str | None) -> Principal | None:
    """Resolve a bearer token, in constant time against every known token."""
    if not _USERS:
        return DEMO
    if not token:
        return None
    # Compare against all tokens rather than a dict hit, so response time does
    # not leak which prefixes are valid.
    found: Principal | None = None
    for known, principal in _USERS.items():
        if compare_digest(known, token):
            found = principal
    return found


def _token_from(authorization: str | None, x_api_key: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return x_api_key.strip() if x_api_key else None


def current_principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """FastAPI dependency: the authenticated caller, or 401."""
    principal = principal_for(_token_from(authorization, x_api_key))
    if principal is None:
        raise HTTPException(
            401,
            "authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def requires(permission: str):
    """Dependency factory gating a route on one permission."""

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(
                403,
                f"role '{principal.role}' may not "
                f"{PERMISSION_LABEL.get(permission, permission)}",
            )
        return principal

    return dependency


def require_engine_permission(principal: Principal, engine: str) -> None:
    """Gate a decision on the engine that produced the card."""
    permission = ENGINE_PERMISSION.get(engine)
    if permission is None:
        raise HTTPException(400, f"unknown engine '{engine}'")
    if not principal.can(permission):
        raise HTTPException(
            403,
            f"role '{principal.role}' may not approve {engine} recommendations",
        )
