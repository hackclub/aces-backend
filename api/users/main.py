"""Users API routes"""

# import asyncio

# import asyncpg
# import orjson
import sqlalchemy
from fastapi import APIRouter, Request, Depends, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta
from sqlalchemy.exc import IntegrityError
# from sqlalchemy.orm import selectinload

from api.auth.main import require_auth, generate_session_id # type: ignore
from db import get_db
from models.user import User


router = APIRouter()

class CreateUserRequest(BaseModel):

    email: str

class UpdateUserRequest(BaseModel):

    id: int
    email: Optional[str]

class DeleteUserRequest(BaseModel):
    
    id: int
    email: str # for silly, maybe not needed...

# there'll be a second endpoint for admins to update
#TODO: Send an email that tells them to verify that their email was right
# @protect
@router.post("/api/users/update")
@require_auth
async def update_user(
    request: Request,
    update_request: UpdateUserRequest,
    response: Response,
    session: AsyncSession = Depends(get_db)
):
@router.post("/api/users/update")
@require_auth
async def update_user(
    request: Request,
    update_request: UpdateUserRequest,
    response: Response,
    session: AsyncSession = Depends(get_db)
):
    """Update user details"""
    
    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User).where(
            User.id == update_request.id
        )
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404) # user doesn't exist

    if user.email != user_email:
        raise HTTPException(status_code=403) # they're trying to update someone elses email, no!
    
    update_data = update_request.model_dump(exclude_unset=True, exclude={"id"})

    ALLOWED_UPDATE_FIELDS = {"email"}
    for field, value in update_data.items():
        if field in ALLOWED_UPDATE_FIELDS:
            setattr(user, field, value)

    if update_request.email is not None:
        ret_jwt = await generate_session_id(update_request.email)
        response.set_cookie(
            key="sessionId", value=ret_jwt, httponly=True, secure=True, max_age=604800
        )   
    try:
        await session.commit()
        await session.refresh(user) #TODO: figure out why we're refreshing every time and if its needed
    except Exception:
        await session.rollback()
        return Response(status_code=500)
    
    return Response(status_code=204)


# @protect
async def get_user(
    request: Request,
    create_request: CreateUserRequest,
    session: AsyncSession = Depends(get_db)
):
    """Get user details"""
    # TODO: implement get user functionality
    #TODO: Figure out how many users this allows (just yourself? everyone? a subset?)


# @protect
@router.post("/api/users/delete")
@require_auth
async def delete_user(
    request: Request,
    delete_request: DeleteUserRequest,
    # response: Response,
    session: AsyncSession = Depends(get_db)
):  # can only delete their own user!!! don't let them delete other users!!!
    """Delete a user account"""
    # TODO: implement delete user functionality

    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User).where(
            User.id == delete_request.id,
            User.email == delete_request.email
        )
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404) # user doesn't exist

    if user.email != user_email:
        raise HTTPException(status_code=403) # they're trying to delete someone elses email, no!
    
    user.marked_for_deletion = True
    user.date_for_deletion = datetime.now(timezone.utc) + timedelta(days=30)

    try:
        await session.commit()
        await session.refresh(user)
    except:
        return Response(status_code=500)

    return JSONResponse({"success": True, "deletion_date": user.date_for_deletion}, status_code=200)




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
