"""Users API routes"""

# import asyncio
# import datetime

# import asyncpg
# import orjson
# import sqlalchemy
from fastapi import APIRouter # , Depends, HTTPException, Request
# from pydantic import BaseModel
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy.orm import selectinload

# from api.auth import require_auth
# from db import get_db, engine
# from models.user import User, UserProject


router = APIRouter()


# @limiter.limit("3/day")
async def create_user():
    """Create a new user"""
    # TODO: implement create user functionality


# @protect
async def update_user():
    """Update user details"""
    # TODO: implement update user functionality


# @protect
async def get_user():
    """Get user details"""
    # TODO: implement get user functionality

# @protect
async def delete_user():  # can only delete their own user!!! don't let them delete other users!!!
    """Delete a user account"""
    # TODO: implement delete user functionality


# disabled for 30 days, no login -> delete


# @protect
async def is_pending_deletion():
    """Check if a user account is pending deletion"""
    # TODO: implement is pending deletion functionality


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
