"""Authentication and authorization for EntitlementLedger.

Provides:
- JWT access tokens with tenant_id + role claims
- Password hashing with bcrypt
- FastAPI dependencies for current_user, require_role
- Tenant isolation via current_user.tenant_id
"""
from __future__ import annotations

import os
import secrets
import json
from datetime import datetime, timedelta
from typing import Optional

ENV = os.environ.get("ENV", "development")

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from database import get_db

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "")
if not SECRET_KEY and ENV == "production":
    raise RuntimeError(
        "JWT_SECRET_KEY must be set in production. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )
if not SECRET_KEY:
    import warnings
    warnings.warn(
        "JWT_SECRET_KEY not set — using random key (tokens will not survive restarts).",
        stacklevel=1,
    )
    SECRET_KEY = secrets.token_urlsafe(48)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "480"))  # 8 hours

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: str,
    tenant_id: str,
    role: str,
    email: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "email": email,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# ---------------------------------------------------------------------------
# FastAPI Dependencies
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)


class CurrentUser:
    """Authenticated user context."""
    def __init__(self, user_id: str, email: str, tenant_id: str, role: str, display_name: str):
        self.user_id = user_id
        self.email = email
        self.tenant_id = tenant_id
        self.role = role
        self.display_name = display_name


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    """Extract and validate the current user from the Authorization header.

    Returns CurrentUser with tenant_id for all tenant-scoped queries.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    role = payload.get("role")
    email = payload.get("email")

    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required claims",
        )

    # Verify user still exists and is active
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT user_id, display_name, is_active FROM users WHERE user_id = ? AND tenant_id = ?",
            (user_id, tenant_id),
        )
        row = await cursor.fetchone()
        if not row or not row["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or deactivated",
            )
        display_name = row["display_name"]
    finally:
        await db.close()

    return CurrentUser(
        user_id=user_id,
        email=email or "",
        tenant_id=tenant_id,
        role=role or "analyst",
        display_name=display_name,
    )


def require_role(*allowed_roles: str):
    """Dependency factory that restricts access to specific roles."""

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed_roles and "admin" not in user.role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' not authorized. Required: {allowed_roles}",
            )
        return user
    return _check


async def optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[CurrentUser]:
    """Like get_current_user but returns None if not authenticated.
    Useful for endpoints that behave differently for authenticated vs anonymous.
    """
    if not credentials:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None
