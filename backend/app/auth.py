"""Supabase access-token verification and authenticated principal shape."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.config import Settings

AuthRole = Literal["HR", "manager", "integration_service"]


@dataclass(frozen=True)
class Principal:
    """Identity and authorization attributes resolved by the backend."""

    user_id: str
    organization_id: UUID | None
    role: AuthRole
    manager_ref: str | None
    email: str | None = None
    is_demo: bool = False


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    """Reuse a bounded-timeout JWKS client with PyJWT's expiring set cache."""
    return PyJWKClient(url, timeout=5)


def verify_supabase_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify signature, issuer, audience, expiry, and subject for a Supabase JWT."""
    project_url = (settings.supabase_url or "").rstrip("/")
    if not project_url:
        raise HTTPException(status_code=503, detail="Supabase authentication is not configured")

    issuer = f"{project_url}/auth/v1"
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm == "HS256":
            secret = settings.supabase_jwt_secret
            if secret is None or not secret.get_secret_value():
                raise HTTPException(status_code=401, detail="Invalid or expired access token")
            signing_key: Any = secret.get_secret_value()
            allowed_algorithms = ["HS256"]
        elif algorithm in {"ES256", "RS256", "EdDSA"}:
            jwks = _jwks_client(f"{issuer}/.well-known/jwks.json")
            signing_key = jwks.get_signing_key_from_jwt(token).key
            allowed_algorithms = [algorithm]
        else:
            raise HTTPException(status_code=401, detail="Invalid or expired access token")

        claims = jwt.decode(
            token,
            signing_key,
            algorithms=allowed_algorithms,
            audience="authenticated",
            issuer=issuer,
            options={"require": ["exp", "sub", "iss", "aud"]},
        )
        if claims.get("is_anonymous") is True:
            raise HTTPException(status_code=401, detail="Signed-in HR account required")
        UUID(str(claims["sub"]))
        return claims
    except HTTPException:
        raise
    except PyJWKClientConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail="Supabase signing keys are temporarily unavailable",
        ) from exc
    except (jwt.PyJWTError, PyJWKClientError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc
