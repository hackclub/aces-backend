# from fastapi import FastAPI
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
# from pyairtable import Api
# from pyairtable.formulas import match
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import orjson
import os
import dotenv
import asyncpg
from api.users import foo


dotenv.load_dotenv()

app = FastAPI()

@app.get("/test")
async def test():
    return foo()
