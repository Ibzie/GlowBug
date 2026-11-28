from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.models.user import User

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except UnknownHashError:
        return False


def login_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = str(user.id)


def logout_session(request: Request) -> None:
    request.session.clear()


async def get_session_user(request: Request, session: AsyncSession) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    try:
        return await session.get(User, UUID(str(user_id)))
    except ValueError:
        request.session.clear()
        return None


async def require_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    user = await get_session_user(request, session)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


async def require_api_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    user = await get_session_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
