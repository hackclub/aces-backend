"""Users API routes"""

# import asyncio

# import asyncpg
# import orjson
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import validators

# from sqlalchemy.orm import selectinload
from api.auth.main import generate_session_id, require_auth  # type: ignore
from db import get_db
from models.user import User

router = APIRouter()


class CreateUserRequest(BaseModel):
    """Create user request from client"""

    email: str


class UpdateUserRequest(BaseModel):
    """Update user request from client"""

    id: int
    email: Optional[str]


class DeleteUserRequest(BaseModel):
    """Delete user request from client"""

    id: int
    email: str  # for silly, maybe not needed...


# there'll be a second endpoint for admins to update
# TODO: Send an email that tells them to verify that their email was right
# @protect
@router.post("/api/users/update")
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
        sqlalchemy.select(User).where(User.id == update_request.id)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404)  # user doesn't exist

    if user.email != user_email:
        raise HTTPException(
            status_code=403
        )  # they're trying to update someone elses email, no!

    update_data = update_request.model_dump(exclude_unset=True, exclude={"id"})

    allowed_update_fields = {"email"}
    for field, value in update_data.items():
        if field in allowed_update_fields:
            setattr(user, field, value)

    try:
        await session.commit()
        await session.refresh(
            user
        )  # TODO: figure out why we're refreshing every time and if its needed
        if update_request.email is not None:
            ret_jwt = await generate_session_id(update_request.email)
            response.set_cookie(
                key="sessionId",
                value=ret_jwt,
                httponly=True,
                secure=True,
                max_age=604800,
            )
    except Exception:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        return Response(status_code=500)

    return Response(status_code=204)


# @protect
async def get_user(
    _request: Request,
    _create_request: CreateUserRequest,
    _session: AsyncSession = Depends(get_db),
):
    """Get user details"""
    # TODO: implement get user functionality
    # TODO: Figure out how many users this allows (just yourself? everyone? a subset?)


# @protect
@router.post("/api/users/delete")
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
        )  # they're trying to delete someone elses email, no!

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
        {"success": True, "deletion_date": user.date_for_deletion.isoformat()},  # type: ignore
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
