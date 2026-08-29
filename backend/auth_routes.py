"""Authentication routes: login, register, /me."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from auth import (
    CurrentUser,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from database import get_db, log_audit

logger = logging.getLogger(__name__)

router = APIRouter()


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str
    tenant_name: str = "default"


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    display_name: str
    role: str
    tenant_id: str


class UserInfo(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    tenant_id: str


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest):
    """Register a new user and tenant.

    If tenant_name doesn't exist, creates it.
    First user in a tenant becomes admin.
    """
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    db = await get_db()
    try:
        # Check if email already exists
        cursor = await db.execute("SELECT user_id FROM users WHERE email = ?", (req.email,))
        if await cursor.fetchone():
            raise HTTPException(409, "Email already registered")

        # Create or find tenant
        tenant_id = req.tenant_name.lower().strip().replace(" ", "_")
        cursor = await db.execute("SELECT tenant_id FROM tenants WHERE tenant_id = ?", (tenant_id,))
        if not await cursor.fetchone():
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?)",
                (tenant_id, req.tenant_name),
            )

        # Check if this is the first user in the tenant (becomes admin)
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users WHERE tenant_id = ?", (tenant_id,))
        row = await cursor.fetchone()
        role = "admin" if row["cnt"] == 0 else "analyst"

        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat()

        await db.execute(
            "INSERT INTO users (user_id, email, password_hash, display_name, role, tenant_id, created_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, req.email, hash_password(req.password), req.display_name, role, tenant_id, now, True),
        )
        await db.commit()

        await log_audit(tenant_id, "user.registered", "user", user_id,
                        user_id=user_id, details={"email": req.email, "role": role})

        token = create_access_token(user_id, tenant_id, role, req.email)
        logger.info("User registered: %s (%s) tenant=%s role=%s", user_id, req.email, tenant_id, role)

        return AuthResponse(
            access_token=token,
            user_id=user_id,
            email=req.email,
            display_name=req.display_name,
            role=role,
            tenant_id=tenant_id,
        )
    finally:
        await db.close()


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest):
    """Authenticate and return a JWT."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT user_id, email, password_hash, display_name, role, tenant_id, is_active "
            "FROM users WHERE email = ?",
            (req.email,),
        )
        row = await cursor.fetchone()

        if not row or not verify_password(req.password, row["password_hash"]):
            raise HTTPException(401, "Invalid email or password")

        if not row["is_active"]:
            raise HTTPException(403, "Account deactivated")

        token = create_access_token(row["user_id"], row["tenant_id"], row["role"], row["email"])

        await log_audit(row["tenant_id"], "user.login", "user", row["user_id"],
                        user_id=row["user_id"], details={"email": req.email})

        return AuthResponse(
            access_token=token,
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            role=row["role"],
            tenant_id=row["tenant_id"],
        )
    finally:
        await db.close()


@router.get("/me", response_model=UserInfo)
async def get_me(user: CurrentUser = Depends(get_current_user)):
    """Return the current authenticated user's info."""
    return UserInfo(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        tenant_id=user.tenant_id,
    )
