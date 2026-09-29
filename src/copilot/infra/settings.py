"""
Runtime configuration for the copilot API's platform layer, read once from
the environment into a frozen dataclass.

One typed object instead of `os.environ.get(...)` scattered across modules:
misconfiguration (AUTH_MODE=jwt with no key, AUTH_MODE=none in production)
fails at startup in `validate()`, not on the first request that happens to
hit the misconfigured path. Request-scoped code takes it via
`Depends(get_settings)` so tests override it with
`app.dependency_overrides[get_settings]` instead of mutating process env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

AuthMode = Literal["none", "jwt"]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw in (None, "") else int(raw)


def _env_list(name: str, default: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.environ.get(name, default).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    environment: str = "development"

    # --- Auth (src/copilot/infra/auth.py, D52) ---
    auth_mode: AuthMode = "none"
    jwt_secret: str | None = None
    jwt_jwks_url: str | None = None
    jwt_algorithms: tuple[str, ...] = ("HS256",)
    jwt_audience: str | None = None
    jwt_issuer: str | None = None
    required_scope: str | None = "copilot:ask"

    # --- Rate limiting + response cache (D53) ---
    rate_limit_per_minute: int = 60
    redis_url: str | None = None
    cache_ttl_seconds: int = 0

    # --- Audit log (D54) ---
    audit_database_url: str | None = None

    # --- Logging (D55) ---
    log_format: Literal["json", "text"] = "text"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        auth_mode = os.environ.get("AUTH_MODE", "none").lower()
        jwks_url = os.environ.get("AUTH_JWKS_URL") or None
        return cls(
            environment=os.environ.get("APP_ENV", "development").lower(),
            auth_mode=auth_mode,  # type: ignore[arg-type]  # validated below
            jwt_secret=os.environ.get("AUTH_JWT_SECRET") or None,
            jwt_jwks_url=jwks_url,
            # Explicit allowlist, never "whatever the token header says" —
            # that's the classic alg-confusion hole (alg=none, or an RS256
            # public key reused as an HS256 secret). Default follows the key
            # type actually configured.
            jwt_algorithms=_env_list("AUTH_JWT_ALGORITHMS", "RS256" if jwks_url else "HS256"),
            jwt_audience=os.environ.get("AUTH_JWT_AUDIENCE") or None,
            jwt_issuer=os.environ.get("AUTH_JWT_ISSUER") or None,
            required_scope=os.environ.get("AUTH_REQUIRED_SCOPE", "copilot:ask") or None,
            rate_limit_per_minute=_env_int("RATE_LIMIT_PER_MINUTE", 60),
            redis_url=os.environ.get("REDIS_URL") or None,
            cache_ttl_seconds=_env_int("CACHE_TTL_SECONDS", 0),
            audit_database_url=os.environ.get("AUDIT_DATABASE_URL") or None,
            log_format="json" if os.environ.get("LOG_FORMAT", "text").lower() == "json" else "text",
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        ).validate()

    def validate(self) -> Settings:
        if self.auth_mode not in ("none", "jwt"):
            raise ValueError(f"AUTH_MODE must be 'none' or 'jwt', got {self.auth_mode!r}")
        if self.auth_mode == "jwt" and not (self.jwt_secret or self.jwt_jwks_url):
            raise ValueError("AUTH_MODE=jwt requires AUTH_JWT_SECRET or AUTH_JWKS_URL")
        # Fail closed: an unauthenticated production deployment is a
        # misconfiguration, not a mode. `none` exists for local dev/tests.
        if self.environment == "production" and self.auth_mode == "none":
            raise ValueError("APP_ENV=production requires AUTH_MODE=jwt")
        if "none" in (a.lower() for a in self.jwt_algorithms):
            raise ValueError("AUTH_JWT_ALGORITHMS must not include 'none'")
        if self.rate_limit_per_minute < 0 or self.cache_ttl_seconds < 0:
            raise ValueError("RATE_LIMIT_PER_MINUTE and CACHE_TTL_SECONDS must be >= 0")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once — env vars don't change under a
    running process, same reasoning as tracing.get_tracer()."""
    return Settings.from_env()
