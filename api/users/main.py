import asyncio
import asyncpg
from fastapi import APIRouter

router = APIRouter()

#@limiter.limit("3/day")
async def create_user(): ... 

#@protect
async def update_user(): ... 

#@protect
async def get_user(): ...

#@protect
async def create_project(): ...

#@protect
async def update_project(): ...

#@protect
async def return_projects(): ...

#@protect
async def delete_user(): ... # can only delete their own user!!! please don't let them delete other users!!!
# disabled for 30 days, no login -> delete

#@protect
async def is_pending_deletion(): ...

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