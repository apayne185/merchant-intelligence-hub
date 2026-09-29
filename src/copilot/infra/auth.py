"""
Bearer-JWT authentication for the copilot API — the resource-server half of
OAuth2: validate an access token an identity provider issued, don't be the
identity provider. See DECISIONS.md D52.

Two key sources, both standard:
  - AUTH_JWKS_URL: RS256/ES256 tokens from a real IdP (Auth0, Entra ID,
    Keycloak, Cognito) — public keys fetched from its JWKS endpoint and
    cached, so key rotation needs no redeploy.
  - AUTH_JWT_SECRET: HS256 shared secret — for local docker-compose and
    tests, minted with `scripts/mint_dev_token.py`.

AUTH_MODE=none (the default outside production — settings.py refuses it
under APP_ENV=production) returns an anonymous principal so local dev and
the existing test suite need no token.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from src.copilot.infra.metrics import GUARDRAIL_EVENTS
from src.copilot.infra.settings import Settings, get_settings

ANONYMOUS = "anonymous"

# auto_error=False: we raise our own 401 with a WWW-Authenticate header
# (RFC 6750) instead of FastAPI's bare 403 for a missing header.
_bearer = HTTPBearer(auto_error=False, description="OAuth2 access token (JWT)")


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str] = frozenset()
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_anonymous(self) -> bool:
        return self.subject == ANONYMOUS


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def _extract_scopes(claims: dict[str, Any]) -> frozenset[str]:
    # OAuth2 `scope` is a space-delimited string (RFC 8693); Entra ID uses
    # `scp`, some IdPs send a list. Accept all three shapes.
    raw = claims.get("scope", claims.get("scp", ""))
    if isinstance(raw, str):
        return frozenset(raw.split())
    if isinstance(raw, list):
        return frozenset(str(s) for s in raw)
    return frozenset()


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    key: Any
    if settings.jwt_jwks_url:
        key = _jwks_client(settings.jwt_jwks_url).get_signing_key_from_jwt(token).key
    else:
        key = settings.jwt_secret
    required = ["exp", "sub"]
    if settings.jwt_audience:
        required.append("aud")
    if settings.jwt_issuer:
        required.append("iss")
    return jwt.decode(
        token,
        key=key,
        algorithms=list(settings.jwt_algorithms),
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={"require": required},
        leeway=30,
    )


def _unauthorized(error: str, description: str) -> HTTPException:
    GUARDRAIL_EVENTS.labels(event=f"auth_{error}").inc()
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=error,
        headers={"WWW-Authenticate": f'Bearer error="{error}", error_description="{description}"'},
    )


def get_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if settings.auth_mode == "none":
        return Principal(subject=ANONYMOUS)
    if creds is None:
        raise _unauthorized("invalid_request", "missing bearer token")
    try:
        claims = decode_token(creds.credentials, settings)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("invalid_token", "token expired") from None
    except (jwt.InvalidTokenError, jwt.PyJWKClientError):
        # Never echo the decode error to the client — it can reveal which
        # check failed (aud vs iss vs signature), useful to an attacker.
        raise _unauthorized("invalid_token", "token validation failed") from None

    scopes = _extract_scopes(claims)
    if settings.required_scope and settings.required_scope not in scopes:
        GUARDRAIL_EVENTS.labels(event="auth_insufficient_scope").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient_scope",
            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{settings.required_scope}"'},
        )
    return Principal(subject=str(claims["sub"]), scopes=scopes, claims=claims)


def mint_dev_token(
    secret: str,
    subject: str = "dev-user",
    scopes: tuple[str, ...] = ("copilot:ask",),
    ttl_seconds: int = 3600,
    audience: str | None = None,
    issuer: str | None = None,
) -> str:
    """HS256 token for local compose/tests. Production tokens come from the
    IdP, never from this function."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": subject,
        "scope": " ".join(scopes),
        "iat": now,
        "nbf": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    if audience:
        claims["aud"] = audience
    if issuer:
        claims["iss"] = issuer
    return jwt.encode(claims, secret, algorithm="HS256")
