"""Users API routes"""

# import asyncio

# import asyncpg
# import orjson
from datetime import datetime, timedelta, timezone

import sqlalchemy
import validators
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

# from sqlalchemy.orm import selectinload
from api.v1.auth.main import require_auth, send_otp_code  # type: ignore
from db import get_db
from models.user import User

router = APIRouter()


class UserResponse(BaseModel):
    """Public representation of a user"""

    id: int
    email: str
    permissions: list[int]
    marked_for_deletion: bool


class UpdateUserRequest(BaseModel):
    """Update user request from client"""

    email: str


class DeleteUserRequest(BaseModel):
    """Delete user request from client"""

    id: int
    email: str  # for silly, maybe not needed...


# there'll be a second endpoint for admins to update
# @protect
@router.patch("/me")
@require_auth
async def update_user(
    request: Request,
    update_request: UpdateUserRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
):
    """Update user details"""

    user_email = request.state.user["sub"]

    if validators.email(update_request.email) is False:
        raise HTTPException(status_code=400, detail="Invalid email format")

    user_raw = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404)  # user doesn't exist

    try:
        new_user_raw = await session.execute(
            sqlalchemy.select(User).where(User.email == update_request.email)
        )

        new_user = new_user_raw.scalar_one_or_none()
        if new_user is not None and new_user.id != user.id:
            raise HTTPException(status_code=409, detail="Email already in use")

        await send_otp_code(to_email=update_request.email, old_email=user_email)
        response.status_code = 200
    except HTTPException:
        raise
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        raise HTTPException(status_code=500) from e

    return response


# @protect
@router.get("/me")
@require_auth
async def get_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Get user details"""

    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404)  # user doesn't exist

    return UserResponse(
        id=user.id,
        email=user.email,
        permissions=user.permissions,
        marked_for_deletion=user.marked_for_deletion,
    )


# @protect
@router.delete("/me")
@require_auth
async def delete_user(
    request: Request,
    delete_request: DeleteUserRequest,
    # response: Response,
    session: AsyncSession = Depends(get_db),
):  # can only delete their own user!!! don't let them delete other users!!!
    """Delete a user account"""
    # TODO: implement delete user functionality

    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User).where(
            User.id == delete_request.id, User.email == delete_request.email
        )
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404)  # user doesn't exist

    if user.email != user_email:
        raise HTTPException(
            status_code=403
        )  # they're trying to delete someone elses user, no!

    user.marked_for_deletion = True
    user.date_for_deletion = datetime.now(timezone.utc) + timedelta(days=30)

    try:
        await session.commit()
        await session.refresh(user)
    except Exception:  # type: ignore # pylint: disable=broad-exception-caught
        return Response(status_code=500)

    if not user.date_for_deletion:
        raise HTTPException(status_code=500)

    return JSONResponse(
        {"deletion_date": user.date_for_deletion.isoformat()},  # type: ignore
        status_code=200,
    )


# disabled for 30 days, no login -> delete


# @protect
async def is_pending_deletion():
    """Check if a user account is pending deletion"""
    # TODO: implement is pending deletion functionality
    # TODO: figure out how we want to decide if they're able to get deletion status


# async def run():
#     conn = await asyncpg.connect(user='user', password='password',
#                                  database='database', host='127.0.0.1')
#     values = await conn.fetch(
#         'SELECT * FROM mytable WHERE id = $1',
#         10,
#     )
#     await conn.close()

# asyncio.run(run())

# def foo():
#     return "abc"
