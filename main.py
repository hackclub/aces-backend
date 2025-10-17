# from fastapi import FastAPI
from typing import Annotated
from fastapi import FastAPI, HTTPException, Request, Form, Depends
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
# from pyairtable import Api
# from pyairtable.formulas import match
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
# from api.auth import client
from api.auth import require_auth, is_user_authenticated
from api.auth import router as auth_router
import orjson
import os
import dotenv
import asyncpg
# from api.users import foo


dotenv.load_dotenv()

app = FastAPI()
app.include_router(auth_router)

# @app.get("/test")
# async def test():
#     return foo()

@app.get("/")
async def home(request: Request):
    # if client.is
    # if client.isAuthenticated() is False:
    #     return HTMLResponse("Not authenticated <a href='/sign-in'>Sign in</a>")

    # return HTMLResponse("Authenticated <a href='/sign-out'>Sign out</a>")
    return FileResponse("static/login.html")

# @require_auth
@app.get("/protectedroute")
async def protected_route(request: Request, depends: str = Depends(is_user_authenticated)):
    return HTMLResponse("<h1>Hello World! This is authenticated!</h1>")

@app.get("/login")
async def serve_login(request: Request):
    pass

# @app.post("/login")
# async def handle_login(email: Annotated[str, Form()], otp: Annotated[int, Form()]):
#     pass

app.mount("/static", StaticFiles(directory="static"), name="static")