from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=256)


class EmailIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=256)
    password: str = Field(min_length=10, max_length=256)


class PatchMeIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    current_password: str | None = Field(default=None, max_length=256)
    new_password: str | None = Field(default=None, min_length=10, max_length=256)


class MembershipOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    org_name: str
    org_slug: str
    role: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    email_verified: bool
    is_superadmin: bool
    created_at: datetime


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    csrf_token: str | None = None
